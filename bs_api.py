"""Brawl Stars official API client.

Wraps `https://api.brawlstars.com/v1/` for read-only player + brawler queries.

Token + IP-whitelist setup:
    1. Login at https://developer.brawlstars.com/ with your Supercell ID.
    2. Create a new key; the page shows your current public IP — paste it
       into the IP whitelist field. The token only works from that IP.
    3. Copy the bearer token into PylaAI Settings tab (or cfg/login.toml
       bs_api_token field).

The client is opt-in: when the token is empty, get_client() returns a
Disabled stub that raises BSApiDisabled on any call. The bot keeps
running on local trophy counts in that case.
"""
import threading
import time
from collections import OrderedDict
from typing import Optional, Tuple
from urllib.parse import quote

import requests

from utils import load_toml_as_dict


_BASE_URL = "https://api.brawlstars.com/v1"
_TIMEOUT_SECONDS = 10.0
_CACHE_TTL_SECONDS = 60.0
_CACHE_MAX_ENTRIES = 32


class BSApiError(Exception):
    def __init__(self, status: int, body: str = ""):
        super().__init__(f"HTTP {status}: {body[:200]}")
        self.status = status
        self.body = body


class BSApiAuthError(BSApiError):
    """401 — token invalid or expired."""


class BSApiForbidden(BSApiError):
    """403 — request IP is not in the token's whitelist."""


class BSApiNotFound(BSApiError):
    """404 — player tag does not exist."""


class BSApiRateLimited(BSApiError):
    """429 — too many requests; respect retry_after_seconds."""

    def __init__(self, retry_after_seconds: float, body: str = ""):
        super().__init__(429, body)
        self.retry_after_seconds = retry_after_seconds


class BSApiDisabled(Exception):
    """Raised by the disabled client stub when no token is configured."""


def normalize_tag(tag: str) -> str:
    """Strip a leading #, uppercase the rest, urlencode for the path."""
    if not tag:
        raise ValueError("empty tag")
    cleaned = tag.strip()
    if cleaned.startswith("#"):
        cleaned = cleaned[1:]
    cleaned = cleaned.upper()
    return quote("#" + cleaned, safe="")


class BSApiClient:
    def __init__(self, token: str):
        if not token:
            raise ValueError("token is required; use get_client() for the disabled stub")
        self.token = token
        self._cache: "OrderedDict[Tuple[str, str], Tuple[float, dict]]" = OrderedDict()
        self._lock = threading.Lock()
        self.next_retry_at = 0.0

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }

    def _cache_get(self, key):
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            ts, value = entry
            if time.time() - ts > _CACHE_TTL_SECONDS:
                self._cache.pop(key, None)
                return None
            self._cache.move_to_end(key)
            return value

    def _cache_put(self, key, value):
        with self._lock:
            self._cache[key] = (time.time(), value)
            self._cache.move_to_end(key)
            while len(self._cache) > _CACHE_MAX_ENTRIES:
                self._cache.popitem(last=False)

    def _request(self, method: str, normalized_tag: str, url: str) -> dict:
        cache_key = (method, normalized_tag)
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        if time.time() < self.next_retry_at:
            wait = self.next_retry_at - time.time()
            raise BSApiRateLimited(wait, "client-side backoff active")

        resp = requests.get(url, headers=self._headers(), timeout=_TIMEOUT_SECONDS)
        body = resp.text
        if resp.status_code == 200:
            data = resp.json()
            self._cache_put(cache_key, data)
            return data
        if resp.status_code == 401:
            raise BSApiAuthError(401, body)
        if resp.status_code == 403:
            raise BSApiForbidden(403, body)
        if resp.status_code == 404:
            raise BSApiNotFound(404, body)
        if resp.status_code == 429:
            retry_after = float(resp.headers.get("Retry-After", "60"))
            self.next_retry_at = time.time() + retry_after
            raise BSApiRateLimited(retry_after, body)
        raise BSApiError(resp.status_code, body)

    def get_player(self, tag: str) -> dict:
        norm = normalize_tag(tag)
        url = f"{_BASE_URL}/players/{norm}"
        return self._request("get_player", norm, url)

    def get_brawlers(self, tag: str) -> list:
        return self.get_player(tag).get("brawlers", [])

    def test_connection(self, tag: str) -> Tuple[bool, str]:
        if not tag:
            return False, "no player tag set"
        try:
            data = self.get_player(tag)
        except BSApiAuthError:
            return False, "401 — token invalid or expired"
        except BSApiForbidden:
            return False, "403 — IP not in whitelist (regenerate token at developer.brawlstars.com)"
        except BSApiNotFound:
            return False, f"404 — player tag {tag} not found"
        except BSApiRateLimited as e:
            return False, f"429 — rate-limited (retry in {e.retry_after_seconds:.0f}s)"
        except requests.exceptions.RequestException as e:
            return False, f"network error: {e!r}"
        except BSApiError as e:
            return False, f"HTTP {e.status}: {e.body[:120]}"
        name = data.get("name", "?")
        trophies = data.get("trophies", "?")
        return True, f"OK — {name} ({trophies} trophies)"


class _DisabledClient:
    """Stub returned by get_client() when no token is configured."""

    def get_player(self, tag: str):
        raise BSApiDisabled("no token configured")

    def get_brawlers(self, tag: str):
        raise BSApiDisabled("no token configured")

    def test_connection(self, tag: str) -> Tuple[bool, str]:
        return False, "no token configured"


_client_lock = threading.Lock()
_client_cached: Optional[object] = None
_client_cached_token: Optional[str] = None


def get_client():
    """Return a memoized BSApiClient or _DisabledClient.

    Re-reads cfg/login.toml each time to detect token edits done via the
    Settings tab. The actual HTTP client is rebuilt only when the token
    value changes.
    """
    global _client_cached, _client_cached_token
    token = (load_toml_as_dict("cfg/login.toml").get("bs_api_token") or "").strip()
    with _client_lock:
        if token != _client_cached_token:
            _client_cached_token = token
            _client_cached = BSApiClient(token) if token else _DisabledClient()
        return _client_cached
