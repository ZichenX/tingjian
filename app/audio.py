from __future__ import annotations

import asyncio
import io
import wave
from pathlib import Path

import numpy as np
import soxr

SAMPLE_RATE = 16000


class AudioError(ValueError):
    pass


def pcm16_to_float(data: bytes) -> np.ndarray:
    if len(data) % 2:
        raise AudioError("音频数据不完整")
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0


def wav_bytes(samples: np.ndarray, sample_rate: int) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        f.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    return out.getvalue()


class AudioResampler:
    def __init__(self, rate: int):
        if type(rate) is not int or not 8000 <= rate <= 96000:
            raise AudioError("不支持的麦克风采样率")
        self.rate = rate
        self.impl = None if rate == SAMPLE_RATE else soxr.ResampleStream(rate, SAMPLE_RATE, 1, dtype="float32", quality="HQ")
        self.input_count = 0

    def feed(self, data: bytes, last: bool = False) -> np.ndarray:
        x = pcm16_to_float(data)
        self.input_count += len(x)
        return x if self.impl is None else self.impl.resample_chunk(x, last=last)


async def decode_upload(path: Path, max_seconds: int) -> np.ndarray:
    # 不接受 URL。限制协议、解复用器、线程、解码时长；命令不经过 shell。
    args = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
        "-threads", "1", "-filter_threads", "1",
        "-protocol_whitelist", "file,pipe",
        "-format_whitelist", "wav,mp3,mov,matroska,webm,ogg,flac,aac",
        "-i", str(path), "-map", "0:a:0", "-vn", "-sn", "-dn",
        "-t", str(max_seconds + 1), "-ac", "1", "-ar", str(SAMPLE_RATE),
        "-f", "f32le", "pipe:1",
    ]
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        raw, _ = await asyncio.wait_for(proc.communicate(), timeout=90)
    except BaseException:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        raise
    if proc.returncode != 0 or not raw:
        raise AudioError("无法读取这份录音，请使用 WAV、MP3、M4A、WebM、OGG 或 FLAC 文件")
    samples = np.frombuffer(raw, dtype="<f4").copy()
    if len(samples) > max_seconds * SAMPLE_RATE:
        raise AudioError(f"录音超过 {max_seconds // 60} 分钟，请分成几份后上传；未截断保存")
    if not np.isfinite(samples).all():
        raise AudioError("录音包含无效音频数据")
    return np.clip(samples, -1, 1)
