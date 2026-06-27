import os


def pyla_main(data, bot_controller=None):
    from bot_runner import pyla_main as run_bot
    return run_bot(data, bot_controller=bot_controller)


def launch():
    from gui.hub import Hub
    from gui.login import login
    from gui.main import App
    from gui.select_brawler import SelectBrawler
    from logger import log
    from config import get_settings
    from utils import (
        api_base_url,
        check_version,
        current_wall_model_is_latest,
        get_brawler_list,
        get_latest_version,
        get_latest_wall_model_file,
        update_missing_brawlers_info,
        update_wall_model_classes,
    )

    settings = get_settings()
    pyla_version = settings.general.pyla_version
    all_brawlers = get_brawler_list()

    if api_base_url != "localhost":
        update_missing_brawlers_info(all_brawlers)
        check_version()
        update_wall_model_classes()
        if not current_wall_model_is_latest():
            log.info("New Wall detection model found, downloading... (this might take a few minutes depending on your internet speed)")
            get_latest_wall_model_file()

    if os.environ.get("PYLA_LEGACY_UI") == "1":
        app = App(login, SelectBrawler, pyla_main, all_brawlers, Hub)
        app.start(pyla_version, get_latest_version)
        return

    from gui.app_v2 import TabbedApp

    def _legacy_brawlers_provider():
        captured = {"data": []}
        legacy_app = App(login, SelectBrawler, lambda data: captured.update(data=data), all_brawlers, Hub)
        legacy_app.start(pyla_version, get_latest_version)
        return captured["data"]

    tabbed = TabbedApp(_legacy_brawlers_provider, pyla_version=pyla_version)
    tabbed.start()


if __name__ == "__main__":
    launch()
