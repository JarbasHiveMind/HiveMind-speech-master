import json
from threading import Thread
from hashlib import sha512, md5
from os.path import join, isfile, isdir
from os import makedirs
from ovos_utils.log import LOG

LOG.name = 'HiveMind'

class WebsocketAudioSource(Thread):
    def __init__(self, queue):
        super(WebsocketAudioSource, self).__init__()
        self.queue = queue
        self.cache = "/path/to/cache"
        if not isdir(self.cache):
            makedirs(self.cache)
        self.running = True
        
    def run(self):
        while self.running:
            try:
                (payload, client, tts_engine, tts_voice) = self.queue.get(timeout=0.5)
                self.handle_speak_message(payload, client, tts_engine, tts_voice)
            except Exception:
                pass

    def handle_speak_message(self, payload, client, tts_engine, tts_voice):
        utterance = payload["utterance"]
        try:
            audio_data = self.get_tts(utterance, tts_engine, tts_voice)
            client.sendMessage(audio_data, True)
        except Exception as e:
            LOG.error(f"Could not convert TTS due to {e}")
            return
        payload = json.dumps(payload)
        client.sendMessage(payload)
        
    def _get_unique_file_path(self, utterance, engine, voice):
        file_name = f"{engine}_{voice}_{sha512(utterance.encode('utf-8')).hexdigest()}"
        return join(self.cache, file_name) + ".wav"
        
    def get_tts(self, utterance, engine, voice):
        cached_file = self._get_unique_file_path(utterance, engine, voice)
        if isfile(cached_file):
            with open(cached_file, "rb") as file:
                return file.read()
        
        return GET_TTS # Replace with TTS call
    
    def getMD5(self, text):
        m = md5()
        m.update(text.encode('utf-8'))
        s = m.hexdigest()[:8].lower()
        return s
