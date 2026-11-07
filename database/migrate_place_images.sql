-- Store real, attributed public images with the place/listing records.
BEGIN;

ALTER TABLE places
    ADD COLUMN IF NOT EXISTS image_url TEXT,
    ADD COLUMN IF NOT EXISTS image_source_url TEXT,
    ADD COLUMN IF NOT EXISTS image_source_name TEXT,
    ADD COLUMN IF NOT EXISTS image_license TEXT,
    ADD COLUMN IF NOT EXISTS image_checked_at TIMESTAMPTZ;

ALTER TABLE tourism_directory_records
    ADD COLUMN IF NOT EXISTS image_url TEXT,
    ADD COLUMN IF NOT EXISTS image_source_url TEXT,
    ADD COLUMN IF NOT EXISTS image_source_name TEXT,
    ADD COLUMN IF NOT EXISTS image_license TEXT,
    ADD COLUMN IF NOT EXISTS image_checked_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS price_min NUMERIC,
    ADD COLUMN IF NOT EXISTS price_max NUMERIC,
    ADD COLUMN IF NOT EXISTS currency VARCHAR(20),
    ADD COLUMN IF NOT EXISTS price_source_url TEXT,
    ADD COLUMN IF NOT EXISTS price_checked_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS price_kind VARCHAR(40);

CREATE INDEX IF NOT EXISTS idx_places_missing_public_images
    ON places(id) WHERE image_url IS NULL;
CREATE INDEX IF NOT EXISTS idx_directory_missing_public_images
    ON tourism_directory_records(id) WHERE image_url IS NULL;

COMMIT;
