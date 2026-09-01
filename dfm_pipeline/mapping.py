"""
ISIN lookup for DFM. Unlike ADX, no dedicated DFM issuers API with ISIN was
found during reconnaissance — DFM's disclosure records give issuer_symbol
(ticker) and issuer name, but no ISIN. Falls back to the general
company_mapping_full_with_gics_no_id.csv (43 DFM rows at the time this was
written). Coverage gaps are expected per new_stock_exchange_guidelines.md —
primary_symbol is always populated regardless of whether ISIN resolves.
"""
from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_COMPANY_MAPPING_CSV = ROOT / "company_mapping_full_with_gics_no_id.csv"


def load_isin_by_symbol() -> dict[str, str]:
    lookup: dict[str, str] = {}
    with open(_COMPANY_MAPPING_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("exchange") == "DFM" and row.get("isin"):
                lookup[row["ticker"]] = row["isin"]
    return lookup
