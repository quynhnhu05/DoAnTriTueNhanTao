"""Independent web-data collector for the tourism assistant.

This entry point starts from the official Viet Nam National Authority of
Tourism directory, then stores verified venue names, street addresses, sites,
first-party HTML preview images and explicit published VND prices in PostgreSQL. It
does not query OpenStreetMap or Overpass. Exact directory matches may enrich an
existing geocoded app POI, while unmatched public listings remain in their own
source table until their location can be verified.

Example:
  python /app/data_collector/crawl_real_tourism_data.py \
      --directory-categories dest,area,vcgt,rest,shop,cslt \
      --image-limit 250 --price-limit 500 --site-delay 4
"""
from __future__ import annotations

import argparse
from html import unescape
from html.parser import HTMLParser
import json
import os
import re
import socket
import sys
import time
import unicodedata
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

import psycopg2

from crawl_vietnamtourism_directory import CATEGORIES, crawl as crawl_directory
from crawl_public_prices import (
    _fold_words,
    _NAME_STOPWORDS,
    _editorial_dated_url,
    _has_expired_public_offer,
    _has_stale_dated_rate_url,
    explicit_vnd_prices,
    page_jsonld,
    price_page_links,
    public_http_url,
    robots_allow,
)
from media_store import materialize_image


DB = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "dbname": os.getenv("DB_NAME", "tourism_db"),
    "user": os.getenv("DB_USER", "tourism_user"),
    "password": os.getenv("DB_PASSWORD", "tourism_password"),
}
_GEMINI_SEARCH_DAY = datetime.now(timezone.utc).date()
_GEMINI_SEARCHES_TODAY = 0
DIRECTORY_TO_PRICE_CATEGORY = {
    "Ăn uống": "Ăn uống", "Lưu trú": "Lưu trú", "Cafe": "Cafe",
    "Tham quan": "Tham quan", "Vui chơi": "Vui chơi",
    "Giải trí": "Giải trí", "Mua sắm": "Mua sắm",
}
MATCH_STOP_WORDS = {
    "khach", "san", "hotel", "hotels", "hostel", "resort", "homestay",
    "accommodation", "da", "nang", "vietnam", "viet", "nam", "duong",
    "street", "road", "so", "phuong", "quan", "huyen", "thanh", "pho",
    "tinh", "xa", "thi", "tran", "ward", "district", "city", "province",
    "danang", "hochiminh", "hanoi", "haiphong", "cantho",
    "hoi", "an", "hue", "saigon", "nha", "trang", "nhatrang", "cat", "ba", "hai", "phong",
    "bai", "chay", "sam", "son", "pu", "luong", "thuoc", "cam", "ranh",
}


def _strong_tokens(value: str):
    return {token for token in _fold_words(value).split()
            if len(token) >= 3 and token not in MATCH_STOP_WORDS and not token.isdigit()}


def _street_identity(address: str):
    """Use only the first address line for a street-level match, not city names."""
    first_lines = ", ".join((address or "").split(",")[:2])
    text = _fold_words(first_lines)
    number = re.match(r"\s*(\d+[a-z]?(?:\s*[-/]\s*\d+[a-z]?)?)\b", text)
    house_number = re.sub(r"\s+", "", number.group(1)) if number else None
    tokens = _strong_tokens(first_lines)
    return tokens, house_number


def _link_is_confident(listing_name, listing_address, place_name, place_address,
                       province, name_frequency):
    folded_address = f" {_fold_words(listing_address)} "
    if f" {_fold_words(province)} " not in folded_address:
        return False
    listing_tokens = _strong_tokens(listing_name)
    place_tokens = _strong_tokens(place_name)
    if not listing_tokens or listing_tokens != place_tokens:
        return False
    listing_street, listing_number = _street_identity(listing_address)
    place_street, place_number = _street_identity(place_address)
    if len(listing_street & place_street) < 2:
        return False
    if listing_number and place_number and listing_number != place_number:
        return False
    # Fuzzy name links are too risky: similar venues can share a district,
    # street, or one generic word. Link only when the distinctive name tokens
    # are exactly the same after removing venue and locality words.
    return True


def link_unambiguous_accommodations(conn):
    """Link official hotel listings only when brand, street and province agree."""
    with conn.cursor() as cur:
        cur.execute("""SELECT td.id, td.name, td.address
            FROM tourism_directory_records td
            WHERE td.provider_category='cslt' AND td.place_id IS NULL
            ORDER BY td.id""")
        listings = cur.fetchall()
        cur.execute("""SELECT pl.id, pl.name, pl.address, pl.latitude, pl.longitude,
                            p.name AS province_name
            FROM places pl JOIN provinces p ON p.id=pl.province_id
            WHERE pl.category='Lưu trú' AND pl.latitude IS NOT NULL
              AND pl.longitude IS NOT NULL
              AND NULLIF(BTRIM(pl.address),'') IS NOT NULL""")
        places = cur.fetchall()

    province_groups = {}
    province_names = sorted({row[5] for row in places if row[5]}, key=lambda value: len(_fold_words(value)), reverse=True)
    name_frequency = {}
    for _, place_name, _, _, _, province in places:
        province_key = _fold_words(province)
        name_key = _fold_words(place_name)
        for token in _strong_tokens(place_name):
            name_frequency.setdefault((province_key, token), set()).add(name_key)
    for place_id, place_name, place_address, lat, lon, province in places:
        province_groups.setdefault(_fold_words(province), []).append((
            place_id, place_name, _strong_tokens(place_name), _street_identity(place_address)[0],
            float(lat), float(lon), province, _street_identity(place_address)[1],
        ))

    links = []
    for listing_id, listing_name, listing_address in listings:
        name_tokens = _strong_tokens(listing_name)
        street_tokens, listing_number = _street_identity(listing_address)
        if not name_tokens or len(street_tokens) < 2:
            continue
        folded_listing_address = f" {_fold_words(listing_address)} "
        province = next((name for name in province_names
                         if f" {_fold_words(name)} " in folded_listing_address), None)
        if not province:
            continue
        province_places = province_groups.get(_fold_words(province), [])
        matches = []
        for place_id, place_name, place_tokens, address_tokens, lat, lon, _, place_number in province_places:
            brand_overlap = name_tokens & place_tokens
            street_overlap = street_tokens & address_tokens
            if brand_overlap and len(street_overlap) >= 2:
                if listing_number and place_number and listing_number != place_number:
                    continue
                if name_tokens != place_tokens:
                    continue
                matches.append((len(brand_overlap), len(street_overlap), place_id,
                                place_name, float(lat), float(lon)))
        if not matches:
            continue
        matches.sort(reverse=True)
        top_score = matches[0][:2]
        top = [row for row in matches if row[:2] == top_score]
        # Collapse duplicate imported place rows for the same hotel when their
        # normalized name and coordinates clearly describe one building.
        identities = {}
        for row in top:
            _, _, place_id, place_name, lat, lon = row
            key = (_fold_words(place_name), round(lat, 3), round(lon, 3))
            identities.setdefault(key, []).append(place_id)
        if len(identities) != 1:
            continue
        canonical_id = min(next(iter(identities.values())))
        links.append((listing_id, canonical_id, listing_name))

    with conn.cursor() as cur:
        for listing_id, place_id, name in links:
            cur.execute("""UPDATE tourism_directory_records SET place_id=%s
                WHERE id=%s AND place_id IS NULL""", (place_id, listing_id))
    conn.commit()
    return len(links)


