from __future__ import annotations

import asyncio
import itertools
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Any

log = logging.getLogger("tingjian")


class BusyError(Exception):
    pass


class InferenceWorker:
    """一个模型执行线程；有界优先级队列，避免多请求同时访问原生模型。

    final=0, upload=1, tts=2, partial=3。预览只能在无积压时入队；过期预览可丢，
    定稿不可静默丢失。请求取消并不等于 C++ 推理取消，工作线程仍会等它完成。
    """
    def __init__(self, maxsize: int = 12, timeout: float = 120, fatal: Callable = os._exit):
        self.queue: asyncio.PriorityQueue = asyncio.PriorityQueue(maxsize=maxsize)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="model")
        self.counter = itertools.count()
        self.timeout = timeout
        self.fatal = fatal
        self.task: asyncio.Task | None = None
        self.running = False

    async def start(self) -> None:
        self.task = asyncio.create_task(self._run())

    async def submit(self, fn: Callable[[], Any], priority: int, valid: Callable[[], bool] | None = None):
        if self.queue.full() or (priority == 3 and (self.running or not self.queue.empty())):
            raise BusyError("服务器忙，请稍后再试")
        future = asyncio.get_running_loop().create_future()
        self.queue.put_nowait((priority, next(self.counter), time.monotonic(), fn, valid, future))
        try:
            return await future
        finally:
            if not future.done():
                future.cancel()

    async def _run(self) -> None:
        while True:
            priority, _, created, fn, valid, future = await self.queue.get()
            try:
                if future.cancelled():
                    continue
                if (valid is not None and not valid()) or (priority == 3 and time.monotonic() - created > 4):
                    future.set_result(None)
                    continue
                if time.monotonic() - created > 60:
                    future.set_exception(BusyError("等待识别超时，请重试"))
                    continue
                self.running = True
                native = asyncio.get_running_loop().run_in_executor(self.pool, fn)
                try:
                    result = await asyncio.wait_for(asyncio.shield(native), self.timeout)
                except asyncio.TimeoutError:
                    # 无法安全杀死 Python 中的 C++ 线程。退出进程让 Docker 重启，
                    # 不假装已取消，也不启动更多线程把服务器拖垮。
                    log.critical("native_inference_timeout_restart")
                    self.fatal(70)
                    raise RuntimeError("模型推理超时")
                if not future.done():
                    future.set_result(result if valid is None or valid() else None)
            except asyncio.CancelledError:
                if not future.done():
                    future.cancel()
                raise
            except Exception as exc:
                log.error("model_error:%s", type(exc).__name__)  # 不记录音频/转写文本
                if not future.done():
                    future.set_exception(exc)
            finally:
                self.running = False
                self.queue.task_done()

    async def close(self) -> None:
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        while not self.queue.empty():
            *_, future = self.queue.get_nowait()
            if not future.done():
                future.cancel()
            self.queue.task_done()
        self.pool.shutdown(wait=False, cancel_futures=True)
