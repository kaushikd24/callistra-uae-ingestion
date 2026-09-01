-- Sidecar table for Nasdaq Dubai disclosures.
-- Listing: https://api.nasdaqdubai.com/candi/v1/candi (id, headline, issuer, date)
-- Detail:  https://feeds.nasdaqdubai.com/apps/sso/source/detail?id={id}
--          (isin, body HTML, resources[] PDF attachments)
--
-- Two content shapes per disclosure:
--   - HTML body (RNS-style, no `resources`) -> machine-readable, we generate
--     the .mmd ourselves (nasdaq_dubai_pipeline/html_to_mmd.py). raw_html_path
--     holds the duplicated raw HTML for frontend rendering per
--     new_stock_exchange_guidelines.md; documents.blob_path/ocr_path both
--     point at the generated .mmd.
--   - PDF resource(s) -> documents.blob_path is the PDF, ingestion_status
--     stays 'ingested' for the OCR runner, same as ADX.
--
-- De-dupe key: nd_id, the "id" field from the candi listing (a stable UUID
-- assigned by Nasdaq Dubai itself).

CREATE TABLE IF NOT EXISTS nasdaq_dubai_documents (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id          uuid NOT NULL REFERENCES documents(id),

    nd_id                text NOT NULL UNIQUE,

    issuer               text,
    isin                 text,
    headline             text,
    seq_no               integer,
    publication_date      timestamptz,

    content_kind          text NOT NULL,  -- 'html' or 'pdf'
    raw_html_path         text,            -- s3:// path to duplicated raw HTML (content_kind='html' only)
    resource_type          text,            -- resources[].type, e.g. 'financial_reports' / 'news' (content_kind='pdf' only)
    resource_category      text,
    resource_description    text,
    resource_r_path        text,

    raw_response          jsonb NOT NULL,

    ingested_at            timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_nasdaq_dubai_documents_issuer ON nasdaq_dubai_documents (issuer);
CREATE INDEX IF NOT EXISTS idx_nasdaq_dubai_documents_publication_date ON nasdaq_dubai_documents (publication_date);
CREATE INDEX IF NOT EXISTS idx_nasdaq_dubai_documents_document_id ON nasdaq_dubai_documents (document_id);
