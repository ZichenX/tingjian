#!/usr/bin/env python3
"""Compare real recordings with human references; one engine per process, no invented benchmark.
JSONL rows: {"audio":"recordings/001.wav","text":"人工逐字参考文本","group":"济南_近讲"}.
Paths are relative to the manifest directory. This is an operator-only CLI, NOT a public API.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import sys
import time
import unicodedata
from collections import defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.audio import decode_upload
from app.config import Settings
from app.catalog import MODES
from app.engine import Engine


def normalize(text: str) -> str:
    # Deliberately do not turn dialect words into Mandarin, nor convert numerals or simplify characters.
    return ''.join(c for c in unicodedata.normalize('NFKC', text).lower()
                   if not c.isspace() and not unicodedata.category(c).startswith('P'))


def distance(reference: str, hypothesis: str) -> int:
    # O(n*m) time, O(m) memory; whole-corpus references are NOT concatenated first.
    previous = list(range(len(hypothesis) + 1))
    for i, a in enumerate(reference, 1):
        current = [i]
        for j, b in enumerate(hypothesis, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j-1] + (a != b)))
        previous = current
    return previous[-1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--engine', choices=MODES, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--no-normalize', action='store_true', help='Count punctuation/whitespace too')
    args = p.parse_args()
    os.environ.update(ASR_MODE=args.engine, TTS_ENABLED='0')
    s = Settings.from_env()
    records = [json.loads(line) for line in args.manifest.read_text().splitlines() if line.strip()]
    if not records: raise SystemExit('测试集为空')
    for record in records:
        if not isinstance(record.get('text'), str) or not record['text'].strip(): raise SystemExit('每条录音必须有非空人工参考文本')
        if not isinstance(record.get('audio'), str): raise SystemExit('每条录音需要 audio 文件路径')
        if len(record['text']) > 10_000: raise SystemExit('每条参考文本最多 10000 字符，请拆分超长录音')
    started = time.perf_counter(); engine = Engine(s); load = time.perf_counter() - started
    results = []; groups = defaultdict(lambda: {'edits': 0, 'reference_chars': 0, 'audio_seconds': 0.0, 'pipeline_seconds': 0.0})
    for index, record in enumerate(records):
        path = (args.manifest.parent / record['audio']).resolve()
        # Excludes file decoding and model loading; includes VAD+ASR; specify this in the report.
        pcm = asyncio.run(decode_upload(path, s.max_upload_seconds))
        started = time.perf_counter()
        segments = list(engine.segments(pcm))
        hypothesis = ''.join(engine.transcribe(seg.samples) for seg in segments)
        elapsed = time.perf_counter() - started
        ref, hyp = record['text'], hypothesis
        if not args.no_normalize: ref, hyp = normalize(ref), normalize(hyp)
        if not ref: raise SystemExit('归一化后参考文本为空，请检查标注')
        edits = distance(ref, hyp); duration = len(pcm) / 16000
        result = {'audio': record['audio'], 'group': record.get('group', 'all'), 'reference': record['text'],
                  'hypothesis': hypothesis, 'edits': edits, 'reference_chars': len(ref), 'cer': edits / len(ref),
                  'audio_seconds': duration, 'pipeline_seconds': elapsed, 'pipeline_rtf': elapsed / duration}
        results.append(result)
        for field in ('edits', 'reference_chars', 'audio_seconds', 'pipeline_seconds'):
            groups[result['group']][field] += result[field]
        print(f'{index+1}/{len(records)} CER={result["cer"]:.3f} RTF={result["pipeline_rtf"]:.3f}', flush=True)
    totals = {field: sum(r[field] for r in results) for field in ('edits','reference_chars','audio_seconds','pipeline_seconds')}
    for value in [totals, *groups.values()]:
        value['cer'] = value['edits'] / value['reference_chars']
        value['pipeline_rtf'] = value['pipeline_seconds'] / value['audio_seconds']
    report = {'engine': args.engine, 'threads': s.threads, 'model_load_seconds': load, 'normalization': 'none' if args.no_normalize else 'NFKC_lowercase_remove_whitespace_unicode_punctuation',
              'timing': 'VAD+ASR only, excluding model load/file decode/network/queue; offline final results, not streaming partial CER',
              'total': totals, 'groups': dict(groups), 'results': results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(totals, indent=2)); print('已保存真实评测结果。CER 可能超过 1，不要把 1-CER 当作统一的准确率。')

if __name__ == '__main__': main()
