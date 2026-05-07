"""Single shared asyncio loop running in a daemon thread.

Replaces the per-call `asyncio.new_event_loop()` + `set_event_loop()` +
`loop.close()` pattern that the bot used for one-shot webhook calls.
Spinning a fresh loop per webhook costs ~tens of ms and leaks futures
when an exception escapes; a single persistent loop is cheaper and
shares connection pools across calls.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any, Coroutine

_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()


def _ensure_loop() -> asyncio.AbstractEventLoop:
    global _loop
    if _loop is not None and _loop.is_running():
        return _loop
    with _loop_lock:
        if _loop is not None and _loop.is_running():
            return _loop
        _loop = asyncio.new_event_loop()
        ready = threading.Event()

        def _run():
            asyncio.set_event_loop(_loop)
            ready.set()
            _loop.run_forever()

        threading.Thread(target=_run, name="async-runtime", daemon=True).start()
        ready.wait()
        return _loop


def run_coro(coro: Coroutine[Any, Any, Any], timeout: float | None = 30.0):
    """Schedule `coro` on the shared loop and block until it completes.

    Mirrors the semantics of the old `loop.run_until_complete(coro)` pattern.
    """
    loop = _ensure_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=timeout)
