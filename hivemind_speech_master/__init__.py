from ovos_utils.log import LOG
from jarbas_hive_mind import HiveMindListener
from jarbas_hive_mind.master import HiveMind, HiveMindProtocol
from hivemind_speech_master.speech.listener import WebsocketAudioListener
from hivemind_speech_master.speech.stream_listener import \
    WebsocketAudioStreamingListener
from hivemind_speech_master.speech.speaker import WebsocketAudioSource
from queue import Queue, Empty

# TODO
# https://github.com/HelloChatterbox/speech2text
# https://github.com/HelloChatterbox/text2speech
# https://github.com/JarbasAl/palavras_chave
from mycroft.tts import TTSFactory
from mycroft.stt import STTFactory


class SpeechMasterProtocol(HiveMindProtocol):
    """"""


class SpeechMasterHiveMind(HiveMind):
    platform = "HiveMindSpeechMasterV0.1"

    def __init__(self, bus=None, announce=True, *args, **kwargs):
        super().__init__(bus=bus, announce=announce, *args, **kwargs)
        # AudioSource for streaming TTS through WS
        self.tts = self.get_tts()
        self.stt = self.get_stt()
        self.audio_source_queue = Queue()
        self.audio_source = WebsocketAudioSource(self.audio_source_queue,
                                                 self.tts)
        self.audio_source.start()

    @staticmethod
    def get_stt():
        return STTFactory.create()

    @staticmethod
    def get_tts():
        return TTSFactory.create()

    # websocket handlers
    def handle_register(self, client, platform):
        audio_queue = Queue()
        audio_listener = WebsocketAudioListener(self, client, audio_queue,
                                                self.stt)
        self.clients[client.peer]["audio_queue"] = audio_queue
        self.clients[client.peer]["audio_listener"] = audio_listener
        audio_listener.start()

    def handle_unregister(self, client, code, reason, context):
        client_data = self.clients[client.peer] or {}
        audio_listener = client_data.get("audio_listener")
        if audio_listener:
            LOG.info("stopping audio listener")
            audio_listener.stop()

    def handle_binary(self, client, payload):
        audio_queue = self.clients[client.peer].get("audio_queue")
        if audio_queue:
            try:
                audio_queue.put(payload)
            except Exception as e:
                LOG.error("Could not put audio in queue: " + str(e))

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


class StreamingSpeechMasterHiveMind(SpeechMasterHiveMind):
    platform = "HiveMindStreamingSpeechMasterV0.1"

    @staticmethod
    def get_stt():
        # stt is handled by individual audio listeners
        return None

    def handle_register(self, client, platform):
        audio_queue = Queue()
        audio_listener = WebsocketAudioStreamingListener(self, client,
                                                         audio_queue)
        self.clients[client.peer]["audio_queue"] = audio_queue
        self.clients[client.peer]["audio_listener"] = audio_listener
        audio_listener.start()


class SpeechMasterListener(HiveMindListener):
    default_protocol = SpeechMasterProtocol
    default_factory = SpeechMasterHiveMind


class StreamingSpeechMasterListener(SpeechMasterListener):
    default_factory = StreamingSpeechMasterHiveMind


def get_listener(port=6799, max_connections=-1, bus=None, streaming=False):
    if streaming:
        return StreamingSpeechMasterListener(port, max_connections, bus)
    return SpeechMasterListener(port, max_connections, bus)
