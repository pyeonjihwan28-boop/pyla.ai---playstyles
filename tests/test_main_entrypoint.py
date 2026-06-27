import importlib
import sys
import unittest
from unittest.mock import patch


class TestMainEntrypoint(unittest.TestCase):
    def test_import_does_not_launch_ui(self):
        sys.modules.pop("main", None)
        with patch("builtins.print"):
            module = importlib.import_module("main")
        self.assertTrue(callable(module.launch))


if __name__ == "__main__":
    unittest.main()
