import webrtcvad
from collections import deque
from queue import Empty
from threading import Thread

# TODO
# https://github.com/HelloChatterbox/speech2text
from mycroft.stt import STTFactory

vad = webrtcvad.Vad(3)


class WebsocketAudioStreamingListener(Thread):
    def __init__(self, factory, client, queue, sample_rate=16000):
        super().__init__()
        self.client = client
        self.factory = factory
        self.stt = STTFactory.create()
        self.sample_rate = sample_rate
        self.vad = webrtcvad.Vad(1)
        self.queue = queue

        BLOCKS_PER_SECOND = 50
        self.block_size = int(
            self.sample_rate / float(BLOCKS_PER_SECOND))  # 320
        padding_ms = 600
        block_duration_ms = 1000 * \
                            self.block_size // self.sample_rate  # 20
        num_padding_blocks = padding_ms // block_duration_ms  # 30
        self.ratio = 0.75

        self.ring_buffer = deque(maxlen=num_padding_blocks)
        self.triggered = False
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
        try:
            is_speech = self.vad.is_speech(audio_block, self.sample_rate)
        except:
            is_speech = False

        if not self.triggered:
            self.ring_buffer.append((audio_block, is_speech))
            num_voiced = len(
                [f for f, speech in self.ring_buffer if speech])
            if num_voiced > self.ratio * self.ring_buffer.maxlen:
                self.triggered = True
                self.stt.stream_start()
                for f, s in self.ring_buffer:
                    self.stt.stream_data(f)
                self.ring_buffer.clear()
        else:
            self.stt.stream_data(audio_block)
            self.ring_buffer.append((audio_block, is_speech))
            num_unvoiced = len(
                [f for f, speech in self.ring_buffer if not speech])
            if num_unvoiced > self.ratio * self.ring_buffer.maxlen:
                self.triggered = False
                text = self.stt.stream_stop()
                self.ring_buffer.clear()
                if text and len(text) > 0:
                    self.factory.emit_utterance_to_bus(self.client, text)

    def stop(self):
        self.stt.stream_stop()
        self.running = False
