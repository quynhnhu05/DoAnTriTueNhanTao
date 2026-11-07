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

WITH source_rows AS (
    SELECT h.*,
           LOWER(REGEXP_REPLACE(BTRIM(h.name), '[^[:alnum:]]', '', 'g')) AS normalized_name
    FROM html_crawled_places h
    WHERE h.province_name ILIKE '%Đà Nẵng%'
), chosen AS (
    SELECT DISTINCT ON (province_code, normalized_name)
           id, name, category, province_code, province_name,
           NULLIF(BTRIM(price_text), '') AS price_text,
           source_url, crawled_at, normalized_name
    FROM source_rows
    WHERE normalized_name <> ''
    ORDER BY province_code, normalized_name,
             (NULLIF(BTRIM(price_text), '') IS NOT NULL) DESC,
             crawled_at DESC, id DESC
), provenance AS (
    SELECT province_code, normalized_name,
           ARRAY_AGG(DISTINCT source_url) FILTER (WHERE source_url IS NOT NULL) AS source_urls,
           ARRAY_AGG(id ORDER BY crawled_at DESC, id DESC) AS source_record_ids
    FROM source_rows
    WHERE normalized_name <> ''
    GROUP BY province_code, normalized_name
), enriched AS (
    SELECT c.*, pr.source_urls, pr.source_record_ids,
           p.id AS linked_place_id, p.address, p.latitude, p.longitude,
           p.opening_hours, p.image_url, p.image_source_url, p.image_source_name,
           p.image_license, p.price_min, p.price_max, p.currency,
           COALESCE(c.price_text, NULLIF(BTRIM(p.price), '')) AS effective_price_text
    FROM chosen c
    JOIN provenance pr USING (province_code, normalized_name)
    LEFT JOIN LATERAL (
        SELECT p.*
        FROM places p
        WHERE p.province = c.province_name
          AND LOWER(REGEXP_REPLACE(BTRIM(p.name), '[^[:alnum:]]', '', 'g')) = c.normalized_name
        ORDER BY (NULLIF(BTRIM(p.image_url), '') IS NOT NULL) DESC,
                 ((NULLIF(BTRIM(p.price), '') IS NOT NULL) OR p.price_min IS NOT NULL) DESC,
                 p.updated_at DESC NULLS LAST, p.id DESC
        LIMIT 1
    ) p ON TRUE
)
INSERT INTO place_crawl_staging (
    province_code, province_name, name, normalized_name, category,
    price_text, price_min, price_max, currency, address, latitude, longitude,
    opening_hours, image_url, image_source_url, image_source_name, image_license,
    source_urls, source_record_ids, linked_place_id, missing_fields, status, crawled_at
)
SELECT province_code, province_name, name, normalized_name, category,
       effective_price_text, price_min, price_max, currency, address, latitude, longitude,
       opening_hours, image_url, image_source_url, image_source_name, image_license,
       COALESCE(source_urls, '{}'), COALESCE(source_record_ids, '{}'), linked_place_id,
       ARRAY_REMOVE(ARRAY[
           CASE WHEN NULLIF(BTRIM(image_url), '') IS NULL THEN 'image' END,
           CASE WHEN NULLIF(BTRIM(effective_price_text), '') IS NULL AND price_min IS NULL THEN 'price' END,
           CASE WHEN NULLIF(BTRIM(address), '') IS NULL THEN 'address' END
       ], NULL),
       CASE WHEN NULLIF(BTRIM(image_url), '') IS NOT NULL
                  AND (NULLIF(BTRIM(effective_price_text), '') IS NOT NULL OR price_min IS NOT NULL)
                  AND NULLIF(BTRIM(address), '') IS NOT NULL
            THEN 'ready_for_use' ELSE 'pending_enrichment' END,
       crawled_at
FROM enriched
ON CONFLICT (province_code, normalized_name) DO UPDATE SET
    name = EXCLUDED.name,
    category = EXCLUDED.category,
    price_text = COALESCE(EXCLUDED.price_text, place_crawl_staging.price_text),
    price_min = COALESCE(EXCLUDED.price_min, place_crawl_staging.price_min),
    price_max = COALESCE(EXCLUDED.price_max, place_crawl_staging.price_max),
    currency = COALESCE(EXCLUDED.currency, place_crawl_staging.currency),
    address = COALESCE(EXCLUDED.address, place_crawl_staging.address),
    latitude = COALESCE(EXCLUDED.latitude, place_crawl_staging.latitude),
    longitude = COALESCE(EXCLUDED.longitude, place_crawl_staging.longitude),
    opening_hours = COALESCE(EXCLUDED.opening_hours, place_crawl_staging.opening_hours),
    image_url = COALESCE(EXCLUDED.image_url, place_crawl_staging.image_url),
    image_source_url = COALESCE(EXCLUDED.image_source_url, place_crawl_staging.image_source_url),
    image_source_name = COALESCE(EXCLUDED.image_source_name, place_crawl_staging.image_source_name),
    image_license = COALESCE(EXCLUDED.image_license, place_crawl_staging.image_license),
    source_urls = EXCLUDED.source_urls,
    source_record_ids = EXCLUDED.source_record_ids,
    linked_place_id = COALESCE(EXCLUDED.linked_place_id, place_crawl_staging.linked_place_id),
    missing_fields = ARRAY_REMOVE(ARRAY[
        CASE WHEN NULLIF(BTRIM(COALESCE(EXCLUDED.image_url, place_crawl_staging.image_url)), '') IS NULL THEN 'image' END,
        CASE WHEN NULLIF(BTRIM(COALESCE(EXCLUDED.price_text, place_crawl_staging.price_text)), '') IS NULL
                  AND COALESCE(EXCLUDED.price_min, place_crawl_staging.price_min) IS NULL THEN 'price' END,
        CASE WHEN NULLIF(BTRIM(COALESCE(EXCLUDED.address, place_crawl_staging.address)), '') IS NULL THEN 'address' END
    ], NULL),
    status = CASE
        WHEN NULLIF(BTRIM(COALESCE(EXCLUDED.image_url, place_crawl_staging.image_url)), '') IS NOT NULL
         AND (NULLIF(BTRIM(COALESCE(EXCLUDED.price_text, place_crawl_staging.price_text)), '') IS NOT NULL
              OR COALESCE(EXCLUDED.price_min, place_crawl_staging.price_min) IS NOT NULL)
         AND NULLIF(BTRIM(COALESCE(EXCLUDED.address, place_crawl_staging.address)), '') IS NOT NULL
        THEN 'ready_for_use' ELSE 'pending_enrichment' END,
    crawled_at = GREATEST(EXCLUDED.crawled_at, place_crawl_staging.crawled_at),
    updated_at = NOW();
