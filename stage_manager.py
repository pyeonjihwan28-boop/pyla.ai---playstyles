import os.path
import sys

import time

from logger import log

from async_runtime import run_coro

import cv2
import requests

from state_finder import get_state, find_game_result
from trophy_observer import TrophyObserver
from utils import find_template_center, async_notify_user, save_brawler_data
from config import get_settings

_settings = get_settings()
user_id = _settings.general.discord_id
debug = _settings.general.super_debug == "yes"
user_webhook = _settings.general.personal_webhook


def notify_user(message_type):
    # message type will be used to have conditions determining the message
    # but for now there's only one possible type of message
    message_data = {
        'content': f"<@{user_id}> Pyla Bot has completed all it's targets !"
    }

    response = requests.post(user_webhook, json=message_data)

    if response.status_code != 204:
        log.warning(
            f'Failed to send message. Be sure to have put a valid webhook url in the config. Status code: {response.status_code}')


# Inter-tick pacing for the FSM. The previous implementation used time.sleep
# inside long while-loops which froze the main bot tick for up to 30s; the
# new model returns from each handler within one tick and uses these to
# gate per-tick actions.
_Q_DOUBLE_PRESS_DELAY = 2.0
_REPLAY_MATCH_TIMEOUT = 25.0
_LOBBY_RETRY_PRESS_INTERVAL = 1.0
_LOBBY_RETRY_MAX_ATTEMPTS = 30


