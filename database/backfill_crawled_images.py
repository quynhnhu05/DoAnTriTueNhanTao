"""Assign unused photos already downloaded from a page to its uncategorized venues."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database.crawll import DEFAULT_URL, backfill_missing_place_images, load_project_env
import psycopg2


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    load_project_env()
    source_url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    try:
        with psycopg2.connect(
            host=os.getenv("DB_HOST", "localhost"),
            port=os.getenv("DB_PORT", "5432"),
            dbname=os.getenv("DB_NAME", "tourism_db"),
            user=os.getenv("DB_USER", "tourism_user"),
            password=os.getenv("DB_PASSWORD", "tourism_password"),
            connect_timeout=8,
        ) as conn:
            with conn.cursor() as cur:
                count = backfill_missing_place_images(cur, source_url)
                # Keep the curation queue in sync with the scraped source rows.
                cur.execute("""
                    UPDATE place_crawl_staging s
                    SET image_url = h.image_url,
                        image_source_url = h.image_source_url,
                        missing_fields = ARRAY_REMOVE(s.missing_fields, 'image'),
                        status = CASE
                            WHEN NULLIF(BTRIM(COALESCE(s.price_text, '')), '') IS NOT NULL
                                 OR s.price_min IS NOT NULL THEN 'ready_for_use'
                            ELSE 'pending_enrichment'
                        END,
                        updated_at = NOW()
                    FROM html_crawled_places h
                    WHERE h.source_url = %s
                      AND NULLIF(BTRIM(h.image_url), '') IS NOT NULL
                      AND s.province_code = h.province_code
                      AND s.normalized_name = LOWER(REGEXP_REPLACE(BTRIM(h.name), '[^[:alnum:]]', '', 'g'))
                """, (source_url,))
                cur.execute("""
                    SELECT COUNT(*), COUNT(*) FILTER (WHERE NULLIF(BTRIM(image_url), '') IS NOT NULL)
                    FROM html_crawled_places WHERE source_url = %s
                """, (source_url,))
                total, with_images = cur.fetchone()
                print(f"Hoàn tất: {with_images}/{total} địa điểm có ảnh; đã ghép thêm {count} ảnh.")
    except psycopg2.Error as exc:
        print(f"Không cập nhật được PostgreSQL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
