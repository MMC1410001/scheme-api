-- One row per file loaded from GCS. Re-uploading a file (new GCS generation)
-- replaces its rows; the cron job skips files it has already loaded.
-- status 'failed': some rows could not be read even after reading the file again; its good rows
-- are loaded and rows_bad says how many were left out. With rows_bad = 0 and row_count = 0, the
-- whole file could not be read (not UTF-8, missing columns, not a workbook). Either way it is not
-- retried until SAP re-uploads it (or the month is loaded with --reload).
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
    status            TEXT NOT NULL DEFAULT 'loaded' CHECK (status IN ('loaded', 'failed')),
    rows_bad          INT NOT NULL DEFAULT 0,
    UNIQUE (kind, gcs_path)
);
-- Upgrade a table made before status and rows_bad existed (its files all count as loaded).
-- Checked by hand rather than with ADD COLUMN IF NOT EXISTS: before PostgreSQL 13 that still adds
-- the CHECK when the column exists, so every run would pile up another constraint.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = current_schema() AND table_name = 'ingested_files'
                     AND column_name = 'status') THEN
        ALTER TABLE ingested_files ADD COLUMN status TEXT NOT NULL DEFAULT 'loaded'
            CONSTRAINT ingested_files_status_check CHECK (status IN ('loaded', 'failed'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = current_schema() AND table_name = 'ingested_files'
                     AND column_name = 'rows_bad') THEN
        ALTER TABLE ingested_files ADD COLUMN rows_bad INT NOT NULL DEFAULT 0;
    END IF;
END $$;

-- API 1. The latest version of each SAP scheme row for the month.
-- Daily files only contain rows SAP updated that day, so rows are upserted:
-- a row replaces the stored one only if its "Last Updated" time is newer or equal.
-- A row is identified by folder month, dealer, agreement, deal suffix AND validity period
-- (scheme_records_key below): one month's folder can hold the same agreement and suffix for two
-- periods (e.g. late SAP updates of last month's scheme next to this month's), and both are kept.
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
    next_slab_rate      NUMERIC,             -- SAP "Next Slab Rate"
    last_updated_at     TIMESTAMP NOT NULL,  -- SAP "Last Updated Date" + "Last Updated Time"
    source_file         TEXT NOT NULL,
    raw                 JSONB NOT NULL       -- the original row, so no source column is ever lost
);
-- The row key. A primary key can't hold the dates (they may be empty), so it is a unique index in
-- which empty dates count as equal. ingestion/load.py names the same columns in its ON CONFLICT.
CREATE UNIQUE INDEX IF NOT EXISTS scheme_records_key ON scheme_records (
    year, month, dealer_code, agreement, deal_suffix,
    (coalesce(valid_from, '-infinity'::date)), (coalesce(valid_to, '-infinity'::date))
);
-- Upgrade a table made with the old primary key (without the dates): drop it, so a second period of
-- the same agreement and suffix is no longer written over the first. Rows already written over come
-- back only by loading those months again with --reload.
DO $$
DECLARE
    old_key TEXT;
BEGIN
    SELECT conname INTO old_key FROM pg_constraint
    WHERE conrelid = 'scheme_records'::regclass AND contype = 'p';
    IF old_key IS NOT NULL THEN
        EXECUTE format('ALTER TABLE scheme_records DROP CONSTRAINT %I', old_key);
    END IF;
END $$;
-- Upgrade a table made before next_slab_rate existed: add the column and fill it from each row's
-- stored SAP row (raw), parsed like the loader does ("0.75 ", "1,234.50", negatives as "0.75-").
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = current_schema() AND table_name = 'scheme_records'
                     AND column_name = 'next_slab_rate') THEN
        ALTER TABLE scheme_records ADD COLUMN next_slab_rate NUMERIC;
        UPDATE scheme_records r SET next_slab_rate = CASE
                WHEN v ~ '^-?[0-9]+(\.[0-9]*)?$' THEN v::numeric
                WHEN v ~ '^[0-9]+(\.[0-9]*)?-$' THEN -rtrim(v, '-')::numeric
            END
        -- Each row is matched to itself by its physical row id (the key columns are not unique on
        -- their own any more: two periods of one agreement and suffix can share them).
        FROM (SELECT ctid AS row_id, replace(replace(btrim(raw->>'next_slab_rate'), ',', ''), '%', '') AS v
              FROM scheme_records) s
        WHERE r.ctid = s.row_id;
    END IF;
END $$;

-- API 1 looks a dealer up across months by validity dates, not by folder month.
CREATE INDEX IF NOT EXISTS scheme_records_dealer_valid_from ON scheme_records (dealer_code, valid_from);

