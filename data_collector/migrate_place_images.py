"""Apply the additive image/provenance migration on existing installations."""
import os
import shutil
from pathlib import Path

import psycopg2
from media_store import materialize_image


DB = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "dbname": os.getenv("DB_NAME", "tourism_db"),
    "user": os.getenv("DB_USER", "tourism_user"),
    "password": os.getenv("DB_PASSWORD", "tourism_password"),
}


def main():
    # Seed the shared Docker media volume with bundled category-matched photos.
    # This keeps generated fallback images available through the frontend's
    # existing read-only /media mount without storing them in PostgreSQL.
    bundled_photos = Path(__file__).resolve().parents[1] / "backend" / "assets" / "generated_photos"
    media_root = Path(os.getenv("MEDIA_DIR") or (Path(__file__).resolve().parents[1] / "data" / "media"))
    photo_dir = media_root / "places" / "generated_photos"
    if bundled_photos.is_dir():
        photo_dir.mkdir(parents=True, exist_ok=True)
        for photo in bundled_photos.glob("*.jpg"):
            shutil.copy2(photo, photo_dir / photo.name)

    conn = psycopg2.connect(**DB)
    try:
        with conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS tourism_directory_records (
                id BIGSERIAL PRIMARY KEY,
                provider VARCHAR(40) NOT NULL DEFAULT 'VNAT_CSDL',
                provider_category VARCHAR(20) NOT NULL,
                provider_id VARCHAR(80) NOT NULL,
                name TEXT NOT NULL, category VARCHAR(100) NOT NULL, address TEXT NOT NULL,
                phone TEXT, email TEXT, website TEXT, source_url TEXT NOT NULL,
                place_id BIGINT REFERENCES places(id) ON DELETE SET NULL,
                first_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(provider, provider_category, provider_id))""")
            for table in ("places", "tourism_directory_records"):
                cur.execute(f"""ALTER TABLE {table}
                    ADD COLUMN IF NOT EXISTS image_url TEXT,
                    ADD COLUMN IF NOT EXISTS image_source_url TEXT,
                    ADD COLUMN IF NOT EXISTS image_source_name TEXT,
                    ADD COLUMN IF NOT EXISTS image_license TEXT,
                    ADD COLUMN IF NOT EXISTS image_checked_at TIMESTAMPTZ""")
            cur.execute("""ALTER TABLE tourism_directory_records
                ADD COLUMN IF NOT EXISTS price_min NUMERIC,
                ADD COLUMN IF NOT EXISTS price_max NUMERIC,
                ADD COLUMN IF NOT EXISTS currency VARCHAR(20),
                ADD COLUMN IF NOT EXISTS price_source_url TEXT,
                ADD COLUMN IF NOT EXISTS price_checked_at TIMESTAMPTZ,
                ADD COLUMN IF NOT EXISTS price_kind VARCHAR(40)""")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_places_missing_public_images ON places(id) WHERE image_url IS NULL")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_directory_missing_public_images ON tourism_directory_records(id) WHERE image_url IS NULL")
        conn.commit()

        # Replace remote image URLs from already verified sources with actual
        # files in the shared app media volume. Keep their source-page URL for
        # attribution; the itinerary itself then loads only local image paths.
        for table in ("places", "tourism_directory_records"):
            with conn.cursor() as cur:
                cur.execute(f"""SELECT id, name, image_url FROM {table}
                    WHERE image_url ~* '^https?://'
                      AND image_source_url IS NOT NULL
                      AND (image_source_name IN ('Website chính thức của cơ sở','Wikimedia Commons')
                           OR image_source_name LIKE 'Facebook · %')
                    ORDER BY id""")
                rows = cur.fetchall()
            for row_id, name, image_url in rows:
                local_path = materialize_image(image_url, name)
                if local_path:
                    with conn.cursor() as cur:
                        cur.execute(f"UPDATE {table} SET image_url=%s WHERE id=%s", (local_path, row_id))
                    conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
