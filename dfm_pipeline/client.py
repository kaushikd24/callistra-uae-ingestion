"""
Thin client for DFM's (Dubai Financial Market) public disclosures API.

No bot protection (plain requests works, no curl_cffi needed) — but real
rate limiting: sustained rapid calls (even ~2/sec sustained over dozens of
requests) eventually get a `200 OK` with an *empty body* (not an HTTP error).
Recovers on its own after roughly a 15s cooldown. Retry-with-backoff is
built into `_get` below rather than left to callers.

One call returns everything needed (headline, issuer, issuer_symbol,
resources[] with PDF paths) — no separate detail call like Nasdaq Dubai.
The server also silently caps `take` at 20 regardless of what's requested,
so pagination must proceed in 20-item pages.

`from`/`to` date-range params are genuinely honoured (verified) — unlike
ADX (rejects extra params) and Nasdaq Dubai (accepts but ignores them).
"""
from __future__ import annotations

import json
import time
from urllib.parse import quote

import requests

API_BASE = "https://api2.dfm.ae/efsah/v1"
DOCS_BASE = "https://feeds.dfm.ae/documents"

PAGE_SIZE = 20  # server-enforced cap regardless of the `take` param


def _parse_json(text: str) -> dict | None:
    text = text.lstrip("﻿")
    if not text.strip():
        return None
    return json.loads(text)


class DFMClient:
    def __init__(self, timeout: int = 20, max_retries: int = 4) -> None:
        self._timeout = timeout
        self._max_retries = max_retries
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "Mozilla/5.0"})

    def _get(self, url: str, params: dict) -> dict:
        delay = 2.0
        for attempt in range(self._max_retries):
            r = self._session.get(url, params=params, timeout=self._timeout)
            r.raise_for_status()
            data = _parse_json(r.text)
            if data is not None:
                return data
            time.sleep(delay)
            delay = min(delay * 1.5, 12.0)
        raise RuntimeError(f"DFM API returned empty body after {self._max_retries} retries: {url}")

    def fetch_page(self, skip: int, from_date: str = "", to_date: str = "",
                    types: str = "", symbol: str = " ", keyword: str = "") -> list[dict]:
        params = {
            "lang": "en",
            "h7_datetime_format": "MMM dd, yyyy HH:mm:ss",
            "from": from_date,
            "to": to_date,
            "announcement_type": "Disclosure",
            "types": types,
            "symbol": symbol,
            "keyword": keyword,
            "cms_resources": "true",
            "take": PAGE_SIZE,
            "skip": skip,
        }
        data = self._get(f"{API_BASE}/prototype_efsah", params)
        return data.get("root", []) or []

    def fetch_count(self, from_date: str = "", to_date: str = "") -> int:
        params = {
            "lang": "en",
            "h7_datetime_format": "MMM dd, yyyy HH:mm:ss",
            "from": from_date,
            "to": to_date,
            "announcement_type": "Disclosure",
            "types": "",
            "symbol": " ",
            "keyword": "",
            "cms_resources": "true",
        }
        data = self._get(f"{API_BASE}/efsah_count", params)
        root = data.get("root", [])
        return root[0]["count"] if root else 0

    def download_resource(self, r_path: str) -> bytes:
        r = self._session.get(DOCS_BASE + quote(r_path), timeout=self._timeout)
        r.raise_for_status()
        return r.content
