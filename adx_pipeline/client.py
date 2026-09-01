"""
Thin client for ADX's (Abu Dhabi Securities Exchange) public API gateway.

Both adx.ae (the site) and apigateway.adx.ae (this API) sit behind Cloudflare.
Plain `requests`/curl gets a 403 challenge page regardless of headers — a
TLS-fingerprint-spoofing client is required. curl_cffi with
impersonate="chrome" clears it with no further challenge (no CAPTCHA, no JS
challenge). No rate limiting observed across repeated calls.

The API key below is not a secret: it is shipped in ADX's own public
Next.js bundle and used by every visitor's browser to load
adx.ae/en/issuers/issuers-information/listed-companies-disclosures.
"""
from __future__ import annotations

import re
from typing import Any

from curl_cffi import requests as curl_requests

BASE_URL = "https://apigateway.adx.ae"
API_KEY = "1863a94c-582b-46f9-b4f0-0d02c0cc5307"

HEADERS = {
    "adx-Gateway-APIKey": API_KEY,
    "Channel-ID": "OSS WEB",
    "Content-Type": "application/json",
    "Accept": "application/json",
}

_CONTENT_ID_RE = re.compile(r"/download/(\d+)")


def extract_content_id(download_url: str) -> str:
    """Pull the numeric document id out of an ADX download URL.

    e.g. https://apigateway.adx.ae/adx/cdn/1.0/content/download/5362281 -> "5362281"
    This is ADX's own stable per-document id — used as our de-dupe key.
    """
    m = _CONTENT_ID_RE.search(download_url)
    if not m:
        raise ValueError(f"could not extract content id from URL: {download_url}")
    return m.group(1)


class ADXClient:
    def __init__(self, timeout: int = 30) -> None:
        self._timeout = timeout
        self._session = curl_requests.Session()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        r = self._session.get(
            f"{BASE_URL}{path}",
            params=params,
            headers=HEADERS,
            impersonate="chrome",
            timeout=self._timeout,
        )
        r.raise_for_status()
        return r.json()

    def fetch_disclosures(self, record_count: int = 200) -> list[dict]:
        """Latest `record_count` disclosures, newest first. No date-range
        filter is accepted by this endpoint (extra query params 400).
        categoryName=cd = "corporate disclosures" — the only feed observed
        to return categoryId "4" (Disclosures) records.
        """
        data = self._get(
            "/adx/tradings/1.1/news",
            params={"categoryName": "cd", "categoryValue": "", "recordCount": record_count},
        )
        return data.get("response", {}).get("news", []) or []

    def fetch_issuers(self) -> list[dict]:
        """Full official issuers directory (equities, debt, funds, etc.)."""
        data = self._get("/adx/tradings/1.1/issuers")
        return data.get("response", {}).get("issuers", []) or []

    def download_pdf(self, url: str) -> bytes:
        r = self._session.get(url, impersonate="chrome", timeout=self._timeout)
        r.raise_for_status()
        return r.content
