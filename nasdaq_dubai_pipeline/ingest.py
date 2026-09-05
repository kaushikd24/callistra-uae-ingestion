"""
Nasdaq Dubai disclosures ingestion worker.

Two content shapes per disclosure (see sql/nasdaq_dubai_documents.sql):
  - HTML body (RNS-style, mainly UK PLC issuers like Hikma) -> machine-readable,
    we generate the .mmd ourselves via html_to_mmd.py. ingestion_status goes
    straight to 'ocr_completed'; the raw HTML is *also* uploaded separately
    (new_stock_exchange_guidelines.md: frontend needs to render the original
    HTML, not just the embeddings markdown).
  - PDF resource -> normal OCR-runner path, same as ADX: ingestion_status
    stays 'ingested'.

translation_required=False throughout (UAE exchanges are English-language
business environments; user-confirmed policy, same as ADX).

No `primary_symbol` — Nasdaq Dubai's API never exposes a ticker anywhere
(listing, detail, or issuer directory), only issuer *names*. company_name is
populated; primary_symbol/symbols are left null/empty rather than guessed.

Usage:
    python -m nasdaq_dubai_pipeline.ingest --once --dry-run
    python -m nasdaq_dubai_pipeline.ingest --once
    python -m nasdaq_dubai_pipeline.ingest
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

from nasdaq_dubai_pipeline.client import NasdaqDubaiClient
from nasdaq_dubai_pipeline.equity_mapping import load_isin_to_eq_id, resolve as resolve_eq_id
from nasdaq_dubai_pipeline.html_to_mmd import html_body_to_mmd
from nasdaq_dubai_pipeline.rules import classify, is_exchange_or_regulator

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("nasdaq_dubai_ingest")

S3_BUCKET = "callistra-uae-documents"
SOURCE_TABLE = "nasdaq_dubai_documents"
SOURCE_SYSTEM = "nasdaq_dubai"
POLL_INTERVAL_SECONDS = 3600

# Nasdaq Dubai's venue is unambiguous even though it never gives us a ticker;
# ISO MIC retained from its pre-rebrand name, Dubai International Financial
# Exchange. callistra_eq_id resolution can't use the generic ticker+MIC
# trigger here (no ticker exists) — see equity_mapping.py for the
# ISIN-based, source-authoritative alternative.
PRIMARY_MIC = "DIFX"

# Same baseline as ADX — Monday of the week this pipeline went live. Not
# recomputed on every run; see new_stock_exchange_guidelines.md BACKFILL GUIDELINES.
CUTOFF_DATE = datetime(2026, 8, 31, tzinfo=timezone.utc)

# Body length below which we treat `body` as "no real content" (near-empty
# placeholder seen on PDF-attachment records, e.g. len ~93).
_MIN_MEANINGFUL_BODY_LEN = 200


def _parse_nd_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw.strip(), "%b %d, %Y %I:%M:%S %p").replace(tzinfo=timezone.utc)
    except ValueError:
        log.warning("could not parse Nasdaq Dubai date %r", raw)
        return None


def _slugify(text: str, max_len: int = 80) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", text or "").strip("-").lower()
    return slug[:max_len] or "document"


def _already_ingested(db, nd_id: str) -> bool:
    row = db.query_one(
        "SELECT 1 FROM nasdaq_dubai_documents WHERE nd_id = %s", (nd_id,)
    )
    return row is not None


def _s3_upload(content: bytes, key: str, content_type: str) -> str:
    s3 = boto3.client("s3")
    s3.upload_fileobj(io.BytesIO(content), S3_BUCKET, key, ExtraArgs={"ContentType": content_type})
    return f"s3://{S3_BUCKET}/{key}"


def _prepare_html_document(client: NasdaqDubaiClient, detail: dict, symbol_slug: str) -> tuple[str, str]:
    """Returns (raw_html_blob_path, mmd_blob_path)."""
    html = detail.get("body") or ""
    mmd = html_body_to_mmd(html, title=detail.get("headline"))
    slug = _slugify(detail.get("headline") or symbol_slug)
    doc_uuid = uuid.uuid4()
    html_key = f"nasdaq_dubai/{symbol_slug}/{slug}-{doc_uuid}.html"
    mmd_key = f"nasdaq_dubai/{symbol_slug}/{slug}-{doc_uuid}.mmd"
    raw_html_path = _s3_upload(html.encode("utf-8"), html_key, "text/html")
    mmd_path = _s3_upload(mmd.encode("utf-8"), mmd_key, "text/markdown")
    return raw_html_path, mmd_path


def _prepare_pdf_document(client: NasdaqDubaiClient, detail: dict, resource: dict, symbol_slug: str) -> str:
    content = client.download_resource(detail["resources_base_url"], resource["r_path"])
    slug = _slugify(resource.get("description") or detail.get("headline") or symbol_slug)
    key = f"nasdaq_dubai/{symbol_slug}/{slug}-{uuid.uuid4()}.pdf"
    return _s3_upload(content, key, "application/pdf")


def _build_rows(item: dict, detail: dict, blob_path: str, content_kind: str,
                 raw_html_path: str | None, resource: dict | None,
                 canonical_doc_type: str, isin_map: dict[str, str]) -> tuple[dict, dict]:
    sidecar_id = uuid.uuid4()
    issuer = detail.get("issuer") or item.get("issuer")
    published_at = _parse_nd_date(detail.get("publication_date") or item.get("publication_date"))
    entity_type = "government" if is_exchange_or_regulator(issuer) else "company"
    isin = (detail.get("isin") or "").strip() or None
    eq_id = resolve_eq_id(isin, isin_map)
    if isin and eq_id is None:
        log.warning("no eq_id mapping for isin=%r (issuer=%s) — onboarding candidate", isin, issuer)

    sidecar_row = {
        "id": sidecar_id,
        "nd_id": item["id"],
        "issuer": issuer,
        "isin": isin,
        "callistra_eq_id": eq_id,
        "headline": detail.get("headline") or item.get("headline"),
        "seq_no": detail.get("seq_no"),
        "publication_date": published_at,
        "content_kind": content_kind,
        "raw_html_path": raw_html_path,
        "resource_type": (resource or {}).get("type"),
        "resource_category": (resource or {}).get("category"),
        "resource_description": (resource or {}).get("description"),
        "resource_r_path": (resource or {}).get("r_path"),
        "raw_response": detail,
    }

    # machine-readable HTML -> straight to ocr_completed, per new_ingestion_guidelines.md
    ingestion_status = "ocr_completed" if content_kind == "html" else "ingested"
    ocr_path = blob_path if content_kind == "html" else None
    ocr_completed_at = datetime.now(timezone.utc) if content_kind == "html" else None

    documents_row = {
        "source_table": SOURCE_TABLE,
        "source_row_id": sidecar_id,
        "source_system": SOURCE_SYSTEM,
        "source_type": "regulatory_filing",
        "doc_name": detail.get("headline") or item.get("headline") or issuer,
        "blob_path": blob_path,
        "ocr_path": ocr_path,
        "ocr_completed_at": ocr_completed_at,
        "ingestion_status": ingestion_status,
        "canonical_doc_type": canonical_doc_type,
        "entity_type": entity_type,
        "published_at": published_at,
        "title": detail.get("headline") or item.get("headline"),
        "company_name": issuer,
        "primary_symbol": None,  # Nasdaq Dubai's API never exposes a ticker
        "symbols": [],
        "exchange": "NASDAQ_DUBAI",
        "primary_mic": PRIMARY_MIC,
        "callistra_eq_id": eq_id,  # source-authoritative; see equity_mapping.py
        "country": "United Arab Emirates",
        "country_code": "AE",
        "translation_required": False,
        "raw_category": None,
        "raw_subcategory": (resource or {}).get("type"),
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
                    doc_name, blob_path, ocr_path, ocr_completed_at, ingestion_status, canonical_doc_type,
                    entity_type, published_at, title, company_name,
                    primary_symbol, symbols, exchange, primary_mic, callistra_eq_id, country, country_code,
                    translation_required, raw_category, raw_subcategory
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s
                ) RETURNING id, callistra_eq_id
                """,
                (
                    documents_row["source_table"], documents_row["source_row_id"],
                    documents_row["source_system"], documents_row["source_type"],
                    documents_row["doc_name"], documents_row["blob_path"], documents_row["ocr_path"],
                    documents_row["ocr_completed_at"],
                    documents_row["ingestion_status"], documents_row["canonical_doc_type"],
                    documents_row["entity_type"], documents_row["published_at"],
                    documents_row["title"], documents_row["company_name"],
                    documents_row["primary_symbol"], documents_row["symbols"],
                    documents_row["exchange"], documents_row["primary_mic"], documents_row["callistra_eq_id"],
                    documents_row["country"],
                    documents_row["country_code"], documents_row["translation_required"],
                    documents_row["raw_category"], documents_row["raw_subcategory"],
                ),
            )
            document_id, resolved_eq_id = cur.fetchone()

            cur.execute(
                """
                INSERT INTO nasdaq_dubai_documents (
                    id, document_id, nd_id, issuer, isin, callistra_eq_id, headline, seq_no,
                    publication_date, content_kind, raw_html_path, resource_type,
                    resource_category, resource_description, resource_r_path, raw_response
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s
                )
                """,
                (
                    sidecar_row["id"], document_id, sidecar_row["nd_id"], sidecar_row["issuer"],
                    sidecar_row["isin"], resolved_eq_id, sidecar_row["headline"], sidecar_row["seq_no"],
                    sidecar_row["publication_date"], sidecar_row["content_kind"],
                    sidecar_row["raw_html_path"], sidecar_row["resource_type"],
                    sidecar_row["resource_category"], sidecar_row["resource_description"],
                    sidecar_row["resource_r_path"], json.dumps(sidecar_row["raw_response"]),
                ),
            )
        finally:
            cur.close()


