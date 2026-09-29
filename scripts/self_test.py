#!/usr/bin/env python3
"""REAL model acceptance gate. This file never injects a mock engine or silently skips inference."""
from __future__ import annotations
import argparse
import asyncio
import io
import json
import os
import platform
import resource
import signal
import sys
import time
import wave
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.audio import decode_upload
from app.catalog import ARTIFACTS, selected, MODES
from app.config import Settings
from app.engine import Engine
from scripts.download_models import verify_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', choices=MODES)
    parser.add_argument('--sample', type=Path, help='Optional genuine speech WAV/audio, not synthetic silence')
    parser.add_argument('--output-dir', type=Path, default=Path(os.getenv('MODEL_DIR', 'models')) / 'acceptance')
    parser.add_argument('--timeout', type=int, default=600)
    args = parser.parse_args()
    if args.engine: os.environ['ASR_MODE'] = args.engine
    # Native inference may not be Python-cancellable: the entire isolated acceptance process times out.
    def timed_out(*_):
        print('FAIL: 模型验收超过时限；请检查内存、线程配置或模型兼容性', file=sys.stderr, flush=True)
        os._exit(124)
    signal.signal(signal.SIGALRM, timed_out); signal.alarm(args.timeout)
    s = Settings.from_env()
    lockpath = s.model_dir / 'artifacts.lock.json'
    if not lockpath.exists(): raise SystemExit('FAIL: 缺少模型完整性记录，请先运行 download_models.py')
    lock = json.loads(lockpath.read_text())
    names = selected(s.asr_mode, s.tts_enabled)
    for name in names:
        if name not in lock['artifacts']: raise SystemExit(f'FAIL: 模型未登记: {name}')
        verify_manifest(s.model_dir, lock['artifacts'][name]['files'])
    sample = args.sample
    if sample is None:
        for name in names:
            root = s.model_dir / ARTIFACTS[name].root / 'test_wavs'
            candidates = [root / '0.wav', root / 'zh.wav', *sorted(root.glob('*.wav'))]
            sample = next((p for p in candidates if p.is_file()), None)
            if sample: break
    if sample is None: raise SystemExit('FAIL: 模型包没有示例语音，请通过 --sample 提供真实讲话录音')
    pcm = asyncio.run(decode_upload(sample, 60))
    started = time.perf_counter(); engine = Engine(s); loaded = time.perf_counter() - started
    started = time.perf_counter(); segments = list(engine.segments(pcm)); segmentation = time.perf_counter() - started
    if not segments: raise SystemExit('FAIL: VAD 未从样例中检测到说话；请使用真实清晰语音')
    results = []; inference = 0.0
    for segment in segments:
        started = time.perf_counter(); text = engine.transcribe(segment.samples); elapsed = time.perf_counter() - started
        inference += elapsed
        results.append({'start': round(segment.start, 3), 'end': round(segment.end, 3), 'text': text, 'inference_seconds': round(elapsed, 3)})
    if not any(r['text'] for r in results): raise SystemExit('FAIL: ASR 没有返回非空文字')
    partial_result = None
    if engine.supports_partial:
        partial_result = engine.transcribe(segments[0].samples, partial=True)
        if not partial_result: raise SystemExit('FAIL: 实时预览模型没有返回文字')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    tts_seconds = generated_seconds = None
    if engine.tts_enabled:
        started = time.perf_counter(); data = engine.speak('您好，我们慢慢说，屏幕上可以看到文字。', .95)
        tts_seconds = time.perf_counter() - started
        with wave.open(io.BytesIO(data)) as w:
            generated_seconds = w.getnframes() / w.getframerate()
            if generated_seconds < .1 or w.getnchannels() != 1: raise SystemExit('FAIL: TTS 输出不正确')
        (args.output_dir / f'{s.asr_mode}-mandarin.wav').write_bytes(data)
    import sherpa_onnx
    report = {'passed': True, 'kind': 'real_model_smoke_test_not_dialect_accuracy_benchmark',
              'engine': s.asr_mode, 'sherpa_onnx': sherpa_onnx.__version__, 'platform': platform.platform(),
              'machine': platform.machine(), 'cpu_threads': s.threads, 'sample': sample.name,
              'model_load_seconds': round(loaded, 3), 'audio_seconds': len(pcm) / 16000,
              'segmentation_seconds': round(segmentation, 3), 'asr_seconds': round(inference, 3),
              'asr_rtf': round(inference / (len(pcm) / 16000), 4), 'segments': results,
              'partial_smoke_text': partial_result,
              'tts_seconds': tts_seconds, 'tts_audio_seconds': generated_seconds,
              'peak_rss_kib_linux': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
              'limits': 'One official/provided sample proves API/inference only; not Shandong CER, concurrency or phone microphone quality.'}
    (args.output_dir / f'{s.asr_mode}.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    signal.alarm(0)
    print('PASS: 真实模型已执行。仍需在公网手机浏览器和山东真实录音上完成验收。')

if __name__ == '__main__': main()
