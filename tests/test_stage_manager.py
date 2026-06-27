import os
import sys
import types
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

sys.modules.pop("utils", None)
sys.modules.pop("trophy_observer", None)
sys.modules.pop("state_finder", None)
sys.modules.pop("stage_manager", None)

from stage_manager import StageManager


class TestStageManagerTargets(unittest.TestCase):
    def test_resolve_push_target_coerces_legacy_string_values(self):
        manager = StageManager.__new__(StageManager)
        manager.Trophy_observer = types.SimpleNamespace(
            current_trophies="500",
            current_wins="",
        )

        target_type, value, push_until = manager._resolve_push_target({
            "type": "trophies",
            "push_until": "600",
        })

        self.assertEqual(target_type, "trophies")
        self.assertEqual(value, 500)
        self.assertEqual(push_until, 600)

    def test_resolve_push_target_defaults_empty_wins(self):
        manager = StageManager.__new__(StageManager)
        manager.Trophy_observer = types.SimpleNamespace(
            current_trophies=0,
            current_wins="",
        )

        target_type, value, push_until = manager._resolve_push_target({
            "type": "wins",
            "push_until": "",
        })

        self.assertEqual(target_type, "wins")
        self.assertEqual(value, 0)
        self.assertEqual(push_until, 300)


if __name__ == "__main__":
    unittest.main()
