import atexit
import math
import socket
import threading
import time
import cv2
from typing import List

import scrcpy
from adbutils import adb

from logger import log
from config import get_settings

_settings = get_settings()


def _port_open(host: str, port: int, timeout: float = 0.3) -> bool:
    """Probe a TCP endpoint with a short timeout. adb.connect blocks for
    seconds per closed port; this skips the call when nothing is listening."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        try:
            return sock.connect_ex((host, port)) == 0
        except OSError:
            return False

# --- Configuration ---
brawl_stars_width, brawl_stars_height = 1920, 1080

key_coords_dict = {
    "H": (1400, 990),
    "G": (1640, 990),
    "M": (1725, 800),
    "Q": (1660, 980),
    "E": (1510, 880),
    "F": (1360, 920),
}

directions_xy_deltas_dict = {
    "w": (0, -150),
    "a": (-150, 0),
    "s": (0, 150),
    "d": (150, 0),
}

BRAWL_STARS_PACKAGE = _settings.general.brawl_stars_package


class WindowController:
    def __init__(self):
        self.scale_factor = None
        self.width = None
        self.height = None
        self.width_ratio = None
        self.height_ratio = None
        self.joystick_x, self.joystick_y = None, None
        # --- 2. ADB & Scrcpy Connection ---
        log.info("Connecting to ADB...")
        try:
            # Connect to device (adbutils automatically handles port detection mostly)
            # but adbutils is usually smarter at finding the open device.
            device_list = adb.device_list()
            if not device_list:
                candidate_ports = [_settings.general.emulator_port, 5555, 16384, 5635] + list(range(5565, 5756, 10))
                for port in candidate_ports:
                    if not _port_open("127.0.0.1", port):
                        continue
                    try:
                        adb.connect(f"127.0.0.1:{port}")
                    except Exception:
                        pass
                device_list = adb.device_list()

            if not device_list:
                 raise ConnectionError("No ADB devices found.")

            self.device = device_list[0]
            log.info(f"Connected to device: {self.device.serial}")

            self.frame_lock = threading.Lock()
            self.frame_cond = threading.Condition(self.frame_lock)
            self.scrcpy_client = scrcpy.Client(device=self.device, max_width=0)
            self.last_frame = None
            self.last_frame_time = 0.0
            self.last_joystick_pos = (None, None)
            self.FRAME_STALE_TIMEOUT = 15.0

            def on_frame(frame):
                if frame is not None:
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    with self.frame_cond:
                        self.last_frame = frame
                        self.last_frame_time = time.time()
                        self.frame_cond.notify_all()

            self.scrcpy_client.add_listener(scrcpy.EVENT_FRAME, on_frame)
            self.scrcpy_client.start(threaded=True)
            atexit.register(self.close)
            log.info("Scrcpy client started successfully.")

        except Exception as e:
            raise ConnectionError(f"Failed to initialize Scrcpy: {e}")
        self.are_we_moving = False
        self.PID_JOYSTICK = 1  # ID for WASD movement
        self.PID_ATTACK = 2  # ID for clicks/attacks
        self.check_if_brawl_stars_crashed_timer = _settings.time_tresholds.check_if_brawl_stars_crashed
        self.time_since_checked_if_brawl_stars_crashed = time.time()

    def get_latest_frame(self):
        with self.frame_lock:
            if self.last_frame is None:
                return None, 0.0
            return self.last_frame, self.last_frame_time

    def restart_brawl_stars(self):
        self.device.app_stop(BRAWL_STARS_PACKAGE)
        time.sleep(1)
        self.device.app_start(BRAWL_STARS_PACKAGE)
        time.sleep(3)
        self.time_since_checked_if_brawl_stars_crashed = time.time()
        log.info("Brawl stars restarted successfully.")

    def screenshot(self):
        c_time = time.time()
        if c_time - self.time_since_checked_if_brawl_stars_crashed > self.check_if_brawl_stars_crashed_timer:
            opened_app = self.device.app_current().package.strip()
            if opened_app != BRAWL_STARS_PACKAGE.strip():
                log.warning(f"Brawl stars has crashed, {opened_app} is the app opened ! Restarting...")
                self.device.app_start(BRAWL_STARS_PACKAGE)
                time.sleep(3)
                self.time_since_checked_if_brawl_stars_crashed = time.time()
            else:
                self.time_since_checked_if_brawl_stars_crashed = c_time
        deadline = time.time() + 15
        with self.frame_cond:
            while self.last_frame is None:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise ConnectionError(
                        "No frame received from scrcpy within 15s. "
                        "Check USB/emulator connection."
                    )
                self.frame_cond.wait(timeout=remaining)
            frame = self.last_frame
            frame_time = self.last_frame_time

        age = time.time() - frame_time
        if frame_time > 0 and age > self.FRAME_STALE_TIMEOUT:
            log.warning(f"scrcpy frame is {age:.1f}s stale -- feed may be frozen")


        if not self.width or not self.height:
            self.width = frame.shape[1]
            self.height = frame.shape[0]
            if (self.width, self.height) != (brawl_stars_width, brawl_stars_height):
                log.warning(f"Unexpected resolution: {self.width}x{self.height}. Expected {brawl_stars_width}x{brawl_stars_height}. Please set your emulator resolution to 1920x1080 for best results.")
            # width_ratio / height_ratio / scale_factor are now ONLY used to
            # translate canonical (1920x1080) input coords into device
            # touch coords for scrcpy. The vision pipeline below sees a
            # canonical-resized frame regardless of source resolution.
            self.width_ratio = self.width / brawl_stars_width
            self.height_ratio = self.height / brawl_stars_height
            self.joystick_x, self.joystick_y = 220 * self.width_ratio, 870 * self.height_ratio
            self.scale_factor = min(self.width_ratio, self.height_ratio)

        if frame.shape[1] != brawl_stars_width or frame.shape[0] != brawl_stars_height:
            interp = cv2.INTER_AREA if frame.shape[1] > brawl_stars_width else cv2.INTER_LINEAR
            frame = cv2.resize(frame, (brawl_stars_width, brawl_stars_height), interpolation=interp)

        return frame
    def touch_down(self, x, y, pointer_id=0):
        # We explicitly pass the pointer_id
        self.scrcpy_client.control.touch(int(x), int(y), scrcpy.ACTION_DOWN, pointer_id)

    def touch_move(self, x, y, pointer_id=0):
        self.scrcpy_client.control.touch(int(x), int(y), scrcpy.ACTION_MOVE, pointer_id)

    def touch_up(self, x, y, pointer_id=0):
        self.scrcpy_client.control.touch(int(x), int(y), scrcpy.ACTION_UP, pointer_id)

    def keys_up(self, keys: List[str]):
        if "".join(keys).lower() == "wasd":
            if self.are_we_moving:
                # Use PID_JOYSTICK so we don't lift the attack finger
                self.touch_up(self.joystick_x, self.joystick_y, pointer_id=self.PID_JOYSTICK)
                self.are_we_moving = False
                self.last_joystick_pos = (None, None)

    def keys_down(self, keys: List[str]):

        delta_x, delta_y = 0, 0
        for key in keys:
            if key in directions_xy_deltas_dict:
                dx, dy = directions_xy_deltas_dict[key]
                delta_x += dx
                delta_y += dy

        if not self.are_we_moving:
            self.touch_down(self.joystick_x, self.joystick_y, pointer_id=self.PID_JOYSTICK)
            self.are_we_moving = True
            self.last_joystick_pos = (self.joystick_x + delta_x, self.joystick_y + delta_y)

        if self.last_joystick_pos != (self.joystick_x + delta_x, self.joystick_y + delta_y):
            self.touch_move(self.joystick_x + delta_x, self.joystick_y + delta_y, pointer_id=self.PID_JOYSTICK)
            self.last_joystick_pos = (self.joystick_x + delta_x, self.joystick_y + delta_y)

    def click(self, x: int, y: int, delay=0.05, already_include_ratio=True, touch_up=True, touch_down=True):
        if not already_include_ratio:
            x = x * self.width_ratio
            y = y * self.height_ratio
        # Use PID_ATTACK for clicks so we don't interrupt movement
        if touch_down: self.touch_down(x, y, pointer_id=self.PID_ATTACK)
        time.sleep(delay)
        if touch_up: self.touch_up(x, y, pointer_id=self.PID_ATTACK)

    def press_key(self, key, delay=0.05, touch_up=True, touch_down=True):
        if key not in key_coords_dict:
            return
        x, y = key_coords_dict[key]
        target_x = x * self.width_ratio
        target_y = y * self.height_ratio
        self.click(target_x, target_y, delay, touch_up=touch_up, touch_down=touch_down)

    def swipe(self, start_x, start_y, end_x, end_y, duration=0.2):
        dist_x = end_x - start_x
        dist_y = end_y - start_y
        distance = math.sqrt(dist_x ** 2 + dist_y ** 2)

        if distance == 0:
            return

        step_len = 25
        steps = max(int(distance / step_len), 1)
        step_delay = duration / steps

        self.touch_down(int(start_x), int(start_y), pointer_id=self.PID_ATTACK)
        for i in range(1, steps + 1):
            t = i / steps
            cx = start_x + dist_x * t
            cy = start_y + dist_y * t
            time.sleep(step_delay)
            self.touch_move(int(cx), int(cy), pointer_id=self.PID_ATTACK)
        self.touch_up(int(end_x), int(end_y), pointer_id=self.PID_ATTACK)

    def close(self):
        if hasattr(self, 'scrcpy_client'):
            self.scrcpy_client.stop()