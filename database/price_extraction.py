"""Associate prices stated in crawled descriptions with matching staged venues."""
from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PRICE_LABEL = re.compile(r"\bgi[aá]\s*(?:tham\s*kh[aả]o|v[eé]|t[ií]e\u0302?\s*tham\s*kh[aả]o)?\s*[:：]", re.I)
PRICE_RANGE = re.compile(
    r"(?P<low>\d[\d.,]*)\s*(?:VND|VNĐ|đồng|đ|₫)?\s*(?:-|–|—|đến|tới)\s*"
    r"(?P<high>\d[\d.,]*)\s*(?P<currency>VND|VNĐ|đồng|đ|₫)?",
    re.I,
)


def _plain(value: str) -> str:
    value = unicodedata.normalize("NFKD", (value or "").casefold().replace("đ", "d"))
    value = "".join(char for char in value if not unicodedata.combining(char))
    return " ".join(re.findall(r"[a-z0-9]+", value))


def _amount(value: str) -> int | None:
    digits = re.sub(r"[^0-9]", "", value)
    return int(digits) if digits else None


def _format_vnd(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def _price_in_context(description: str, address: str) -> str | None:
    """Return a price only when this address occurs immediately before its label."""
    if not description or not address:
        return None
    # Use the first address component (street/number), excluding ward and city.
    street = address.split(",", 1)[0].strip()
    street_key = _plain(street)
    if len(street_key) < 5:
        return None
    text_key = _plain(description)
    for label in PRICE_LABEL.finditer(description):
        match = PRICE_RANGE.search(description, label.end(), label.end() + 100)
        if not match:
            continue
        context = _plain(description[max(0, label.start() - 240):label.start()])
        if street_key not in context:
            continue
        trailing = context.rsplit(street_key, 1)[-1]
        if len(trailing) > 90:
            continue
        low, high = _amount(match.group("low")), _amount(match.group("high"))
        if low is None or high is None or low > 100_000_000 or high > 100_000_000:
            continue
        if low > high:
            low, high = high, low
        return f"{_format_vnd(low)} – {_format_vnd(high)} VNĐ"
    return None


def backfill_prices(cur, province_code: str) -> int:
    """Fill missing staged prices from crawled prose, matching the venue address."""
    cur.execute("""
        SELECT DISTINCT description FROM html_crawled_places
        WHERE province_code=%s AND description IS NOT NULL
    """, (province_code,))
    descriptions = [row[0] for row in cur.fetchall()]
    cur.execute("""
        SELECT id, address FROM place_crawl_staging
        WHERE province_code=%s AND price_text IS NULL AND address IS NOT NULL
    """, (province_code,))
    updates = []
    for place_id, address in cur.fetchall():
        prices = {_price_in_context(description, address) for description in descriptions}
        prices.discard(None)
        # Skip conflicting source ranges instead of attaching an uncertain value.
        if len(prices) == 1:
            updates.append((prices.pop(), place_id))
    for price, place_id in updates:
        cur.execute("""
            UPDATE place_crawl_staging
            SET price_text=%s,
                missing_fields=ARRAY(SELECT field FROM unnest(COALESCE(missing_fields, ARRAY[]::text[])) AS field WHERE field <> 'price'),
                updated_at=NOW()
            WHERE id=%s AND price_text IS NULL
        """, (price, place_id))
    return len(updates)


def main() -> int:
    import os
    import psycopg2
    from database.crawll import load_project_env

    load_project_env()
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"), port=int(os.getenv("DB_PORT", "5432")),
        dbname=os.getenv("DB_NAME", "tourism_db"), user=os.getenv("DB_USER", "tourism_user"),
        password=os.getenv("DB_PASSWORD", "tourism_password"), connect_timeout=10,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT code FROM provinces WHERE name ILIKE '%Đà Nẵng%' LIMIT 1")
            row = cur.fetchone()
            if not row:
                raise RuntimeError("Không tìm thấy Đà Nẵng trong bảng tỉnh/thành")
            count = backfill_prices(cur, row[0])
        conn.commit()
        print(f"Đã gắn giá từ mô tả crawl vào {count} địa điểm Đà Nẵng khớp địa chỉ.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

