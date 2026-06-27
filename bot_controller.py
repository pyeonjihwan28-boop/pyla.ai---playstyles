"""BotController — thread boundary between bot daemon and Tk UI.

The bot's main loop runs in a daemon thread launched by BotController.start().
UI code triggers pause/resume/stop via threading.Event flags; bot writes
status updates to update_queue (queue.Queue) which the Tk thread drains via
root.after().

signal_completion() replaces the historical sys.exit() calls inside the
bot path so the Tk mainloop doesn't crash when the bot finishes.
"""
import queue
import threading
from typing import Optional

from bot_state import (
    BotState,
    STATUS_COMPLETED,
    STATUS_ERROR,
    STATUS_IDLE,
    STATUS_PAUSED,
    STATUS_RUNNING,
    STATUS_STOPPED,
)
from logger import log


class BotController:
    def __init__(self):
        self.pause_event = threading.Event()
        self.stop_event = threading.Event()
        self._lock = threading.Lock()
        self._state = BotState()
        self.update_queue: "queue.Queue[dict]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._completion_reason: Optional[str] = None

    @property
    def state(self) -> BotState:
        with self._lock:
            return BotState(**vars(self._state))

    def update(self, **fields):
        with self._lock:
            for k, v in fields.items():
                setattr(self._state, k, v)
            ev = self._state.to_event()
        self.update_queue.put(ev)

    def start(self, brawlers_data):
        if self._thread is not None and self._thread.is_alive():
            log.warning("BotController.start called while a worker is already running")
            return
        self.stop_event.clear()
        self.pause_event.clear()
        self._completion_reason = None
        self.update(status=STATUS_RUNNING, message="starting")
        self._thread = threading.Thread(
            target=self._worker,
            args=(brawlers_data,),
            name="pyla-bot",
            daemon=True,
        )
        self._thread.start()

    def _worker(self, brawlers_data):
        # Imported lazily so the controller module itself stays importable
        # even when GUI code is loaded before the bot pipeline.
        from bot_runner import pyla_main
        try:
            pyla_main(brawlers_data, bot_controller=self)
        except SystemExit as e:
            # Legacy bot paths may still raise SystemExit through library code
            # we don't fully control; convert to a clean completion.
            log.warning(f"bot worker raised SystemExit({e.code}); converting to completion")
            self.signal_completion(f"sysexit_{e.code}")
        except Exception as e:
            log.exception("bot worker crashed")
            with self._lock:
                self._state.status = STATUS_ERROR
                self._state.message = f"crash: {e!r}"
                ev = self._state.to_event()
            self.update_queue.put(ev)
        finally:
            if self._completion_reason is None and self.state.status == STATUS_RUNNING:
                self.signal_completion("worker_exit")

    def pause(self):
        self.pause_event.set()
        self.update(status=STATUS_PAUSED, message="paused by user")

    def resume(self):
        self.pause_event.clear()
        self.update(status=STATUS_RUNNING, message="resumed")

    def stop(self):
        self.stop_event.set()
        # Make sure a paused worker wakes up to observe the stop flag.
        self.pause_event.clear()
        self.update(status=STATUS_STOPPED, message="stop requested")

    def signal_completion(self, reason: str):
        if self._completion_reason is not None:
            return
        self._completion_reason = reason
        log.info(f"bot completion signalled: {reason}")
        self.update(status=STATUS_COMPLETED, message=reason)

    def reset(self):
        # Return to a clean idle state — UI calls this when user clears the
        # completion banner before starting another run.
        if self._thread is not None and self._thread.is_alive():
            log.warning("BotController.reset called while worker is alive; ignored")
            return
        self.stop_event.clear()
        self.pause_event.clear()
        self._completion_reason = None
        self.update(status=STATUS_IDLE, message="")
