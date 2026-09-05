"""
DFM (Dubai Financial Market) disclosures ingestion worker.

Simplest of the three UAE pipelines: one API call returns headline, issuer,
issuer_symbol, and resources[] together (no separate detail call like
Nasdaq Dubai), and every disclosure carries at least one PDF resource (no
machine-readable HTML path like Nasdaq Dubai's RNS-style filings) — so this
always goes through the OCR runner, same as ADX.

translation_required=False (UAE English-language business environment,
same policy as ADX and Nasdaq Dubai).

DFM's `from`/`to` params are genuinely honoured server-side (verified), so
unlike ADX/Nasdaq Dubai this worker filters by CUTOFF_DATE via the API
itself rather than fetching everything and discarding client-side.

Usage:
    python -m dfm_pipeline.ingest --once --dry-run
    python -m dfm_pipeline.ingest --once
    python -m dfm_pipeline.ingest
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

from dfm_pipeline.client import DFMClient
from dfm_pipeline.mapping import load_isin_by_symbol
from dfm_pipeline.rules import classify

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("dfm_ingest")

S3_BUCKET = "callistra-uae-documents"
SOURCE_TABLE = "dfm_documents"
SOURCE_SYSTEM = "dfm"
POLL_INTERVAL_SECONDS = 3600

# DFM's venue is never ambiguous — written source-authoritatively so the
# resolve_equity_v6 trigger resolves on exact (mic, ticker), same as ADX's
# XADS. Registered as a generic exchange_mic_alias too (DFM -> XDFM) for
# consistency, though the direct write already covers the resolution path.
PRIMARY_MIC = "XDFM"

# Same baseline as ADX and Nasdaq Dubai — Monday of the week this pipeline
# went live. Not recomputed on every run; see BACKFILL GUIDELINES.
CUTOFF_DATE = datetime(2026, 8, 31, tzinfo=timezone.utc)

# Safety cap on pages pulled per poll cycle, in case a huge backlog exists
# between the cutoff and now (shouldn't happen in steady-state hourly polling).
_MAX_PAGES_PER_CYCLE = 50


def _parse_dfm_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw.strip(), "%b %d, %Y %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        log.warning("could not parse DFM date %r", raw)
        return None


def _slugify(text: str, max_len: int = 80) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", text or "").strip("-").lower()
    return slug[:max_len] or "document"


def _already_ingested(db, dfm_id: str) -> bool:
    row = db.query_one("SELECT 1 FROM dfm_documents WHERE dfm_id = %s", (dfm_id,))
    return row is not None


def _upload_pdf(client: DFMClient, resource: dict, symbol_slug: str) -> str:
    content = client.download_resource(resource["r_path"])
    slug = _slugify(resource.get("description") or symbol_slug)
    key = f"dfm/{symbol_slug}/{slug}-{uuid.uuid4()}.pdf"
    s3 = boto3.client("s3")
    s3.upload_fileobj(io.BytesIO(content), S3_BUCKET, key, ExtraArgs={"ContentType": "application/pdf"})
    return f"s3://{S3_BUCKET}/{key}"


def _build_rows(item: dict, resource: dict, blob_path: str, isin: str | None,
                 canonical_doc_type: str) -> tuple[dict, dict]:
    sidecar_id = uuid.uuid4()
    published_at = _parse_dfm_date(item.get("publication_date"))
    symbol = item.get("issuer_symbol")

    sidecar_row = {
        "id": sidecar_id,
        "dfm_id": item["id"],
        "issuer_symbol": symbol,
        "issuer": item.get("issuer"),
        "issuer_ar": item.get("issuer_ar"),
        "isin": isin,
        "headline": item.get("headline"),
        "announcement_type": item.get("announcement_type"),
        "report_interval": item.get("report_interval"),
        "integrated_period": item.get("integrated_period"),
        "integrated_language": item.get("integrated_language"),
        "integrated_report_type": item.get("integrated_report_type"),
        "dividends_payment_date": item.get("dividends_payment_date"),
        "publication_date": published_at,
        "resource_type": resource.get("type"),
        "resource_category": resource.get("category"),
        "resource_description": resource.get("description"),
        "resource_r_path": resource.get("r_path"),
        "raw_response": item,
    }

    documents_row = {
        "source_table": SOURCE_TABLE,
        "source_row_id": sidecar_id,
        "source_system": SOURCE_SYSTEM,
        "source_type": "regulatory_filing",
        "doc_name": item.get("headline") or symbol,
        "blob_path": blob_path,
        "ingestion_status": "ingested",  # PDF -> OCR runner
        "canonical_doc_type": canonical_doc_type,
        "entity_type": "company",
        "published_at": published_at,
        "title": item.get("headline"),
        "company_name": item.get("issuer"),
        "primary_symbol": symbol,
        "symbols": [symbol] if symbol else [],
        "exchange": "DFM",
        "primary_mic": PRIMARY_MIC,
        "country": "United Arab Emirates",
        "country_code": "AE",
        "translation_required": False,
        "raw_category": item.get("announcement_type"),
        "raw_subcategory": resource.get("type"),
    }
    return sidecar_row, documents_row


def _persist(db, sidecar_row: dict, documents_row: dict) -> None:
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
                INSERT INTO dfm_documents (
                    id, document_id, dfm_id, issuer_symbol, issuer, issuer_ar, isin,
                    callistra_eq_id,
                    headline, announcement_type, report_interval, integrated_period,
                    integrated_language, integrated_report_type, dividends_payment_date,
                    publication_date, resource_type, resource_category,
                    resource_description, resource_r_path, raw_response
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s,
                    %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s
                )
                """,
                (
                    sidecar_row["id"], document_id, sidecar_row["dfm_id"],
                    sidecar_row["issuer_symbol"], sidecar_row["issuer"], sidecar_row["issuer_ar"],
                    sidecar_row["isin"],
                    resolved_eq_id,
                    sidecar_row["headline"], sidecar_row["announcement_type"],
                    sidecar_row["report_interval"], sidecar_row["integrated_period"],
                    sidecar_row["integrated_language"], sidecar_row["integrated_report_type"],
                    sidecar_row["dividends_payment_date"], sidecar_row["publication_date"],
                    sidecar_row["resource_type"], sidecar_row["resource_category"],
                    sidecar_row["resource_description"], sidecar_row["resource_r_path"],
                    json.dumps(sidecar_row["raw_response"]),
                ),
            )
        finally:
            cur.close()


