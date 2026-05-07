"""Pydantic-validated settings loaded from cfg/*.toml.

`get_settings()` returns a memoized Settings instance: load once, reuse
everywhere. Models use `extra='allow'` so unknown TOML keys don't break
loading; explicit fields are typed for the keys the bot actually consumes.

Note: existing utils.load_toml_as_dict / cprint are intentionally left
intact for callers we haven't migrated (gui/, lobby_automation,
trophy_observer). New code should prefer get_settings().
"""
from functools import lru_cache
from typing import Any, Dict, List

try:
    import tomllib  # Python 3.11+
    _HAS_TOMLLIB = True
except ModuleNotFoundError:  # pragma: no cover - 3.10 and below
    import toml as _toml
    _HAS_TOMLLIB = False

from pydantic import BaseModel, ConfigDict


class _AllowExtra(BaseModel):
    model_config = ConfigDict(extra='allow')


class GeneralConfig(_AllowExtra):
    personal_webhook: str = ""
    discord_id: str = ""
    super_debug: str = "no"
    cpu_or_gpu: str = "auto"
    max_ips: str = "auto"
    pyla_version: str = "0.0.0"
    long_press_star_drop: str = "no"
    trophies_multiplier: float = 1
    run_for_minutes: int = 0
    current_emulator: str = "LDPlayer"
    emulator_port: int = 5037
    api_base_url: str = "default"
    brawl_stars_package: str = "com.supercell.brawlstars"


class BotConfig(_AllowExtra):
    gamemode_type: int = 3
    bot_uses_gadgets: str = "yes"
    minimum_movement_delay: float = 0.1
    gamemode: str = "brawlball"
    unstuck_movement_delay: float = 3.0
    unstuck_movement_hold_time: float = 1.5
    wall_model_classes: List[str] = []
    gadget_pixels_minimum: float = 1300.0
    hypercharge_pixels_minimum: float = 2000.0
    super_pixels_minimum: float = 2400.0
    idle_pixels_minimum: float = 10000.0
    wall_detection_confidence: float = 0.9
    entity_detection_confidence: float = 0.6
    seconds_to_hold_attack_after_reaching_max: float = 1.5
    play_again_on_win: str = "no"


class TimeTresholdsConfig(_AllowExtra):
    state_check: float = 5
    no_detections: float = 10
    game_start: float = 0
    idle: float = 5
    gadget: float = 0.5
    hypercharge: float = 1.0
    super: float = 0.1
    wall_detection: float = 0.2
    no_detection_proceed: float = 6.5
    check_if_brawl_stars_crashed: float = 10


class LobbyConfig(_AllowExtra):
    template_matching: Dict[str, Any] = {}
    lobby: Dict[str, Any] = {}
    pixel_counter_crop_area: Dict[str, List[int]] = {}


class Settings(BaseModel):
    general: GeneralConfig
    bot: BotConfig
    time_tresholds: TimeTresholdsConfig
    lobby: LobbyConfig

    @classmethod
    def load(cls):
        def _read(path: str) -> dict:
            if _HAS_TOMLLIB:
                with open(path, "rb") as f:
                    return tomllib.load(f)
            with open(path, "r", encoding="utf-8") as f:
                return _toml.load(f)
        return cls(
            general=GeneralConfig(**_read("cfg/general_config.toml")),
            bot=BotConfig(**_read("cfg/bot_config.toml")),
            time_tresholds=TimeTresholdsConfig(**_read("cfg/time_tresholds.toml")),
            lobby=LobbyConfig(**_read("cfg/lobby_config.toml")),
        )


@lru_cache(maxsize=1)
def get_settings():
    return Settings.load()
