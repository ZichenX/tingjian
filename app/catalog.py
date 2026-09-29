"""Upstream model artifacts. URLs are maintainer-provided, not arbitrary mirrors."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path

CTC_DIR = "sherpa-onnx-fire-red-asr2-ctc-zh_en-int8-2026-02-25"
AED_DIR = "sherpa-onnx-fire-red-asr2-zh_en-int8-2026-02-26"
SENSE_DIR = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"
NANO_DIR = "sherpa-onnx-funasr-nano-int8-2025-12-30"
TTS_DIR = "vits-melo-tts-zh_en"
MODES = ("dual", "aed-only", "ctc-only", "sensevoice", "funasr-nano")

@dataclass(frozen=True)
class Artifact:
    name: str
    group: str
    root: str
    files: dict[str, int]  # minimum plausible bytes, NOT cryptographic hashes
    archive: bool = True

    @property
    def url(self) -> str:
        return f"https://github.com/k2-fsa/sherpa-onnx/releases/download/{self.group}/{self.name}"

ARTIFACTS = {
    "vad": Artifact("silero_vad.onnx", "asr-models", "", {"silero_vad.onnx": 100_000}, False),
    "ctc": Artifact(CTC_DIR + ".tar.bz2", "asr-models", CTC_DIR,
                    {"model.int8.onnx": 600_000_000, "tokens.txt": 30_000}),
    "aed": Artifact(AED_DIR + ".tar.bz2", "asr-models", AED_DIR,
                    {"encoder.int8.onnx": 600_000_000, "decoder.int8.onnx": 300_000_000, "tokens.txt": 30_000}),
    "sensevoice": Artifact(SENSE_DIR + ".tar.bz2", "asr-models", SENSE_DIR,
                           {"model.int8.onnx": 150_000_000, "tokens.txt": 100_000}),
    "funasr-nano": Artifact(NANO_DIR + ".tar.bz2", "asr-models", NANO_DIR,
                            {"encoder_adaptor.int8.onnx": 150_000_000, "llm.int8.onnx": 400_000_000,
                             "embedding.int8.onnx": 100_000_000, "Qwen3-0.6B/tokenizer.json": 1_000_000,
                             "Qwen3-0.6B/vocab.json": 100_000, "Qwen3-0.6B/merges.txt": 100_000}),
    "tts": Artifact(TTS_DIR + ".tar.bz2", "tts-models", TTS_DIR,
                    {"model.onnx": 100_000_000, "tokens.txt": 200, "lexicon.txt": 1_000_000,
                     "dict/jieba.dict.utf8": 100_000, "date.fst": 1000, "number.fst": 1000}),
}

def selected(mode: str, tts: bool = True) -> list[str]:
    if mode not in MODES:
        raise ValueError(f"Unknown engine: {mode}")
    engines = {"dual": ["ctc", "aed"], "aed-only": ["aed"], "ctc-only": ["ctc"],
               "sensevoice": ["sensevoice"], "funasr-nano": ["funasr-nano"]}
    return ["vad", *engines[mode], *(["tts"] if tts else [])]

def check_files(base: Path, names: list[str]) -> None:
    for name in names:
        spec = ARTIFACTS[name]
        for relative, minimum in spec.files.items():
            p = base / spec.root / relative
            if not p.is_file() or p.stat().st_size < minimum:
                raise FileNotFoundError(f"模型缺失或不完整: {p}; 请运行 scripts/deploy.sh 下载与校验模型")
