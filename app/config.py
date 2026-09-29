from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
from app.catalog import MODES


@dataclass(frozen=True)
class Settings:
    origin: str
    secret: str
    code_hash: str
    model_dir: Path = ROOT / "models"
    asr_mode: str = "dual"
    # ONNX Runtime execution provider for ASR recognizers.  ``cpu`` remains
    # the portable default; ``cuda`` requires a CUDA-enabled sherpa-onnx build
    # and a GPU allocation at runtime.
    provider: str = "cpu"
    tts_enabled: bool = True
    threads: int = 3
    max_live: int = 2
    max_upload_mb: int = 50
    max_upload_seconds: int = 600
    session_seconds: int = 1800
    cookie_days: int = 30
    silence: float = 0.7
    segment_seconds: float = 12.0
    partial_interval: float = 1.8
    inference_timeout: float = 120.0
    queue_size: int = 12
    hotwords: str = ""

    @property
    def secure(self) -> bool:
        return self.origin.startswith("https://")

    @property
    def cookie_name(self) -> str:
        return "__Host-tingjian" if self.secure else "tingjian-dev"

    def validate(self) -> None:
        u = urlsplit(self.origin)
        if u.path or u.query or u.fragment or u.username or not u.hostname:
            raise ValueError("APP_ORIGIN 必须是无路径的完整站点来源，例如 https://listen.example.com")
        if u.scheme != "https" and not (u.scheme == "http" and u.hostname in {"localhost", "127.0.0.1"}):
            raise ValueError("公网必须使用 HTTPS；HTTP 仅允许 localhost/127.0.0.1")
        if len(self.secret) < 32 or not re.fullmatch(r"pbkdf2_sha256:310000:[a-f0-9]{32}:[a-f0-9]{64}", self.code_hash):
            raise ValueError("缺少安全配置，请运行 scripts/configure.py；禁止空访问码启动")
        if self.asr_mode not in MODES:
            raise ValueError("ASR_MODE 不在支持列表: " + ", ".join(MODES))
        if self.provider not in {"cpu", "cuda"}:
            raise ValueError("ASR_PROVIDER 只能是 cpu 或 cuda")
        if not 1 <= self.threads <= 32 or not 1 <= self.max_live <= 16:
            raise ValueError("线程或并发配置超出允许范围")
        if not 0.3 <= self.silence <= 2 or not 4 <= self.segment_seconds <= 25:
            raise ValueError("VAD_SILENCE 必须在 0.3~2 秒，SEGMENT_SECONDS 必须在 4~25 秒")
        if not 1 <= self.max_upload_mb <= 100 or not 10 <= self.max_upload_seconds <= 1800:
            raise ValueError("上传限制超出支持范围")
        if not 1 <= self.partial_interval <= 10 or not 30 <= self.inference_timeout <= 600:
            raise ValueError("预览刷新/推理超时配置超出支持范围")
        if len(self.hotwords) > 1000:
            raise ValueError("NANO_HOTWORDS 过长，最多 1000 字符")
        if not 60 <= self.session_seconds <= 7200 or not 1 <= self.cookie_days <= 90:
            raise ValueError("会话时长超出支持范围")

    @classmethod
    def from_env(cls) -> "Settings":
        if os.environ.get("TTS_ENABLED", "1") not in {"0", "1"}:
            raise ValueError("TTS_ENABLED 只能为 0 或 1")
        s = cls(
            origin=os.environ.get("APP_ORIGIN", ""),
            secret=os.environ.get("SESSION_SECRET", ""),
            code_hash=os.environ.get("ACCESS_CODE_HASH", ""),
            model_dir=Path(os.environ.get("MODEL_DIR", str(ROOT / "models"))),
            asr_mode=os.environ.get("ASR_MODE", "dual"),
            provider=os.environ.get("ASR_PROVIDER", "cpu").strip().lower(),
            tts_enabled=os.environ.get("TTS_ENABLED", "1") == "1",
            threads=int(os.environ.get("ASR_THREADS", "3")),
            max_live=int(os.environ.get("MAX_LIVE_SESSIONS", "2")),
            max_upload_mb=int(os.environ.get("MAX_UPLOAD_MB", "50")),
            max_upload_seconds=int(os.environ.get("MAX_UPLOAD_SECONDS", "600")),
            session_seconds=int(os.environ.get("MAX_SESSION_SECONDS", "1800")),
            cookie_days=int(os.environ.get("COOKIE_DAYS", "30")),
            silence=float(os.environ.get("VAD_SILENCE", "0.7")),
            segment_seconds=float(os.environ.get("SEGMENT_SECONDS", "12")),
            partial_interval=float(os.environ.get("PARTIAL_INTERVAL", "1.8")),
            inference_timeout=float(os.environ.get("INFERENCE_TIMEOUT", "120")),
            hotwords=os.environ.get("NANO_HOTWORDS", ""),
        )
        s.validate()
        return s
