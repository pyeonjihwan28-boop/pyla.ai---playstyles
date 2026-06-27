import time

import window_controller
from async_runtime import run_coro
from lobby_automation import LobbyAutomation
from play import Play
from stage_manager import StageManager
from state_finder import get_state
from time_management import TimeManagement
from utils import async_notify_user, cprint
from window_controller import WindowController
from logger import log
from config import get_settings

_settings = get_settings()


def pyla_main(data, bot_controller=None):
    class Main:

        def __init__(self):
            self.bot_controller = bot_controller
            self.window_controller = WindowController()
            self.Play = Play(*self.load_models(), self.window_controller)
            self.Time_management = TimeManagement()
            self.lobby_automator = LobbyAutomation(self.window_controller)
            self.Stage_manager = StageManager(
                data, self.lobby_automator, self.window_controller,
                bot_controller=bot_controller,
            )
            self.states_requiring_data = ["lobby"]
            if data[0]['automatically_pick']:
                log.info("Picking brawler automatically")
                self.lobby_automator.select_brawler(data[0]['brawler'])
            self.Play.current_brawler = data[0]['brawler']
            self.no_detections_action_threshold = 60 * 8
            self.initialize_stage_manager()
            self.state = None
            try:
                self.max_ips = int(_settings.general.max_ips)
            except ValueError:
                self.max_ips = None
            self.run_for_minutes = int(_settings.general.run_for_minutes)
            self.start_time = time.time()
            self.time_to_stop = False
            self.in_cooldown = False
            self.cooldown_start_time = 0
            self.cooldown_duration = 3 * 60

        def initialize_stage_manager(self):
            self.Stage_manager.Trophy_observer.win_streak = data[0]['win_streak']
            self.Stage_manager.Trophy_observer.current_trophies = data[0]['trophies']
            self.Stage_manager.Trophy_observer.current_wins = data[0]['wins'] if data[0]['wins'] != "" else 0

        @staticmethod
        def load_models():
            folder_path = "./models/"
            model_names = ['mainInGameModel.onnx', 'tileDetector.onnx']
            loaded_models = []

            for name in model_names:
                loaded_models.append(folder_path + name)
            return loaded_models

        def restart_brawl_stars(self):
            self.window_controller.restart_brawl_stars()
            self.Play.time_since_detections["player"] = time.time()
            self.Play.time_since_detections["enemy"] = time.time()
            if self.window_controller.device.app_current().package != window_controller.BRAWL_STARS_PACKAGE:
                screenshot = self.window_controller.screenshot()
                run_coro(async_notify_user("bot_is_stuck", screenshot))
                log.error("Bot got stuck. User notified. Shutting down.")
                self.window_controller.keys_up(list("wasd"))
                if self.bot_controller is not None:
                    self.bot_controller.signal_completion("stuck")
                    return
                self.window_controller.close()
                import sys
                sys.exit(1)

        def manage_time_tasks(self, frame):
            if self.Time_management.state_check():
                state = get_state(frame)
                if state == "match" and self.state != "match":
                    self.Play.on_match_start()
                self.state = state
                if state != "match":
                    self.Play.time_since_last_proceeding = time.time()
                frame_data = None
                self.Stage_manager.do_state(state, frame_data)

            if self.Time_management.no_detections_check():
                frame_data = self.Play.time_since_detections
                for key, value in frame_data.items():
                    if time.time() - value > self.no_detections_action_threshold:
                        self.restart_brawl_stars()

            if self.Time_management.idle_check():
                self.lobby_automator.check_for_idle(frame)

        def _wait_if_paused(self):
            if self.bot_controller is None or not self.bot_controller.pause_event.is_set():
                return

            self.window_controller.keys_up(list("wasd"))
            while (
                self.bot_controller.pause_event.is_set()
                and not self.bot_controller.stop_event.is_set()
            ):
                time.sleep(0.05)

        def main(self): #this is for timer to stop after time
            s_time = time.time()
            c = 0
            while True:
                if self.bot_controller is not None and self.bot_controller.stop_event.is_set():
                    break
                self._wait_if_paused()
                if self.bot_controller is not None and self.bot_controller.stop_event.is_set():
                    break
                if self.max_ips:
                    frame_start = time.perf_counter()
                if self.run_for_minutes > 0 and not self.in_cooldown:
                    elapsed_time = (time.time() - self.start_time) / 60
                    if elapsed_time >= self.run_for_minutes:
                        cprint(f"timer is done, {self.run_for_minutes} is over. continuing for 3 minutes if in game", "#AAE5A4")
                        self.in_cooldown = True # tries to finish game if in game
                        self.cooldown_start_time = time.time()
                        self.Stage_manager.states['lobby'] = lambda: 0

                if self.in_cooldown:
                    if time.time() - self.cooldown_start_time >= self.cooldown_duration:
                        cprint("stopping bot fully", "#AAE5A4")
                        break

                if abs(s_time - time.time()) > 1:
                    elapsed = time.time() - s_time
                    if elapsed > 0:
                        log.debug(f"{c / elapsed:.2f} IPS")
                    s_time = time.time()
                    c = 0

                frame = self.window_controller.screenshot()

                _, last_ft = self.window_controller.get_latest_frame()
                if last_ft > 0 and (time.time() - last_ft) > self.window_controller.FRAME_STALE_TIMEOUT:
                    self.Play.window_controller.keys_up(list("wasd"))
                    log.warning("Stale frame detected -- restarting the game.")
                    self.window_controller.restart_brawl_stars()

                self.manage_time_tasks(frame)


                brawler = self.Stage_manager.brawlers_pick_data[0]['brawler']
                self.Play.main(frame, brawler, self)
                c += 1

                if self.max_ips:
                    target_period = 1 / self.max_ips
                    work_time = time.perf_counter() - frame_start
                    if work_time < target_period:
                        time.sleep(target_period - work_time)

    main = Main()
    main.main()
