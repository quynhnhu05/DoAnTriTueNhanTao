CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS provinces (
    id BIGSERIAL PRIMARY KEY,
    code VARCHAR(10) NOT NULL UNIQUE,
    name VARCHAR(150) NOT NULL,
    type VARCHAR(50) NOT NULL
);

CREATE TABLE IF NOT EXISTS gis_provinces (
    id BIGSERIAL PRIMARY KEY,
    province_id BIGINT NOT NULL UNIQUE REFERENCES provinces(id) ON DELETE CASCADE,
    province_code VARCHAR(10) NOT NULL UNIQUE,
    province_name VARCHAR(150) NOT NULL,
    geometry GEOMETRY(MULTIPOLYGON, 4326) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_gis_provinces_geometry
ON gis_provinces USING GIST(geometry);

CREATE TABLE IF NOT EXISTS places (
    id BIGSERIAL PRIMARY KEY,

    name TEXT NOT NULL,

    category VARCHAR(100) NOT NULL,

    subcategory VARCHAR(150),

    province VARCHAR(150),

    district VARCHAR(150),

    ward VARCHAR(150),

    address TEXT,

    latitude DOUBLE PRECISION,

    longitude DOUBLE PRECISION,

    location GEOGRAPHY(POINT, 4326),

    phone VARCHAR(100),

    website TEXT,

    opening_hours TEXT,

    price TEXT,

    price_min NUMERIC,

    price_max NUMERIC,

    currency VARCHAR(20),

    rating NUMERIC(3,2),

    review_count INTEGER,

    description TEXT,

    source VARCHAR(100),

    source_url TEXT,

    osm_id BIGINT,

    osm_type VARCHAR(12),

    price_source_url TEXT,

    price_checked_at TIMESTAMPTZ,

    price_kind VARCHAR(40),

    image_url TEXT,

    image_source_url TEXT,

    image_source_name TEXT,

    image_license TEXT,

    image_checked_at TIMESTAMPTZ,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Supplemental public listings from official tourism registers. These
-- records retain source provenance independently from the map dataset; a POI
-- joins to places only after its coordinates/name have been verified.
CREATE TABLE IF NOT EXISTS tourism_directory_records (
    id BIGSERIAL PRIMARY KEY,
    provider VARCHAR(40) NOT NULL DEFAULT 'VNAT_CSDL',
    provider_category VARCHAR(20) NOT NULL,
    provider_id VARCHAR(80) NOT NULL,
    name TEXT NOT NULL,
    category VARCHAR(100) NOT NULL,
    address TEXT NOT NULL,
    phone TEXT,
    email TEXT,
    website TEXT,
    image_url TEXT,
    image_source_url TEXT,
    image_source_name TEXT,
    image_license TEXT,
    image_checked_at TIMESTAMPTZ,
    price_min NUMERIC,
    price_max NUMERIC,
    currency VARCHAR(20),
    price_source_url TEXT,
    price_checked_at TIMESTAMPTZ,
    price_kind VARCHAR(40),
    source_url TEXT NOT NULL,
    place_id BIGINT REFERENCES places(id) ON DELETE SET NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(provider, provider_category, provider_id)
);

-- Raw, province-linked results from public HTML research pages. These rows
-- retain source data separately from map-ready places, which require verified
-- coordinates and addresses before the itinerary planner can use them.
CREATE TABLE IF NOT EXISTS html_crawled_places (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    category VARCHAR(100) NOT NULL,
    province_code VARCHAR(10) NOT NULL REFERENCES provinces(code),
    province_name VARCHAR(150) NOT NULL,
    price_text TEXT,
    description TEXT,
    address TEXT,
    opening_hours TEXT,
    image_url TEXT,
    image_source_url TEXT,
    source_url TEXT NOT NULL,
    crawled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_url, province_code, name)
);
CREATE INDEX IF NOT EXISTS idx_html_crawled_places_province
    ON html_crawled_places(province_code, category);
-- Retains every image discovered in a crawled page, including photos that
-- could not yet be confidently matched to a particular venue.
CREATE TABLE IF NOT EXISTS html_crawled_page_images (
    id BIGSERIAL PRIMARY KEY,
    source_url TEXT NOT NULL,
    image_source_url TEXT NOT NULL,
    image_local_path TEXT,
    alt_text TEXT,
    title_text TEXT,
    surrounding_text TEXT,
    crawled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_url, image_source_url)
);
-- Holds newly crawled rows until an image, price, and address are available.
CREATE TABLE IF NOT EXISTS place_crawl_staging (
    id BIGSERIAL PRIMARY KEY,
    province_code VARCHAR(10) NOT NULL REFERENCES provinces(code),
    province_name VARCHAR(150) NOT NULL,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    category VARCHAR(100) NOT NULL,
    price_text TEXT,
    price_min NUMERIC,
    price_max NUMERIC,
    currency VARCHAR(20),
    address TEXT,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    opening_hours TEXT,
    image_url TEXT,
    image_source_url TEXT,
    image_source_name TEXT,
    image_license TEXT,
    source_urls TEXT[] NOT NULL DEFAULT '{}',
    source_record_ids BIGINT[] NOT NULL DEFAULT '{}',
    linked_place_id BIGINT REFERENCES places(id) ON DELETE SET NULL,
    missing_fields TEXT[] NOT NULL DEFAULT '{}',
    status VARCHAR(30) NOT NULL DEFAULT 'pending_enrichment'
        CHECK (status IN ('pending_enrichment', 'ready_for_use')),
    crawled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (province_code, normalized_name)
);

CREATE INDEX IF NOT EXISTS idx_place_crawl_staging_province_status
    ON place_crawl_staging(province_code, status, category);

CREATE INDEX IF NOT EXISTS idx_tourism_directory_place_id
    ON tourism_directory_records(place_id) WHERE place_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_places_missing_public_images
    ON places(id) WHERE image_url IS NULL;
CREATE INDEX IF NOT EXISTS idx_directory_missing_public_images
    ON tourism_directory_records(id) WHERE image_url IS NULL;
-- Supports the per-place latest-source lookup used by the API. Keep the
-- address directory import from turning every app request into a table scan.
CREATE INDEX IF NOT EXISTS idx_tourism_directory_latest_place
    ON tourism_directory_records(place_id, last_seen_at DESC, id DESC)
    INCLUDE (source_url, phone, website)
    WHERE place_id IS NOT NULL;

-- Google permits caching Place IDs; all other Places content must be requested
-- live and displayed with Google's required attribution.
CREATE TABLE IF NOT EXISTS google_place_ids (
    place_id TEXT PRIMARY KEY,
    search_query VARCHAR(500) NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_places_name
ON places(name);

CREATE INDEX IF NOT EXISTS idx_places_category
ON places(category);

CREATE INDEX IF NOT EXISTS idx_places_province
ON places(province);

CREATE INDEX IF NOT EXISTS idx_places_district
ON places(district);

CREATE INDEX IF NOT EXISTS idx_places_location
ON places
USING GIST(location);
CREATE UNIQUE INDEX IF NOT EXISTS idx_places_osm_feature
ON places(source, osm_type, osm_id)
WHERE source = 'OpenStreetMap' AND osm_type IS NOT NULL AND osm_id IS NOT NULL;

