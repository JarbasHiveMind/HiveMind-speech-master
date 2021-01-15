from collections import deque
from queue import Empty
from threading import Thread


class WebsocketAudioListener(Thread):
    def __init__(self, factory, client, queue, stt, sample_rate=16000):
        super(WebsocketAudioListener, self).__init__()
        self.client = client
        self.factory = factory
        self.stt = stt
        self.sample_rate = sample_rate
        self.queue = queue

        BLOCKS_PER_SECOND = 50
        self.block_size = int(self.sample_rate / float(BLOCKS_PER_SECOND))  # 320
        padding_ms = 600
        block_duration_ms = 1000 * self.block_size // self.sample_rate  # 20
        num_padding_blocks = padding_ms // block_duration_ms  # 30

        self.ring_buffer = deque(maxlen=num_padding_blocks)
        self.running = True

    def run(self):
        audio_data = bytearray()
        while self.keep_running():
            if len(audio_data) < self.block_size:
                try:
                    audio_from_queue = self.queue.get(timeout=1)
                    audio_data.extend(audio_from_queue)
                except Empty:
                    pass
            else:
                audio_block = audio_data[: self.block_size]
                audio_data = audio_data[self.block_size:]
                self.process_audio(audio_block)

        self.stop()

    def keep_running(self):
        return self.running and self.factory.clients.get(self.client.peer)

    def process_audio(self, audio_block):
        text = ""
        # TODO
        if text and len(text) > 0:
            self.factory.emit_utterance_to_bus(self.client, text)

    def stop(self):
        self.running = False
