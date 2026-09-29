from __future__ import annotations
import logging
import re
from pathlib import Path
import numpy as np
from app.catalog import AED_DIR, CTC_DIR, SENSE_DIR, NANO_DIR, TTS_DIR, check_files, selected
from app.config import Settings
from app.segmenter import Segmenter
from app.audio import wav_bytes

log = logging.getLogger("tingjian")

def clean_text(text: str) -> str:
    # Remove metadata markers, never replace dialect words or invent missing speech.
    text = re.sub(r"<\|[^<>]{0,80}\|>", "", text)
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text).strip()

class Engine:
    def __init__(self, settings: Settings):
        import sherpa_onnx as sherpa  # no automatic downloads; fail visibly if unavailable
        self.sherpa = sherpa
        self.settings = settings
        self.mode = settings.asr_mode
        self.supports_partial = self.mode in {"dual", "ctc-only", "sensevoice"}
        self.tts_enabled = settings.tts_enabled
        check_files(settings.model_dir, selected(self.mode, self.tts_enabled))
        shared = {"num_threads": settings.threads, "provider": settings.provider, "debug": False}
        # In dual mode the CTC recognizer is only used for provisional text.
        # It keeps the same native settings as before; the latency improvement
        # comes from separating its best-effort queue from AED final work.
        base = settings.model_dir
        self.partial = None
        if self.mode == "dual":
            p = base / CTC_DIR
            self.partial = sherpa.OfflineRecognizer.from_fire_red_asr_ctc(
                model=str(p / "model.int8.onnx"), tokens=str(p / "tokens.txt"), **shared)
        elif self.mode == "ctc-only":
            p = base / CTC_DIR
            self.partial = sherpa.OfflineRecognizer.from_fire_red_asr_ctc(
                model=str(p / "model.int8.onnx"), tokens=str(p / "tokens.txt"), **shared)
        if self.mode in {"dual", "aed-only"}:
            p = base / AED_DIR
            self.final = sherpa.OfflineRecognizer.from_fire_red_asr(
                encoder=str(p / "encoder.int8.onnx"), decoder=str(p / "decoder.int8.onnx"),
                tokens=str(p / "tokens.txt"), **shared)
        elif self.mode == "ctc-only":
            self.final = self.partial
        elif self.mode == "sensevoice":
            p = base / SENSE_DIR
            self.final = sherpa.OfflineRecognizer.from_sense_voice(
                model=str(p / "model.int8.onnx"), tokens=str(p / "tokens.txt"),
                language="zh", use_itn=True, **shared)
            self.partial = self.final
        elif self.mode == "funasr-nano":
            p = base / NANO_DIR
            self.final = sherpa.OfflineRecognizer.from_funasr_nano(
                encoder_adaptor=str(p / "encoder_adaptor.int8.onnx"),
                llm=str(p / "llm.int8.onnx"), embedding=str(p / "embedding.int8.onnx"),
                tokenizer=str(p / "Qwen3-0.6B"), language="中文", itn=True,
                hotwords=settings.hotwords, max_new_tokens=256, **shared)
        self.tts = None
        if self.tts_enabled:
            p = base / TTS_DIR
            vits = sherpa.OfflineTtsVitsModelConfig(
                model=str(p / "model.onnx"), tokens=str(p / "tokens.txt"),
                lexicon=str(p / "lexicon.txt"), dict_dir=str(p / "dict"))
            cfg = sherpa.OfflineTtsConfig(
                model=sherpa.OfflineTtsModelConfig(vits=vits, num_threads=min(2, settings.threads), provider="cpu"),
                rule_fsts=",".join(str(p / f) for f in ("date.fst", "number.fst")), max_num_sentences=1)
            if not cfg.validate():
                raise ValueError("TTS 配置无效；请检查 Melo 模型、字典与 FST 文件")
            self.tts = sherpa.OfflineTts(cfg)
        # Validate VAD at startup, not only after the first user grants microphone access.
        vad = self.new_segmenter()
        vad.feed(np.zeros(1600, np.float32))
        vad.flush()
        log.info("models_loaded mode=%s provider=%s tts=%s", self.mode, settings.provider, self.tts_enabled)

    def warmup(self) -> None:
        """Run one short native call per enabled model before accepting users.

        ONNX Runtime and the native decoder may lazily create kernels and thread
        pools on their first real request. Warming them with deterministic audio
        does not change model weights, VAD boundaries, or user-visible text; it
        moves that one-time cost into startup before readiness is announced.
        """
        samples = np.zeros(8000, dtype=np.float32)
        if self.partial is not None:
            self.transcribe(samples, partial=True)
        self.transcribe(samples)
        if self.tts_enabled:
            self.speak("您好", 0.95)
        log.info("models_warmed mode=%s tts=%s", self.mode, self.tts_enabled)

    def new_segmenter(self) -> Segmenter:
        cfg = self.sherpa.VadModelConfig()
        cfg.silero_vad.model = str(self.settings.model_dir / "silero_vad.onnx")
        cfg.silero_vad.threshold = 0.5
        cfg.silero_vad.min_silence_duration = self.settings.silence
        cfg.silero_vad.min_speech_duration = 0.18
        cfg.silero_vad.max_speech_duration = self.settings.segment_seconds
        cfg.sample_rate = 16000
        cfg.num_threads = 1
        vad = self.sherpa.VoiceActivityDetector(cfg, buffer_size_in_seconds=self.settings.segment_seconds + 5)
        return Segmenter(vad, self.settings.segment_seconds)

    def segments(self, samples: np.ndarray):
        segmenter = self.new_segmenter()
        for pos in range(0, len(samples), 16000):
            yield from segmenter.feed(samples[pos:pos + 16000])
        yield from segmenter.flush()

    def transcribe(self, samples: np.ndarray, partial: bool = False) -> str:
        rec = self.partial if partial else self.final
        if rec is None or len(samples) < 3200:
            return ""
        if len(samples) > 30 * 16000:
            raise ValueError("单段音频过长，VAD 分段异常")
        stream = rec.create_stream()
        stream.accept_waveform(16000, samples)
        rec.decode_stream(stream)
        return clean_text(stream.result.text)

    def speak(self, text: str, speed: float = 0.95) -> bytes:
        if self.tts is None:
            raise ValueError("本服务器未启用普通话朗读")
        cfg = self.sherpa.GenerationConfig()
        cfg.sid = 0
        cfg.speed = speed
        audio = self.tts.generate(text, cfg)
        samples = np.asarray(audio.samples, dtype=np.float32)
        if not len(samples) or not np.isfinite(samples).all():
            raise ValueError("未生成有效语音；请缩短文字后重试")
        return wav_bytes(samples, audio.sample_rate)
