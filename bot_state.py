"""Thread-safe snapshot of bot status, written by the bot daemon thread and
read by the Tk UI thread via BotController.update_queue.

The bot thread NEVER touches widgets; it pushes BotState diffs as dict
events into a queue.Queue, and the UI thread drains them via
root.after(100, poll). This keeps customtkinter (Tk) safe from the
classic cross-thread Tcl interpreter crashes.
"""
from dataclasses import dataclass, field
from typing import Optional


STATUS_IDLE = "idle"
STATUS_RUNNING = "running"
STATUS_PAUSED = "paused"
STATUS_STOPPED = "stopped"
STATUS_COMPLETED = "completed"
STATUS_ERROR = "error"


@dataclass
class BotState:
    status: str = STATUS_IDLE
    current_brawler: Optional[str] = None
    fps: float = 0.0
    computed_trophies: int = 0
    api_trophies: Optional[int] = None
    last_result: Optional[str] = None
    message: str = ""

    def to_event(self) -> dict:
        return {
            "status": self.status,
            "current_brawler": self.current_brawler,
            "fps": self.fps,
            "computed_trophies": self.computed_trophies,
            "api_trophies": self.api_trophies,
            "last_result": self.last_result,
            "message": self.message,
        }
