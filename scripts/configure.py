#!/usr/bin/env python3
"""Safe .env management. No shell evaluation; never keep the plaintext access code."""
from __future__ import annotations
import argparse
import getpass
import os
import re
import secrets
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.catalog import MODES
from app.security import hash_code

ROOT = Path(__file__).resolve().parents[1]

def load_env(path: Path) -> dict[str, str]:
    result = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, sep, value = line.partition("=")
            if not sep or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
                raise ValueError(".env 格式不正确；请使用 configure.py 管理配置")
            result[key] = value
    return result

def write_env(path: Path, values: dict[str, str]):
    for key, value in values.items():
        if "\n" in value or "\r" in value or "$" in value or "#" in value or '"' in value or "'" in value:
            raise ValueError(f"{key} 包含不支持的配置字符")
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("# Sensitive deployment configuration. Do not commit or share.\n")
        f.write("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", help="Only the hostname, without https:// or a port")
    parser.add_argument("--email", help="TLS certificate contact email")
    parser.add_argument("--engine", choices=MODES)
    parser.add_argument("--tts", choices=["on", "off"])
    parser.add_argument("--accept-model-licenses", action="store_true")
    parser.add_argument("--rotate-code", action="store_true", help="Also revoke ALL existing cookies by rotating the signing secret")
    parser.add_argument("--code-file", type=Path, help="Read a code from a protected file instead of a shell argument")
    parser.add_argument("--get", choices=["DOMAIN", "APP_ORIGIN", "ASR_MODE", "ASR_PROVIDER", "TTS_ENABLED", "MODEL_LICENSES_ACK"])
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="Change a documented non-secret parameter")
    parser.add_argument("--dev", action="store_true", help="LOCAL DEVELOPMENT ONLY: origin http://localhost:8000")
    args = parser.parse_args()
    path = ROOT / ".env"
    old = load_env(path)
    if args.get:
        if args.get not in old:
            raise SystemExit("请先完成配置")
        print(old[args.get]); return
    initial = not old
    values = load_env(ROOT / ".env.example")
    values.update(old)
    if initial and not args.domain and not args.dev:
        if not sys.stdin.isatty():
            raise SystemExit("首次配置需要 --domain 和 --email")
        args.domain = input("公网域名，例如 listen.example.com: ").strip()
    if args.domain:
        domain = args.domain.strip().lower()
        if len(domain) > 253 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", domain) or "." not in domain:
            raise SystemExit("域名不正确；只填写主机名，不包含 https://、路径或端口")
        if any(not 1 <= len(label) <= 63 or label.startswith("-") or label.endswith("-") for label in domain.split(".")):
            raise SystemExit("域名不正确")
        import ipaddress
        try: ipaddress.ip_address(domain)
        except ValueError: pass
        else: raise SystemExit("公网部署请使用域名，不使用裸 IP；浏览器麦克风需要可信 HTTPS")
        values["DOMAIN"] = domain
        values["APP_ORIGIN"] = "https://" + domain
    if args.dev:
        values["DOMAIN"] = "localhost"
        values["APP_ORIGIN"] = "http://localhost:8000"
        values["MODEL_DIR"] = str(ROOT / "models")
    if initial and not args.email and not args.dev:
        if not sys.stdin.isatty(): raise SystemExit("首次配置需要 --email")
        args.email = input("证书联系邮箱: ").strip()
    if args.email:
        if not re.fullmatch(r"[^\s@'\"#$]+@[^\s@'\"#$]+\.[^\s@'\"#$]+", args.email): raise SystemExit("邮箱格式不正确")
        values["ACME_EMAIL"] = args.email
    if args.engine: values["ASR_MODE"] = args.engine
    if args.tts: values["TTS_ENABLED"] = "1" if args.tts == "on" else "0"
    if args.accept_model_licenses: values["MODEL_LICENSES_ACK"] = "1"
    if initial or args.rotate_code or args.code_file:
        if args.code_file:
            if args.code_file.stat().st_mode & 0o077:
                raise SystemExit("访问码文件权限过宽；请先 chmod 600 该文件")
            if args.code_file.stat().st_size > 1024:
                raise SystemExit("访问码文件过大")
            code = args.code_file.read_text().strip()
        elif sys.stdin.isatty():
            code = getpass.getpass("设置访问码（至少 12 字符；留空自动生成 12 位数字）: ")
        else:
            raise SystemExit("非交互首次部署请用 --code-file 提供至少 12 字符访问码；禁止通过命令行参数传明文")
        if not code:
            code = "".join(secrets.choice("0123456789") for _ in range(12))
            print("请安全记下访问码（只显示这一次）:", code)
        if not 12 <= len(code) <= 128: raise SystemExit("访问码必须是 12～128 个字符")
        values["ACCESS_CODE_HASH"] = hash_code(code)
        values["SESSION_SECRET"] = secrets.token_urlsafe(48)
    editable = {"ASR_PROVIDER", "ASR_THREADS","MAX_LIVE_SESSIONS","APP_MEMORY","MAX_UPLOAD_MB","MAX_UPLOAD_SECONDS", "MAX_SESSION_SECONDS", "COOKIE_DAYS","VAD_SILENCE","SEGMENT_SECONDS","PARTIAL_INTERVAL","INFERENCE_TIMEOUT","NANO_HOTWORDS","MODEL_BASE_URL"}
    for item in args.set:
        key, sep, value = item.partition("=")
        if not sep or key not in editable: raise SystemExit("--set 只支持文档列出的非敏感运行参数")
        values[key] = value
    # Reuse application validation before replacing a previously working file.
    from app.config import Settings
    before = dict(os.environ)
    try:
        os.environ.update(values); Settings.from_env()
    finally:
        os.environ.clear(); os.environ.update(before)
    if not re.fullmatch(r"[1-9][0-9]*[gm]", values["APP_MEMORY"]): raise SystemExit("APP_MEMORY 应类似 12g 或 4096m")
    write_env(path, values)
    print(f"已写入 {path}，权限 600。引擎={values['ASR_MODE']}；普通话朗读={values['TTS_ENABLED']}")
    if values["MODEL_LICENSES_ACK"] != "1":
        print("下载前请阅读 MODEL_LICENSES.md，并执行 configure.py --accept-model-licenses")
    print("配置修改后，请运行 bash scripts/deploy.sh；只修改 .env 不会更新已运行的容器。")

if __name__ == "__main__":
    main()
