import base64
from autobahn.twisted.websocket import WebSocketServerProtocol, \
    WebSocketServerFactory
from jarbas_hive_mind.database import ClientDatabase
from jarbas_hive_mind.exceptions import UnauthorizedKeyError
from ovos_utils.log import LOG
from ovos_utils.messagebus import Message, get_mycroft_bus
from ovos_utils import get_ip
from jarbas_hive_mind.utils import decrypt_from_json, encrypt_as_json
from jarbas_hive_mind.interface import HiveMindMasterInterface
import json
from jarbas_hive_mind.discovery.ssdp import SSDPServer
from jarbas_hive_mind.discovery.upnp_server import UPNPHTTPServer
from jarbas_hive_mind.discovery.zero import ZeroConfAnnounce
from jarbas_hive_mind.speech.listener import WebsocketAudioListener
from jarbas_hive_mind.speech.speaker import WebsocketAudioSource

from mycroft.tts import TTSFactory

import uuid

platform = "HiveMindV0.7"


# protocol
class HiveMindProtocol(WebSocketServerProtocol):

    @staticmethod
    def decode_auth(request):
        auth = request.headers.get("authorization")
        if not auth:
            cookie = request.headers.get("cookie")
            if cookie:
                auth = cookie.replace("X-Authorization=", "")
                userpass_encoded = bytes(auth, encoding="utf-8")
        else:
            userpass_encoded = bytes(auth, encoding="utf-8")
            if userpass_encoded.startswith(b"Basic "):
                userpass_encoded = userpass_encoded[6:-2]
            else:
                userpass_encoded = userpass_encoded[2:-1]

        userpass_decoded = base64.b64decode(userpass_encoded).decode("utf-8")
        name, key = userpass_decoded.split(":")
        return name, key

    def onConnect(self, request):

        LOG.info("Client connecting: {0}".format(request.peer))

        name, key = self.decode_auth(request)

        ip = request.peer.split(":")[1]
        context = {"source": self.peer}
        self.platform = request.headers.get("platform", "unknown")

        try:
            with ClientDatabase() as users:
                user = users.get_client_by_api_key(key)
                if not user:
                    raise UnauthorizedKeyError
                self.crypto_key = users.get_crypto_key(key)
        except UnauthorizedKeyError:
            LOG.error("Client provided an invalid api key")
            self.factory.mycroft_send("hive.client.connection.error",
                                      {"error": "invalid api key",
                                       "ip": ip,
                                       "api_key": key,
                                       "platform": self.platform},
                                      context)
            raise

        # send message to internal mycroft bus
        data = {"ip": ip, "headers": request.headers}
        with ClientDatabase() as users:
            self.blacklist = users.get_blacklist_by_api_key(key)
        self.factory.mycroft_send("hive.client.connect", data, context)
        # return a pair with WS protocol spoken (or None for any) and
        # custom headers to send in initial WS opening handshake HTTP response
        headers = {"server": platform}
        return (None, headers)

    def onOpen(self):
        """
       Connection from client is opened. Fires after opening
       websockets handshake has been completed and we can send
       and receive messages.

       Register client in factory, so that it is able to track it.
       """
        self.factory.register_client(self, self.platform)
        LOG.info("WebSocket connection open.")

    def onMessage(self, payload, isBinary):
        if isBinary:
            LOG.debug(
                "Binary message received: {0} bytes".format(len(payload)))
        else:
            payload = self.decode(payload)
            # LOG.debug(
            #    "Text message received: {0}".format(payload))

        self.factory.on_message(self, payload, isBinary)

    def onClose(self, wasClean, code, reason):
        self.factory.unregister_client(self, reason="connection closed")
        LOG.info("WebSocket connection closed: {0}".format(reason))
        ip = self.peer.split(":")[1]
        data = {"ip": ip, "code": code, "reason": "connection closed",
                "wasClean": wasClean}
        context = {"source": self.peer}
        self.factory.mycroft_send("hive.client.disconnect", data, context)

    def connectionLost(self, reason):
        """
       Client lost connection, either disconnected or some error.
       Remove client from list of tracked connections.
       """
        self.factory.unregister_client(self, reason="connection lost")
        LOG.info("WebSocket connection lost: {0}".format(reason))
        ip = self.peer.split(":")[1]
        data = {"ip": ip, "reason": "connection lost"}
        context = {"source": self.peer}
        self.factory.mycroft_send("hive.client.disconnect", data, context)

    def decode(self, payload):
        payload = payload.decode("utf-8")
        if self.crypto_key:
            if "ciphertext" in payload:
                payload = decrypt_from_json(self.crypto_key, payload)
            else:
                LOG.warning("Message was unencrypted")
        return payload

    def sendMessage(self,
                    payload,
                    isBinary=False,
                    fragmentSize=None,
                    sync=False,
                    doNotCompress=False):
        if isinstance(payload, dict):
            payload = json.dumps(payload)
        if self.crypto_key and not isBinary:
            payload = encrypt_as_json(self.crypto_key, payload)
        if isinstance(payload, str):
            payload = bytes(payload, encoding="utf-8")
        super().sendMessage(payload, isBinary,
                            fragmentSize=fragmentSize,
                            sync=sync,
                            doNotCompress=doNotCompress)


