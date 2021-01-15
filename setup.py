from setuptools import setup

setup(
    name='hivemind_speech_master',
    version='0.1.0',
    packages=['hivemind_speech_master',
              'hivemind_speech_master.speech'],
    include_package_data=True,
    install_requires=["jarbas_hive_mind>=0.10.7",
                      "webrtcvad==2.0.10"],
    url='https://github.com/JarbasHiveMind/HiveMind-speech-master',
    license='Apache2.0',
    author='jarbasAI',
    author_email='jarbasai@mailfence.com',
    description='Remote audio processing for mycroft'
)
