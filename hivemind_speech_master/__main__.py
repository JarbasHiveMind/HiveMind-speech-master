from hivemind_speech_master import get_listener
from jarbas_hive_mind.configuration import CONFIGURATION
from ovos_utils import create_daemon


def start_mind(config=None, bus=None, daemonic=False, streaming=True):
    config = config or CONFIGURATION

    # listen
    listener = get_listener(bus=bus, streaming=streaming)

    # read port and ssl settings
    listener.load_config(config)

    if daemonic:
        create_daemon(listener.listen)
    else:
        listener.listen()


if __name__ == '__main__':
    # TODO argparse
    config = {"port": 6799,
              "ssl": {"use_ssl": False}}
    start_mind(config)

    # that's it, now external applications can connect to the HiveMind

    # use configuration to set things like
    #  - blacklisted/whitelisted ips
    #  - blacklisted/whitelisted message_types
    #  - blacklisted/whitelisted intents - Coming soon
    #  - blacklisted/whitelisted skills  - Coming soon

    # you can send messages to the mycroft bus to send/broadcast to clients
    # 'Message(hive.client.broadcast',
    #           {"payload":
    #               {"msg_type": "speak",
    #               "data": {"utterance": "Connected to the HiveMind"}
    #           })

    # or you can listen to hive mind events
    # "hive.client.connection.error"
    # "hive.client.connect"
    # "hive.client.disconnect"
    # "hive.client.send.error"
