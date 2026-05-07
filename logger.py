"""Project-wide logger.

Replaces ad-hoc `print()` calls. Level is DEBUG when
`super_debug = "yes"` in general_config.toml, otherwise INFO.

Use as:
    from logger import log
    log.info("message")
    log.debug("verbose state")
    log.warning("non-fatal")
    log.error("failure")
"""
import logging
import sys

from utils import load_toml_as_dict

_super_debug = load_toml_as_dict("cfg/general_config.toml").get("super_debug") == "yes"
_level = logging.DEBUG if _super_debug else logging.INFO

_handler = logging.StreamHandler(sys.stderr)
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

log = logging.getLogger("pyla")
log.setLevel(_level)
if not log.handlers:
    log.addHandler(_handler)
log.propagate = False