class HiveMind(WebSocketServerFactory):
    def __init__(self, bus=None, announce=True, *args, **kwargs):
        super(HiveMind, self).__init__(*args, **kwargs)
        # list of clients
        self.listener = None
        self.clients = {}
        # ip block policy
        self.ip_list = []
        self.blacklist = True  # if False, ip_list is a whitelist
        # mycroft_ws
        self.bus = bus or get_mycroft_bus()
        self.register_mycroft_messages()

        self.interface = HiveMindMasterInterface(self)
        self.announce = announce
        self.upnp_server = None
        self.ssdp = None
        self.zero = None
        
        # AudioSource for streaming TTS through WS
        self.tts = TTSFactory.create()
        self.audio_source_queue = Queue()
        self.audio_source = WebsocketAudioSource(self.audio_source_queue, self.tts)
        self.audio_source.start()

    def start_announcing(self):
        device_uuid = uuid.uuid4()
        local_ip_address = get_ip()
        hivemind_socket = self.listener.address.replace("0.0.0.0",
                                                        local_ip_address)

        if self.zero is None:
            LOG.info("Registering zeroconf:HiveMind-websocket " +
                     hivemind_socket)
            self.zero = ZeroConfAnnounce(uuid=device_uuid,
                                         port=self.port,
                                         host=hivemind_socket)
            self.zero.daemon = True
            self.zero.start()

        if self.ssdp is None or self.upnp_server is None:
            self.upnp_server = UPNPHTTPServer(8088,
                                              friendly_name="JarbasHiveMind Master",
                                              manufacturer='JarbasAI',
                                              manufacturer_url='https://ai-jarbas.gitbook.io/jarbasai/',
                                              model_description='Jarbas HiveMind',
                                              model_name="HiveMind-core",
                                              model_number="0.9",
                                              model_url="https://github.com/OpenJarbas/HiveMind-core",
                                              serial_number=platform,
                                              uuid=device_uuid,
                                              presentation_url=hivemind_socket,
                                              host=local_ip_address)
            self.upnp_server.start()

            self.ssdp = SSDPServer()
            self.ssdp.register('local',
                               'uuid:{}::upnp:HiveMind-websocket'.format(device_uuid),
                               'upnp:HiveMind-websocket',
                               self.upnp_server.path)
            self.ssdp.start()

    def bind(self, listener):
        self.listener = listener
        if self.announce:
            self.start_announcing()

    @property
    def peer(self):
        if self.listener:
            return self.listener.peer
        return None

    @property
    def node_id(self):
        return self.peer + ":MASTER"

    def mycroft_send(self, type, data=None, context=None):
        data = data or {}
        context = context or {}
        if "client_name" not in context:
            context["client_name"] = platform
        self.bus.emit(Message(type, data, context))

    def register_mycroft_messages(self):
        self.bus.on("message", self.handle_outgoing_mycroft)
        self.bus.on('hive.send', self.handle_send)

    def shutdown(self):
        self.bus.remove('message', self.handle_outgoing_mycroft)
        self.bus.remove('hive.send', self.handle_send)

    # websocket handlers
    def register_client(self, client, platform=None):
        """
       Add client to list of managed connections.
       """
        platform = platform or "unknown"
        LOG.info("registering client: " + str(client.peer))
        t, ip, sock = client.peer.split(":")
        # see if ip address is blacklisted
        if ip in self.ip_list and self.blacklist:
            LOG.warning("Blacklisted ip tried to connect: " + ip)
            self.unregister_client(client, reason="Blacklisted ip")
            return
        # see if ip address is whitelisted
        elif ip not in self.ip_list and not self.blacklist:
            LOG.warning("Unknown ip tried to connect: " + ip)
            #  if not whitelisted kick
            self.unregister_client(client, reason="Unknown ip")
            return
            
        audio_queue = Queue()
        audio_listener = WebsocketAudioListener(
            self, client, audio_queue)
        self.clients[client.peer] = {"instance": client,
                                     "status": "connected",
                                     "platform": platform,
                                     "audio_queue": audio_queue,
                                     "audio_listener": audio_listener}
        audio_listener.start()

    def unregister_client(self, client, code=3078,
                          reason="unregister client request"):
        """
       Remove client from list of managed connections.
       """

        LOG.info("deregistering client: " + str(client.peer))
        if client.peer in self.clients.keys():
            client_data = self.clients[client.peer] or {}
            audio_listener = client_data.get("audio_listener")
            if audio_listener:
                LOG.info("stopping audio listener")
                audio_listener.stop()
            j, ip, sock_num = client.peer.split(":")
            context = {"user": client_data.get("names", ["unknown_user"])[0],
                       "source": client.peer}
            self.bus.emit(
                Message("hive.client.disconnect",
                        {"reason": reason, "ip": ip, "sock": sock_num},
                        context))
            client.sendClose(code, reason)
            self.clients.pop(client.peer)

    def on_message(self, client, payload, isBinary):
        """
       Process message from client, decide what to do internally here
       """
        client_protocol, ip, sock_num = client.peer.split(":")

        if isBinary:
            audio_queue = self.clients[client.peer].get("audio_queue")
            if audio_queue:
                try:
                    audio_queue.put(payload)
                except Exception as e:
                    LOG.error("Could not put audio in queue: " + e)
        else:
            # Check protocol
            data = json.loads(payload)
            payload = data["payload"]
            msg_type = data["msg_type"]
            data["source_peer"] = client.peer

            # slave does not know peer name on master, update it
            if data.get("route"):
                data["route"][-1]["source"] = client.peer
                if self.peer not in data["route"][-1]["targets"]:
                    data["route"][-1]["targets"].append(self.peer)
            else:
                data["route"] = [{"source": client.peer,
                                  "targets": [self.peer]}]

            if msg_type == "bus":
                self.handle_bus_message(payload, client)
            elif msg_type == "propagate":
                self.handle_propagate_message(data, client)
            elif msg_type == "broadcast":
                self.handle_broadcast_message(data, client)
            elif msg_type == "escalate":
                self.handle_escalate_message(data, client)

    # HiveMind protocol messages -  from DOWNstream
    def emit_utterance_to_bus(self, client, utterance):
        bus_message = {
            "type": "recognizer_loop:utterance",
            "data": {
                "utterances": [utterance],
                "context": {
                    "source": client.peer,
                    "destination": ["skills"],
                }
            },
        }
        self.handle_bus_message(bus_message, client)
        
    def handle_bus_message(self, payload, client):
        # Generate mycroft Message
        if isinstance(payload, str):
            payload = json.loads(payload)
        msg_type = payload.get("msg_type") or payload["type"]
        data = payload.get("data") or {}
        context = payload.get("context") or {}
        message = Message(msg_type, data, context)
        message.context["source"] = client.peer
        message.context["destination"] = "skills"
        self.handle_incoming_mycroft(message, client)

    def handle_broadcast_message(self, data, client):
        # Slaves are not allowed to broadcast, by definition broadcast goes
        # downstream only, use propagate instead
        LOG.debug("Ignoring broadcast message from downstream, illegal action")
        # TODO kick client for misbehaviour so it stops doing that?

    def handle_propagate_message(self, data, client):

        payload = data["payload"]

        LOG.info("Received propagate message at: " + self.node_id)
        LOG.debug("ROUTE: " + str(data["route"]))
        LOG.debug("PAYLOAD: " + str(payload))

        self.interface.propagate(payload, data)

    def handle_escalate_message(self, data, client):
        payload = data["payload"]

        LOG.info("Received escalate message at: " + self.node_id)
        LOG.debug("ROUTE: " + str(data["route"]))
        LOG.debug("PAYLOAD: " + str(payload))

        # TODO Try to answer

        # else escalate again
        self.interface.escalate(payload, data)

    # parsed protocol messages
    def handle_incoming_mycroft(self, message, client):
        # A Slave wants to inject a message in internal mycroft bus
        # You are a Master, authorize bus message

        client_protocol, ip, sock_num = client.peer.split(":")

        # messages/skills/intents per user
        if message.msg_type in client.blacklist.get("messages", []):
            LOG.warning(client.peer + " sent a blacklisted message "
                                      "type: " + message.msg_type)
            return
        # TODO check intent / skill that will trigger

        # send client message to internal mycroft bus
        LOG.info("Forwarding message to mycroft bus from client: " +
                 str(client.peer))
        self.mycroft_send(message.msg_type, message.data, message.context)

    def handle_client_bus(self, message, client):
        # this message is going inside the client bus
        # take any metrics you need
        LOG.info("Monitoring bus from client: " + client.peer)
        assert isinstance(message, Message)

    # mycroft handlers
    def handle_send(self, message):
        payload = message.data.get("payload")
        peer = message.data.get("peer")
        msg_type = message.data["msg_type"]
        if msg_type == "propagate":
            self.interface.propagate(payload, message.data)

        elif msg_type == "broadcast":
            # slaves can not broadcast and will send a bus message instead
            # if the mycroft device has it's own hive the broadcast is
            # handled here
            self.interface.broadcast(payload, message.data)

        elif msg_type == "escalate":
            # only slaves can escalate, ignore silently
            pass
        # NOT a protocol specific message, send directly to requested peer
        elif peer:
            if peer in self.clients:
                # send message to client
                client = self.clients[peer].get("instance")
                self.interface.send(payload, client)
            else:
                LOG.error("That client is not connected")
                self.mycroft_send("hive.client.send.error",
                                  {"error": "That client is not connected",
                                   "peer": peer}, message.context)

    def handle_outgoing_mycroft(self, message=None):
        # forward internal messages to clients if they are the target
        if isinstance(message, dict):
            message = json.dumps(message)
        if isinstance(message, str):
            message = Message.deserialize(message)
        if message.msg_type == "complete_intent_failure":
            message.msg_type = "hive.complete_intent_failure"
        message.context = message.context or {}
        peers = message.context.get("destination") or []
        if not isinstance(peers, list):
            peers = [peers]
        for peer in peers:
            if peer and peer in self.clients:
                client = self.clients[peer].get("instance")
                payload = {"msg_type": "bus",
                           "payload": message.serialize()
                           }
                self.interface.send(payload, client)
