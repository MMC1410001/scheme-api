-- One row per file loaded from GCS. Re-uploading a file (new GCS generation)
-- replaces its rows; the cron job skips files it has already loaded.
-- status 'failed': some rows could not be read even after reading the file again; its good rows
-- are loaded and rows_bad says how many were left out. With rows_bad = 0 and row_count = 0, the
-- whole file could not be read (not UTF-8, missing columns, not a workbook). Either way it is not
-- retried until SAP re-uploads it, a newer loader version is deployed, or the month is loaded with --reload.
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
    -- The loader version that read the file (ingestion/load.py LOADER_VERSION). A file read by an
    -- older version is read again by the next run, so a change in what the loader keeps reaches
    -- files already loaded without --reload.
    loader_version    INT NOT NULL DEFAULT 0,
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
    -- Files loaded before loader_version existed count as version 0, so they are read again.
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = current_schema() AND table_name = 'ingested_files'
                     AND column_name = 'loader_version') THEN
        ALTER TABLE ingested_files ADD COLUMN loader_version INT NOT NULL DEFAULT 0;
    END IF;
END $$;

-- The column names of SAP files, stored once: scheme_records.raw holds a row's values only, in this
-- order (scheme_records.raw_layout says which layout). The view scheme_records_raw puts them together.
CREATE TABLE IF NOT EXISTS raw_layouts (
    id      SERIAL PRIMARY KEY,
    columns TEXT[] NOT NULL UNIQUE
);

-- API 1. The latest version of each SAP scheme row for the month.
-- Daily files only contain rows SAP updated that day, so rows are upserted:
-- a row replaces the stored one only if its "Last Updated" time is newer or equal.
-- A row is identified by folder month, dealer, agreement, deal suffix, component AND validity period
-- (scheme_records_key below): one month's folder can hold the same agreement and suffix for two
-- periods (e.g. late SAP updates of last month's scheme next to this month's), and both are kept.
-- The component is in the key because SAP can give two components the same deal suffix and period
-- (Oct 2026: ALWD FULL MONTH and ADDN TEXTURE PER 250, suffix 65); without it one would overwrite the other.
CREATE TABLE IF NOT EXISTS scheme_records (
    year                INT NOT NULL,
    month               INT NOT NULL,
    dealer_code         TEXT NOT NULL,
    agreement           TEXT NOT NULL,
    deal_suffix         TEXT NOT NULL,
    -- Every component SAP sends (names change from month to month); API 1 picks which to return.
    component           TEXT NOT NULL CONSTRAINT scheme_records_component_present CHECK (btrim(component) <> ''),
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
    invoiced_qty        NUMERIC,             -- SAP "Invoiced Quantity (Q)"
    last_updated_at     TIMESTAMP NOT NULL,  -- SAP "Last Updated Date" + "Last Updated Time"
    source_file         TEXT NOT NULL,
    -- The original row's values, every one (zeros and empty ones too), so no source column is ever
    -- lost; names in raw_layouts. Rows stored before raw_layouts are name -> value objects until
    -- the loader rewrites them (ingestion/load.py, compact_raw). Read it through scheme_records_raw.
    raw                 JSONB NOT NULL,
    raw_layout          INT REFERENCES raw_layouts (id)
);
-- Upgrade a table made before raw_layouts: add the column (its rows keep raw as objects until the
-- loader's one-off rewrite).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = current_schema() AND table_name = 'scheme_records'
                     AND column_name = 'raw_layout') THEN
        ALTER TABLE scheme_records ADD COLUMN raw_layout INT REFERENCES raw_layouts (id);
    END IF;
END $$;
-- Upgrade a table whose row key has no component: drop that key so it is made again below. The new
-- key only adds a column, so every stored row stays unique under it.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname = current_schema() AND indexname = 'scheme_records_key'
               AND indexdef NOT LIKE '%component%') THEN
        DROP INDEX scheme_records_key;
    END IF;
END $$;
-- The row key. A primary key can't hold the dates (they may be empty), so it is a unique index in
-- which empty dates count as equal. ingestion/load.py names the same columns in its ON CONFLICT.
CREATE UNIQUE INDEX IF NOT EXISTS scheme_records_key ON scheme_records (
    year, month, dealer_code, agreement, deal_suffix, component,
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

-- Upgrade a table made when only the 10 template components were stored: drop the CHECK that
-- listed them (named scheme_records_component_check), so every component can be loaded.
ALTER TABLE scheme_records DROP CONSTRAINT IF EXISTS scheme_records_component_check;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'scheme_records'::regclass
                   AND conname = 'scheme_records_component_present') THEN
        ALTER TABLE scheme_records ADD CONSTRAINT scheme_records_component_present
            CHECK (btrim(component) <> '');
    END IF;
END $$;

-- Upgrade a table made before invoiced_qty existed: add the column and fill it from each row's
-- stored SAP row (raw), parsed like next_slab_rate above.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = current_schema() AND table_name = 'scheme_records'
                     AND column_name = 'invoiced_qty') THEN
        ALTER TABLE scheme_records ADD COLUMN invoiced_qty NUMERIC;
        UPDATE scheme_records r SET invoiced_qty = CASE
                WHEN v ~ '^-?[0-9]+(\.[0-9]*)?$' THEN v::numeric
                WHEN v ~ '^[0-9]+(\.[0-9]*)?-$' THEN -rtrim(v, '-')::numeric
            END
        -- raw is a name -> value object, or values only named by raw_layouts (main 0.12).
        FROM (SELECT x.ctid AS row_id, replace(replace(btrim(CASE WHEN x.raw_layout IS NULL
                         THEN x.raw->>'invoiced_quantity_q'
                         ELSE x.raw->>(array_position(l.columns, 'invoiced_quantity_q') - 1) END), ',', ''), '%', '') AS v
              FROM scheme_records x LEFT JOIN raw_layouts l ON l.id = x.raw_layout) s
        WHERE r.ctid = s.row_id;
    END IF;
END $$;

-- One-off clean-ups the loader has done, so each runs once per database (ingestion/load.py, prune_all).
CREATE TABLE IF NOT EXISTS loader_maintenance (
    name    TEXT PRIMARY KEY,
    done_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- API 1 looks a dealer up across months by validity dates, not by folder month.
CREATE INDEX IF NOT EXISTS scheme_records_dealer_valid_from ON scheme_records (dealer_code, valid_from);


-- scheme_records with raw as name -> value, as SAP sent the row, for checking data by hand, e.g.
--   SELECT raw->>'remarks' FROM scheme_records_raw WHERE dealer_code = '6100000001';
-- Made again on each run, so a release can add a column anywhere in it (OR REPLACE only appends).
DROP VIEW IF EXISTS scheme_records_raw;
CREATE VIEW scheme_records_raw AS
SELECT r.year, r.month, r.dealer_code, r.agreement, r.deal_suffix, r.component, r.valid_from, r.valid_to,
       r.qualification_basis, r.qualification, r.gsr_b, r.rebate_rate, r.clear_cut, r.shortfall_amount,
       r.shortfall_pct, r.next_slab_rate, r.invoiced_qty, r.last_updated_at, r.source_file,
       CASE WHEN r.raw_layout IS NULL THEN r.raw
            ELSE (SELECT coalesce(jsonb_object_agg(l.columns[u.i], u.value), '{}'::jsonb)
                  FROM jsonb_array_elements(r.raw) WITH ORDINALITY AS u(value, i)) END AS raw
FROM scheme_records r
LEFT JOIN raw_layouts l ON l.id = r.raw_layout;