def unlink_confidently_mismatched_accommodations(conn):
    """Undo unsafe old accommodation links and archive their prior values.

    Earlier releases could attach an official listing to a different hotel.
    Remove only fields that exactly match that listing, and save full JSON
    snapshots first so the repair is auditable and reversible.
    """
    with conn.cursor() as cur:
        cur.execute("""SELECT td.id, td.name, td.address, pl.name, pl.address,
                            p.name, pl.province_id, pl.latitude, pl.longitude
            FROM tourism_directory_records td
            JOIN places pl ON pl.id=td.place_id
            JOIN provinces p ON p.id=pl.province_id
            WHERE td.provider_category='cslt' AND td.place_id IS NOT NULL""")
        rows = cur.fetchall()
        cur.execute("""SELECT pl.province_id, p.name, pl.name
            FROM places pl JOIN provinces p ON p.id=pl.province_id
            WHERE pl.category='Lưu trú'""")
        names = cur.fetchall()
    frequencies = {}
    for province_id, province, name in names:
        folded_name = _fold_words(name)
        for token in _strong_tokens(name):
            frequencies.setdefault((_fold_words(province), token), set()).add(folded_name)
    mismatched = []
    for directory_id, dn, da, pn, pa, province, province_id, _, _ in rows:
        if not _link_is_confident(dn, da, pn, pa, province, frequencies):
            mismatched.append(directory_id)
    if mismatched:
        with conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS tourism_directory_link_repair_audit (
                id BIGSERIAL PRIMARY KEY,
                directory_record_id BIGINT NOT NULL,
                place_id BIGINT NOT NULL,
                reason TEXT NOT NULL,
                directory_snapshot JSONB NOT NULL,
                place_snapshot JSONB NOT NULL,
                repaired_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")
            cur.execute("""INSERT INTO tourism_directory_link_repair_audit
                (directory_record_id, place_id, reason, directory_snapshot, place_snapshot)
                SELECT td.id, pl.id, 'Accommodation listing did not match place name, street, and province',
                       to_jsonb(td), to_jsonb(pl)
                FROM tourism_directory_records td
                JOIN places pl ON pl.id=td.place_id
                WHERE td.id=ANY(%s)""", (mismatched,))
            # Clear only values that can be traced exactly to the bad linked
            # listing. Independent address/map fields are left untouched.
            cur.execute("""WITH bad_places AS (
                    SELECT DISTINCT place_id FROM tourism_directory_records WHERE id=ANY(%s)
                )
                UPDATE places pl SET
                    website=CASE WHEN EXISTS (SELECT 1 FROM tourism_directory_records td WHERE td.id=ANY(%s) AND td.place_id=pl.id AND NULLIF(BTRIM(td.website),'')=pl.website) THEN NULL ELSE pl.website END,
                    phone=CASE WHEN EXISTS (SELECT 1 FROM tourism_directory_records td WHERE td.id=ANY(%s) AND td.place_id=pl.id AND NULLIF(BTRIM(td.phone),'')=pl.phone) THEN NULL ELSE pl.phone END,
                    source_url=CASE WHEN EXISTS (SELECT 1 FROM tourism_directory_records td WHERE td.id=ANY(%s) AND td.place_id=pl.id AND NULLIF(BTRIM(td.source_url),'')=pl.source_url) THEN NULL ELSE pl.source_url END,
                    image_url=CASE WHEN EXISTS (SELECT 1 FROM tourism_directory_records td WHERE td.id=ANY(%s) AND td.place_id=pl.id AND td.image_source_url IS NOT NULL AND pl.image_source_url=td.image_source_url) THEN NULL ELSE pl.image_url END,
                    image_source_url=CASE WHEN EXISTS (SELECT 1 FROM tourism_directory_records td WHERE td.id=ANY(%s) AND td.place_id=pl.id AND td.image_source_url IS NOT NULL AND pl.image_source_url=td.image_source_url) THEN NULL ELSE pl.image_source_url END,
                    image_source_name=CASE WHEN EXISTS (SELECT 1 FROM tourism_directory_records td WHERE td.id=ANY(%s) AND td.place_id=pl.id AND td.image_source_url IS NOT NULL AND pl.image_source_url=td.image_source_url) THEN NULL ELSE pl.image_source_name END,
                    image_license=CASE WHEN EXISTS (SELECT 1 FROM tourism_directory_records td WHERE td.id=ANY(%s) AND td.place_id=pl.id AND td.image_source_url IS NOT NULL AND pl.image_source_url=td.image_source_url) THEN NULL ELSE pl.image_license END,
                    image_checked_at=CASE WHEN EXISTS (SELECT 1 FROM tourism_directory_records td WHERE td.id=ANY(%s) AND td.place_id=pl.id AND td.image_source_url IS NOT NULL AND pl.image_source_url=td.image_source_url) THEN NULL ELSE pl.image_checked_at END,
                    price_min=CASE WHEN EXISTS (SELECT 1 FROM tourism_directory_records td WHERE td.id=ANY(%s) AND td.place_id=pl.id AND td.price_source_url IS NOT NULL AND pl.price_source_url=td.price_source_url) THEN NULL ELSE pl.price_min END,
                    price_max=CASE WHEN EXISTS (SELECT 1 FROM tourism_directory_records td WHERE td.id=ANY(%s) AND td.place_id=pl.id AND td.price_source_url IS NOT NULL AND pl.price_source_url=td.price_source_url) THEN NULL ELSE pl.price_max END,
                    price_source_url=CASE WHEN EXISTS (SELECT 1 FROM tourism_directory_records td WHERE td.id=ANY(%s) AND td.place_id=pl.id AND td.price_source_url IS NOT NULL AND pl.price_source_url=td.price_source_url) THEN NULL ELSE pl.price_source_url END,
                    price_checked_at=CASE WHEN EXISTS (SELECT 1 FROM tourism_directory_records td WHERE td.id=ANY(%s) AND td.place_id=pl.id AND td.price_source_url IS NOT NULL AND pl.price_source_url=td.price_source_url) THEN NULL ELSE pl.price_checked_at END,
                    price_kind=CASE WHEN EXISTS (SELECT 1 FROM tourism_directory_records td WHERE td.id=ANY(%s) AND td.place_id=pl.id AND td.price_source_url IS NOT NULL AND pl.price_source_url=td.price_source_url) THEN NULL ELSE pl.price_kind END,
                    price_source=CASE WHEN EXISTS (SELECT 1 FROM tourism_directory_records td WHERE td.id=ANY(%s) AND td.place_id=pl.id AND td.price_source_url IS NOT NULL AND pl.price_source_url=td.price_source_url) THEN NULL ELSE pl.price_source END,
                    currency=CASE WHEN EXISTS (SELECT 1 FROM tourism_directory_records td WHERE td.id=ANY(%s) AND td.place_id=pl.id AND td.price_source_url IS NOT NULL AND pl.price_source_url=td.price_source_url) THEN NULL ELSE pl.currency END,
                    updated_at=CURRENT_TIMESTAMP
                FROM bad_places bp
                WHERE pl.id=bp.place_id""", (mismatched, *([mismatched] * 15)))
            cur.execute("UPDATE tourism_directory_records SET place_id=NULL WHERE id=ANY(%s)", (mismatched,))
        conn.commit()
    return len(mismatched)


def clear_nonstay_hotel_prices(conn):
    """Quarantine accommodation prices found on restaurant/service pages."""
    bad_path = r"(dining|restaurant|menu|spa|massage|treatment|pool|fitness|gym|transport|meeting|banquet)"
    with conn.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS tourism_price_repair_audit (
            id BIGSERIAL PRIMARY KEY,
            table_name TEXT NOT NULL,
            record_id BIGINT NOT NULL,
            reason TEXT NOT NULL,
            record_snapshot JSONB NOT NULL,
            repaired_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        cur.execute("""INSERT INTO tourism_price_repair_audit (table_name,record_id,reason,record_snapshot)
            SELECT 'places',id,'Accommodation rate came from a non-room service page',to_jsonb(places)
            FROM places WHERE category='Lưu trú' AND price_kind='published_offer'
              AND price_source_url ~* %s""", (bad_path,))
        cur.execute("""INSERT INTO tourism_price_repair_audit (table_name,record_id,reason,record_snapshot)
            SELECT 'tourism_directory_records',id,'Accommodation rate came from a non-room service page',to_jsonb(tourism_directory_records)
            FROM tourism_directory_records WHERE category='Lưu trú' AND price_kind='published_offer'
              AND price_source_url ~* %s""", (bad_path,))
        cur.execute("""UPDATE tourism_directory_records SET price_min=NULL,price_max=NULL,currency=NULL,
            price_source_url=NULL,price_checked_at=NULL,price_kind=NULL
            WHERE category='Lưu trú' AND price_kind='published_offer' AND price_source_url ~* %s""", (bad_path,))
        directory_cleared = cur.rowcount
        cur.execute("""UPDATE places SET price_min=NULL,price_max=NULL,currency=NULL,price_source=NULL,
            price_source_url=NULL,price_checked_at=NULL,price_kind=NULL,updated_at=CURRENT_TIMESTAMP
            WHERE category='Lưu trú' AND price_kind='published_offer' AND price_source_url ~* %s""", (bad_path,))
        places_cleared = cur.rowcount
        # Metadata may have been mirrored to duplicate POIs at the same
        # building before a bad directory link was removed. Use the archived
        # exact source/name/category/location to clean only those copies.
        cur.execute("""INSERT INTO tourism_price_repair_audit (table_name,record_id,reason,record_snapshot)
            SELECT 'places',pl.id,'Metadata mirrored from an audited mismatched hotel listing',to_jsonb(pl)
            FROM places pl JOIN tourism_directory_link_repair_audit a
              ON pl.name=a.place_snapshot->>'name'
             AND pl.category=a.place_snapshot->>'category'
             AND pl.province_id=(a.place_snapshot->>'province_id')::bigint
             AND ABS(pl.latitude-(a.place_snapshot->>'latitude')::double precision)<0.001
             AND ABS(pl.longitude-(a.place_snapshot->>'longitude')::double precision)<0.001
             AND pl.price_source_url=a.directory_snapshot->>'price_source_url'
            WHERE a.directory_snapshot->>'price_source_url' IS NOT NULL
            ON CONFLICT DO NOTHING""")
        cur.execute("""WITH bad_copies AS (
                SELECT DISTINCT a.place_snapshot->>'name' AS place_name,
                       a.place_snapshot->>'category' AS category,
                       (a.place_snapshot->>'province_id')::bigint AS province_id,
                       (a.place_snapshot->>'latitude')::double precision AS latitude,
                       (a.place_snapshot->>'longitude')::double precision AS longitude,
                       a.directory_snapshot->>'price_source_url' AS source_url
                FROM tourism_directory_link_repair_audit a
                WHERE a.directory_snapshot->>'price_source_url' IS NOT NULL
            )
            UPDATE places pl SET price_min=NULL,price_max=NULL,currency=NULL,price_source=NULL,
                price_source_url=NULL,price_checked_at=NULL,price_kind=NULL,updated_at=CURRENT_TIMESTAMP
            FROM bad_copies b
            WHERE pl.name=b.place_name AND pl.category=b.category AND pl.province_id=b.province_id
              AND ABS(pl.latitude-b.latitude)<0.001 AND ABS(pl.longitude-b.longitude)<0.001
              AND pl.price_source_url=b.source_url""")
        places_cleared += cur.rowcount
    conn.commit()
    return places_cleared, directory_cleared


def mirror_place_metadata(conn, place_id, fields):
    """Copy verified sources across exact-name duplicate POIs at one building."""
    if not place_id:
        return
    with conn.cursor() as cur:
        cur.execute("""SELECT name, category, province_id, latitude, longitude
            FROM places WHERE id=%s""", (place_id,))
        row = cur.fetchone()
        if not row:
            return
        name, category, province_id, lat, lon = row
        cur.execute("""UPDATE places SET
                image_url=CASE WHEN image_url LIKE '/media/places/%%' THEN image_url ELSE COALESCE(%s,image_url) END,
                image_source_url=CASE WHEN image_url LIKE '/media/places/%%' THEN image_source_url ELSE COALESCE(%s,image_source_url) END,
                image_source_name=CASE WHEN image_url LIKE '/media/places/%%' THEN image_source_name ELSE COALESCE(%s,image_source_name) END,
                image_license=CASE WHEN image_url LIKE '/media/places/%%' THEN image_license ELSE COALESCE(%s,image_license) END,
                image_checked_at=CASE WHEN image_url LIKE '/media/places/%%' THEN image_checked_at ELSE COALESCE(%s,image_checked_at) END,
                price_min=COALESCE(%s,price_min), price_max=COALESCE(%s,price_max),
                price_source_url=COALESCE(%s,price_source_url),
                price_checked_at=COALESCE(%s,price_checked_at), price_kind=COALESCE(%s,price_kind),
                currency=COALESCE(%s,currency), updated_at=CURRENT_TIMESTAMP
            WHERE name=%s AND category=%s AND province_id=%s AND id<>%s
              AND ABS(latitude-%s)<0.001 AND ABS(longitude-%s)<0.001""",
            (*fields, name, category, province_id, place_id, lat, lon))


def ensure_schema(conn):
    """Idempotent additive migration for existing app databases."""
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
        cur.execute("CREATE INDEX IF NOT EXISTS idx_directory_missing_public_prices ON tourism_directory_records(id) WHERE price_checked_at IS NULL")
    conn.commit()


def listing_matches_page(name: str, page_url: str, page_text: str, jsonld: list) -> bool:
    """Require a strong venue-name match before accepting an extracted price."""
    tokens = {token for token in _fold_words(name).split()
              if len(token) >= 3 and token not in _NAME_STOPWORDS}
    if not tokens:
        return False
    page_tokens = set(_fold_words(" ".join([page_url, page_text] + [json.dumps(x, ensure_ascii=False) for x in jsonld])).split())
    overlap = tokens & page_tokens
    needed = len(tokens) if len(tokens) <= 2 else max(2, len(tokens) - 1)
    return len(overlap) >= needed


def scan_listing_price(record: tuple, delay: float) -> tuple[list[float], str | None, str]:
    record_id, name, category, address, website = record
    price_category = DIRECTORY_TO_PRICE_CATEGORY.get(category)
    if not price_category or not website:
        return [], None, "skipped"
    candidates: list[tuple[str, list, str]] = []
    if not robots_allow(website):
        return [], None, "blocked_by_robots_or_private_url"
    page = page_jsonld(website)
    if page:
        page_url, jsonld, text, links = page
        candidates.append((page_url, jsonld, text))
        for linked_url in price_page_links(page_url, links, limit=3):
            if delay:
                time.sleep(delay)
            linked = page_jsonld(linked_url)
            if linked:
                linked_url, linked_jsonld, linked_text, _ = linked
                candidates.append((linked_url, linked_jsonld, linked_text))

    for url, jsonld, text in candidates:
        if _has_expired_public_offer(text) or _has_stale_dated_rate_url(url) or _editorial_dated_url(url):
            continue
        if not listing_matches_page(name, url, text, jsonld):
            continue
        prices = explicit_vnd_prices(jsonld, text, price_category, url)
        if prices:
            return prices, url, "published_vnd_price"
    return [], candidates[-1][0] if candidates else website, "no_public_price"


class SocialImageParser(HTMLParser):
    """Collect page identity and image metadata from a first-party venue page."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.images = []
        self.meta = {}
        self.title_parts = []
        self.in_title = False
        self.in_jsonld = False
        self.jsonld_parts = []
        self.jsonld_documents = []

    def handle_starttag(self, tag, attrs):
        attrs = {str(key or "").lower(): str(value or "") for key, value in attrs}
        if tag.lower() == "meta":
            key = (attrs.get("property") or attrs.get("name") or "").lower()
            if key in {"og:image", "og:image:secure_url", "twitter:image", "twitter:image:src"}:
                self.images.append({"url": attrs.get("content", ""), "context": "open_graph"})
            elif key in {"og:title", "og:description", "twitter:title", "description"}:
                self.meta[key] = attrs.get("content", "")
        elif tag.lower() == "link" and "image_src" in attrs.get("rel", "").lower():
            self.images.append({"url": attrs.get("href", ""), "context": "link"})
        elif tag.lower() == "title":
            self.in_title = True
        elif tag.lower() == "script" and "ld+json" in attrs.get("type", "").lower():
            self.in_jsonld = True
            self.jsonld_parts = []
        elif tag.lower() == "img" and attrs.get("src"):
            self.images.append({"url": attrs["src"], "context": "alt", "alt": attrs.get("alt", "")})

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self.in_title = False
        elif tag.lower() == "script" and self.in_jsonld:
            self.in_jsonld = False
            raw = "".join(self.jsonld_parts).strip()
            if raw:
                try:
                    self.jsonld_documents.append(json.loads(raw))
                except (ValueError, TypeError):
                    pass
            self.jsonld_parts = []

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)
        if self.in_jsonld:
            self.jsonld_parts.append(data)


def _image_name_tokens(value: str) -> set[str]:
    return {token for token in _fold_words(value or "").split()
            if len(token) >= 3 and token not in _NAME_STOPWORDS}


def _matches_image_identity(name: str, evidence: str, address: str = "") -> bool:
    """Match alternate captions by venue name and corroborating locality."""
    tokens = _image_name_tokens(name)
    found = set(_fold_words(evidence or "").split())
    overlap = tokens & found
    location_stopwords = {
        "street", "road", "st", "so", "duong", "phuong", "ward", "quan",
        "district", "huyen", "city", "thanh", "pho", "province", "tinh", "xa",
    }
    location_tokens = {token for token in _fold_words(address or "").split()
                       if len(token) >= 2 and token not in location_stopwords}
    location = location_tokens & found
    if len(tokens) >= 2 and tokens.issubset(found):
        return True
    # Accept shortened/alternate names only when the caption also confirms
    # the locality. This avoids requiring an exact spelling while keeping
    # generic city photos from being attached to a particular business.
    if len(tokens) >= 3:
        needed = max(2, (len(tokens) * 2 + 2) // 3)
        return len(overlap) >= needed and bool(location)
    # Do not apply venue-name stop words (such as "Hoi An") to the address:
    # two locality tokens plus a one-word venue name disambiguate hotel pages.
    return len(tokens) == 1 and tokens.issubset(found) and len(location) >= 2


def _jsonld_image_for_venue(value, name: str, address: str = ""):
    if isinstance(value, list):
        for item in value:
            found = _jsonld_image_for_venue(item, name, address)
            if found:
                return found
    elif isinstance(value, dict):
        entity_name = str(value.get("name") or "")
        if entity_name and _matches_image_identity(name, entity_name, address):
            image = value.get("image") or value.get("photo")
            if isinstance(image, str):
                return image
            if isinstance(image, dict):
                return image.get("contentUrl") or image.get("url")
            if isinstance(image, list):
                for item in image:
                    if isinstance(item, str):
                        return item
                    if isinstance(item, dict) and (item.get("contentUrl") or item.get("url")):
                        return item.get("contentUrl") or item.get("url")
        for item in value.values():
            if isinstance(item, (dict, list)):
                found = _jsonld_image_for_venue(item, name, address)
                if found:
                    return found
    return None


class SameHostRedirectHandler(HTTPRedirectHandler):
    """Do not let a directory-listed site redirect the crawler to another host."""
    def __init__(self, original_host):
        super().__init__()
        self.original_host = original_host.casefold().rstrip(".")

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlparse(newurl)
        if (parsed.scheme != "https" or not parsed.hostname
                or parsed.hostname.casefold().rstrip(".") != self.original_host
                or not public_http_url(newurl)):
            raise URLError("رفض إعادة التوجيه إلى مضيف مختلف أو غير عام")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class PublicHttpsRedirectHandler(HTTPRedirectHandler):
    """Allow public HTTPS redirects for search-result pages, never private hosts."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlparse(newurl)
        if (parsed.scheme != "https" or not parsed.hostname
                or not public_http_url(newurl)):
            raise URLError("Gemini image-source page redirected to a non-public URL")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _read_public_image_source_page(page_url: str, venue_name: str, address: str = "",
                                  official_website: str = ""):
    """Extract only venue-specific JSON-LD photos or images with matching alt text."""
    parsed = urlparse((page_url or "").strip())
    if (parsed.scheme != "https" or not parsed.hostname or not public_http_url(page_url)):
        return None
    request = Request(page_url, headers={
        "User-Agent": "DoAnTriTueNhanTAO/1.0 (academic travel planner; public image metadata)",
        "Accept": "text/html,application/xhtml+xml",
    })
    try:
        opener = build_opener(PublicHttpsRedirectHandler())
        with opener.open(request, timeout=15) as response:
            final_url = response.geturl()
            final = urlparse(final_url)
            if (final.scheme != "https" or not final.hostname
                    or not public_http_url(final_url)):
                return None
            content_type = response.headers.get("Content-Type", "").lower()
            if "html" not in content_type and "xhtml" not in content_type:
                return None
            html = response.read(1_500_000).decode("utf-8", "replace")
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, socket.timeout):
        return None

    parser = SocialImageParser()
    parser.feed(html)
    page_identity = " ".join([
        " ".join(parser.title_parts), parser.meta.get("og:title", ""),
        parser.meta.get("twitter:title", ""), parser.meta.get("og:description", ""),
        parser.meta.get("description", ""),
    ])
    # A matching search result/page title must corroborate the venue; a city-only
    # directory page is not enough to associate its lead image with a business.
    if not _matches_image_identity(venue_name, page_identity, address):
        return None

    candidates = []
    for document in parser.jsonld_documents:
        entity_image = _jsonld_image_for_venue(document, venue_name, address)
        if entity_image:
            candidates.append(entity_image)
    official_host = (urlparse(official_website).hostname or "").casefold().rstrip(".")
    source_is_third_party = (final.hostname or "").casefold().rstrip(".") != official_host
    for item in parser.images:
        context = item.get("context")
        caption_matches = _matches_image_identity(venue_name, item.get("alt", ""), address)
        if context == "alt" and caption_matches:
            candidates.append(item.get("url", ""))
        elif context == "open_graph" and source_is_third_party:
            # A search-grounded third-party business page may expose only its
            # hotel/venue hero photo as OG metadata. Require the page itself to
            # match the business and locality; never use this rule on its own site.
            candidates.append(item.get("url", ""))

    blocked_image_tokens = {
        "logo", "brand", "banner", "header", "icon", "map", "placeholder",
        "favicon", "default", "sprite", "avatar", "thumbnail",
    }
    for raw_url in candidates:
        image_url = urljoin(final_url, (raw_url or "").strip())
        image_parsed = urlparse(image_url)
        path_tokens = set(_fold_words(image_parsed.path).split())
        if (image_parsed.scheme == "https" and image_parsed.hostname
                and public_http_url(image_url)
                and not (path_tokens & blocked_image_tokens)):
            return {
                "image_url": image_url,
                "source_url": final_url,
                "source_name": f"Ảnh từ trang nguồn · {image_parsed.hostname}",
                "license": None,
            }
    return None


def gemini_image_source_pages(venue_name: str, address: str = ""):
    """Discover candidate venue pages with Gemini's grounded Google Search.

    Search results supply source pages, not proof that a photo depicts the venue. Each
    page is fetched and its own structured data/image alt text is checked before
    an image can be used. Calls are opt-in and capped by collect_images.
    """
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return []
    model = (
        os.getenv("GEMINI_SEARCH_MODEL")
        or os.getenv("GEMINI_MODEL")
        or "gemini-3.8-flash"
    ).strip()
    prompt = (
        "Search the web for public pages about this exact tourism business that contain "
        "its own photos. Do not invent businesses, pages, or image URLs. "
        f"Business: {venue_name}. Address: {address}. Location: Vietnam."
    )
    body = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1000},
    }).encode("utf-8")
    request = Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=body, headers={
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
        "User-Agent": "DoAnTriTueNhanTAO/1.0",
    }, method="POST")
    try:
        with urlopen(request, timeout=35) as response:
            payload = json.loads(response.read(2_000_000).decode("utf-8", "replace"))
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, socket.timeout) as exc:
        status = getattr(exc, "code", None)
        if isinstance(exc, HTTPError):
            try:
                error_data = json.loads(exc.read(64_000).decode("utf-8", "replace"))
                error_message = str((error_data.get("error") or {}).get("message") or exc.reason)
            except (ValueError, AttributeError):
                error_message = str(exc.reason)
        else:
            error_message = str(exc)
        print(f"[Gemini ảnh] tìm nguồn thất bại ({status or type(exc).__name__}) cho {venue_name}: {error_message[:150]}",
              file=sys.stderr, flush=True)
        return []

    sources = []
    for candidate in payload.get("candidates", []):
        grounding = candidate.get("groundingMetadata") or {}
        source_urls = [
            (chunk.get("web") or {}).get("uri", "")
            for chunk in grounding.get("groundingChunks", [])
            if isinstance(chunk, dict)
        ]
        for source_url in source_urls:
            source_url = str(source_url).strip()
            parsed = urlparse(source_url)
            if (parsed.scheme == "https" and parsed.hostname
                    and public_http_url(source_url)
                    and source_url not in sources):
                sources.append(source_url)
    return sources[:6]


