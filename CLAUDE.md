# Callistra UAE Ingestion

Ingests stock-exchange disclosures for UAE-listed companies into Callistra.
Scope, in order: **ADX** → **Nasdaq Dubai** → **DFM** — all three built, tested
end-to-end, and **deployed live** (see "Deployment" below). No cross-exchange
de-dupe (see "Dual/triple listings" below) — each exchange's pipeline ingests
independently.

Read `new_ingestion_guidelines.md` and `new_stock_exchange_guidelines.md` first —
they're the project-wide contract this repo implements. Also read
`internal_equity_id_usage_guidelines.md` before touching anything equity-ID
related (see "Equity-ID resolution" below) — it governs `callistra_eq_id`,
which the guideline itself describes as the sole source of truth for entity
identity across the whole product. This file is UAE/repo-specific notes only.

## Equity-ID resolution (`callistra_eq_id`)

`callistra_equity.*` (schema, tables, `resolve_equity_v6()` function, and the
`documents_resolve_equity_v6` BEFORE INSERT/UPDATE trigger) is shared
platform infrastructure, already live in production — this repo does not own
or build it, only feeds it correctly. `equity_entities_v6.csv` /
`equity_listings_v6.csv` (root) are the versioned master-data snapshot; the
live `callistra_equity.listings`/`entities` tables are the actual runtime
source the trigger queries and can drift ahead of the CSV snapshot (as they
did here — always check Postgres directly, not just the CSV, before
concluding something is missing).

Being tackled **strictly one exchange at a time**, per the guideline's
explicit mandate (never parallelize this across exchanges):

