from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import OrderedDict, deque
from dataclasses import dataclass


def hash_code(code: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    rounds = 310_000
    digest = hashlib.pbkdf2_hmac("sha256", code.encode(), salt.encode(), rounds).hex()
    return f"pbkdf2_sha256:{rounds}:{salt}:{digest}"


def verify_code(code: str, encoded: str) -> bool:
    try:
        name, rounds, salt, expected = encoded.split(":")
        if name != "pbkdf2_sha256" or not 100_000 <= int(rounds) <= 1_000_000:
            return False
        got = hashlib.pbkdf2_hmac("sha256", code.encode(), salt.encode(), int(rounds)).hex()
        return hmac.compare_digest(got, expected)
    except (ValueError, TypeError):
        return False


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


@dataclass(frozen=True)
class Session:
    sid: str
    exp: int


class Auth:
    def __init__(self, secret: str, days: int = 30):
        self.key = secret.encode()
        self.days = days

    def issue(self) -> tuple[str, Session]:
        session = Session(secrets.token_urlsafe(24), int(time.time()) + self.days * 86400)
        data = b64(json.dumps({"sid": session.sid, "exp": session.exp}, separators=(",", ":")).encode())
        sig = b64(hmac.digest(self.key, data.encode(), "sha256"))
        return f"{data}.{sig}", session

    def read(self, token: str | None) -> Session | None:
        try:
            if not token or len(token) > 1024:
                return None
            data, sig = token.split(".")
            if not hmac.compare_digest(sig, b64(hmac.digest(self.key, data.encode(), "sha256"))):
                return None
            payload = json.loads(base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)))
            sid, exp = payload["sid"], payload["exp"]
            if not isinstance(sid, str) or len(sid) != 32 or type(exp) is not int or exp <= time.time():
                return None
            return Session(sid, exp)
        except (ValueError, KeyError, TypeError):
            return None

    def csrf(self, session: Session) -> str:
        return b64(hmac.digest(self.key, f"csrf:{session.sid}:{session.exp}".encode(), "sha256"))


class RateLimiter:
    """单进程滑动窗口；内存有界。必须结合单 worker 与公网边缘防护使用。"""
    def __init__(self, max_keys: int = 4096):
        self.entries: OrderedDict[str, deque[float]] = OrderedDict()
        self.max_keys = max_keys

    def allow(self, key: str, limit: int, seconds: float) -> bool:
        now = time.monotonic()
        q = self.entries.setdefault(key, deque())
        self.entries.move_to_end(key)
        while q and q[0] <= now - seconds:
            q.popleft()
        while len(self.entries) > self.max_keys:
            self.entries.popitem(last=False)
        if len(q) >= limit:
            return False
        q.append(now)
        return True


class Capacity:
    """事件循环内无 await 地获取名额，避免 check-then-act 竞态。"""
    def __init__(self, max_live: int):
        self.max_live = max_live
        self.active: dict[str, tuple[str, str]] = {}

    def acquire(self, sid: str, ip: str, kind: str) -> bool:
        if sid in self.active:
            return False
        kinds = [k for _, k in self.active.values()]
        if kind == "live":
            if kinds.count("live") >= self.max_live or any(p == ip and k == "live" for p, k in self.active.values()):
                return False
        elif kinds.count(kind) >= 1:  # 同时最多一个上传任务、一个 TTS 请求
            return False
        self.active[sid] = (ip, kind)
        return True

    def release(self, sid: str) -> None:
        self.active.pop(sid, None)
