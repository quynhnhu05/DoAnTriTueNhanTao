"""Import public tourism listings from the Viet Nam National Authority of Tourism.

The public directory exposes paginated destination, resort-area, restaurant,
accommodation, recreation and shopping listings. This collector keeps source
URLs and crawled-at timestamps, skips records without a usable address, and
only enriches an existing map POI when its name is an exact normalized match.
Unmatched listings are retained in a separate staging table; the script never
guesses coordinates, prices, or a place identity.

Run inside the backend container, for example:
  python /app/data_collector/crawl_vietnamtourism_directory.py \
      --categories dest,area,vcgt --delay 1.5
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import os
import re
import sys
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import psycopg2
from psycopg2.extras import execute_values


BASE = "https://csdl.vietnamtourism.gov.vn"
USER_AGENT = "DoAnTriTueNhanTao/1.0 (academic tourism directory importer)"
DB = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "dbname": os.getenv("DB_NAME", "tourism_db"),
    "user": os.getenv("DB_USER", "tourism_user"),
    "password": os.getenv("DB_PASSWORD", "tourism_password"),
}
CATEGORIES = {
    "dest": "Tham quan",
    "area": "Tham quan",
    "vcgt": "Vui chơi",
    "rest": "Ăn uống",
    "cslt": "Lưu trú",
    "shop": "Mua sắm",
}


def normalize(value: str | None) -> str:
    value = (value or "").replace("Đ", "D").replace("đ", "d")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


@dataclass
class Listing:
    item_id: str = ""
    name: str = ""
    category: str = ""
    provider_category: str = ""
    address: str = ""
    phone: str = ""
    email: str = ""
    website: str = ""
    source_url: str = ""
    approved: bool = False


class ListingParser(HTMLParser):
    """Extract visible listing cards without depending on third-party parsers."""
    def __init__(self, category: str):
        super().__init__(convert_charrefs=True)
        self.category = category
        self.div_depth = 0
        self.card_depth: int | None = None
        self.card: Listing | None = None
        self.anchor: dict | None = None
        self.span: dict | None = None
        self.cards: list[Listing] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "div":
            self.div_depth += 1
            classes = attrs.get("class", "").split()
            if self.card is None and "listing-shot" in classes:
                self.card = Listing(category=CATEGORIES[self.category], provider_category=self.category)
                self.card_depth = self.div_depth
        if self.card is None:
            return
        if tag == "a" and attrs.get("href", "").startswith(f"/{self.category}/?item="):
            self.anchor = {"href": attrs["href"], "text": ""}
        if tag == "span" and "d-block" in attrs.get("class", "").split():
            self.span = {"text": ""}

    def handle_data(self, data):
        if self.anchor is not None:
            self.anchor["text"] += data
        if self.span is not None:
            self.span["text"] += data

    def handle_endtag(self, tag):
        if self.card is not None and tag == "a" and self.anchor is not None:
            text = " ".join(self.anchor["text"].split())
            href = self.anchor["href"]
            item = re.search(r"[?&]item=(\d+)", href)
            if item and text:
                self.card.item_id = item.group(1)
                self.card.name = text
                self.card.source_url = BASE + href
            self.anchor = None
        if self.card is not None and tag == "span" and self.span is not None:
            text = " ".join(self.span["text"].split())
            label, sep, value = text.partition(":")
            label = normalize(label)
            value = value.strip()
            if sep and label == "dia chi":
                self.card.address = value
            elif sep and ("dien thoai" in label or label == "phone"):
                self.card.phone = value
            elif sep and label == "email":
                self.card.email = value
            elif sep and label == "website":
                self.card.website = value
            self.span = None
        if tag == "div":
            if self.card is not None and self.card_depth == self.div_depth:
                if self.card.item_id and self.card.name:
                    self.cards.append(self.card)
                self.card = None
                self.card_depth = None
            self.div_depth = max(0, self.div_depth - 1)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)


def fetch(url: str, attempts: int = 3) -> str:
    error = None
    for attempt in range(attempts):
        request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
        try:
            with urlopen(request, timeout=35) as response:
                return response.read(5_000_000).decode("utf-8", "replace")
        except HTTPError as exc:
            error = exc
            if exc.code not in {429, 500, 502, 503, 504} or attempt == attempts - 1:
                raise
            time.sleep(8 * (attempt + 1))
        except (URLError, TimeoutError, OSError) as exc:
            error = exc
            if attempt == attempts - 1:
                raise
            time.sleep(4 * (attempt + 1))
    raise error


def parse_page(category: str, html: str):
    parser = ListingParser(category)
    parser.feed(html)
    total = re.search(r"Tổng số:\s*([\d.,]+)\s+kết quả", html, re.I)
    final_page = re.search(r'href="\?page=(\d+)"[^>]*>\s*Cuối cùng', html, re.I)
    count = int(total.group(1).replace(".", "").replace(",", "")) if total else None
    pages = int(final_page.group(1)) if final_page else (1 if count is not None else None)
    return parser.cards, count, pages


def has_usable_address(address: str) -> bool:
    clean = re.sub(r"\s+", " ", address or "").strip(" ,.-")
    if len(clean) < 5:
        return False
    parts = [part.strip() for part in clean.split(",") if part.strip()]
    if len(parts) == 1 and normalize(parts[0]).startswith(("thanh pho ", "tinh ")):
        return False
    return len(parts) >= 2 or bool(re.search(r"\b(đường|phường|xã|quận|huyện|thị xã|thành phố|tỉnh|thôn|ấp|bản)\b", clean, re.I))


def ensure_tables(conn):
    with conn.cursor() as cur:
        cur.execute("""
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
                source_url TEXT NOT NULL,
                place_id BIGINT REFERENCES places(id) ON DELETE SET NULL,
                first_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(provider, provider_category, provider_id)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tourism_directory_name ON tourism_directory_records (name)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tourism_directory_category ON tourism_directory_records (category)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tourism_directory_place_id ON tourism_directory_records(place_id) WHERE place_id IS NOT NULL")
        # Earlier collector builds used display categories in this key. Restore
        # the provider's actual section slug before importing more sections.
        cur.execute("""
            UPDATE tourism_directory_records
            SET provider_category = substring(source_url FROM 'vietnamtourism\\.gov\\.vn/([^/]+)/\\?item=')
            WHERE provider='VNAT_CSDL'
              AND provider_category IN ('Tham quan','Vui chơi','Ăn uống','Lưu trú','Mua sắm')
              AND source_url ~ 'vietnamtourism\\.gov\\.vn/[^/]+/\\?item='
        """)
    conn.commit()


