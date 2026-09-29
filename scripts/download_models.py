#!/usr/bin/env python3
"""Fetch pinned official artifacts; resumable downloads, safe extraction, local SHA256 lock.
First download is trust-on-first-use unless --checksums supplies independently verified hashes.
A locally generated checksum is NOT an upstream signature. Never silently change a locked hash.
"""
from __future__ import annotations
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.catalog import ARTIFACTS, MODES, selected, check_files

MAX_DOWNLOAD = 5 * 1024**3
MAX_EXTRACTED = 12 * 1024**3


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(4 * 1024**2): h.update(block)
    return h.hexdigest()


def atomic_json(path: Path, value: dict):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def download(url: str, target: Path, retries: int = 5):
    """HTTPS only. A changed/ignored Range restarts cleanly; incomplete data stays .part."""
    if urlsplit(url).scheme != 'https': raise ValueError('模型下载必须使用 HTTPS')
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + '.part')
    for attempt in range(retries):
        start = part.stat().st_size if part.exists() else 0
        headers = {'User-Agent': 'Tingjian-model-installer/1.0', 'Accept-Encoding': 'identity'}
        if start: headers['Range'] = f'bytes={start}-'
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=60) as response:
                if urlsplit(response.url).scheme != 'https': raise ValueError('拒绝非 HTTPS 重定向')
                append = response.status == 206 and start > 0
                if append and not response.headers.get('Content-Range', '').startswith(f'bytes {start}-'):
                    raise ValueError('服务器返回的断点范围不正确')
                if not append: start = 0
                expected = int(response.headers.get('Content-Length', '0'))
                if expected + start > MAX_DOWNLOAD: raise ValueError('模型下载体积超限')
                received = 0
                with part.open('ab' if append else 'wb') as out:
                    while chunk := response.read(1024 * 1024):
                        out.write(chunk); received += len(chunk)
                        if received + start > MAX_DOWNLOAD: raise ValueError('模型下载体积超限')
                    out.flush(); os.fsync(out.fileno())
                if expected and expected != received: raise OSError('下载不完整')
            if part.stat().st_size < 128: raise ValueError('下载结果过小，可能是错误页面')
            os.replace(part, target); return
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and start:
                total = exc.headers.get('Content-Range', '')
                if total == f'bytes */{start}': os.replace(part, target); return
                part.unlink(missing_ok=True)
            if attempt == retries - 1: raise RuntimeError(f'{target.name}: HTTP {exc.code}，请检查网络或按离线安装文档操作') from None
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            if attempt == retries - 1: raise RuntimeError(f'{target.name}: 下载失败 ({type(exc).__name__})；保留断点，重新执行可续传') from None
        time.sleep(min(2 ** attempt, 16))


def safe_extract(archive: Path, destination: Path, expected_root: str):
    """Only regular files/directories under the expected top-level directory; no links/devices."""
    total = count = 0
    with tarfile.open(archive, 'r:bz2') as tar:
        for entry in tar:
            name = PurePosixPath(entry.name)
            if name.is_absolute() or '..' in name.parts or '\\' in entry.name:
                raise ValueError('模型压缩包包含不安全路径')
            if not name.parts or name.parts == ('.',): continue
            if name.parts[0] != expected_root:
                # macOS resource fork metadata is unnecessary and never loaded.
                if name.parts[0] == '__MACOSX': continue
                raise ValueError('模型压缩包顶层目录与固定版本不一致')
            if not entry.isdir() and not entry.isfile(): raise ValueError('拒绝模型包中的符号链接、硬链接或设备文件')
            count += 1; total += entry.size
            if total > MAX_EXTRACTED or count > 50_000: raise ValueError('模型解压体积或文件数超限')
            target = destination.joinpath(*name.parts)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            if entry.isdir(): target.mkdir(exist_ok=True, mode=0o755)
            else:
                source = tar.extractfile(entry)
                if source is None: raise ValueError('模型包文件不可读取')
                with source, target.open('wb') as out: shutil.copyfileobj(source, out, 1024 * 1024)
                os.chmod(target, 0o644)
    for directory, _, _ in os.walk(destination): os.chmod(directory, 0o755)


def file_manifest(base: Path, root: str, single_name: str | None = None) -> dict:
    files = [base / single_name] if single_name else sorted((base / root).rglob('*'))
    return {str(p.relative_to(base)): {'size': p.stat().st_size, 'sha256': sha256(p)}
            for p in files if p.is_file() and not p.is_symlink()}


