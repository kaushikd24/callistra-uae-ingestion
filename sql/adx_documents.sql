-- Sidecar table for ADX (Abu Dhabi Securities Exchange) disclosures.
-- Source: https://apigateway.adx.ae/adx/tradings/1.1/news?categoryName=cd
--
-- De-dupe key: adx_content_id — the numeric document id embedded in ADX's
-- own download URL (e.g. https://apigateway.adx.ae/adx/cdn/1.0/content/download/5362281
-- -> "5362281"). Stable across re-polls; ADX assigns one per document.

CREATE TABLE IF NOT EXISTS adx_documents (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id          uuid NOT NULL REFERENCES documents(id),

    adx_content_id       text NOT NULL UNIQUE,

    entity_symbol        text NOT NULL,
    entity_name_en        text,
    entity_name_ar        text,
    isin                 text,

    -- Denormalized copy of documents.callistra_eq_id (the FK-constrained
    -- source of truth — see internal_equity_id_usage_guidelines.md) for
    -- sidecar-local querying/audit without a join.
    callistra_eq_id       text,

    title_en             text,
    title_ar             text,
    simple_title_en       text,

    category_id           text,
    category_name_en       text,
    subcategory_id        text,
    subcategory_name_en    text,   -- ADX's own filing-type taxonomy value

    published_date        timestamptz,
    event_date            timestamptz,

    pdf_url_en             text NOT NULL,
    pdf_url_ar             text,
    ex_para               text,

    raw_response          jsonb NOT NULL,

    ingested_at            timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_adx_documents_entity_symbol ON adx_documents (entity_symbol);
CREATE INDEX IF NOT EXISTS idx_adx_documents_published_date ON adx_documents (published_date);
CREATE INDEX IF NOT EXISTS idx_adx_documents_document_id ON adx_documents (document_id);