def run_once(max_items: int, dry_run: bool) -> int:
    client = DFMClient()
    isin_by_symbol = load_isin_by_symbol()

    db = None
    if not dry_run:
        from analytics_db.db import get_analytics_db

        db = get_analytics_db()
        if not db.is_configured():
            raise RuntimeError(
                "analytics_db is not configured. Run with --dry-run to test without DB writes."
            )

    from_date = CUTOFF_DATE.strftime("%Y-%m-%d")
    written = 0
    skip = 0
    for _ in range(_MAX_PAGES_PER_CYCLE):
        items = client.fetch_page(skip=skip, from_date=from_date)
        if not items:
            break
        log.info("fetched %d disclosures (skip=%d)", len(items), skip)

        for item in items:
            dfm_id = item["id"]
            if not dry_run and _already_ingested(db, dfm_id):
                continue

            resources = item.get("resources") or []
            if not resources:
                log.warning("id=%s (%s) has no resources, skipping", dfm_id, item.get("issuer"))
                continue
            if len(resources) > 1:
                log.info("id=%s has %d resources, using the first", dfm_id, len(resources))
            resource = resources[0]

            symbol = item.get("issuer_symbol")
            symbol_slug = _slugify(symbol or item.get("issuer") or "unknown")

            try:
                blob_path = _upload_pdf(client, resource, symbol_slug)
            except Exception:
                log.exception("failed to download/upload PDF for id=%s (%s)", dfm_id, symbol)
                continue

            canonical_doc_type = classify(item.get("headline") or "", resource.get("type"))
            isin = isin_by_symbol.get(symbol) if symbol else None

            sidecar_row, documents_row = _build_rows(item, resource, blob_path, isin, canonical_doc_type)

            if dry_run:
                log.info(
                    "[dry-run] would insert: symbol=%s type=%s title=%s blob=%s",
                    symbol, canonical_doc_type, documents_row["doc_name"], blob_path,
                )
            else:
                _persist(db, sidecar_row, documents_row)
                log.info("ingested: symbol=%s type=%s dfm_id=%s", symbol, canonical_doc_type, dfm_id)

            written += 1
            if written >= max_items:
                log.info("cycle complete: %d new documents (max_items reached)", written)
                return written

        skip += 20
        time.sleep(0.5)  # DFM rate-limits sustained rapid calls

    log.info("cycle complete: %d new documents", written)
    return written


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-items", type=int, default=100, help="stop after this many new documents in a cycle")
    args = parser.parse_args()

    if args.once:
        run_once(max_items=args.max_items, dry_run=args.dry_run)
        return

    while True:
        try:
            run_once(max_items=args.max_items, dry_run=args.dry_run)
        except Exception:
            log.exception("poll cycle failed")
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