def get_existing(conn):
    with conn.cursor() as cur:
        cur.execute("""SELECT id, name, category, address, phone, website, source_url
                       FROM places WHERE latitude IS NOT NULL AND longitude IS NOT NULL""")
        rows = cur.fetchall()
    index: dict[str, list[tuple]] = {}
    for row in rows:
        index.setdefault(normalize(row[1]), []).append(row)
    return index


def persist(conn, listings: list[Listing], existing):
    accepted = [item for item in listings if item.item_id and item.name and has_usable_address(item.address)]
    if not accepted:
        return 0, 0, 0
    matched = enriched = 0
    rows = []
    link_rows = []
    for item in accepted:
        # Accommodation rows use the stricter address + province matcher in
        # crawl_real_tourism_data.link_unambiguous_accommodations(). Linking
        # them here by name alone can attach a same-named hotel from another
        # city and cause the background refresh to undo that link repeatedly.
        candidates = ([] if item.provider_category == "cslt"
                      else existing.get(normalize(item.name), []))
        # An official listing may correct an OSM category tag, so match by an
        # exact normalized name first. If duplicate map rows exist, prefer the
        # same tourism category; ambiguous identities stay staged for review.
        same_category = [row for row in candidates if normalize(row[2]) == normalize(item.category)]
        if same_category:
            candidates = same_category
        place_id = candidates[0][0] if len(candidates) == 1 else None
        if place_id:
            matched += 1
            current = candidates[0]
            # Official register can fill blanks, but never overwrites populated
            # user/OSM values with less precise details.
            current_address = (current[3] or "").strip()
            address = item.address if len(item.address) > len(current_address) + 8 else None
            phone = item.phone if not (current[4] or "").strip() else None
            website = item.website if not (current[5] or "").strip() else None
            source_url = item.source_url if not current[6] else None
            if any((address, phone, website, source_url)):
                with conn.cursor() as cur:
                    cur.execute("""UPDATE places SET address=CASE WHEN %s IS NOT NULL THEN %s ELSE address END,
                                  phone=COALESCE(phone,%s), website=COALESCE(website,%s),
                                  source_url=COALESCE(source_url,%s),
                                  updated_at=CURRENT_TIMESTAMP WHERE id=%s""",
                                (address, address, phone, website, source_url, place_id))
                enriched += 1
        rows.append((item.provider_category, item.item_id, item.name, item.category, item.address,
                     item.phone or None, item.email or None, item.website or None,
                     item.source_url, place_id))
        if place_id:
            link_rows.append((place_id, item.category, item.item_id, item.source_url))
    with conn.cursor() as cur:
        execute_values(cur, """INSERT INTO tourism_directory_records
            (provider_category,provider_id,name,category,address,phone,email,website,source_url,place_id)
            VALUES %s ON CONFLICT (provider,provider_category,provider_id) DO UPDATE SET
              name=EXCLUDED.name, category=EXCLUDED.category, address=EXCLUDED.address,
              phone=COALESCE(EXCLUDED.phone,tourism_directory_records.phone),
              email=COALESCE(EXCLUDED.email,tourism_directory_records.email),
              website=COALESCE(EXCLUDED.website,tourism_directory_records.website),
              source_url=EXCLUDED.source_url, place_id=COALESCE(EXCLUDED.place_id,tourism_directory_records.place_id),
              last_seen_at=CURRENT_TIMESTAMP""", rows,
            template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", page_size=500)
    conn.commit()
    return len(accepted), matched, enriched


def crawl(conn, categories: list[str], delay: float, max_pages: int | None, province: str | None):
    ensure_tables(conn)
    existing = get_existing(conn)
    totals = {"listed": 0, "matched": 0, "enriched": 0, "bad_address": 0}
    for category in categories:
        params = {"province": province} if province else {}
        first_url = BASE + "/" + category + "/" + ("?" + urlencode(params) if params else "")
        html = fetch(first_url)
        listings, total_count, pages = parse_page(category, html)
        if pages is None:
            print(f"[{category}] Không đọc được phân trang; bỏ qua.", file=sys.stderr, flush=True)
            continue
        if max_pages:
            pages = min(pages, max_pages)
        print(f"[{category}] {total_count or '?'} bản ghi, {pages} trang.", flush=True)
        for page in range(1, pages + 1):
            if page > 1:
                if delay:
                    time.sleep(delay)
                page_params = dict(params, page=page)
                html = fetch(BASE + "/" + category + "/?" + urlencode(page_params))
                listings, _, _ = parse_page(category, html)
            valid = sum(has_usable_address(item.address) for item in listings)
            totals["bad_address"] += len(listings) - valid
            accepted, matched, enriched = persist(conn, listings, existing)
            totals["listed"] += accepted
            totals["matched"] += matched
            totals["enriched"] += enriched
            print(f"  trang {page}/{pages}: {accepted} bản ghi có địa chỉ; khớp {matched}, bổ sung thông tin {enriched}.", flush=True)
        if category != categories[-1] and delay:
            time.sleep(delay)
    return totals


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--categories", default="dest,area,vcgt,rest,shop,cslt",
                        help="Comma-separated site categories: dest,area,vcgt,rest,shop,cslt")
    parser.add_argument("--province", help="Directory province selector value, e.g. 48,49")
    parser.add_argument("--delay", type=float, default=1.5, help="Seconds between listing-page requests")
    parser.add_argument("--max-pages", type=int, help="Limit pages per category for a small refresh")
    args = parser.parse_args()
    categories = [item.strip() for item in args.categories.split(",") if item.strip()]
    unknown = sorted(set(categories) - CATEGORIES.keys())
    if unknown or not categories:
        parser.error("category không hỗ trợ: " + ", ".join(unknown or categories))
    if args.delay < 1:
        parser.error("--delay phải ít nhất 1 giây để tránh gây tải cho máy chủ nguồn")
    try:
        with psycopg2.connect(**DB) as conn:
            totals = crawl(conn, categories, args.delay, args.max_pages, args.province)
        print(json.dumps(totals, ensure_ascii=False, indent=2))
    except (HTTPError, URLError, TimeoutError, OSError, psycopg2.Error) as exc:
        print(f"Lỗi thu thập: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