- **ADX — done.** Registered `ADX → XADS` in `callistra_equity.exchange_mic_aliases`
  (generic alias — safe, "ADX" has no other exchange meaning). Cross-checked
  all 92 official ADX equities against v6: 87/92 already mapped; 4 of the 5
  gaps were delisted rights-issue instruments (not currently ingested, low
  priority); the 5th, `GFH` (GFH Bank B.S.C.), was a real gap — it already
  existed as entity `EQ0021000` (listed on XKUW/XBAH/XDFM) just missing the
  ADX listing, so a listing row was added rather than minting a new ID (added
  to both live Postgres and `equity_listings_v6.csv` to keep the versioned
  artifact in sync). `adx_pipeline/ingest.py` now writes `primary_mic='XADS'`
  source-authoritatively (ADX's venue is never ambiguous) and threads the
  trigger-resolved `callistra_eq_id` back into `adx_documents.callistra_eq_id`
  (a denormalized copy of the documents-table source of truth, added per the
  guideline's sidecar-update instruction). Backfilled all existing production
  rows: 35/58 resolved to a real `eq_id`; the other 23 are genuine fund/ETF
  tickers (ADX instrument_type='Fund') correctly out of equity scope — v6 has
  no concept of them and shouldn't. Zero sidecar/documents mismatches verified.
- **Nasdaq Dubai — done.** No ticker exists anywhere in the API, so
  `resolve_equity_v6()` can never apply (it short-circuits to `missing_ticker`
  the instant `primary_symbol` is null, before even looking at MIC/exchange).
  Built a fully source-authoritative alternative instead:
  `nasdaq_dubai_pipeline/equity_mapping.py` loads a small curated
  `nasdaq_dubai_isin_eq_id_mapping.csv` (ISIN → `eq_id`), and `ingest.py`
  writes `callistra_eq_id` directly at insert time — the trigger's
  `IF NEW.callistra_eq_id IS NOT NULL THEN ... RETURN NEW` short-circuit means
  a source-supplied ID is never second-guessed. Every mapping entry was
  confirmed via the exact bridge the guideline recommends for this case
  (Section 6.1): OpenFIGI's public ISIN→FIGI mapping API, then checked
  against `callistra_equity.entities.share_class_figi`.
  - `Depa PLC` (ISIN AEDFXA0NFP81 → FIGI BBG001T264B4) and `ENBD REIT (CEIC)
    PLC` (AEDFXA1CN004 → BBG00G59X1P3): confirmed absent from v6 by FIGI, not
    just missing a listing — minted new entities `EQ0058904`/`EQ0058905` and
    added `DIFX` listings (Postgres + `equity_listings_v6.csv`).
  - `Hikma Pharmaceuticals PLC`'s Nasdaq Dubai listing resolves to a GDR
    (FIGI `BBG001SQ1J24`) — genuinely different from the existing ordinary-
    share entity's FIGI (`BBG001SNK879`), which is exactly the ADR/GDR case
    Section 6.5 says never to auto-merge by name. Surfaced to the user
    explicitly; **user directed linking it to the existing `EQ0024367`**
    rather than minting a separate GDR entity — recorded in the mapping CSV's
    `notes` column so the reasoning isn't lost.
  - Bond/sukuk SPV issuers (`Arada Sukuk Limited`, `Bank of China (Dubai)
    Branch`) and the exchange's own circulars correctly stay unresolved —
    not equities, logged as `WARNING ... onboarding candidate` rather than
    guessed at.
  - MIC used: `DIFX` (Nasdaq Dubai's ISO 10383 code retained from its
    pre-rebrand name, Dubai International Financial Exchange) — flagged to
    the user as general reference knowledge, not verified against any of our
    own data sources, since nothing in v6 or Postgres referenced it before
    this work.
  - Found and fixed a real, previously-undocumented reliability bug while
    testing this: `feeds.nasdaqdubai.com`'s detail endpoint occasionally
    returns `200` with an empty body under rapid sequential calls, which
    previously crashed the whole poll cycle uncaught. `client.py` now
    retries with backoff, same pattern already used in `dfm_pipeline`.
  - Backfilled all 9 existing production documents (5 resolved, 4 correctly
    left unresolved). Zero sidecar/documents mismatches.
- **DFM — done.** Unlike Nasdaq Dubai, DFM's API gives a real ticker
  (`issuer_symbol`), so the standard ticker+MIC path applies directly — no
  curated mapping CSV needed here. Registered `DFM → XDFM` in
  `exchange_mic_aliases` (generic, safe — mirrors ADX) and `dfm_pipeline/ingest.py`
  now writes `primary_mic='XDFM'` source-authoritatively, threading the
  trigger-resolved `callistra_eq_id` into `dfm_documents.callistra_eq_id`
  (denormalized copy, `sql/dfm_documents.sql` updated). Checked all 12 unique
  tickers seen in production against live Postgres `XDFM` listings:
  - 9/12 (`DTC`, `EMPOWER`, `MAZAYA`, `MKHZN`, `NIND`, `ORIENT`, `SUKOON`,
    `TAALEEM`, `TALABAT`) already had an exact `(XDFM, ticker)` listing —
    resolved with zero new data needed. `MAZAYA`/`MKHZN`/`NIND` are genuine
    dual listings with Kuwait (KSE) under the same `eq_id`, correctly
    disambiguated by MIC alone, same pattern as ADX's `ORAS`/Nasdaq Dubai case.
  - `CHAE`/`CHAESHIN` (Lunate S&P UAE ETFs) have zero footprint in v6 —
    correctly out of equity scope, same as ADX's fund tickers; left unresolved.
  - `ETIHADENERGY` (Etihad Energy Holding PJSC) was a real gap: no `XDFM`
    listing existed for that ticker, but entity `EQ0024649` already existed
    under ticker `GULFNAV` ("Gulf Navigation Holding PJSC") with
    `equity_entities_v6.csv`'s `legal_name` already updated to
    `ETIHAD ENERGY HOLDING PJSC` (exact match) and an unchanged
    `share_class_figi` — a company rename/ticker-change, not a new entity.
    Couldn't independently corroborate via the ISIN→FIGI bridge (DFM's API
    gives no ISIN, and OpenFIGI ticker lookups under Dubai exchange codes
    returned nothing) — surfaced to the user explicitly; **user directed
    adding the listing** rather than leaving it unresolved. Added an
    `(XDFM, ETIHADENERGY)` listing for `EQ0024649` (Postgres +
    `equity_listings_v6.csv`), keeping the old `GULFNAV` listing intact per
    rule 2 (never overwrite raw source data).
  - Backfilled all 19 existing production documents: 17/19 resolved
    (including both pre-existing `ETIHADENERGY` rows, which picked up
    `EQ0024649` once `primary_mic` was backfilled — the trigger treats a
    changed `primary_mic` on UPDATE as explicitly supplied and re-resolves),
    2 correctly left unresolved (the ETFs). Zero sidecar/documents mismatches.

## Root files

- `adx_issuers_directory.csv` — official ADX issuer list (203 rows: symbol, ISIN,
  name EN/AR, sector, instrument type, status). Source of truth for ISIN lookups.
  Rebuild via `ADXClient().fetch_issuers()`.
- `adx_filing_type_mapping.csv` — ADX `subCategoryNameEn` → Callistra `canonical_doc_type`,
  covering all 43 subcategories observed across ADX's full available history
  (2024-07-16 to present, ~9.9k documents). Three low-confidence entries are flagged
  in the `notes` column (Sustainability Report, FACT Sheet, one 1-off "for listing" row).
- `company_mapping_full_with_gics_no_id.csv` — the general cross-exchange company
  mapping. Has decent ADX coverage but is **not** exchange-authoritative; use
  `adx_issuers_directory.csv` for ADX-specific lookups instead.

## ADX pipeline (`adx_pipeline/`)

- `client.py` — `ADXClient`, wraps `apigateway.adx.ae`. **Must use `curl_cffi` with
  `impersonate="chrome"`** — both `adx.ae` and `apigateway.adx.ae` sit behind
  Cloudflare; plain `requests`/curl gets a 403 challenge page regardless of headers.
  No rate limiting observed. The `adx-Gateway-APIKey` header value is not a secret —
  it's shipped in ADX's own public JS bundle.
  - `fetch_disclosures(record_count)` → `GET /adx/tradings/1.1/news?categoryName=cd`.
    No date-range params accepted (400s on anything extra) — always returns the
    latest N, newest first. `recordCount=10000` reaches back to the full available
    history (~2 years at time of writing).
  - `fetch_issuers()` → `GET /adx/tradings/1.1/issuers`, backs the CSV above.
  - PDF downloads (`urlEn`/`urlAr` from a disclosure record) need **no auth at all**,
    but do still need curl_cffi (same Cloudflare gate).
- `mapping.py` — loads the two root CSVs; `classify(subcategory_id, subcategory_name)`
  falls back to `"other"` for anything ADX introduces that isn't in the mapping CSV yet
  (rather than guessing) — if you see unexpected `other` classifications, check whether
  ADX has added a new subcategory and extend `adx_filing_type_mapping.csv`.
- `ingest.py` — the worker. `python -m adx_pipeline.ingest --once --dry-run` to test
  fetch/classify/S3 without DB writes; drop `--dry-run` for real writes; drop `--once`
  for the long-running hourly poll loop.

### De-dupe

Key is `adx_content_id` — the numeric id embedded in ADX's own download URL
(`.../content/download/5362281` → `"5362281"`), stable across re-polls. Enforced by
a `UNIQUE` constraint on `adx_documents.adx_content_id`.

### CUTOFF_DATE

Hardcoded in `ingest.py` to the Monday of the week this pipeline first went live
(2026-08-31). Per the backfill guidelines, this is intentionally **not** recomputed
on every run — it exists so the live poller doesn't silently backfill ADX's full
history on first run. Do not change it unless intentionally re-baselining.

### Translation

`translation_required=False` for ADX. Verified empirically: every one of the ~9,889
disclosures in the available history has a distinct, genuinely different `urlEn` PDF
(not the same file as `urlAr`) — ADX provides real English documents for everything.

### Known DB gotcha

`analytics_db` uses pg8000, whose DB-API paramstyle is `format` (positional `%s`)
**not** `pyformat` (`%(name)s`). Named dict params silently fail — use positional
tuples. Also: `_rows_to_dicts` builds dicts via `dict(zip(columns, row))`, so two
same-named columns in a query (e.g. two unaliased `COUNT(*)`) will collide — alias them.

## Dual/triple listings across UAE exchanges

Checked ADX's 203 issuers against a 65-issuer sample of active Nasdaq Dubai filers
(by ISIN and normalized company name):

- **No exact ISIN overlaps.** Where the same institution appears on both (e.g. banks
  issuing Eurobonds on Nasdaq Dubai), it's under a completely different ISIN than
  any ADX-listed security — different instrument, different disclosures.
- **One confirmed dual listing**: Orascom Construction PLC — `ORAS` on ADX
  (ISIN `AEE01702O253`) vs a *different* ISIN on Nasdaq Dubai (`AEDFXA14NUL7`).
  Same company, two ISINs. This is why ISIN alone can't be the cross-exchange
  de-dupe key.

Decision (user-confirmed): **no cross-exchange de-dupe for now** — both exchange
pipelines ingest everything independently. ISIN and normalized company name are
populated reliably on every row so a future dedup pass has what it needs without
a backfill.

## Infra

- S3 bucket: `callistra-uae-documents` (`ap-south-1`), shared across all UAE
  exchanges under exchange-prefixed keys: `adx/{symbol}/...`, will be
  `nasdaq_dubai/{symbol}/...`, `dfm/{symbol}/...`.
- `analytics_db/db.py` and `.env` — copied from the shared infra, do not edit
  `db.py` (see `deploy_to_callistra_infra_guidelines.md` / `new_ingestion_guidelines.md`).

## Status

All three exchanges are deployed and running live on the ingestion VM as of
2026-09-01. See "Deployment" below for how, and per-exchange history:

- **ADX**: built and tested locally (real fetch → S3 upload → DB write, dedupe
  verified on re-run), then deployed. First production cycle ingested 6 more
  real documents on top of the 10 from local testing (16 total).
- **Nasdaq Dubai**: built and tested locally (3 real disclosures — 2 PDF, 1 HTML —
  dedupe + cutoff-date filtering both verified), then deployed. First production
  cycle found 0 new (everything in its latest-100 window was already ingested
  during local testing against the same prod DB).
- **DFM**: built and tested locally (7 real disclosures across two local runs,
  dedupe verified), then deployed. First production cycle found 0 new, same
  reason as Nasdaq Dubai.

## Deployment

Dokku app **`callistra-uae-ingestion`** on the shared ingestion VM
(`48.217.83.220`), outbound-only (no port, no domain). One Dockerfile, one
image, three independent Procfile process types — not a single unified
runner — because the three pipelines already had clean separate `main()`
entrypoints and independent failure modes / poll cadences worth keeping
isolated (mirrors the `callistra-news-rss` precedent of multiple named
process types in one app, rather than `callistra-regulatory-ingestion`'s
single-runner-cycles-through-sources pattern — both exist elsewhere in this
infra; picked based on what this repo's code already looked like, not a
house-wide rule):

```
adx:         python -m adx_pipeline.ingest
nasdaqdubai: python -m nasdaq_dubai_pipeline.ingest
dfm:         python -m dfm_pipeline.ingest
```

Each runs its own `while True: run_once(); sleep(3600)` loop — hourly polling,
per `new_stock_exchange_guidelines.md`. All three were scaled from the default
`count=0` (Dokku's behavior for any non-`web` process type on first deploy) to
`count=1` immediately after the first push:

```bash
ssh azureuser@48.217.83.220 "sudo dokku ps:scale callistra-uae-ingestion adx=1 nasdaqdubai=1 dfm=1"
```

Env vars pushed via `push-env.sh` (curated, not the whole shared `.env`):
`GCLOUD_ADC_B64`, `CALLISTRA_DB_INSTANCE_CONNECTION_NAME`, `CALLISTRA_DB_NAME`,
`CALLISTRA_DB_USER`, `CALLISTRA_DB_PASSWORD`, `AWS_ACCESS_KEY_ID`,
`AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION` — everything `analytics_db/db.py`
needs (Cloud SQL connector auth + DB identity) plus boto3's implicit AWS env
vars (S3 uploads; boto3 never appears in an `os.getenv` grep since it reads
these itself, easy to miss when curating the var list).

Verified live, not just "build succeeded": `docker ps` showed all three
containers `Up` (not exited/restarting) after scaling, and `dokku logs -p
<process>` showed real per-document `ingested: symbol=... type=...` lines
and a `cycle complete: N new documents` line for each — not just boot
messages. Cross-checked against the DB directly (`SELECT source_table,
COUNT(*) ... GROUP BY source_table`) to confirm the row counts matched
what each process's logs claimed.

Two remotes on this repo: `origin` (GitHub, `kaushikd24/callistra-uae-ingestion`)
and `dokku` (`dokku@48.217.83.220:callistra-uae-ingestion`) — pushing to one
never touches the other; both were pushed for this deploy.

## Nasdaq Dubai pipeline (`nasdaq_dubai_pipeline/`)

- `client.py` — `NasdaqDubaiClient`. No bot protection anywhere (unlike ADX) — plain
  `requests` works fine; curl_cffi used only for consistency with the ADX client.
  Three hosts: `api.nasdaqdubai.com/candi/v1` (listing + count, no auth), 
  `feeds.nasdaqdubai.com/apps/sso` (per-document detail + issuer directory, no auth).
- `html_to_mmd.py` — converts the detail record's `body` HTML (RNS-style, seen for
  UK PLC issuers like Hikma) into the `.mmd` embedding-markdown format. RNS HTML
  wraps the whole announcement in an outer single-cell `<table>` used purely for
  page layout — genuine data tables are nested inside it. The heuristic: a `<table>`
  is "real" if any row has 2+ cells; pure single-cell wrapper tables are unwrapped
  (not treated as tables) and recursed into. Tested against a real Hikma buyback
  disclosure with a 2,717-row nested trade-by-trade table (9.2MB HTML body) — the
  first version of this heuristic had two real bugs, both fixed and verified:
  (1) `find_parent("table")` walking the *whole* tree during recursion caused
  genuinely nested real tables to be skipped entirely; (2) markdownify escapes
  underscores as emphasis markers, so a marker like `[[TABLE_0]]` doesn't survive
  round-tripping through the text — switched to an alphanumeric-only marker.
- `rules.py` — title-based `classify(headline, issuer, resource_type)`. See its
  docstring for the full coverage breakdown (~69% direct rule match, ~7.6% bare
  issuer-name headlines with no signal, ~23.5% falls to `other`/resource-type
  fallback — user declined an offline GPT-nano pass to close that gap for now;
  revisit if it matters later). Rules built from the **entire** available Nasdaq
  Dubai corpus (18,201 disclosures, Nov 2005–present, pulled via full `skip`/`take`
  pagination of `/candi/v1/candi`) rather than a sample.
- `ingest.py` — the worker, same `--once`/`--dry-run`/`--take` CLI shape as ADX's.
  Branches on `resources[]` vs `body` per document to decide `content_kind`
  ('pdf' → normal OCR-runner path, `ingestion_status='ingested'`; 'html' → generates
  the `.mmd` itself, `ingestion_status='ocr_completed'` directly, `ocr_completed_at`
  set at ingest time). If a document has multiple `resources[]`, only the first is
  used (logged); revisit if multi-attachment filings turn out to matter.

### De-dupe

Key is `nd_id` — the `id` field from the `candi` listing, a stable UUID Nasdaq Dubai
itself assigns. Enforced by `UNIQUE` on `nasdaq_dubai_documents.nd_id`.

### No primary_symbol

Nasdaq Dubai's API never exposes a ticker anywhere (listing, detail, or the
`issuers2` directory) — only issuer *names*. `documents.primary_symbol` is left
`NULL` and `symbols` empty for this source; `company_name` (the issuer name) is
the identifier that's actually populated. Don't try to backfill a symbol by
guessing from `company_mapping_full_with_gics_no_id.csv` name-matching without
checking with the user first — that CSV has essentially no Nasdaq Dubai coverage
to begin with (see "Dual/triple listings" above).

