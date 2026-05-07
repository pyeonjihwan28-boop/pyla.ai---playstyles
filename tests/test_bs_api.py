"""Mock-based unit tests for the Brawl Stars API client."""
import os
import sys
import types
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _stub_utils(token: str = ""):
    """Inject a minimal utils module so bs_api can import without pulling
    the real toml-loading machinery (which would touch cfg/login.toml on
    the developer machine running the tests)."""
    stub = types.ModuleType("utils")
    stub.load_toml_as_dict = lambda path: {"bs_api_token": token}
    stub.save_dict_as_toml = lambda data, path: None
    sys.modules["utils"] = stub


_stub_utils("test-token")
import bs_api  # noqa: E402  imported after stub
import importlib  # noqa: E402


def _mock_response(status_code: int, json_data=None, headers=None, body: str = ""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.headers = headers or {}
    resp.text = body
    return resp


class TestTagNormalization(unittest.TestCase):
    def test_tag_normalization_collapses_variants(self):
        a = bs_api.normalize_tag("#abc123")
        b = bs_api.normalize_tag("abc123")
        c = bs_api.normalize_tag("#ABC123")
        self.assertEqual(a, b)
        self.assertEqual(b, c)
        self.assertEqual(a, "%23ABC123")


class TestBSApiClient(unittest.TestCase):
    def setUp(self):
        # Re-stub utils with a non-empty token before each test.
        _stub_utils("test-token")
        importlib.reload(bs_api)
        self.client = bs_api.BSApiClient("test-token")

    def test_get_player_success(self):
        with patch("bs_api.requests.get") as mock_get:
            mock_get.return_value = _mock_response(200, {"name": "ZeroOne", "trophies": 50000})
            data = self.client.get_player("#ABC")
            self.assertEqual(data["name"], "ZeroOne")
            args, kwargs = mock_get.call_args
            self.assertIn("Authorization", kwargs["headers"])
            self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-token")
            self.assertIn("%23ABC", args[0])

    def test_get_player_401_raises_auth_error(self):
        with patch("bs_api.requests.get") as mock_get:
            mock_get.return_value = _mock_response(401, body="invalid token")
            with self.assertRaises(bs_api.BSApiAuthError):
                self.client.get_player("#ABC")

    def test_get_player_403_raises_forbidden(self):
        with patch("bs_api.requests.get") as mock_get:
            mock_get.return_value = _mock_response(403, body="ip not allowed")
            with self.assertRaises(bs_api.BSApiForbidden):
                self.client.get_player("#ABC")

    def test_get_player_404_raises_not_found(self):
        with patch("bs_api.requests.get") as mock_get:
            mock_get.return_value = _mock_response(404, body="player not found")
            with self.assertRaises(bs_api.BSApiNotFound):
                self.client.get_player("#NOPE")

    def test_get_player_429_raises_rate_limited(self):
        with patch("bs_api.requests.get") as mock_get:
            mock_get.return_value = _mock_response(429, headers={"Retry-After": "30"})
            with self.assertRaises(bs_api.BSApiRateLimited) as ctx:
                self.client.get_player("#ABC")
            self.assertAlmostEqual(ctx.exception.retry_after_seconds, 30.0)

    def test_cache_hit_avoids_second_request(self):
        with patch("bs_api.requests.get") as mock_get:
            mock_get.return_value = _mock_response(200, {"name": "ZeroOne", "trophies": 1})
            self.client.get_player("#ABC")
            self.client.get_player("#ABC")  # within 60s — should hit cache
            self.assertEqual(mock_get.call_count, 1)


class TestDisabledClient(unittest.TestCase):
    def test_disabled_client_when_no_token(self):
        _stub_utils("")
        importlib.reload(bs_api)
        client = bs_api.get_client()
        with self.assertRaises(bs_api.BSApiDisabled):
            client.get_player("#ABC")
        ok, msg = client.test_connection("#ABC")
        self.assertFalse(ok)
        self.assertIn("no token", msg.lower())


if __name__ == "__main__":
    unittest.main()