def _reserve_gemini_image_search(daily_limit: int) -> bool:
    """Keep optional Gemini web-search calls bounded across batches."""
    global _GEMINI_SEARCH_DAY, _GEMINI_SEARCHES_TODAY
    today = datetime.now(timezone.utc).date()
    if today != _GEMINI_SEARCH_DAY:
        _GEMINI_SEARCH_DAY = today
        _GEMINI_SEARCHES_TODAY = 0
    if _GEMINI_SEARCHES_TODAY >= daily_limit:
        return False
    _GEMINI_SEARCHES_TODAY += 1
    return True


def _first_public_https_url(value: str) -> str | None:
    """Extract one valid HTTPS URL from messy directory fields."""
    for candidate in re.findall(r"https?://[^\s<>\"']+", str(value or ""), re.I):
        candidate = candidate.rstrip(".,;)")
        parsed = urlparse(candidate)
        if (parsed.scheme == "https" and parsed.hostname
                and not re.search(r"[\x00-\x20]", parsed.path)
                and public_http_url(candidate)):
            return candidate
    return None


def official_website_image(website: str, venue_name: str, address: str = ""):
    """Read a first-party preview image from a venue website's HTML metadata.

    Attribute the image to its registered website. No open license is asserted;
    callers persist image_license as NULL.
    """
    website = _first_public_https_url(website)
    if not website:
        return None
    parsed = urlparse(website)
    if not parsed.hostname or not robots_allow(website):
        return None
    request = Request(website, headers={
        "User-Agent": "DoAnTriTueNhanTao/1.0 (academic travel planner; public preview image)",
        "Accept": "text/html,application/xhtml+xml",
    })
    opener = build_opener(SameHostRedirectHandler(parsed.hostname))
    try:
        with opener.open(request, timeout=15) as response:
            final_url = response.geturl()
            final = urlparse(final_url)
            if (final.scheme != "https" or not final.hostname
                    or final.hostname.casefold().rstrip(".") != parsed.hostname.casefold().rstrip(".")
                    or not public_http_url(final_url)):
                return None
            content_type = response.headers.get("Content-Type", "").lower()
            if "html" not in content_type and "xhtml" not in content_type:
                return None
            html = response.read(1_500_000).decode("utf-8", "replace")
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, socket.timeout):
        return None

    parser = SocialImageParser()
    parser.feed(html)
    page_identity = " ".join([
        " ".join(parser.title_parts), parser.meta.get("og:title", ""),
        parser.meta.get("twitter:title", ""), parser.meta.get("og:description", ""),
        parser.meta.get("description", ""),
    ])
    candidate_urls = []
    for document in parser.jsonld_documents:
        entity_image = _jsonld_image_for_venue(document, venue_name, address)
        if entity_image:
            candidate_urls.append(entity_image)
    for item in parser.images:
        raw = item.get("url", "").strip()
        # Do not accept OpenGraph/link preview art just because the page belongs
        # to the hotel. Those are often logos or site-wide banners (the bug seen
        # on Sincerity Hotel). Require a matching venue caption or venue JSON-LD.
        if (item.get("context") == "alt"
                and _matches_image_identity(venue_name, item.get("alt", ""), address)):
            candidate_urls.append(raw)
    blocked_image_tokens = {
        "logo", "brand", "banner", "header", "icon", "map", "placeholder",
        "favicon", "default", "sprite", "avatar", "thumbnail",
    }
    for raw_url in candidate_urls:
        image_url = urljoin(final_url, raw_url.strip())
        image_parsed = urlparse(image_url)
        if (image_parsed.scheme == "https" and image_parsed.hostname
                and public_http_url(image_url)):
            image_path = set(_fold_words(image_parsed.path).split())
            if image_path & blocked_image_tokens:
                continue
            return {
                "image_url": image_url,
                "source_url": final_url,
                "source_name": "Website chính thức của cơ sở",
                "license": None,
            }
    return None


