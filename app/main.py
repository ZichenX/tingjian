from __future__ import annotations

import asyncio
import contextlib
import hmac
import ipaddress
import json
import logging
import math
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from typing import Callable

from fastapi import FastAPI, Request, WebSocket, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.websockets import WebSocketDisconnect

from app.audio import AudioError, AudioResampler, decode_upload
from app.config import Settings, ROOT
from app.engine import Engine
from app.security import Auth, RateLimiter, Capacity, verify_code
from app.worker import InferenceWorker, BusyError

log = logging.getLogger("tingjian")


def peer_ip(conn) -> str:
    # The app normally sits behind Caddy, which overwrites X-Real-IP. A named
    # Cloudflare Tunnel can connect directly from loopback instead; in that
    # layout Cloudflare's edge supplies CF-Connecting-IP. Only consult that
    # header when the transport peer is loopback, so a direct client cannot
    # spoof it by sending a request header to an exposed application port.
    transport_peer = conn.client.host if conn.client else ""
    try:
        transport_is_loopback = ipaddress.ip_address(transport_peer).is_loopback
    except ValueError:
        transport_is_loopback = False
    if transport_is_loopback:
        candidate = conn.headers.get("cf-connecting-ip", "")[:64]
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            # A loopback connector is trusted only for a valid Cloudflare
            # client address. Do not fall back to a client-controlled
            # X-Real-IP header when that header is absent or malformed.
            return transport_peer or "unknown"
    candidate = conn.headers.get("x-real-ip", "")[:64]
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return transport_peer or "unknown"


def equal_token(a, b: str) -> bool:
    return isinstance(a, str) and len(a) <= 256 and hmac.compare_digest(a.encode(), b.encode())


async def read_json(request: Request, limit: int = 4096) -> dict:
    if not request.headers.get("content-type", "").lower().startswith("application/json"):
        raise HTTPException(415, "请使用 JSON 请求")
    body = bytearray()
    try:
        async with asyncio.timeout(15):
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > limit:
                    raise HTTPException(413, "请求内容过长")
        result = json.loads(body)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "请求格式不正确") from None
    except TimeoutError:
        raise HTTPException(408, "请求超时，请重试") from None