### Exchange/regulator-issued circulars

~27% of the full corpus is issued by "Nasdaq Dubai" or "Dubai Financial Services
Authority" themselves (margin parameter circulars, CSD notices, listing rules),
not by a listed company. `rules.py`'s `is_exchange_or_regulator()` catches these
by issuer name and sets `documents.entity_type='government'` instead of `'company'`
— most of them also get caught by the `CIRCULAR NO.`/`NOTICE NO.` title rules and
classified as `regulatory_general`.

## DFM pipeline (`dfm_pipeline/`)

The simplest of the three — one API call returns everything (headline, issuer,
**issuer_symbol** — a real ticker, unlike Nasdaq Dubai — and `resources[]` with
PDF paths), and every disclosure sampled (2,500/2,500) carried at least one PDF
resource, so there's no machine-readable HTML path to handle like Nasdaq Dubai's
RNS filings. Always goes through the OCR runner, same as ADX.

- `client.py` — `DFMClient`. No bot protection (plain `requests`, no curl_cffi
  needed) but **real rate limiting**: sustained rapid calls eventually get a
  `200 OK` with an empty body (not an HTTP error), recovering after ~15s. Retry-
  with-backoff is built into `_get` rather than left to callers — don't bypass it
  by calling the raw endpoints directly. The server also silently caps `take` at
  20 regardless of what's requested (`PAGE_SIZE` constant) — pagination must
  proceed in 20-item pages or you'll think you're getting more than you are.