class _MetadataText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def commons_image(venue_name: str, address: str = ""):
    """Find an openly licensed Commons image whose file metadata names the venue."""
    name_tokens = _image_name_tokens(venue_name)
    if not name_tokens:
        return None
    query = " ".join([venue_name, address]).strip()
    headers = {"User-Agent": "AI-Smart-Travel/1.0 (academic project; public image lookup)",
               "Accept": "application/json"}
    try:
        search_params = urlencode({
            "action": "query", "list": "search", "srsearch": query,
            "srnamespace": "6", "srlimit": "8", "format": "json",
        })
        search_request = Request("https://commons.wikimedia.org/w/api.php?" + search_params,
                                 headers=headers)
        with urlopen(search_request, timeout=20) as response:
            search_payload = json.loads(response.read(1_000_000).decode("utf-8", "replace"))
        titles = [item.get("title", "") for item in search_payload.get("query", {}).get("search", [])
                  if item.get("title")]
        if not titles:
            return None
        image_params = urlencode({
            "action": "query", "prop": "imageinfo", "titles": "|".join(titles),
            "iiprop": "url|extmetadata", "iiurlwidth": "1200", "format": "json",
        })
        image_request = Request("https://commons.wikimedia.org/w/api.php?" + image_params,
                                headers=headers)
        with urlopen(image_request, timeout=20) as response:
            payload = json.loads(response.read(2_000_000).decode("utf-8", "replace"))
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, socket.timeout):
        return None
    pages = payload.get("query", {}).get("pages", [])
    if isinstance(pages, dict):
        pages = list(pages.values())
    for page in pages:
        infos = page.get("imageinfo") or []
        if not infos:
            continue
        info = infos[0]
        metadata = info.get("extmetadata") or {}
        description_html = metadata.get("ImageDescription", {}).get("value", "")
        text_parser = _MetadataText()
        text_parser.feed(unescape(str(description_html)))
        evidence = " ".join([page.get("title", ""), " ".join(text_parser.parts)])
        if not _matches_image_identity(venue_name, evidence, address):
            continue
        folded_file = _fold_words(page.get("title", ""))
        if any(term in folded_file for term in ("logo", "icon", "banner", "map", "flag")):
            continue
        license_name = str(metadata.get("LicenseShortName", {}).get("value", "")).strip()
        image_url = info.get("thumburl") or info.get("url")
        file_page = info.get("descriptionurl")
        if not license_name or not image_url or not file_page:
            continue
        parsed_image = urlparse(image_url)
        if parsed_image.scheme != "https" or not parsed_image.hostname or not public_http_url(image_url):
            continue
        return {
            "image_url": image_url,
            "source_url": file_page,
            "source_name": f"Wikimedia Commons · {license_name}",
            "license": license_name,
        }
    return None


