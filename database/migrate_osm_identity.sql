BEGIN;
ALTER TABLE places ADD COLUMN IF NOT EXISTS osm_type VARCHAR(12);
ALTER TABLE places ADD COLUMN IF NOT EXISTS price_source_url TEXT;
ALTER TABLE places ADD COLUMN IF NOT EXISTS price_checked_at TIMESTAMPTZ;
ALTER TABLE places ADD COLUMN IF NOT EXISTS price_kind VARCHAR(40);
DROP INDEX IF EXISTS idx_places_osm_id;
CREATE UNIQUE INDEX IF NOT EXISTS idx_places_osm_feature
    ON places(source, osm_type, osm_id)
    WHERE source = 'OpenStreetMap' AND osm_type IS NOT NULL AND osm_id IS NOT NULL;
COMMIT;
