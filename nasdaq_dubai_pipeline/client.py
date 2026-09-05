"""
Thin client for Nasdaq Dubai's public disclosures APIs.

Unlike ADX, none of this sits behind Cloudflare or any other bot protection —
plain `requests`/curl works fine. We still use curl_cffi for consistency with
the ADX client and because it's already a dependency, but impersonation isn't
required here.

Three hosts, three purposes:
  - api.nasdaqdubai.com/candi/v1      listing + count (id, headline, issuer, date)
  - feeds.nasdaqdubai.com/apps/sso    per-document detail (body HTML, resources[], ISIN)
                                        and the issuer directory
"""
from __future__ import annotations

import json
import time

from curl_cffi import requests as curl_requests

CANDI_BASE = "https://api.nasdaqdubai.com/candi/v1"
FEEDS_BASE = "https://feeds.nasdaqdubai.com/apps/sso"


def _parse_json(text: str) -> dict | None:
    # responses are served with a leading UTF-8 BOM
    text = text.lstrip("﻿")
    if not text.strip():
        return None
    return json.loads(text)


class NasdaqDubaiClient:
    def __init__(self, timeout: int = 30, max_retries: int = 3) -> None:
        self._timeout = timeout
        self._max_retries = max_retries
        self._session = curl_requests.Session()

    def _get(self, url: str, params: dict | None = None) -> dict:
        # feeds.nasdaqdubai.com occasionally returns a 200 with an empty
        # body under rapid sequential calls (observed during pipeline
        # build/testing) — not documented rate limiting, but retrying
        # clears it every time seen so far.
        delay = 1.5
        for attempt in range(self._max_retries):
            r = self._session.get(url, params=params, impersonate="chrome", timeout=self._timeout)
            r.raise_for_status()
            data = _parse_json(r.text)
            if data is not None:
                return data
            time.sleep(delay)
            delay = min(delay * 1.5, 8.0)
        raise RuntimeError(f"Nasdaq Dubai API returned empty body after {self._max_retries} retries: {url}")

    def fetch_listing(self, skip: int = 0, take: int = 200) -> list[dict]:
        """id, headline, issuer, publication_date — newest first."""
        data = self._get(f"{CANDI_BASE}/candi", params={"skip": skip, "take": take})
        return data.get("root", []) or []

    def fetch_count(self) -> int:
        data = self._get(f"{CANDI_BASE}/candi_count")
        root = data.get("root", [])
        return root[0]["count"] if root else 0

    def fetch_detail(self, doc_id: str) -> dict | None:
        """Full record: headline, issuer, isin, seq_no, body (HTML), resources[],
        resources_base_url. `resources` is empty when the disclosure is
        machine-readable RNS-style HTML (use `body`); populated when a PDF
        (or other file) was attached instead."""
        data = self._get(f"{FEEDS_BASE}/source/detail", params={"id": doc_id})
        root = data.get("root", [])
        return root[0] if root else None

    def fetch_issuers(self) -> list[dict]:
        """id, name, status ('Enabled'/'Disabled') — no ISIN here; ISIN comes
        from individual disclosure detail records instead."""
        data = self._get(f"{FEEDS_BASE}/source/issuers2")
        return data.get("root", []) or []

    def download_resource(self, resources_base_url: str, r_path: str) -> bytes:
        r = self._session.get(f"{resources_base_url}{r_path}", impersonate="chrome", timeout=self._timeout)
        r.raise_for_status()
        return r.content
