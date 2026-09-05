# Callistra Internal Equity-ID Usage Guidelines (V1)

**Status:** Initial internal draft — review and amend before treating as a
production-wide contract.

# PROJECT OVERVIEW

This is Callistra, a financial markets research platform, where we ingest stock exchange filings, financial filings, company IR material, regulatory documents, clinical trials, etc. and made the entire corpus retrievable for end users and AI Agents. The product is an enterprise SaaS, all systems are proprietary.

## 1. Purpose and scope

Callistra ingests exchange filings, regulatory filings, issuer IR material,
news, broker research, clinical-trial material, and other documents. A single
issuer can be referred to by different tickers on different venues. For
example, an issuer may be `ORSTED` in Copenhagen and `D2G` in Frankfurt.

`callistra_eq_id` is Callistra's internal, stable equity-entity identifier. It
allows documents about the same practical company/entity to be retrieved
together even when their ticker or venue differs.

This guide governs how ingestion repositories populate and use equity identity
data. It supplements:

- `new_ingestion_guidelines.md`, which defines the shared document/OCR/
  translation/embedding contract; and
- `new_stock_exchange_guidelines.md`, which defines stock-exchange ingestion
  policy.

It does not require every document to have an equity ID. Documents about a
government, regulator, clinical trial, broad market topic, or an unknown issuer
must retain a NULL `callistra_eq_id`.

## 2. Core rules

1. **MIC plus ticker is the listing key.** A ticker by itself is not a safe
   identifier. `JD`, `RM`, and similar symbols are reused across markets.
2. **Keep raw source data.** Never replace a source's exchange label or ticker
   with a normalized value. Store the raw value and the normalized identity
   separately.
3. **Exact evidence beats inference.** ISIN, share-class FIGI, an official
   issuer/listing ID, and exact `(MIC, ticker)` are preferred in that order.
4. **Fail closed.** If the identity cannot be established confidently, leave
   `callistra_eq_id` NULL and create an onboarding candidate. Never use a
   ticker-only production fallback.
5. **The source can be authoritative.** A repository that has verified the
   issuer/listing may write `callistra_eq_id` directly. The Postgres resolver
   is a safe fallback, not a replacement for source knowledge.
6. **Do not use GICS to establish identity.** GICS is classification metadata,
   not issuer proof. It must never merge two entities merely because they share
   a sector or industry.

## 3. Current shared database contract

`public.documents` remains the single integration point for OCR, translation,
embeddings, retrieval, and document-level equity identity.

### 3.1 Equity columns on `documents`

| Column | Owner at ingestion | Meaning |
|---|---|---|
| `primary_symbol` | Source | Raw primary ticker/symbol relevant to the document. |
| `symbols` | Source | Other raw symbols known to be relevant; not an instruction to map each symbol. |
| `exchange` | Source | Raw exchange/market label exactly as supplied, such as `NSE`, `Frankfurt`, or `EURONEXT`. |
| `country_code` | Source | ISO alpha-2 issuer/source country when available. It is supporting metadata, not a listing identifier. |
| `primary_mic` | Source when known; Postgres fallback otherwise | Normalized ISO 10383 MIC for the relevant listing, such as `XNSE`, `XBOM`, `XFRA`, or `XPAR`. |
| `callistra_eq_id` | Source when verified; Postgres fallback otherwise | Stable Callistra entity ID. Nullable and foreign-key constrained to `callistra_equity.entities`. |

The normal ingestion fields and lifecycle ownership in
`new_ingestion_guidelines.md` remain unchanged. In particular, ingestion code
must not write embedding-owned fields or advance ingestion status beyond its
permitted state.

### 3.2 Required behaviour by source type

| Source type | `primary_mic` | `callistra_eq_id` | Notes |
|---|---|---|---|
| Official exchange filings | Required when the venue is known | Set when source mapping is verified; otherwise allow fallback | This is the target state for all exchange repositories. |
| Financial regulator with issuer listing data | Required when derivable | Set when verified | `SEC` is a regulator, not an exchange/MIC. |
| Issuer IR | Usually absent at raw discovery | Set only from an independent issuer/listing match | Never map historical IR from ticker alone. |
| Broker/news feed | Set when the provider supplies a credible venue | Set only if provider identity is verified | Provider exchange labels often need source-scoped normalization. |
| Non-company/regulatory/clinical source | NULL | NULL | Correct and expected. |