def commons_category_image(category: str, address: str = "", used_sources=None):
    """Find a distinct openly licensed photo suitable for a place category.

    This is a fallback for the class-project demo when no venue-specific image
    exists. It searches by category and locality, records Commons attribution,
    and never reuses a source page already assigned to another record.
    """
    folded_category = _fold_words(category)
    if any(term in folded_category for term in ("luu tru", "khach san", "hotel", "resort", "homestay")):
        topics = ("hotel", "resort")
    elif any(term in folded_category for term in ("cafe", "ca phe", "coffee")):
        topics = ("coffee shop", "cafe")
    elif any(term in folded_category for term in ("an uong", "nha hang", "restaurant", "food")):
        topics = ("restaurant", "Vietnamese food")
    elif any(term in folded_category for term in ("mua sam", "sieu thi", "shopping", "market")):
        topics = ("market", "shopping mall")
    elif any(term in folded_category for term in ("thien nhien", "nature", "bien", "beach")):
        topics = ("Vietnam landscape", "beach Vietnam")
    else:
        topics = ("tourist attraction Vietnam", "Vietnam landmark")

    locality_parts = [part.strip() for part in (address or "").split(",") if part.strip()]
    locality = locality_parts[-1] if locality_parts else "Vietnam"
    queries = list(dict.fromkeys(f"{topic} {locality}" for topic in topics))
    used_sources = used_sources if used_sources is not None else set()
    headers = {"User-Agent": "AI-Smart-Travel/1.0 (academic travel itinerary; public image lookup)",
               "Accept": "application/json"}

    for query in queries:
        try:
            search_params = urlencode({
                "action": "query", "list": "search", "srsearch": query,
                "srnamespace": "6", "srlimit": "20", "format": "json",
            })
            request = Request("https://commons.wikimedia.org/w/api.php?" + search_params,
                              headers=headers)
            with urlopen(request, timeout=20) as response:
                search_payload = json.loads(response.read(1_000_000).decode("utf-8", "replace"))
            titles = [item.get("title", "") for item in search_payload.get("query", {}).get("search", [])
                      if item.get("title")]
            if not titles:
                continue
            image_params = urlencode({
                "action": "query", "prop": "imageinfo", "titles": "|".join(titles),
                "iiprop": "url|extmetadata", "iiurlwidth": "1200", "format": "json",
            })
            image_request = Request("https://commons.wikimedia.org/w/api.php?" + image_params,
                                    headers=headers)
            with urlopen(image_request, timeout=20) as response:
                payload = json.loads(response.read(2_000_000).decode("utf-8", "replace"))
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, socket.timeout):
            continue

        pages = payload.get("query", {}).get("pages", [])
        if isinstance(pages, dict):
            pages = list(pages.values())
        for page in pages:
            title = str(page.get("title", ""))
            folded_title = _fold_words(title)
            if any(term in folded_title for term in ("logo", "icon", "banner", "map", "flag", "poster")):
                continue
            infos = page.get("imageinfo") or []
            if not infos:
                continue
            info = infos[0]
            metadata = info.get("extmetadata") or {}
            license_name = str(metadata.get("LicenseShortName", {}).get("value", "")).strip()
            image_url = info.get("thumburl") or info.get("url")
            file_page = info.get("descriptionurl")
            if not license_name or not image_url or not file_page or file_page in used_sources:
                continue
            parsed_image = urlparse(image_url)
            if parsed_image.scheme != "https" or not parsed_image.hostname or not public_http_url(image_url):
                continue
            used_sources.add(file_page)
            return {
                "image_url": image_url,
                "source_url": file_page,
                "source_name": f"Wikimedia Commons · {license_name}",
                "license": license_name,
            }
    return None