def run_once(take: int, dry_run: bool) -> int:
    client = NasdaqDubaiClient()
    isin_map = load_isin_to_eq_id()

    db = None
    if not dry_run:
        from analytics_db.db import get_analytics_db

        db = get_analytics_db()
        if not db.is_configured():
            raise RuntimeError(
                "analytics_db is not configured. Run with --dry-run to test without DB writes."
            )

    items = client.fetch_listing(skip=0, take=take)
    log.info("fetched %d disclosures from Nasdaq Dubai", len(items))

    written = 0
    for item in items:
        published_at = _parse_nd_date(item.get("publication_date"))
        if published_at and published_at < CUTOFF_DATE:
            continue

        nd_id = item["id"]
        if not dry_run and _already_ingested(db, nd_id):
            continue

        detail = client.fetch_detail(nd_id)
        if detail is None:
            log.warning("no detail record for id=%s, skipping", nd_id)
            continue

        issuer = detail.get("issuer") or item.get("issuer") or "unknown"
        symbol_slug = _slugify(issuer)
        resources = detail.get("resources") or []
        body = detail.get("body") or ""

        try:
            if resources:
                if len(resources) > 1:
                    log.info("id=%s has %d resources, using the first", nd_id, len(resources))
                resource = resources[0]
                blob_path = _prepare_pdf_document(client, detail, resource, symbol_slug)
                content_kind, raw_html_path = "pdf", None
            elif len(body) >= _MIN_MEANINGFUL_BODY_LEN:
                raw_html_path, blob_path = _prepare_html_document(client, detail, symbol_slug)
                content_kind, resource = "html", None
            else:
                log.warning("id=%s (%s) has neither a resource nor a meaningful body, skipping", nd_id, issuer)
                continue
        except Exception:
            log.exception("failed to prepare/upload content for id=%s (%s)", nd_id, issuer)
            continue

        canonical_doc_type = classify(
            detail.get("headline") or item.get("headline") or "",
            issuer,
            (resources[0].get("type") if resources else None),
        )

        sidecar_row, documents_row = _build_rows(
            item, detail, blob_path, content_kind, raw_html_path,
            resources[0] if resources else None, canonical_doc_type, isin_map,
        )

        if dry_run:
            log.info(
                "[dry-run] would insert: issuer=%s kind=%s type=%s title=%s blob=%s",
                issuer, content_kind, canonical_doc_type, documents_row["doc_name"], blob_path,
            )
        else:
            _persist(db, sidecar_row, documents_row)
            log.info("ingested: issuer=%s kind=%s type=%s nd_id=%s", issuer, content_kind, canonical_doc_type, nd_id)

        written += 1

    log.info("cycle complete: %d new documents", written)
    return written


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--take", type=int, default=100, help="how many latest disclosures to pull per cycle")
    args = parser.parse_args()

    if args.once:
        run_once(take=args.take, dry_run=args.dry_run)
        return

    while True:
        try:
            run_once(take=args.take, dry_run=args.dry_run)
        except Exception:
            log.exception("poll cycle failed")
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