class SecurityHeaders:
    def __init__(self, app, origin: str):
        self.app = app
        ws_origin = origin.replace("https://", "wss://").replace("http://", "ws://")
        self.headers = {
            "content-security-policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
              f"connect-src 'self' {ws_origin}; img-src 'self' data:; media-src 'self' blob:; "
              "worker-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
            "x-content-type-options": "nosniff",
            "x-frame-options": "DENY",
            "referrer-policy": "no-referrer",
            "permissions-policy": "microphone=(self), camera=(), geolocation=()",
        }

    async def __call__(self, scope, receive, send):
        async def secured(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend((k.encode(), v.encode()) for k, v in self.headers.items())
                headers.append((b"cache-control", b"no-store"))
                message["headers"] = headers
            await send(message)
        await self.app(scope, receive, secured if scope["type"] == "http" else send)


def create_app(settings: Settings | None = None, engine_factory: Callable = Engine) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.validate()
    auth = Auth(settings.secret, settings.cookie_days)
    rates = RateLimiter()
    capacity = Capacity(settings.max_live)
    password_slots = asyncio.Semaphore(2)

    @asynccontextmanager
    async def lifespan(app):
        worker = InferenceWorker(settings.queue_size, settings.inference_timeout)
        # In dual mode CTC previews and AED finals use different native
        # recognizers. Keeping previews on a tiny best-effort lane prevents a
        # long preview decode from delaying a final result. Modes that reuse
        # one recognizer stay on the single worker to avoid concurrent access
        # to the same native object.
        preview_worker = (InferenceWorker(2, settings.inference_timeout)
                          if settings.asr_mode == "dual" else worker)
        app.state.ready = False
        app.state.worker = worker
        app.state.preview_worker = preview_worker
        # There is no "pretend ready" path when an actual model is missing.
        app.state.engine = await asyncio.to_thread(engine_factory, settings)
        warmup = getattr(app.state.engine, "warmup", None)
        if warmup is not None:
            await asyncio.to_thread(warmup)
        await worker.start()
        if preview_worker is not worker:
            await preview_worker.start()
        app.state.ready = True
        try:
            yield
        finally:
            app.state.ready = False
            if preview_worker is not worker:
                await preview_worker.close()
            await worker.close()

    app = FastAPI(title="听见", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.capacity = capacity
    app.state.ready = False
    app.add_middleware(TrustedHostMiddleware,
                       allowed_hosts=[urlsplit(settings.origin).hostname, "127.0.0.1", "localhost"])
    app.add_middleware(SecurityHeaders, origin=settings.origin)

    def origin_required(request):
        if request.headers.get("origin") != settings.origin:
            raise HTTPException(403, "请从本站页面操作")

    def session_required(request, write: bool = False):
        session = auth.read(request.cookies.get(settings.cookie_name))
        if not session and not settings.auth_required:
            token, session = auth.issue()
            # The session is still signed and scoped to this browser, but no
            # access code is needed.  /api/session attaches this token as a
            # cookie before the first write or WebSocket connection.
            request.state.session_token = token
        if not session:
            raise HTTPException(401, "请先输入访问码")
        if write:
            origin_required(request)
            if not equal_token(request.headers.get("x-csrf-token"), auth.csrf(session)):
                raise HTTPException(403, "页面验证已失效，请刷新后重试")
        return session

    @app.exception_handler(BusyError)
    async def busy_handler(request, exc):
        return JSONResponse({"detail": "服务器正忙，请稍后再试"}, status_code=429, headers={"Retry-After": "5"})

    @app.exception_handler(AudioError)
    async def audio_handler(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/health/live")
    async def live():
        return {"ok": True}

    @app.get("/health/ready")
    async def ready():
        return JSONResponse({"ready": app.state.ready}, status_code=200 if app.state.ready else 503)

    @app.get("/api/session")
    async def session_info(request: Request):
        s = session_required(request)
        engine = app.state.engine
        response = JSONResponse({"authenticated": True,
                "auth_required": settings.auth_required, "csrf": auth.csrf(s), "tts": engine.tts_enabled,
                "partial": engine.supports_partial, "engine": settings.asr_mode,
                "max_upload_mb": settings.max_upload_mb, "max_upload_seconds": settings.max_upload_seconds,
                "max_session_seconds": settings.session_seconds, "max_tts_chars": 120})
        token = getattr(request.state, "session_token", None)
        if token:
            response.set_cookie(settings.cookie_name, token, max_age=settings.cookie_days * 86400,
                                secure=settings.secure, httponly=True, samesite="strict", path="/")
        return response

    @app.post("/api/login")
    async def login(request: Request):
        if not settings.auth_required:
            raise HTTPException(404, "访问码登录已关闭")
        origin_required(request)
        if not rates.allow("login-all", 100, 600) or not rates.allow("login:" + peer_ip(request), 10, 600):
            raise HTTPException(429, "尝试次数较多，请十分钟后重试")
        body = await read_json(request, 2048)
        code = body.get("code", "")
        if not isinstance(code, str) or not 1 <= len(code) <= 128:
            raise HTTPException(400, "请输入有效的访问码")
        try:
            await asyncio.wait_for(password_slots.acquire(), 0.5)
        except TimeoutError:
            raise HTTPException(429, "请稍后再试") from None
        try:
            valid = await asyncio.to_thread(verify_code, code, settings.code_hash)
        finally:
            password_slots.release()
        if not valid:
            raise HTTPException(401, "访问码不正确，请再核对一下")
        token, s = auth.issue()
        response = JSONResponse({"ok": True})
        response.set_cookie(settings.cookie_name, token, max_age=settings.cookie_days * 86400,
                            secure=settings.secure, httponly=True, samesite="strict", path="/")
        return response

    @app.post("/api/logout")
    async def logout(request: Request):
        session_required(request, True)
        response = JSONResponse({"ok": True})
        response.delete_cookie(settings.cookie_name, path="/", secure=settings.secure,
                               httponly=True, samesite="strict")
        return response

    @app.post("/api/tts")
    async def tts(request: Request):
        s = session_required(request, True)
        if not app.state.engine.tts_enabled:
            raise HTTPException(503, "此服务器暂未启用朗读")
        if not rates.allow("tts:" + s.sid, 90, 600) or not rates.allow("tts-all", 300, 600):
            raise HTTPException(429, "朗读次数较多，请稍后再试")
        body = await read_json(request)
        text, speed = body.get("text"), body.get("speed", 0.95)
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 120:
            raise HTTPException(400, "每次朗读请使用 1～120 个字")
        if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not math.isfinite(speed) or not 0.7 <= speed <= 1.2:
            raise HTTPException(400, "语速设置不正确")
        if not any(c.isalnum() for c in text):
            raise HTTPException(400, "请先写一些需要朗读的文字")
        if not capacity.acquire(s.sid, peer_ip(request), "tts"):
            raise BusyError()
        try:
            data = await app.state.worker.submit(lambda: app.state.engine.speak(text.strip(), speed), 2)
            response = Response(data, media_type="audio/wav", headers={"Content-Disposition": 'inline; filename="mandarin.wav"'})
            token = getattr(request.state, "session_token", None)
            if token:
                response.set_cookie(settings.cookie_name, token, max_age=settings.cookie_days * 86400,
                                    secure=settings.secure, httponly=True, samesite="strict", path="/")
            return response
        except BusyError:
            raise
        except Exception:
            log.error("tts_request_failed")
            raise HTTPException(503, "朗读暂时没有成功，请缩短文字后重试") from None
        finally:
            capacity.release(s.sid)

    @app.post("/api/transcribe")
    async def upload(request: Request):
        s = session_required(request, True)
        if not rates.allow("upload:" + s.sid, 12, 600):
            raise HTTPException(429, "上传次数较多，请稍后再试")
        if not capacity.acquire(s.sid, peer_ip(request), "upload"):
            raise BusyError()
        temp = tempfile.TemporaryDirectory(prefix="tingjian-")
        path = Path(temp.name) / "recording.bin"
        limit = settings.max_upload_mb * 1024 * 1024
        try:
            try:
                length = int(request.headers.get("content-length", "0"))
            except ValueError:
                raise HTTPException(400, "上传长度不正确") from None
            if length < 0 or length > limit:
                raise HTTPException(413, f"录音文件不能超过 {settings.max_upload_mb} MB")
            count = 0
            async with asyncio.timeout(180):
                with path.open("wb") as f:
                    async for chunk in request.stream():
                        count += len(chunk)
                        if count > limit:
                            raise HTTPException(413, f"录音文件不能超过 {settings.max_upload_mb} MB")
                        f.write(chunk)
            if count == 0:
                raise HTTPException(400, "请选择一份录音")
            samples = await decode_upload(path, settings.max_upload_seconds)
            path.unlink(missing_ok=True)
        except TimeoutError:
            temp.cleanup()
            capacity.release(s.sid)
            raise HTTPException(408, "上传或解码超时，请缩短录音后重试") from None
        except BaseException:
            temp.cleanup()
            capacity.release(s.sid)
            raise

        async def events():
            def event(data):
                return (json.dumps(data, ensure_ascii=False) + "\n").encode()
            try:
                yield event({"type": "progress", "progress": 0, "message": "正在分段识别"})
                segments = await asyncio.to_thread(lambda: list(app.state.engine.segments(samples)))
                count = len(segments)
                for index, seg in enumerate(segments):
                    if await request.is_disconnected():
                        return
                    text = await app.state.worker.submit(lambda seg=seg: app.state.engine.transcribe(seg.samples), 1)
                    yield event({"type": "final", "id": seg.id, "text": text, "start": seg.start,
                                 "end": seg.end, "progress": round(100 * (index + 1) / max(count, 1))})
                yield event({"type": "done", "ok": True, "segments": count})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("upload_failed:%s", type(exc).__name__)
                yield event({"type": "error", "message": "这份录音未全部识别成功，已有文字已保留，请分段后重试"})
            finally:
                temp.cleanup()
                capacity.release(s.sid)
        return StreamingResponse(events(), media_type="application/x-ndjson", headers={"X-Accel-Buffering": "no"})

    @app.websocket("/api/live")
    async def socket(ws: WebSocket):
        session = auth.read(ws.cookies.get(settings.cookie_name))
        if not session and not settings.auth_required:
            _, session = auth.issue()
        if not session or ws.headers.get("origin") != settings.origin:
            await ws.close(code=1008)
            return
        if not rates.allow("ws:" + session.sid, 30, 600):
            await ws.close(code=1013)
            return
        await ws.accept()
        owned = False
        completed = False
        partial_job = None
        jobs: set[asyncio.Task] = set()
        failure = asyncio.Event()
        send_lock = asyncio.Lock()
        last_final = -1

        async def send(message):
            async with send_lock:
                await asyncio.wait_for(ws.send_json(message), 10)

        try:
            hello = await asyncio.wait_for(ws.receive_json(), 10)
            if not isinstance(hello, dict) or hello.get("type") != "start" or not equal_token(hello.get("csrf"), auth.csrf(session)):
                raise AudioError("页面验证已失效，请刷新后重试")
            resampler = AudioResampler(hello.get("sample_rate"))
            if not capacity.acquire(session.sid, peer_ip(ws), "live"):
                raise BusyError()
            owned = True
            segmenter = await asyncio.to_thread(app.state.engine.new_segmenter)
            start = time.monotonic()
            partial_at = start
            await send({"type": "ready", "partial": app.state.engine.supports_partial,
                        "max_seconds": settings.session_seconds})

            async def final(seg):
                nonlocal last_final
                try:
                    text = await app.state.worker.submit(lambda: app.state.engine.transcribe(seg.samples), 0)
                    last_final = max(last_final, seg.id)
                    await send({"type": "final", "id": seg.id, "text": text, "start": seg.start, "end": seg.end})
                except asyncio.CancelledError:
                    raise
                except Exception:
                    failure.set()
                    with contextlib.suppress(Exception):
                        await send({"type": "error", "message": "有一句话没有识别成功，请停止后重试"})

            def enqueue(segments):
                for seg in segments:
                    if len(jobs) >= 4:
                        raise BusyError("定稿任务积压")
                    task = asyncio.create_task(final(seg))
                    jobs.add(task)
                    task.add_done_callback(jobs.discard)

            async def preview(identifier, audio):
                valid = lambda: not completed and segmenter.active_id == identifier and identifier > last_final
                try:
                    text = await app.state.preview_worker.submit(
                        lambda: app.state.engine.transcribe(audio, True), 3, valid)
                    if text and valid():
                        await send({"type": "partial", "id": identifier, "text": text})
                except (BusyError, asyncio.CancelledError):
                    pass
                except Exception:
                    log.warning("preview_skipped")

            while True:
                remaining = settings.session_seconds - (time.monotonic() - start)
                if remaining <= 0 or int(time.time()) >= session.exp:
                    await send({"type": "notice", "message": "本次听写到时，正在整理最后一句"})
                    break
                message = await asyncio.wait_for(ws.receive(), min(40, remaining + 0.1))
                if message["type"] == "websocket.disconnect":
                    raise WebSocketDisconnect()
                if message.get("bytes") is not None:
                    data = message["bytes"]
                    if not 0 < len(data) <= resampler.rate or not rates.allow("frame:" + session.sid, 100, 1):
                        raise AudioError("音频发送过快或格式异常")
                    audio = resampler.feed(data)
                    if resampler.input_count / resampler.rate > time.monotonic() - start + 4:
                        raise AudioError("实时接口不接受加速上传，请使用“上传录音”")
                    enqueue(segmenter.feed(audio))
                    now = time.monotonic()
                    if app.state.engine.supports_partial and now - partial_at >= settings.partial_interval and (partial_job is None or partial_job.done()):
                        current = segmenter.preview()
                        if current:
                            partial_at = now
                            partial_job = asyncio.create_task(preview(*current))
                else:
                    text = message.get("text", "")
                    if len(text) > 1024:
                        raise AudioError("控制消息过长")
                    control = json.loads(text)
                    if not isinstance(control, dict) or control.get("type") != "stop":
                        raise AudioError("不支持的控制消息")
                    break
                if failure.is_set():
                    break
            completed = True
            if partial_job:
                partial_job.cancel()
            enqueue(segmenter.feed(resampler.feed(b"", last=True)))
            enqueue(segmenter.flush())
            if jobs:
                await asyncio.wait_for(asyncio.gather(*list(jobs)), settings.inference_timeout + 65)
            await send({"type": "done", "ok": not failure.is_set()})
            await ws.close(code=1000)
        except WebSocketDisconnect:
            pass
        except (AudioError, BusyError, ValueError, TimeoutError) as exc:
            completed = True
            if isinstance(exc, BusyError):
                message = "服务器暂时忙不过来，已确认的文字已保留，请稍后重试"
            elif isinstance(exc, TimeoutError):
                message = "连接或识别超时，已确认的文字已保留，请重新开始"
            elif isinstance(exc, AudioError):
                message = str(exc)
            else:
                message = "请求格式不正确，请刷新页面后重试"
            with contextlib.suppress(Exception):
                await send({"type": "error", "message": message})
                await ws.close(code=1013 if isinstance(exc, BusyError) else 1008)
        except Exception as exc:
            log.error("live_failed:%s", type(exc).__name__)
            with contextlib.suppress(Exception):
                await send({"type": "error", "message": "连接暂时中断，已确认的文字已保留，请重新开始"})
                await ws.close(code=1011)
        finally:
            completed = True
            if partial_job:
                partial_job.cancel()
                await asyncio.gather(partial_job, return_exceptions=True)
            for task in list(jobs):
                task.cancel()
            if jobs:
                await asyncio.gather(*list(jobs), return_exceptions=True)
            if owned:
                capacity.release(session.sid)

    @app.get("/")
    async def home():
        return FileResponse(ROOT / "web" / "index.html")

    app.mount("/assets", StaticFiles(directory=ROOT / "web"), name="assets")
    return app