def collect_images(conn, limit: int, delay: float, refresh_all: bool = False,
                   name_filter: str = "", location_filter: str = ""):
    if limit <= 0:
        return 0, 0
    # Escape the LIKE wildcard for psycopg2 because this query also uses %s
    # parameters; psycopg2 turns %% back into one SQL wildcard.
    missing_local_image = "(image_url IS NULL OR image_url NOT LIKE '/media/places/%%')"
    # Do not retry a missing photo immediately after a failed fetch. Remember
    # negative results for 90 days so bounded batches can advance through the
    # national catalog instead of getting stuck on the same unreachable sites.
    freshness = "TRUE" if refresh_all else "(image_checked_at IS NULL OR image_checked_at < NOW() - INTERVAL '90 days')"
    with conn.cursor() as cur:
        cur.execute(f"""WITH candidates AS (
              SELECT 'directory'::text AS target_type, td.id, td.name,
                     concat_ws(', ', td.address, p.province) AS location_text, td.place_id,
                     td.website, td.provider_category, td.category AS venue_category,
                     td.image_url, td.image_checked_at, td.price_kind, td.price_checked_at,
                     td.price_min, td.price_max, td.price_source_url,
                     (td.place_id IS NOT NULL)::int AS linked, 0::int AS popularity
              FROM tourism_directory_records td LEFT JOIN places p ON p.id=td.place_id
              UNION ALL
              SELECT 'place'::text AS target_type, pl.id, pl.name,
                     concat_ws(', ', pl.address, pl.ward, pl.district, p.name) AS location_text,
                     pl.id AS place_id, COALESCE(NULLIF(directory.website,''), NULLIF(pl.website,'')) AS website,
                     CASE WHEN pl.category='Lưu trú' THEN 'cslt' ELSE 'place' END AS provider_category,
                     pl.category AS venue_category, pl.image_url, pl.image_checked_at,
                     pl.price_kind, pl.price_checked_at, pl.price_min, pl.price_max,
                     pl.price_source_url, 1 AS linked, COALESCE(pl.review_count,0) AS popularity
              FROM places pl
              LEFT JOIN provinces p ON p.id=pl.province_id
              LEFT JOIN LATERAL (
                  SELECT website FROM tourism_directory_records
                  WHERE place_id=pl.id AND NULLIF(BTRIM(website),'') IS NOT NULL
                  ORDER BY last_seen_at DESC, id DESC LIMIT 1
              ) directory ON TRUE
              WHERE pl.category IN ('Lưu trú','Ăn uống','Cafe','Tham quan','Vui chơi','Giải trí','Thiên nhiên','Mua sắm')
            ), ranked AS (
              SELECT *, row_number() OVER (
                  PARTITION BY venue_category
                  ORDER BY CASE WHEN provider_category='cslt' AND price_kind='published_offer'
                                     AND price_source_url IS NOT NULL
                                     AND price_checked_at >= NOW()-INTERVAL '45 days'
                                     AND (price_min IS NOT NULL OR price_max IS NOT NULL) THEN 0
                                WHEN provider_category='cslt' THEN 1
                                WHEN price_kind='published_offer' AND price_source_url IS NOT NULL
                                     AND price_checked_at >= NOW()-INTERVAL '45 days'
                                     AND (price_min IS NOT NULL OR price_max IS NOT NULL) THEN 2
                                ELSE 3 END,
                           linked DESC, popularity DESC, image_checked_at NULLS FIRST, id
              ) AS category_rank
              FROM candidates
              WHERE {missing_local_image} AND {freshness}
                AND (%s = '' OR name ILIKE %s)
                AND (%s = '' OR location_text ILIKE %s)
            )
            SELECT target_type, id, name, location_text, place_id, website, provider_category, venue_category
            FROM ranked
            ORDER BY category_rank, linked DESC, popularity DESC,
                     CASE WHEN provider_category='cslt' THEN 0
                          WHEN target_type='place' AND provider_category='place' THEN 1
                          ELSE 2 END,
                     id
            LIMIT %s""", (name_filter, f"%{name_filter}%",
                         location_filter, f"%{location_filter}%", limit))
        records = cur.fetchall()
    saved = 0
    with conn.cursor() as cur:
        cur.execute("""SELECT image_source_url FROM places WHERE image_source_url IS NOT NULL
                       UNION SELECT image_source_url FROM tourism_directory_records
                       WHERE image_source_url IS NOT NULL""")
        used_image_sources = {row[0] for row in cur.fetchall() if row[0]}
    try:
        gemini_limit = max(0, int(os.getenv("GEMINI_IMAGE_SEARCH_LIMIT", "20")))
    except ValueError:
        gemini_limit = 20
    gemini_enabled = bool(os.getenv("GEMINI_API_KEY", "").strip()) and gemini_limit > 0
    for index, (target_type, record_id, name, address, place_id, website, provider_category, venue_category) in enumerate(records, start=1):
        image = None
        try:
            # Prefer identity-checked first-party images, then a Commons file
            # whose title/description names this venue and whose license is recorded.
            image = official_website_image(website, name, address) if website else None
            if image is None:
                image = commons_image(name, address)
            if image and image.get("source_url") in used_image_sources:
                image = None
            if image is None:
                image = commons_category_image(venue_category, address, used_image_sources)
            if image is None and gemini_enabled and _reserve_gemini_image_search(gemini_limit):
                source_pages = gemini_image_source_pages(name, address)
                for source_page in source_pages:
                    image = _read_public_image_source_page(
                        source_page, name, address, official_website=website or "")
                    if image:
                        print(f"[Gemini ảnh] đã xác minh trang ảnh: {name}", flush=True)
                        break
                if image is None:
                    print(f"[Gemini ảnh] không tìm được ảnh khớp: {name}", flush=True)
            if image:
                local_image = materialize_image(image["image_url"], name)
                if local_image:
                    image["image_url"] = local_image
                    used_image_sources.add(image["source_url"])
                else:
                    print(f"[ảnh {index}/{len(records)}] không tải được ảnh vào kho ứng dụng: {name}", flush=True)
                    image = None
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"[ảnh {index}/{len(records)}] lỗi nguồn {name}: {str(exc)[:150]}", file=sys.stderr, flush=True)
        with conn.cursor() as cur:
            if image and target_type == "directory":
                cur.execute("""UPDATE tourism_directory_records SET image_url=%s, image_source_url=%s,
                    image_source_name=%s, image_license=%s, image_checked_at=NOW() WHERE id=%s""",
                    (image["image_url"], image["source_url"], image["source_name"], image["license"], record_id))
                if place_id:
                    cur.execute("""UPDATE places SET
                        image_url=CASE WHEN image_url LIKE '/media/places/%%' THEN image_url ELSE COALESCE(%s,image_url) END,
                        image_source_url=CASE WHEN image_url LIKE '/media/places/%%' THEN image_source_url ELSE COALESCE(%s,image_source_url) END,
                        image_source_name=CASE WHEN image_url LIKE '/media/places/%%' THEN image_source_name ELSE COALESCE(%s,image_source_name) END,
                        image_license=CASE WHEN image_url LIKE '/media/places/%%' THEN image_license ELSE COALESCE(%s,image_license) END,
                        image_checked_at=NOW(), updated_at=CURRENT_TIMESTAMP
                        WHERE id=%s""", (image["image_url"], image["source_url"], image["source_name"], image["license"], place_id))
                    mirror_place_metadata(conn, place_id, (
                        image["image_url"], image["source_url"], image["source_name"], image["license"],
                        datetime.now(timezone.utc), None, None, None, None, None, None,
                    ))
                saved += 1
                source_note = "giấy phép mở" if image.get("license") else "website cơ sở"
                print(f"[ảnh {index}/{len(records)}] đã lưu ảnh từ {source_note}: {name}", flush=True)
            elif image:
                cur.execute("""UPDATE places SET
                    image_url=CASE WHEN image_url LIKE '/media/places/%%' THEN image_url ELSE COALESCE(%s,image_url) END,
                    image_source_url=CASE WHEN image_url LIKE '/media/places/%%' THEN image_source_url ELSE COALESCE(%s,image_source_url) END,
                    image_source_name=CASE WHEN image_url LIKE '/media/places/%%' THEN image_source_name ELSE COALESCE(%s,image_source_name) END,
                    image_license=CASE WHEN image_url LIKE '/media/places/%%' THEN image_license ELSE COALESCE(%s,image_license) END,
                    image_checked_at=NOW(),
                    updated_at=CURRENT_TIMESTAMP WHERE id=%s""",
                    (image["image_url"], image["source_url"], image["source_name"], image["license"], place_id))
                mirror_place_metadata(conn, place_id, (
                    image["image_url"], image["source_url"], image["source_name"], image["license"],
                    datetime.now(timezone.utc), None, None, None, None, None, None,
                ))
                saved += 1
                source_note = "giấy phép mở" if image.get("license") else "website cơ sở"
                print(f"[ảnh {index}/{len(records)}] đã lưu ảnh từ {source_note}: {name}", flush=True)
            else:
                table = "tourism_directory_records" if target_type == "directory" else "places"
                cur.execute(f"UPDATE {table} SET image_checked_at=NOW() WHERE id=%s", (record_id,))
        conn.commit()
        if index < len(records) and delay:
            time.sleep(delay)
    return len(records), saved