- `rules.py` — title-based `classify(headline, resource_type)`. DFM's headlines
  are far more templated than Nasdaq Dubai's: a 2,500-record sample (Oct 2025–
  Sep 2026, full pagination) had only 297 unique normalized headlines, and the
  rules + `resources[].type` fallback together give **100% coverage** on that
  sample (nothing fell through to `other`). No GPT-nano needed here.
- `mapping.py` — `load_isin_by_symbol()`. No dedicated DFM issuers API with ISIN
  was found (a `/mw/v1` market-watch host exists per the site's runtime config
  but its endpoint paths weren't discoverable via the same reconnaissance that
  worked for ADX/Nasdaq Dubai) — falls back to the general
  `company_mapping_full_with_gics_no_id.csv` (43 DFM rows at the time this was
  written). Expect ISIN gaps; `primary_symbol` is always populated regardless
  (verified — `issuer_symbol` is on every record).
- `ingest.py` — the worker. Unlike ADX/Nasdaq Dubai, DFM's `from`/`to` params are
  genuinely honoured server-side (verified), so this worker filters by
  `CUTOFF_DATE` via the API itself (`from_date=CUTOFF_DATE`) instead of pulling
  everything and discarding client-side — more efficient and it means polling
  naturally shrinks to just new disclosures over time. `--max-items` (not
  `--take`/`--record-count` like the other two) caps how many *new* documents a
  cycle writes, since pagination here can span many pages of already-ingested
  rows before hitting fresh ones.

### De-dupe

Key is `dfm_id` — the `id` field from the API response, a stable UUID DFM itself
assigns. Enforced by `UNIQUE` on `dfm_documents.dfm_id`.

### No exchange/regulator special-casing

Unlike Nasdaq Dubai, "DFM - Dubai Financial Market PJSC" showing up as an issuer
is **not** an exchange circular — DFM PJSC is itself listed on its own exchange
and these are that company's routine disclosures (BOD meetings, quarterly
results, earnings calls). `entity_type='company'` unconditionally for this
source; no CMA/SCA-authored regulatory circulars were observed in the 2,500-record
sample. Revisit if that turns out to be wrong once more history is ingested.