## 4. Normalized resolution order

The live Postgres function is:

```sql
callistra_equity.resolve_equity_v6(
    p_ticker,
    p_primary_mic,
    p_exchange,
    p_source_system
)
```

The `documents` trigger uses this order:

```text
source-provided callistra_eq_id
    → source-provided primary_mic + exact ticker
    → source-system exchange alias + exact ticker
    → generic exchange alias + exact ticker
    → unresolved
```

The function returns an entity ID, resolved MIC, mapping method, and failure
reason. It deliberately does not resolve a ticker without a venue.

### 4.1 Source-authoritative writes

When a repository has high-confidence evidence, it should write both fields in
the same transaction as `documents` and its sidecar row:

```text
primary_mic       = verified MIC
callistra_eq_id   = verified existing EQ ID
```

The `documents.callistra_eq_id` foreign key rejects an unknown ID. A
source-provided non-NULL ID is preserved by the trigger.

Do not invent an ID in the ingestion repository. If no approved ID exists,
leave it NULL and submit a candidate to the master-data onboarding flow.

### 4.2 Exchange aliases

Raw labels are normalized through controlled alias tables:

```text
callistra_equity.exchange_mic_aliases
callistra_equity.exchange_mic_source_aliases
```

Use a generic alias only when the label has one global meaning. Use a
source-scoped alias when a label is overloaded.

Examples:

- `NSE` → `XNSE`; `BSE` → `XBOM`.
- `Frankfurt` → `XFRA`; `XETRA` → `XETR`.
- `TSE` must be source-scoped: Tokyo and Toronto are different venues.
- `EURONEXT` must **not** map to one global MIC. It is a venue family.

`country_code` may help choose a source-specific exchange rule, but it must not
authorize a ticker-only lookup.

## 5. Sidecar-table requirements

Each new ingestion source already requires a sidecar table linked one-to-one to
`documents`. For an equity-capable source, retain all source-native identity
evidence there. Do not overload `documents` with every provider-specific key.

Recommended fields, where the source provides them:

| Field | Purpose |
|---|---|
| `source_issuer_id` | Stable issuer/company key from the source. |
| `source_listing_id` / `instrument_id` | Stable security/listing key from the source. |
| `raw_ticker`, `raw_exchange` | Exact source values, even if they also populate `documents`. |
| `issuer_isin` / `listing_isin` | Strong linkage evidence. Record whether the ISIN is issuer or security specific. |
| `share_class_figi` | Strong security-level linkage where available. |
| `venue_mic` / `market_code` | Official venue identity; preferred over a display name. |
| `issuer_name_raw` | Audit and review support. |
| `identity_evidence` | Raw payload or compact JSON supporting the decision. |

The sidecar and `documents` row must be inserted in one database transaction,
as required by the general ingestion guidelines.

## 6. Source-specific edge cases

### 6.1 Euronext

Euronext announcements can carry the raw exchange label `EURONEXT`, while an
issuer trades on Paris, Amsterdam, Brussels, Lisbon, Dublin, Oslo, or another
venue. `EURONEXT` is not enough for `(MIC, ticker)` resolution.

For Euronext, preserve the official issuer ISIN and the actual market(s) in its
sidecar. Resolve in this order:

```text
issuer/listing ISIN → FIGI bridge → eq_id
actual Euronext market → MIC + exact symbol → eq_id
otherwise → onboarding candidate
```

Do not install `EURONEXT → XPAR` or any other global alias.

### 6.2 India

NSE and BSE are separate venues. A company may have a mnemonic NSE symbol and
a numeric BSE code. For Reliance Industries, for example:

```text
XNSE / RELIANCE
XBOM / 500325
```

Do not write an NSE mnemonic as the BSE ticker. Where the source only has an
issuer-level identity, set a verified `callistra_eq_id` directly and preserve
the raw symbol in the sidecar.

### 6.3 SEC and other regulators

`SEC` identifies the regulator, not a listing venue. Resolve through issuer
evidence such as CIK plus the issuer's reported exchange/symbol, ISIN, FIGI,
or a curated SEC universe. Keep the regulator value in `documents.regulator`
or source metadata; do not place it in `primary_mic`.

