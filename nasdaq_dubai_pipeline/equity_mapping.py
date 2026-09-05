"""
ISIN -> callistra_eq_id mapping for Nasdaq Dubai.

Nasdaq Dubai's API exposes no ticker anywhere (see CLAUDE.md's "No
primary_symbol" note), so the standard ticker+MIC resolver
(callistra_equity.resolve_equity_v6) cannot apply here — it returns
'missing_ticker' immediately whenever primary_symbol is NULL. Resolution
instead happens source-side, via ISIN, using a small curated mapping built
through the ISIN -> FIGI -> eq_id bridge (OpenFIGI's public mapping API,
then a callistra_equity.entities lookup by share_class_figi), per
internal_equity_id_usage_guidelines.md section 6.1.

Every row in nasdaq_dubai_isin_eq_id_mapping.csv was reviewed and confirmed
by a human before being added — never append to it automatically from
within the ingestion worker. An ISIN with no entry is a genuine onboarding
candidate, not a bug: ingest.py logs it and leaves callistra_eq_id NULL
(fail closed) rather than guessing.
"""
from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_MAPPING_CSV = ROOT / "nasdaq_dubai_isin_eq_id_mapping.csv"


def load_isin_to_eq_id() -> dict[str, str]:
    mapping: dict[str, str] = {}
    with open(_MAPPING_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            mapping[row["isin"].strip()] = row["eq_id"].strip()
    return mapping


def resolve(isin: str | None, mapping: dict[str, str]) -> str | None:
    """A raw `isin` field can hold multiple slash-separated ISINs (seen for
    bond issuers with several tranches) — check each individually."""
    if not isin:
        return None
    for candidate in isin.split("/"):
        candidate = candidate.strip()
        if candidate in mapping:
            return mapping[candidate]
    return None
