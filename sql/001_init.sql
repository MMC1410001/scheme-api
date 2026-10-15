-- One row per file loaded from GCS. Re-uploading a file (new GCS generation)
-- replaces its rows; the cron job skips files it has already loaded.
CREATE TABLE IF NOT EXISTS ingested_files (
    id                BIGSERIAL PRIMARY KEY,
    kind              TEXT NOT NULL CHECK (kind = 'scheme'),
    gcs_path          TEXT NOT NULL,
    generation        TEXT NOT NULL,
    source_updated_at TIMESTAMPTZ NOT NULL,
    year              INT,
    month             INT,
    row_count         INT NOT NULL DEFAULT 0,
    loaded_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (kind, gcs_path)
);

-- API 1. The latest version of each SAP scheme row for the month.
-- Daily files only contain rows SAP updated that day, so rows are upserted:
-- a row replaces the stored one only if its "Last Updated" time is newer or equal.
CREATE TABLE IF NOT EXISTS scheme_records (
    year                INT NOT NULL,
    month               INT NOT NULL,
    dealer_code         TEXT NOT NULL,
    agreement           TEXT NOT NULL,
    deal_suffix         TEXT NOT NULL,
    component           TEXT NOT NULL CHECK (component IN (
                            'EB SLAB 1', 'EB SLAB 2', 'EB SLAB 3', 'VALUE',
                            'LUXURY', 'PREMIUM 01', 'PREMIUM 02', 'EB FULL MONTH')),
    valid_from          DATE,
    valid_to            DATE,
    qualification_basis TEXT NOT NULL CHECK (qualification_basis IN ('GSR', 'INVOICE_QTY')),
    qualification       NUMERIC,
    gsr_b               NUMERIC,
    rebate_rate         NUMERIC,
    clear_cut           NUMERIC,
    shortfall_amount    NUMERIC,
    shortfall_pct       NUMERIC,
    last_updated_at     TIMESTAMP NOT NULL,  -- SAP "Last Updated Date" + "Last Updated Time"
    source_file         TEXT NOT NULL,
    raw                 JSONB NOT NULL,      -- the original row, so no source column is ever lost
    PRIMARY KEY (year, month, dealer_code, agreement, deal_suffix)
);