### 6.4 Company IR

IR sites often omit venue and can use a foreign or duplicate ticker. Preserve
the discovered domain, source company name, original universe row, ISIN or
profile data when available. Require a second independent signal before setting
an ID. A ticker-only match is explicitly prohibited.

### 6.5 ADRs, GDRs, share classes, and cross-listings

The current v6 practical model is sufficient for immediate document grouping,
but some issuer-versus-security splits remain. Do not automatically merge
ordinary shares, ADRs/GDRs, and different share classes based on name
similarity. Record the security relationship and escalate it through the
master-data review process.

## 7. New-source onboarding checklist

Before production deployment of an equity-capable source:

- [ ] Read both shared ingestion guidelines and this document.
- [ ] Build or obtain the source's official issuer/listing universe.
- [ ] Store official ticker, venue/MIC, company name, country, and ISIN when
      available.
- [ ] Identify whether identifiers are issuer-level or security/listing-level.
- [ ] Map source venue labels/codes to MICs; add only reviewed aliases.
- [ ] Resolve the universe against the current v6 master using the evidence
      hierarchy in this document.
- [ ] Create a checkpoint CSV/table for unresolved listings and ambiguities.
- [ ] Add a source sidecar with the identity evidence in Section 5.
- [ ] Populate raw document fields, `primary_mic`, and verified
      `callistra_eq_id` in the same transaction.
- [ ] Test with a large, document-rich company and at least one normal issuer.
- [ ] Test a cross-listing or reused-symbol edge case where relevant.
- [ ] Confirm rows with no confident mapping remain NULL rather than guessed.

## 8. v6 master-data lifecycle

The v6 CSVs are the portable master-data artifact:

```text
equity_entities_v6.csv    -- one row per current practical entity
equity_listings_v6.csv    -- ticker/venue rows that resolve to an entity
```

They must be treated as versioned master data, not as ad hoc files edited by
individual ingestion workers.

### 8.1 Immutable identity rules

- An existing `eq_id` must not be reminted during a refresh.
- A share-class FIGI must not silently move to another `eq_id`.
- An existing canonical `(MIC, ticker) → eq_id` mapping must not change or
  disappear without an explicit reviewed decision.
- One `(MIC, ticker)` must map to at most one canonical `eq_id`.
- Ambiguous pairs remain quarantined and unavailable to the automatic resolver.

### 8.2 Candidate/onboarding flow

Every new or unresolved listing should enter a durable queue/table with at
least:

```text
source_system, source_issuer_id, source_listing_id,
raw_ticker, mic, raw_exchange, isin, share_class_figi,
issuer_name, country_code, candidate_eq_ids,
evidence, decision, reviewer, decided_at, master_version
```

Resolution order:

1. Exact verified ISIN / share-class FIGI matches an existing entity.
2. Exact `(MIC, ticker)` matches an existing canonical listing.
3. Official issuer/listing universe and corroborating name/venue evidence
   support an existing entity.
4. Human review decides whether to add a listing to an existing entity, retain
   a quarantine, or mint a new entity ID.

No new ID should be minted merely because a source has a ticker absent from v6.
It may be a new listing of an existing issuer, an ADR, a right, a fund, a
delisted instrument, or a provider-specific symbol.

### 8.3 Refresh and promotion procedure

1. Produce a new master version from raw/reference sources and reviewed
   decisions.
2. Load it into an isolated Postgres staging schema.
3. Run acceptance checks:
   - entity-ID and FIGI stability;
   - new/removed/reassigned canonical listings;
   - `(MIC, ticker)` conflict count and conflict samples;
   - primary-listing sanity;
   - known-company regression set (for example NVIDIA, Alphabet, Exxon,
     Ørsted, Reliance, TSMC, Roche, Nestlé, Xiaomi, and Novo Nordisk).
4. Review all non-additive changes explicitly.
5. Promote entities before listings in one transaction.
6. Run the document fallback backfill only for documents without an existing
   source-owned or reviewed `callistra_eq_id`.
7. Record the version date, row counts, validation results, and decision log.

## 9. GICS classification policy

### 9.1 Current position

