"""Loads adx_filing_type_mapping.csv and the ADX issuers directory CSV."""
from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_FILING_TYPE_CSV = ROOT / "adx_filing_type_mapping.csv"
_ISSUERS_CSV = ROOT / "adx_issuers_directory.csv"


def load_filing_type_mapping() -> dict[tuple[str, str], str]:
    """(subCategoryId, subCategoryNameEn) -> canonical_doc_type"""
    mapping: dict[tuple[str, str], str] = {}
    with open(_FILING_TYPE_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = (row["adx_subcategory_id"], row["adx_subcategory_name"])
            mapping[key] = row["canonical_doc_type"]
    return mapping


def classify(subcategory_id: str, subcategory_name: str) -> str:
    mapping = load_filing_type_mapping()
    canon = mapping.get((subcategory_id, subcategory_name))
    if canon is None:
        # Unseen subcategory ADX hasn't used in the last 2 years — do not
        # guess silently, fall back to the safe default and let the caller
        # surface it for a human to add to adx_filing_type_mapping.csv.
        return "other"
    return canon


def load_issuer_directory() -> dict[str, dict]:
    """symbol -> issuer row (isin, sector, instrument_type, status, ...)"""
    issuers: dict[str, dict] = {}
    with open(_ISSUERS_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            issuers[row["symbol"]] = row
    return issuers