def verify_manifest(base: Path, manifest: dict):
    if not manifest: raise ValueError('模型锁文件缺少内容校验记录')
    for name, value in manifest.items():
        rel = PurePosixPath(name)
        if rel.is_absolute() or '..' in rel.parts: raise ValueError('模型锁文件路径不安全')
        path = base / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size != value['size'] or sha256(path) != value['sha256']:
            raise ValueError(f'已安装模型校验失败: {name}；请勿忽略，参照故障排查重新安装该模型')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', choices=MODES, default=os.getenv('ASR_MODE', 'dual'))
    parser.add_argument('--model-dir', type=Path, default=Path(os.getenv('MODEL_DIR', 'models')))
    parser.add_argument('--no-tts', action='store_true')
    parser.add_argument('--all', action='store_true', help='Download all evaluation alternatives, not load them together')
    parser.add_argument('--offline', action='store_true', help='Use archives already placed in models/.cache; never access network')
    parser.add_argument('--verify-only', action='store_true')
    parser.add_argument('--checksums', type=Path, help='JSON {official_artifact_filename: trusted_sha256}')
    parser.add_argument('--require-trusted-hashes', action='store_true')
    args = parser.parse_args()
    if os.getenv('MODEL_LICENSES_ACK') != '1': raise SystemExit('先阅读 MODEL_LICENSES.md，再配置 MODEL_LICENSES_ACK=1；确认不代表许可自动授予。')
    names = list(ARTIFACTS) if args.all else selected(args.engine, not args.no_tts and os.getenv('TTS_ENABLED', '1') == '1')
    base = args.model_dir.resolve(); base.mkdir(parents=True, exist_ok=True); os.chmod(base, 0o755)
    cache = base / '.cache'; cache.mkdir(exist_ok=True)
    trusted = json.loads(args.checksums.read_text()) if args.checksums else {}
    if not isinstance(trusted, dict) or any(not isinstance(v, str) or not re.fullmatch('[a-fA-F0-9]{64}', v) for v in trusted.values()):
        raise SystemExit('可信哈希文件格式应是 {"压缩包名称": "64位SHA256"}')
    mirror = os.getenv('MODEL_BASE_URL', '').rstrip('/')
    if mirror and (urlsplit(mirror).scheme != 'https' or urlsplit(mirror).query or urlsplit(mirror).username):
        raise SystemExit('MODEL_BASE_URL 仅支持可信 HTTPS 基础路径，不得带凭据或查询参数')
    with (base / '.download.lock').open('a') as guard:
        try: fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise SystemExit('另一个下载任务正在运行') from None
        lockpath = base / 'artifacts.lock.json'
        lock = json.loads(lockpath.read_text()) if lockpath.exists() else {'schema': 1, 'artifacts': {}}
        if lock.get('schema') != 1: raise SystemExit('不支持的模型锁文件版本')
        for name in names:
            spec = ARTIFACTS[name]; entry = lock['artifacts'].get(name)
            expected = trusted.get(spec.name, '').lower()
            if args.require_trusted_hashes and not expected: raise SystemExit(f'缺少独立确认的哈希: {spec.name}')
            if entry:
                if expected and entry['archive_sha256'] != expected: raise SystemExit(f'{name}: 锁文件与可信哈希冲突，停止')
                verify_manifest(base, entry['files']); check_files(base, [name])
                print(f'OK 完整性校验: {name}', flush=True); continue
            if args.verify_only: raise SystemExit(f'{name}: 未安装或没有模型锁文件')
            archive = cache / spec.name
            url = f'{mirror}/{spec.group}/{spec.name}' if mirror else spec.url
            if not archive.is_file():
                if args.offline: raise SystemExit(f'离线缓存缺失: {archive}')
                print(f'下载 {spec.name} ...', flush=True); download(url, archive)
            digest = sha256(archive)
            if expected and digest != expected: raise SystemExit(f'{name}: 下载文件 SHA256 不匹配，停止；不要删除可信校验记录绕过错误')
            print(f'安装 {name}；SHA256={digest}；来源校验={"独立可信哈希" if expected else "HTTPS + 首次信任 TOFU"}', flush=True)
            with tempfile.TemporaryDirectory(prefix='.extract-', dir=base) as staging:
                stage = Path(staging)
                if spec.archive:
                    safe_extract(archive, stage, spec.root); check_files(stage, [name])
                    target = base / spec.root
                    if target.exists():
                        if target.is_symlink() or not target.is_dir(): raise ValueError('模型目标路径不安全')
                        shutil.rmtree(target)  # Only a fixed catalog directory; cached archive remains available.
                    os.replace(stage / spec.root, target)
                else:
                    shutil.copyfile(archive, stage / spec.name); check_files(stage, [name])
                    os.chmod(stage / spec.name, 0o644); os.replace(stage / spec.name, base / spec.name)
            entry = {'url': url, 'archive_sha256': digest, 'trust': 'supplied-sha256' if expected else 'https-tofu',
                     'installed_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                     'files': file_manifest(base, spec.root, None if spec.archive else spec.name)}
            lock['artifacts'][name] = entry; atomic_json(lockpath, lock)
        print('模型下载/完整性校验完成。还必须执行 scripts/self_test.py 验证真实推理。')

if __name__ == '__main__':
    try: main()
    except (OSError, ValueError, tarfile.TarError, KeyError) as exc:
        raise SystemExit(f'模型准备失败: {exc}') from None
