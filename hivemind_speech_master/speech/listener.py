import webrtcvad
from collections import deque
from queue import Queue, Empty
from threading import Thread
from precise_runner import PreciseEngine, PreciseRunner, ReadWriteStream
from ovos_utils.log import LOG

LOG.name = 'HiveMind'

vad = webrtcvad.Vad(3)
class WebsocketAudioListener(Thread):
    def __init__(self, factory, client, queue, sample_rate=16000):
        super(WebsocketAudioListener, self).__init__()
        self.client = client
        self.factory = factory
        self.sample_rate = sample_rate
        self.vad = webrtcvad.Vad(1)
        self.queue = queue

        self.hotword_found = False
        self.hotword_stream = ReadWriteStream()

        def on_activation():
            self.hotword_found = True

        trigger_level = 1
        sensitivity = 0.5

        self.hotword_runner = PreciseRunner(
            PreciseEngine('/opt/backend/precise-engine/precise-engine', '/opt/backend/hey-mycroft.pb'),
            trigger_level, sensitivity,
            stream=self.hotword_stream, on_activation=on_activation,
        )
        self.hotword_runner.start()
        
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
        while self.keep_running():
            self.wait_for_hotword()
            if not self.keep_running(): break
            results = CALL_STT(self.vad_generator()) # pseudo-code, change with method that will receive audio bytes
            try:
                for r in results:
                    try:
                        if r.chunks[0].final:
                            self.emit_utterance(r.chunks[0].alternatives[0].text)
                            break
                    except LookupError:
                        LOG.debug('No available chunks')
            except grpc._channel._Rendezvous as err:
                LOG.error('Error code %s, message: %s' % (err._state.code, err._state.details))
        self.stop()
        
    def keep_running(self):
        return self.running and self.factory.clients.get(self.client.peer)

    def queue_generator(self):
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
                yield audio_block

    def wait_for_hotword(self):
        client_config = self.factory.clients.get(self.client.peer)
        if not client_config or not client_config.get('use_hotword'): 
            LOG.debug('Skip hotword detection')
            return
        LOG.debug(client_config)
        buffered_audio = bytearray()
        LOG.debug('Start hotword detection')
        self.factory.emit_hotword_message_to_ona("start", self.client)
        for audio_block in self.queue_generator():
            buffered_audio.extend(audio_block)
            if len(buffered_audio) > 2048:
                self.hotword_stream.write(bytes(buffered_audio[:2048]))
                buffered_audio = buffered_audio[2048:]
            if not client_config.get('use_hotword'):
                break
                
            if self.hotword_found:
                self.hotword_found = False
                LOG.debug('Hotword detected')
                self.factory.emit_hotword_message_to_ona("detected", self.client)
                break
        
    def vad_generator(self):        
        for audio_block in self.queue_generator():
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
                    for f, s in self.ring_buffer:
                        yield f
                    self.ring_buffer.clear()
            else:
                yield audio_block
                self.ring_buffer.append((audio_block, is_speech))
                num_unvoiced = len(
                    [f for f, speech in self.ring_buffer if not speech])
                if num_unvoiced > self.ratio * self.ring_buffer.maxlen:
                    self.triggered = False
                    self.ring_buffer.clear()
                    break

    def emit_utterance(self, utterance):
        if len(utterance) > 0:
            self.factory.emit_utterance_to_bus(self.client, utterance)

    def stop(self):
        self.hotword_runner.stop()
        self.running = False