def collect_prices(conn, limit: int, delay: float, refresh_all: bool):
    if limit <= 0:
        return 0, 0
    freshness = "TRUE" if refresh_all else "(td.price_checked_at IS NULL OR td.price_checked_at < NOW() - INTERVAL '30 days')"
    with conn.cursor() as cur:
        # `places` also has price_kind/source columns. Qualify every field in
        # ORDER BY so this query remains valid after joining the venue table.
        cur.execute(f"""WITH candidates AS (
              SELECT td.id, td.name, td.category, td.address, td.website, td.place_id,
                     td.provider_category, td.price_kind, td.price_source_url,
                     td.price_checked_at, td.price_min, td.price_max,
                     td.last_seen_at,
                     CASE WHEN td.place_id IS NOT NULL THEN 0 ELSE 1 END AS link_priority,
                     CASE WHEN td.provider_category='cslt' AND td.price_kind='published_offer'
                                    AND td.price_source_url IS NOT NULL
                                    AND td.price_checked_at >= NOW()-INTERVAL '45 days'
                                    AND (td.price_min IS NOT NULL OR td.price_max IS NOT NULL) THEN 0
                          WHEN td.provider_category='cslt' THEN 1
                          WHEN td.price_kind='published_offer' AND td.price_source_url IS NOT NULL
                                    AND td.price_checked_at >= NOW()-INTERVAL '45 days'
                                    AND (td.price_min IS NOT NULL OR td.price_max IS NOT NULL) THEN 2
                          ELSE 3 END AS source_priority
              FROM tourism_directory_records td
              LEFT JOIN places pl ON pl.id=td.place_id
              WHERE NULLIF(BTRIM(td.website),'') IS NOT NULL AND {freshness}
            ), ranked AS (
              SELECT *, row_number() OVER (
                  PARTITION BY category
                  ORDER BY source_priority, link_priority, last_seen_at DESC, id
              ) AS category_rank
              FROM candidates
            )
            SELECT id, name, category, address, website, place_id
            FROM ranked
            ORDER BY category_rank, source_priority, link_priority, last_seen_at DESC, id
            LIMIT %s""", (limit,))
        records = cur.fetchall()
    published = 0
    for index, row in enumerate(records, start=1):
        record_id, name, category, address, website, place_id = row
        try:
            prices, source_url, status = scan_listing_price(row[:5], delay)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            prices, source_url, status = [], None, f"fetch_error: {str(exc)[:300]}"
        checked = datetime.now(timezone.utc)
        with conn.cursor() as cur:
            cur.execute("""UPDATE tourism_directory_records SET
                price_min=%s, price_max=%s, currency=CASE WHEN %s THEN 'VND' ELSE NULL END,
                price_source_url=%s, price_checked_at=%s,
                price_kind=CASE WHEN %s THEN 'published_offer' ELSE NULL END WHERE id=%s""",
                (min(prices) if prices else None, max(prices) if prices else None, bool(prices),
                 source_url if prices else None, checked, bool(prices), record_id))
            if prices and place_id:
                # Publish the same source-verified price to the app POI only
                # when the official listing has an exact place link.
                cur.execute("""UPDATE places SET price_min=%s, price_max=%s, currency='VND',
                    price_source_url=%s, price_checked_at=%s, price_kind='published_offer',
                    price_source='Website chính thức của cơ sở',
                    updated_at=CURRENT_TIMESTAMP WHERE id=%s""",
                    (min(prices), max(prices), source_url, checked, place_id))
                mirror_place_metadata(conn, place_id, (
                    None, None, None, None, None, min(prices), max(prices), source_url,
                    checked, "published_offer", "VND",
                ))
                cur.execute("""UPDATE places SET price_source='Website chính thức của cơ sở'
                    WHERE price_source_url=%s AND price_kind='published_offer'""", (source_url,))
        conn.commit()
        if prices:
            published += 1
            print(f"[giá {index}/{len(records)}] {name}: {min(prices):,.0f}–{max(prices):,.0f} VND", flush=True)
        else:
            print(f"[giá {index}/{len(records)}] {name}: {status}", flush=True)
        if index < len(records) and delay:
            time.sleep(delay)
    return len(records), published


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory-categories", default="dest,area,vcgt,rest,shop,cslt",
                        help="Official directory sections to import")
    parser.add_argument("--directory-delay", type=float, default=1.5)
    parser.add_argument("--directory-max-pages", type=int)
    parser.add_argument("--province")
    parser.add_argument("--image-limit", type=int, default=250)
    parser.add_argument("--image-delay", type=float, default=2.0)
    parser.add_argument("--refresh-images", action="store_true",
                        help="Recheck existing no-image results immediately")
    parser.add_argument("--image-name", default="",
                        help="Only check image records whose venue name contains this text")
    parser.add_argument("--image-location", default="",
                        help="Only check photo candidates whose public address contains this location")
    parser.add_argument("--price-limit", type=int, default=100)
    parser.add_argument("--site-delay", type=float, default=4.0)
    parser.add_argument("--refresh-prices", action="store_true")
    parser.add_argument("--skip-directory", action="store_true")
    parser.add_argument("--skip-images", action="store_true")
    parser.add_argument("--skip-prices", action="store_true")
    args = parser.parse_args()
    categories = [item.strip() for item in args.directory_categories.split(",") if item.strip()]
    unknown = sorted(set(categories) - CATEGORIES.keys())
    if unknown or not categories:
        parser.error(f"Danh mục không hợp lệ: {unknown or categories}")

    conn = psycopg2.connect(**DB)
    try:
        ensure_schema(conn)
        unlinked = unlink_confidently_mismatched_accommodations(conn)
        print(f"Gỡ liên kết khách sạn sai và lưu nhật ký phục hồi: {unlinked}.", flush=True)
        cleared_places, cleared_directory = clear_nonstay_hotel_prices(conn)
        print(f"Gỡ giá khách sạn lấy nhầm từ trang dịch vụ: {cleared_places} địa điểm, {cleared_directory} danh bạ.", flush=True)
        if not args.skip_directory:
            print("Bước 1/3: nhập tên, địa chỉ, website từ danh bạ du lịch chính thức.", flush=True)
            print(crawl_directory(conn, categories, args.directory_delay,
                                  args.directory_max_pages, args.province), flush=True)
        print("Liên kết các khách sạn chỉ khi tên, đường và tỉnh/thành khớp duy nhất.", flush=True)
        print("Số khách sạn đã nối với địa điểm bản đồ:",
              link_unambiguous_accommodations(conn), flush=True)
        if not args.skip_images:
            print("Bước 2/3: tìm và lưu ảnh có nguồn/giấy phép.", flush=True)
            print("Kết quả ảnh (đã kiểm tra, đã lưu):",
                  collect_images(conn, args.image_limit, args.image_delay, args.refresh_images,
                                 args.image_name, args.image_location), flush=True)
        if not args.skip_prices:
            print("Bước 3/3: quét giá VND được công khai trên website từng cơ sở.", flush=True)
            print("Kết quả giá (đã quét, có giá xác minh):",
                  collect_prices(conn, args.price_limit, args.site_delay, args.refresh_prices), flush=True)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