class StageManager:

    def __init__(self, brawlers_data, lobby_automator, window_controller):
        self.Lobby_automation = lobby_automator
        self.lobby_config = _settings.lobby.model_dump()
        self.close_popup_icon = None
        self.brawlers_pick_data = brawlers_data
        brawler_list = [brawler["brawler"] for brawler in brawlers_data]
        self.Trophy_observer = TrophyObserver(brawler_list)
        self.time_since_last_stat_change = time.time()
        self.long_press_star_drop = _settings.general.long_press_star_drop
        self.play_again_on_win = _settings.bot.play_again_on_win == "yes"
        self.window_controller = window_controller
        # Tick-driven FSM state for end_game (replaces 25s while-loop).
        # Transitions: None -> q1_pressed -> q2_pressed (Q-flow)
        #              None -> f_pressed (replay flow; falls back to Q-flow on timeout)
        # Reset to None when do_state() observes a non-end_* state.
        self.end_phase = None
        self.end_phase_at = 0.0
        # Tick-driven retry for lobby reach (replaces 30 x time.sleep(1) loop).
        self.lobby_retry = None  # dict: {attempts, last_press_at, next_brawler_name} | None
        self.states = {
            'shop': self.quit_shop,
            'brawler_selection': self.quit_shop,
            'popup': self.close_pop_up,
            'end_draw': self.end_game,
            'end_victory': self.end_game,
            'end_defeat': self.end_game,
            'lobby': self.start_game,
            'star_drop': self.click_star_drop,
            'trophy_reward': lambda: self.window_controller.press_key("Q")
        }

    @staticmethod
    def validate_trophies(trophies_string):
        trophies_string = trophies_string.lower()
        while "s" in trophies_string:
            trophies_string = trophies_string.replace("s", "5")
        numbers = ''.join(filter(str.isdigit, trophies_string))

        if not numbers:
            return False

        trophy_value = int(numbers)
        return trophy_value

    def _resolve_push_target(self, brawler):
        type_of_push = brawler['type']
        values = {
            "trophies": self.Trophy_observer.current_trophies,
            "wins": self.Trophy_observer.current_wins,
        }
        if type_of_push not in values:
            type_of_push = "trophies"
        value = values[type_of_push]
        if value == "" and type_of_push == "wins":
            value = 0
        push_until = brawler['push_until']
        if push_until == "" and type_of_push == "wins":
            push_until = 300
        if push_until == "" and type_of_push == "trophies":
            push_until = 1000
        return type_of_push, value, push_until

    def _exit_all_targets_completed(self, message):
        log.info(message)
        screenshot = self.window_controller.screenshot()
        run_coro(async_notify_user("completed", screenshot))
        if os.path.exists("latest_brawler_data.json"):
            os.remove("latest_brawler_data.json")
        log.info("Bot stopping: all targets completed.")
        self.window_controller.keys_up(list("wasd"))
        self.window_controller.close()
        sys.exit(0)

    def start_game(self):
        # Resume an in-progress lobby retry (kicked off in a previous tick).
        if self.lobby_retry is not None:
            self._tick_lobby_retry()
            return

        log.info("state is lobby, starting game")
        type_of_push, value, push_until = self._resolve_push_target(self.brawlers_pick_data[0])

        if value >= push_until:
            if len(self.brawlers_pick_data) <= 1:
                log.info(
                    f"Brawler reached required trophies/wins (value={value}, "
                    f"target={push_until}). No more brawlers selected for "
                    f"pushing in the menu. Bot will now pause itself until closed."
                )
                screenshot = self.window_controller.screenshot()
                run_coro(async_notify_user("completed", screenshot))
                log.info("Bot stopping: all targets completed with no more brawlers.")
                self.window_controller.keys_up(list("wasd"))
                self.window_controller.close()
                sys.exit(0)
            screenshot = self.window_controller.screenshot()
            run_coro(async_notify_user(self.brawlers_pick_data[0]["brawler"], screenshot))
            self.brawlers_pick_data.pop(0)
            self.Trophy_observer.change_trophies(self.brawlers_pick_data[0]['trophies'])
            self.Trophy_observer.current_wins = self.brawlers_pick_data[0]['wins'] if self.brawlers_pick_data[0]['wins'] != "" else 0
            self.Trophy_observer.win_streak = self.brawlers_pick_data[0]['win_streak']
            next_brawler_name = self.brawlers_pick_data[0]['brawler']
            if self.brawlers_pick_data[0]["automatically_pick"]:
                log.info("Picking next automatically picked brawler")
                screenshot = self.window_controller.screenshot()
                current_state = get_state(screenshot)
                if current_state == "lobby":
                    self.Lobby_automation.select_brawler(next_brawler_name)
                else:
                    log.info("Trying to reach the lobby to switch brawler")
                    self.window_controller.press_key("Q")
                    log.debug("Pressed Q to return to lobby")
                    self.lobby_retry = {
                        "attempts": 1,
                        "last_press_at": time.time(),
                        "next_brawler_name": next_brawler_name,
                    }
                    return  # press-Q-to-start happens after retry succeeds
            else:
                log.info("Next brawler is in manual mode, waiting 10 seconds to let user switch.")

        # q btn is over the start btn
        self.window_controller.keys_up(list("wasd"))
        self.window_controller.press_key("Q")
        log.info("Pressed Q to start a match")

    def _tick_lobby_retry(self):
        retry = self.lobby_retry
        now = time.time()
        if now - retry["last_press_at"] < _LOBBY_RETRY_PRESS_INTERVAL:
            return  # too soon, wait for next tick

        screenshot = self.window_controller.screenshot()
        current_state = get_state(screenshot)
        if current_state == "lobby":
            self.Lobby_automation.select_brawler(retry["next_brawler_name"])
            self.lobby_retry = None
            self.window_controller.keys_up(list("wasd"))
            self.window_controller.press_key("Q")
            log.info("Pressed Q to start a match (after lobby retry)")
            return

        if retry["attempts"] >= _LOBBY_RETRY_MAX_ATTEMPTS:
            log.warning("Failed to reach lobby after max attempts")
            self.lobby_retry = None
            return

        self.window_controller.press_key("Q")
        log.debug("Pressed Q to return to lobby")
        retry["attempts"] += 1
        retry["last_press_at"] = now

    def click_star_drop(self):
        if self.long_press_star_drop == "yes":
            self.window_controller.press_key("Q", 10)
        else:
            self.window_controller.press_key("Q")

    def end_game(self):
        # Tick-driven end-screen handler. Each call returns within one main loop
        # iteration; the previous design ran a 25s while-loop that froze the
        # outer pipeline.
        now = time.time()

        if self.end_phase is None:
            self._record_match_result_once()
            screenshot = self.window_controller.screenshot()
            current_state = get_state(screenshot)
            result = current_state.split("_")[1] if current_state.startswith("end_") else None
            if self.play_again_on_win and result == "victory":
                self.window_controller.press_key("F")
                log.info("Game has ended, pressed F (play again)")
                self.end_phase = "f_pressed"
            else:
                log.info("Game has ended, pressing Q")
                self.window_controller.press_key("Q")
                self.end_phase = "q1_pressed"
            self.end_phase_at = now
            return

        if self.end_phase == "q1_pressed":
            if now - self.end_phase_at >= _Q_DOUBLE_PRESS_DELAY:
                log.debug("Pressing Q again")
                self.window_controller.press_key("Q")
                self.end_phase = "q2_pressed"
                self.end_phase_at = now
            return

        if self.end_phase == "q2_pressed":
            # Q double-press done; outer FSM will move us out of end_* once the
            # game responds. Nothing to do.
            return

        if self.end_phase == "f_pressed":
            # Play-again pressed. If match doesn't start in time, fall back to Q-flow.
            if now - self.end_phase_at >= _REPLAY_MATCH_TIMEOUT:
                log.warning("Match did not start within 25s, pressing Q to return to lobby.")
                self.window_controller.press_key("Q")
                self.end_phase = "q1_pressed"
                self.end_phase_at = now
            return

    def _record_match_result_once(self):
        # Save trophies/wins for the current end-screen if we haven't yet.
        # Guarded by the >10s gate so transient end-screen flickers don't
        # double-record a match.
        if time.time() - self.time_since_last_stat_change <= 10:
            return

        screenshot = self.window_controller.screenshot()
        current_state = get_state(screenshot)
        if not current_state.startswith("end_"):
            return
        result = current_state.split("_")[1]

        current_brawler = self.brawlers_pick_data[0]['brawler']
        self.Trophy_observer.add_trophies(result, current_brawler)
        self.Trophy_observer.add_win(result)
        self.time_since_last_stat_change = time.time()

        type_to_push, value, push_until = self._resolve_push_target(self.brawlers_pick_data[0])
        self.brawlers_pick_data[0][type_to_push] = value
        save_brawler_data(self.brawlers_pick_data)

        if value >= push_until and len(self.brawlers_pick_data) <= 1:
            self._exit_all_targets_completed(
                "Brawler reached required trophies/wins. No more brawlers selected for "
                "pushing in the menu. Bot will now pause itself until closed."
            )

    def quit_shop(self):
        self.window_controller.click(100 * self.window_controller.width_ratio,
                                     60 * self.window_controller.height_ratio)

    def close_pop_up(self):
        screenshot = self.window_controller.screenshot()
        if self.close_popup_icon is None:
            # Frame is canonical 1920x1080 — load template at native PNG size,
            # no per-device scale_factor resize.
            self.close_popup_icon = cv2.imread("images/states/close_popup.png")
        popup_location = find_template_center(screenshot, self.close_popup_icon)
        if popup_location:
            self.window_controller.click(*popup_location)

    def do_state(self, state, data=None):
        # Reset end-screen FSM when the game has clearly moved past the end screen.
        if not state.startswith("end") and self.end_phase is not None:
            self.end_phase = None
            self.end_phase_at = 0.0
            self.time_since_last_stat_change = time.time()

        handler = self.states.get(state)
        if handler is None:
            return
        if data is not None:
            handler(data)
            return
        handler()
