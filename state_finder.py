import os
import sys
import cv2
sys.path.append(os.path.abspath('/'))
from logger import log
from config import get_settings

_settings = get_settings()
orig_screen_width, orig_screen_height = 1920, 1080

states_path = r"./images/states/"

star_drops_path = r"./images/star_drop_types/"
images_with_star_drop = []
for file in os.listdir(star_drops_path):
    if "star_drop" in file:
        images_with_star_drop.append(file)

end_results_path = r"./images/end_results/"

region_data = _settings.lobby.template_matching
super_debug = _settings.general.super_debug == "yes"
if super_debug:
    debug_folder = "./debug_frames/"
    if not os.path.exists(debug_folder):
        os.makedirs(debug_folder)

# End-screen icons (victory/defeat/draw) are animated/glowing, so 0.7
# matched too loosely and produced false transitions out of "match".
# Tighten those specifically; everything else keeps the historical 0.7.
DEFAULT_THRESHOLD = 0.7
TEMPLATE_THRESHOLDS = {
    'victory.png': 0.85,
    'defeat.png': 0.85,
    'draw.png': 0.85,
}


def is_template_in_region(image, template_path, region):
    # Frame is canonical 1920x1080 (resized by WindowController.screenshot),
    # so region coords from lobby_config.toml are used directly.
    orig_x, orig_y, orig_width, orig_height = region
    cropped_image = image[orig_y:orig_y + orig_height, orig_x:orig_x + orig_width]
    loaded_template = load_template(template_path)
    result = cv2.matchTemplate(cropped_image, loaded_template,
                               cv2.TM_CCOEFF_NORMED)
    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
    threshold = TEMPLATE_THRESHOLDS.get(os.path.basename(template_path), DEFAULT_THRESHOLD)
    return max_val > threshold

cached_templates = {}

def load_template(image_path):
    if image_path in cached_templates:
        return cached_templates[image_path]
    image = cv2.imread(image_path)
    cached_templates[image_path] = image
    return image

crop_region = _settings.lobby.lobby['trophy_observer']
def find_game_result(screenshot):
    is_victory = is_template_in_region(screenshot, end_results_path + 'victory.png', crop_region)
    if is_victory:
        return "victory"

    is_defeat = is_template_in_region(screenshot, end_results_path + 'defeat.png', crop_region)
    if is_defeat:
        return "defeat"

    is_draw = is_template_in_region(screenshot, end_results_path + 'draw.png', crop_region)
    if is_draw:
        return "draw"
    return False



def get_in_game_state(image):
    game_result = is_in_end_of_a_match(image)
    if game_result: return f"end_{game_result}"
    if is_in_shop(image): return "shop"
    if is_in_offer_popup(image): return "popup"
    if is_in_lobby(image): return "lobby"
    if is_in_brawler_selection(image):
        return "brawler_selection"

    if is_in_brawl_pass(image) or is_in_star_road(image):
        return "shop"

    if is_in_star_drop(image):
        return "star_drop"

    if is_in_trophy_reward(image):
        return "trophy_reward"

    return "match"


def is_in_shop(image) -> bool:
    return is_template_in_region(image, states_path + 'powerpoint.png', region_data["powerpoint"])


def is_in_brawler_selection(image) -> bool:
    return is_template_in_region(image, states_path + 'brawler_menu_task.png', region_data["brawler_menu_task"])


def is_in_offer_popup(image) -> bool:
    return is_template_in_region(image, states_path + 'close_popup.png', region_data["close_popup"])


def is_in_lobby(image) -> bool:
    return is_template_in_region(image, states_path + 'lobby_menu.png', region_data["lobby_menu"])


def is_in_end_of_a_match(image):
    return find_game_result(image)



def is_in_trophy_reward(image):
    return is_template_in_region(image, states_path + 'trophies_screen.png', region_data["trophies_screen"])



def is_in_brawl_pass(image):
    return is_template_in_region(image, states_path + 'brawl_pass_house.PNG', region_data['brawl_pass_house'])


def is_in_star_road(image):
    return is_template_in_region(image, states_path + "go_back_arrow.png", region_data['go_back_arrow'])


def is_in_star_drop(image):
    for image_filename in images_with_star_drop: #kept getting errors so tried changing from image to image_filename
        if is_template_in_region(image, star_drops_path + image_filename, region_data['star_drop']):
            return True
    return False

def get_state(screenshot):
    screenshot_bgr = cv2.cvtColor(screenshot, cv2.COLOR_RGB2BGR)
    if super_debug: cv2.imwrite(f"./debug_frames/state_screenshot_{len(os.listdir('./debug_frames'))}.png", screenshot_bgr)
    state = get_in_game_state(screenshot_bgr)
    log.debug(f"State: {state}")
    return state


