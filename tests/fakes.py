"""EXPLICIT TEST DOUBLES. These do not recognize real speech and must NEVER serve production."""
import numpy as np
from app.audio import wav_bytes
from app.segmenter import Segment

class FakeSegmenter:
    def __init__(self):
        self.samples = np.empty(0, np.float32); self.seq = 0; self.active_id = None; self.offset = 0
    def feed(self, audio):
        self.samples = np.concatenate((self.samples, audio))
        if len(self.samples): self.active_id = self.seq
        out = []
        while len(self.samples) >= 32000:
            out.append(Segment(self.seq, self.samples[:32000].copy(), self.offset/16000, (self.offset+32000)/16000))
            self.samples = self.samples[32000:]; self.offset += 32000; self.seq += 1
            self.active_id = self.seq if len(self.samples) else None
        return out
    def preview(self):
        return (self.seq, self.samples.copy()) if len(self.samples) >= 12000 else None
    def flush(self):
        if not len(self.samples): return []
        result = [Segment(self.seq, self.samples.copy(), self.offset/16000, (self.offset+len(self.samples))/16000)]
        self.samples = np.empty(0, np.float32); self.active_id = None; self.seq += 1
        return result

class FakeEngine:
    def __init__(self, settings):
        self.tts_enabled = settings.tts_enabled; self.supports_partial = True
    def new_segmenter(self): return FakeSegmenter()
    def segments(self, samples):
        if np.max(np.abs(samples), initial=0) < 0.005: return
        segmenter = self.new_segmenter()
        yield from segmenter.feed(samples); yield from segmenter.flush()
    def transcribe(self, samples, partial=False):
        # A deliberately fixed test sentence, not a speech recognition result.
        return '测试预览' if partial else '【界面测试文字】咱们慢慢说，屏幕上的字看得清楚。'
    def speak(self, text, speed=.95):
        t = np.arange(3200, dtype=np.float32) / 16000
        return wav_bytes(.12 * np.sin(t * 2 * np.pi * 440), 16000)