The current v6 artifacts contain identity fields (`eq_id`, share-class FIGI,
legal name, listing/MIC/ticker data) but do not carry a governed GICS
classification column. GICS data exists in historical source datasets and must
be reattached through a controlled mapping process.

### 9.2 Recommended model

Keep classification separate from identity, preferably in a versioned table:

```sql
callistra_equity.entity_classifications
(
    eq_id,
    classification_system,       -- 'GICS'
    classification_version,      -- licensed/source taxonomy version
    sector_code,
    sector_name,
    industry_group_code,
    industry_group_name,
    industry_code,
    industry_name,
    sub_industry_code,
    sub_industry_name,
    effective_from,
    effective_to,
    source_name,
    source_record_id,
    mapping_method,
    confidence,
    reviewed_at,
    PRIMARY KEY (eq_id, classification_system, classification_version, effective_from)
)
```

Use only authorized/licensed MSCI GICS data and retain the relevant taxonomy
version. GICS classifications change over time, so overwriting a historical
classification without version/effective-date evidence is not acceptable.

### 9.3 GICS remapping process

1. Preserve the original GICS source rows, source identifier, and taxonomy
   version.
2. Join to v6 using the strongest identity bridge available: share-class FIGI,
   ISIN, then exact `(MIC, ticker)`.
3. Send remaining candidates through a reviewed issuer-name/official-universe
   match; do not use sector/name similarity alone.
4. Store match method and confidence.
5. Quarantine conflicts, including different GICS classifications attached to
   competing candidate entities.
6. Publish only reviewed records to `entity_classifications`.

GICS should improve filtering, analytics, and retrieval after identity is
resolved. It must not alter an `eq_id` or force a listing merge.

## 10. Operational monitoring

Review these regularly by source system:

- documents with a ticker and a known/normalized MIC but NULL `callistra_eq_id`;
- documents with a ticker but no MIC;
- unknown or ambiguous raw exchange labels;
- newly observed `(MIC, ticker)` pairs absent from v6;
- documents resolved by fallback versus source-owned mapping;
- quarantined listing conflicts;
- stale onboarding candidates;
- classification mappings lacking a current GICS version.

The desired long-term direction is increasing source-owned, evidence-backed
assignments. The Postgres resolver remains as a deterministic guardrail and
coverage fallback.

## IMPORTANT NOTE:

To the engineer/AI Agent reading this, PLEASE UNDERSTAND THAT AN EQ_ID WOULD BE THE ONLY SOURCE OF TRUTH FOR A COMPANY COVERED BY CALLISTRA.

Therefore, ALL INGESTION ENGINEERS, AI AGENTS, CLAUDES, CODEXES, MUST CONFIRM THE MAPPING OF AN EQ_ID TO THEIR EXCHANGE'S TICKER PERFECTLY AND MUST RUN ALL TYPES OF GREP -NI "[TICKER NAME]" EQUITY_LISTINGS_V6.CSV AND MUST MAP CORRECTLY.

THERE WOULD BE MULTIPLE CASES WHEN THE CURRENT V6 CSVS WOULD NOT HAVE EQ_ID FOR A DESIRED TICKER. CONFIRM THAT ANY EQ_ID DOESNT EXIST, AND THEN PROCEED WITH GENERATING A NEW EQ_ID ONLY WHEN CONFIRMED FOR ONE ECONOMICAL ENTITY. THE POSTGRES ALSO HAS TO BE UPDATED ON SPOT.

ALL ENGINEERS, CLAUDES, CODEXES, AI AGENTS MUST UNDERSTAND THAT THIS PROCESS OF MAPPING EACH EXCHANGE'S INGESTION TO AN EQ_ID MUST NOT BE PARALLELISED AND SHOULD REMAIN STRICTLY SEQUENTIAL, ONE EXCHANGE AT A TIME ONLY.

## WHAT TO DO IMMEDIATELY:

If you have an existing, official & authoritative source of company mapping, then use that to DIFF against the equity_listings_v6.csv (and the equity_entities_v6.csv), and then attempt to understand via various keyword match techniques that your ingestion source's tickers are present in the v6 csvs or not. If present, good -- proceed as it is, if not, generate new eq_id for them, and map them correctly.

ALL SIDECAR TABLES MUST BE UPDATED: ALTER [table] ADD COLUMN callistra_eq_id;

### END.