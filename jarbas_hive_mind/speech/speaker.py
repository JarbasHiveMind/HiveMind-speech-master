from threading import Thread
import tempfile
from queue import Empty

class WebsocketAudioSource(Thread):
    def __init__(self, queue, tts):
        super(WebsocketAudioSource, self).__init__()
        self.queue = queue
        self.tts = tts
        self.running = True
        
    def run(self):
        while self.running:
            try:
                (utterance, client) = self.queue.get(timeout=0.5)
                self.handle_speak_message(utterance, client)
            except Empty:
                pass

    def handle_speak_message(self, utterance, client):
        with tempfile.NamedTemporaryFile() as wav_file:
            (wav_file, _) = self.tts.get_tts(utterance, wav_file)
            audio_data = wav_file.read()
            client.sendMessage(audio_data, True)
        
