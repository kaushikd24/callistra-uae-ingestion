-- Sidecar table for DFM (Dubai Financial Market) disclosures.
-- Single API call returns everything: headline, issuer, issuer_symbol,
-- resources[] (PDF attachments) — no separate detail call needed, unlike
-- Nasdaq Dubai. https://api2.dfm.ae/efsah/v1/prototype_efsah
--
-- Always PDF-based (0/2500 sampled records had zero resources) — no
-- machine-readable HTML path like Nasdaq Dubai's RNS-style disclosures.
--
-- De-dupe key: dfm_id, the "id" field from the API response (a stable UUID
-- DFM itself assigns).

CREATE TABLE IF NOT EXISTS dfm_documents (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id           uuid NOT NULL REFERENCES documents(id),

    dfm_id                text NOT NULL UNIQUE,

    issuer_symbol          text,
    issuer                text,
    issuer_ar              text,
    isin                  text,  -- not in the API response; joined from company_mapping_full_with_gics_no_id.csv by symbol

    -- Resolved via the standard ticker+MIC path (resolve_equity_v6 trigger,
    -- primary_mic='XDFM' written source-authoritatively). Denormalized copy
    -- of documents.callistra_eq_id.
    callistra_eq_id       text,

    headline               text,
    announcement_type       text,

    report_interval         text,
    integrated_period       text,
    integrated_language     text,
    integrated_report_type   text,
    dividends_payment_date   text,

    publication_date        timestamptz,

    resource_type           text,   -- resources[].type, e.g. 'financial_reports' / 'news' / 'general_meetings' / 'integrated_reports'
    resource_category        text,
    resource_description      text,
    resource_r_path          text,

    raw_response            jsonb NOT NULL,

    ingested_at             timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_dfm_documents_issuer_symbol ON dfm_documents (issuer_symbol);
CREATE INDEX IF NOT EXISTS idx_dfm_documents_publication_date ON dfm_documents (publication_date);
CREATE INDEX IF NOT EXISTS idx_dfm_documents_document_id ON dfm_documents (document_id);
