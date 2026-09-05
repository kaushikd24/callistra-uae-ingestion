"""
ADX (Abu Dhabi Securities Exchange) disclosures ingestion worker.

Poll loop: fetch the latest N disclosures from ADX's public news API, skip
anything already ingested (de-duped on adx_content_id) or older than
CUTOFF_DATE, download the English PDF, upload it to S3, and write one row
each to `adx_documents` (sidecar) and `documents` (per new_ingestion_guidelines.md).

PDFs need OCR (ADX is not a machine-readable source) -> documents.ingestion_status
is set to 'ingested' and left for the OCR runner. translation_required=False:
ADX ships a genuine, distinct English PDF for every disclosure (verified
across ~2 years / ~9.9k documents in the corpus at build time).

Usage:
    python -m adx_pipeline.ingest --once --dry-run   # fetch/classify/S3 only, no DB writes
    python -m adx_pipeline.ingest --once              # single poll cycle, writes to DB
    python -m adx_pipeline.ingest                      # long-running hourly poll loop
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone

import boto3

from adx_pipeline.client import ADXClient, extract_content_id
from adx_pipeline.mapping import classify, load_issuer_directory

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("adx_ingest")

S3_BUCKET = "callistra-uae-documents"
SOURCE_TABLE = "adx_documents"
SOURCE_SYSTEM = "adx"
POLL_INTERVAL_SECONDS = 3600  # hourly, per new_stock_exchange_guidelines.md

# ADX's venue is unambiguous and permanently known, so we write it
# source-authoritatively rather than relying only on the
# callistra_equity.exchange_mic_aliases fallback ("ADX" -> "XADS"), per
# internal_equity_id_usage_guidelines.md section 3.2 (target state for
# exchange repos). callistra_eq_id itself is left to the documents trigger
# (resolve_document_equity_v6) since we have no evidence beyond ticker+MIC
# that it doesn't already use.
PRIMARY_MIC = "XADS"

# Fixed at pipeline setup time (Monday of the week this pipeline went live),
# per BACKFILL GUIDELINES in new_stock_exchange_guidelines.md — the live
# poller must not silently backfill everything published before it existed.
# Recompute only if intentionally re-baselining, not on every run.
CUTOFF_DATE = datetime(2026, 8, 31, tzinfo=timezone.utc)

_ADX_DATE_FMT = "%b %d, %Y %I:%M:%S %p"  # unused fallback; ADX uses "YYYY-MM-DD HH:MM:SS.f"


def _parse_adx_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    raw = raw.strip()
    try:
        return datetime.strptime(raw.split(".")[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        log.warning("could not parse ADX date %r", raw)
        return None


def _slugify(text: str, max_len: int = 80) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return slug[:max_len] or "document"


def _already_ingested(db, adx_content_id: str) -> bool:
    row = db.query_one(
        "SELECT 1 FROM adx_documents WHERE adx_content_id = %s", (adx_content_id,)
    )
    return row is not None


def _upload_pdf(client: ADXClient, url: str, symbol: str, title: str) -> str:
    content = client.download_pdf(url)
    key = f"adx/{symbol}/{_slugify(title)}-{uuid.uuid4()}.pdf"
    s3 = boto3.client("s3")
    s3.upload_fileobj(io.BytesIO(content), S3_BUCKET, key, ExtraArgs={"ContentType": "application/pdf"})
    return f"s3://{S3_BUCKET}/{key}"


def _build_rows(record: dict, issuer_row: dict | None, blob_path: str) -> tuple[dict, dict]:
    """Returns (sidecar_row, documents_row), both keyed by column name."""
    sidecar_id = uuid.uuid4()
    published_at = _parse_adx_date(record.get("publishedDate"))
    canonical_doc_type = classify(record["subCategoryId"], record["subCategoryNameEn"])
    symbol = record["entity"]

    sidecar_row = {
        "id": sidecar_id,
        "adx_content_id": extract_content_id(record["urlEn"]),
        "entity_symbol": symbol,
        "entity_name_en": record.get("entityNameEn"),
        "entity_name_ar": record.get("entityNameAr"),
        "isin": (issuer_row or {}).get("isin"),
        "title_en": record.get("titleEn"),
        "title_ar": record.get("titleAr"),
        "simple_title_en": record.get("simpleTitleEn"),
        "category_id": record.get("categoryId"),
        "category_name_en": record.get("categoryNameEn"),
        "subcategory_id": record.get("subCategoryId"),
        "subcategory_name_en": record.get("subCategoryNameEn"),
        "published_date": published_at,
        "event_date": _parse_adx_date(record.get("eventDate")),
        "pdf_url_en": record["urlEn"],
        "pdf_url_ar": record.get("urlAr"),
        "ex_para": record.get("exPara"),
        "raw_response": record,
    }

    documents_row = {
        "source_table": SOURCE_TABLE,
        "source_row_id": sidecar_id,
        "source_system": SOURCE_SYSTEM,
        "source_type": "regulatory_filing",
        "doc_name": record.get("titleEn") or record.get("simpleTitleEn") or symbol,
        "blob_path": blob_path,
        "ingestion_status": "ingested",  # PDF -> OCR runner picks it up
        "canonical_doc_type": canonical_doc_type,
        "entity_type": "company",
        "published_at": published_at,
        "title": record.get("titleEn"),
        "company_name": record.get("entityNameEn"),
        "primary_symbol": symbol,
        "symbols": [symbol],
        "exchange": "ADX",
        "primary_mic": PRIMARY_MIC,
        "country": "United Arab Emirates",
        "country_code": "AE",
        "translation_required": False,
        "raw_category": record.get("categoryNameEn"),
        "raw_subcategory": record.get("subCategoryNameEn"),
    }
    return sidecar_row, documents_row


def _persist(db, sidecar_row: dict, documents_row: dict) -> None:
    # pg8000 (the driver behind analytics_db) only supports the DB-API
    # "format" paramstyle (positional %s) — no %(name)s dict params.
    with db.connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO documents (
                    source_table, source_row_id, source_system, source_type,
                    doc_name, blob_path, ingestion_status, canonical_doc_type,
                    entity_type, published_at, title, company_name,
                    primary_symbol, symbols, exchange, primary_mic, country, country_code,
                    translation_required, raw_category, raw_subcategory
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s
                ) RETURNING id, callistra_eq_id
                """,
                (
                    documents_row["source_table"], documents_row["source_row_id"],
                    documents_row["source_system"], documents_row["source_type"],
                    documents_row["doc_name"], documents_row["blob_path"],
                    documents_row["ingestion_status"], documents_row["canonical_doc_type"],
                    documents_row["entity_type"], documents_row["published_at"],
                    documents_row["title"], documents_row["company_name"],
                    documents_row["primary_symbol"], documents_row["symbols"],
                    documents_row["exchange"], documents_row["primary_mic"], documents_row["country"],
                    documents_row["country_code"], documents_row["translation_required"],
                    documents_row["raw_category"], documents_row["raw_subcategory"],
                ),
            )
            document_id, resolved_eq_id = cur.fetchone()

            cur.execute(
                """
                INSERT INTO adx_documents (
                    id, document_id, adx_content_id, entity_symbol, entity_name_en,
                    entity_name_ar, isin, callistra_eq_id, title_en, title_ar, simple_title_en,
                    category_id, category_name_en, subcategory_id, subcategory_name_en,
                    published_date, event_date, pdf_url_en, pdf_url_ar, ex_para, raw_response
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    sidecar_row["id"], document_id, sidecar_row["adx_content_id"],
                    sidecar_row["entity_symbol"], sidecar_row["entity_name_en"],
                    sidecar_row["entity_name_ar"], sidecar_row["isin"], resolved_eq_id,
                    sidecar_row["title_en"], sidecar_row["title_ar"], sidecar_row["simple_title_en"],
                    sidecar_row["category_id"], sidecar_row["category_name_en"],
                    sidecar_row["subcategory_id"], sidecar_row["subcategory_name_en"],
                    sidecar_row["published_date"], sidecar_row["event_date"],
                    sidecar_row["pdf_url_en"], sidecar_row["pdf_url_ar"], sidecar_row["ex_para"],
                    json.dumps(sidecar_row["raw_response"]),
                ),
            )
        finally:
            cur.close()


def run_once(record_count: int, dry_run: bool) -> int:
    client = ADXClient()
    issuers = load_issuer_directory()

    db = None
    if not dry_run:
        from analytics_db.db import get_analytics_db

        db = get_analytics_db()
        if not db.is_configured():
            raise RuntimeError(
                "analytics_db is not configured (no CALLISTRA_DB_* env vars). "
                "Run with --dry-run to test fetch/classify/S3 without DB writes."
            )

    records = client.fetch_disclosures(record_count=record_count)
    log.info("fetched %d disclosures from ADX", len(records))

    written = 0
    for record in records:
        published_at = _parse_adx_date(record.get("publishedDate"))
        if published_at and published_at < CUTOFF_DATE:
            continue

        content_id = extract_content_id(record["urlEn"])
        if not dry_run and _already_ingested(db, content_id):
            continue

        symbol = record["entity"]
        issuer_row = issuers.get(symbol)
        if issuer_row is None:
            log.warning("symbol %r not in adx_issuers_directory.csv — ISIN will be null", symbol)

        try:
            blob_path = _upload_pdf(client, record["urlEn"], symbol, record.get("titleEn") or symbol)
        except Exception:
            log.exception("failed to download/upload PDF for content_id=%s (%s)", content_id, symbol)
            continue

        sidecar_row, documents_row = _build_rows(record, issuer_row, blob_path)

        if dry_run:
            log.info(
                "[dry-run] would insert: symbol=%s type=%s title=%s blob=%s",
                symbol, documents_row["canonical_doc_type"], documents_row["doc_name"], blob_path,
            )
        else:
            _persist(db, sidecar_row, documents_row)
            log.info("ingested: symbol=%s type=%s content_id=%s", symbol, documents_row["canonical_doc_type"], content_id)

        written += 1

    log.info("cycle complete: %d new documents", written)
    return written


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="run a single poll cycle and exit")
    parser.add_argument("--dry-run", action="store_true", help="skip DB writes; log what would happen")
    parser.add_argument("--record-count", type=int, default=200, help="how many latest disclosures to pull per cycle")
    args = parser.parse_args()

    if args.once:
        run_once(record_count=args.record_count, dry_run=args.dry_run)
        return

    while True:
        try:
            run_once(record_count=args.record_count, dry_run=args.dry_run)
        except Exception:
            log.exception("poll cycle failed")
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
