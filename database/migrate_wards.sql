CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS wards (
    id SERIAL PRIMARY KEY,
    code VARCHAR(40) NOT NULL UNIQUE,
    name VARCHAR(150) NOT NULL,
    type VARCHAR(30),
    province_id INTEGER NOT NULL REFERENCES provinces(id),
    geometry GEOMETRY(MultiPolygon, 4326) NOT NULL
);

ALTER TABLE wards ADD COLUMN IF NOT EXISTS ma1 INTEGER;
ALTER TABLE wards ADD COLUMN IF NOT EXISTS matinhxa VARCHAR(30);
ALTER TABLE wards ADD COLUMN IF NOT EXISTS source_path TEXT;

ALTER TABLE places ADD COLUMN IF NOT EXISTS ward_id INTEGER;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_places_ward'
    ) THEN
        ALTER TABLE places
            ADD CONSTRAINT fk_places_ward
            FOREIGN KEY (ward_id) REFERENCES wards(id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_wards_geometry ON wards USING GIST (geometry);
CREATE INDEX IF NOT EXISTS idx_wards_province_id ON wards (province_id);
CREATE INDEX IF NOT EXISTS idx_wards_matinhxa ON wards (matinhxa);
CREATE INDEX IF NOT EXISTS idx_places_ward_id ON places (ward_id);
CREATE INDEX IF NOT EXISTS idx_places_location_geometry
    ON places USING GIST ((location::geometry));

-- Populate ward_id from the official ward/commune boundaries already in the
-- database. This makes every POI address include its commune/ward even when
-- the original OSM record has no addr:suburb/addr:ward tag.
WITH place_ward AS (
    SELECT
        p.id AS place_id,
        (
            SELECT w.id
            FROM wards AS w
            WHERE w.province_id = p.province_id
              AND ST_Covers(
                    w.geometry,
                    ST_SetSRID(ST_MakePoint(p.longitude, p.latitude), 4326)
                  )
            ORDER BY w.id
            LIMIT 1
        ) AS ward_id
    FROM places AS p
    WHERE p.ward_id IS NULL
      AND p.province_id IS NOT NULL
      AND p.latitude IS NOT NULL
      AND p.longitude IS NOT NULL
)
UPDATE places AS p
SET ward_id = place_ward.ward_id
FROM place_ward
WHERE p.id = place_ward.place_id
  AND place_ward.ward_id IS NOT NULL;

CREATE OR REPLACE FUNCTION assign_place_ward_from_coordinates()
RETURNS trigger AS $$
BEGIN
    IF NEW.ward_id IS NULL
       AND NEW.province_id IS NOT NULL
       AND NEW.latitude IS NOT NULL
       AND NEW.longitude IS NOT NULL THEN
        SELECT w.id
        INTO NEW.ward_id
        FROM wards AS w
        WHERE w.province_id = NEW.province_id
          AND ST_Covers(
                w.geometry,
                ST_SetSRID(ST_MakePoint(NEW.longitude, NEW.latitude), 4326)
              )
        ORDER BY w.id
        LIMIT 1;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_assign_place_ward_from_coordinates ON places;
CREATE TRIGGER trg_assign_place_ward_from_coordinates
BEFORE INSERT OR UPDATE OF province_id, latitude, longitude ON places
FOR EACH ROW
EXECUTE FUNCTION assign_place_ward_from_coordinates();
