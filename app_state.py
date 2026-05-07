"""Persistent per-user app state — push queue and (future) settings.

Lives in cfg/app_state.toml alongside the existing config files. The
queue is stored as a list of inline tables under [[push_queue]] so
TOML serialization stays line-oriented and human-editable.
"""
from dataclasses import dataclass, asdict, fields
from typing import List

from utils import load_toml_as_dict, save_dict_as_toml


_APP_STATE_PATH = "cfg/app_state.toml"


@dataclass
class QueueEntry:
    brawler_name: str
    target_type: str = "trophies"  # "trophies" | "wins"
    target_value: int = 0
    current_trophies: int = 0
    current_wins: int = 0
    win_streak: int = 0
    automatically_pick: bool = True


def _coerce_entry(d: dict) -> QueueEntry:
    valid = {f.name for f in fields(QueueEntry)}
    payload = {k: v for k, v in d.items() if k in valid}
    if "brawler_name" not in payload:
        # tolerate older or hand-edited keys
        payload["brawler_name"] = d.get("brawler", "")
    return QueueEntry(**payload)


def load_queue() -> List[QueueEntry]:
    cfg = load_toml_as_dict(_APP_STATE_PATH)
    raw = cfg.get("push_queue", [])
    if not isinstance(raw, list):
        return []
    return [_coerce_entry(item) for item in raw if isinstance(item, dict)]


def save_queue(entries: List[QueueEntry]) -> None:
    cfg = dict(load_toml_as_dict(_APP_STATE_PATH))
    cfg["push_queue"] = [asdict(e) for e in entries]
    save_dict_as_toml(cfg, _APP_STATE_PATH)


def to_legacy_dict_list(entries: List[QueueEntry]) -> list:
    """Translate to the dict shape expected by the existing bot pipeline.

    Bot reads brawlers_pick_data with keys: brawler, type, push_until,
    trophies, wins, win_streak, automatically_pick. Numeric fields are
    kept as ints (the legacy code handles "" defaults internally).
    """
    return [
        {
            "brawler": e.brawler_name,
            "type": e.target_type,
            "push_until": e.target_value,
            "trophies": e.current_trophies,
            "wins": e.current_wins,
            "win_streak": e.win_streak,
            "automatically_pick": e.automatically_pick,
        }
        for e in entries
    ]
