from __future__ import annotations
from dataclasses import dataclass
import numpy as np

RATE = 16000
WINDOW = 512

@dataclass
class Segment:
    id: int
    samples: np.ndarray
    start: float
    end: float

class Segmenter:
    """Wrap Silero/sherpa VAD. Completed segments come from VAD, not preview buffers.

    A bounded look-back buffer is used ONLY for provisional captions. Preview audio
    may differ slightly at the boundaries; it can never overwrite a completed turn.
    """
    def __init__(self, vad, max_seconds: float = 12):
        self.vad = vad
        self.keep = int((max_seconds + 3) * RATE)
        self.buffer = np.empty(0, np.float32)
        self.pending = np.empty(0, np.float32)
        self.position = 0
        self.seq = 0
        self.active_id: int | None = None
        self.active_start = 0
        self.last_end = 0

    def _drain(self) -> list[Segment]:
        out = []
        while not self.vad.empty():
            f = self.vad.front
            audio = np.asarray(f.samples, dtype=np.float32).copy()
            start = int(f.start)
            if self.active_id is None:
                self.active_id = self.seq
                self.seq += 1
            out.append(Segment(self.active_id, audio, start / RATE, (start + len(audio)) / RATE))
            self.last_end = max(self.last_end, start + len(audio))
            self.active_id = None
            self.vad.pop()
        return out

    def feed(self, samples: np.ndarray) -> list[Segment]:
        self.pending = np.concatenate((self.pending, np.asarray(samples, np.float32)))
        out = []
        offset = 0
        while len(self.pending) - offset >= WINDOW:
            frame = self.pending[offset:offset + WINDOW]
            offset += WINDOW
            self.buffer = np.concatenate((self.buffer, frame))[-self.keep:]
            self.position += WINDOW
            self.vad.accept_waveform(frame)
            if self.active_id is None and self.vad.is_speech_detected():
                self.active_id = self.seq
                self.seq += 1
                self.active_start = max(self.last_end, self.position - int(0.65 * RATE), 0)
            out.extend(self._drain())
        self.pending = self.pending[offset:].copy()
        return out

    def preview(self) -> tuple[int, np.ndarray] | None:
        if self.active_id is None:
            return None
        length = min(len(self.buffer), self.position - self.active_start)
        if length < int(0.75 * RATE):
            return None
        return self.active_id, self.buffer[-length:].copy()

    def flush(self) -> list[Segment]:
        out = []
        if len(self.pending):
            out.extend(self.feed(np.zeros(WINDOW - len(self.pending), np.float32)))
        self.vad.flush()
        out.extend(self._drain())
        self.active_id = None
        return out
