"""Import restaurant listings with address, hours, prices and photos from Zalopay."""
from __future__ import annotations

import argparse
import os
import re
import sys
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urljoin

import psycopg2
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from database.crawll import download_page_images, load_project_env, persist_page_images, prepare_certificates
from database.import_danang_guide import db_connect, plain

SOURCE_URL = "https://zalopay.vn/quan-an-ngon-da-nang-5286"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
SEO_SUFFIXES = ("quan an ngon", "dac san", "du lich", "gia re", "an sang", "an dem", "son tra", "lien chieu", "da nang an gi")


def address_key(value: str | None) -> str:
    if not value:
        return ""
    parts = [part.strip() for part in value.split(",") if part.strip()]
    if not parts:
        return ""
    lead = parts[0]
    if re.fullmatch(r"(?:k|ki[eệ]t|s[oố]\s*)?\d+[a-zA-Z]?(?:/\d+[a-zA-Z]?)*", lead, re.I) and len(parts) > 1:
        lead = f"{lead} {parts[1]}"
    key = plain(lead)
    key = re.sub(r"^(?:k|so)\s+(?=\d)", "", key)
    return key


def parse_entries(root: BeautifulSoup) -> list[dict]:
    headings = [node for node in root.select("h3") if re.match(r"^\s*\d+\s*[.)]", node.get_text(" ", strip=True))]
    entries: list[dict] = []
    seen_images: set[str] = set()
    for heading in headings:
        full_title = re.sub(r"^\s*\d+\s*[.)-]\s*", "", heading.get_text(" ", strip=True)).strip()
        name = full_title
        if " - " in name:
            before, after = name.split(" - ", 1)
            if any(term in plain(after) for term in SEO_SUFFIXES):
                name = before.strip()
        fields: dict[str, str] = {}
        paragraphs: list[str] = []
        image_url = ""
        for node in heading.find_all_next(True):
            if node.name in {"h2", "h3", "h4", "h5", "h6"}:
                break
            if node.name == "li":
                text = node.get_text(" ", strip=True)
                match = re.match(r"\s*(Địa chỉ|Địa điểm|Giờ mở cửa|Giờ hoạt động|Giá tham khảo|Giá)\s*[:：]\s*(.*)", text, re.I)
                if match:
                    key = plain(match.group(1))
                    field = "address" if key in {"dia chi", "dia diem"} else "hours" if key in {"gio mo cua", "gio hoat dong"} else "price"
                    fields[field] = match.group(2).strip()
            elif node.name == "p":
                text = node.get_text(" ", strip=True)
                if text and not text.lower().startswith(("xem thêm", ">>>", "tham khảo thêm")):
                    paragraphs.append(text)
            elif node.name == "img" and not image_url:
                figure = node.find_parent("figure")
                candidate = (node.get("data-src") or node.get("data-lazy-src") or node.get("src") or "").strip()
                candidate = urljoin(SOURCE_URL, candidate)
                if figure and candidate.startswith("https://") and candidate not in seen_images:
                    image_url = candidate
                    seen_images.add(candidate)
        if not name or not fields.get("address"):
            continue
        entries.append({
            "name": name, "full_title": full_title, "category": "Ăn uống",
            "address": fields.get("address"), "opening_hours": fields.get("hours"),
            "price_text": fields.get("price"), "description": " ".join(paragraphs)[:3000] or None,
            "image_source_url": image_url or None,
        })
    return entries


def price_bounds(value: str | None) -> tuple[int | None, int | None]:
    if not value:
        return None, None
    numbers = re.findall(r"\d[\d.,]*", value)
    parsed = [int(re.sub(r"\D", "", item)) for item in numbers]
    parsed = [item for item in parsed if 0 <= item <= 100_000_000]
    if not parsed:
        return None, None
    return (min(parsed), max(parsed)) if len(parsed) > 1 else (parsed[0], parsed[0])


def match_existing(entry: dict, existing: list[dict]) -> dict | None:
    wanted_name = plain(entry["name"])
    wanted_address = address_key(entry.get("address"))
    choices = []
    for row in existing:
        candidate_name = plain(row["name"])
        name_score = SequenceMatcher(None, wanted_name, candidate_name).ratio()
        wanted_words, candidate_words = set(wanted_name.split()), set(candidate_name.split())
        overlap = len(wanted_words & candidate_words) / max(1, min(len(wanted_words), len(candidate_words)))
        candidate_address = address_key(row.get("address"))
        addr_score = SequenceMatcher(None, wanted_address, candidate_address).ratio() if wanted_address and candidate_address else 0
        same_address = bool(wanted_address and candidate_address and (wanted_address == candidate_address or addr_score >= .88))
        if same_address and (name_score >= .35 or overlap >= .45):
            # Prefer the original record when this source was imported before;
            # this allows a rerun to merge its duplicate instead of duplicating it.
            choices.append((int(not row.get("from_zalopay")), name_score + overlap + addr_score, row))
    return max(choices, key=lambda pair: (pair[0], pair[1]))[2] if choices else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Read and summarize source without changing the database or downloading images")
    args = parser.parse_args()
    load_project_env()
    ca_bundle = prepare_certificates()
    response = requests.get(SOURCE_URL, headers={"User-Agent": USER_AGENT}, timeout=(10, 45), verify=ca_bundle)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "html.parser")
    root = soup.select_one("article .entry-content, .entry-content, [itemprop='articleBody']") or soup
    entries = parse_entries(root)
    if args.dry_run:
        print(f"Tìm thấy {len(entries)} quán; {sum(bool(e['price_text']) for e in entries)} có giá, {sum(bool(e['opening_hours']) for e in entries)} có giờ, {sum(bool(e['image_source_url']) for e in entries)} có ảnh.")
        with db_connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT id,name,normalized_name,address,source_urls FROM place_crawl_staging WHERE province_code=(SELECT code FROM provinces WHERE name ILIKE '%Đà Nẵng%' LIMIT 1)")
            existing = [{"id": r[0], "name": r[1], "normalized_name": r[2], "address": r[3], "from_zalopay": SOURCE_URL in (r[4] or [])} for r in cur.fetchall()]
        original_rows = [row for row in existing if not row["from_zalopay"]]
        matches = sum(match_existing(entry, original_rows) is not None for entry in entries)
        print(f"Địa chỉ khớp với {matches} mục hiện có; {len(entries)-matches} mục sẽ được thêm mới.")
        for entry in entries:
            print(f"{entry['name']} | {entry['address']} | {entry['price_text'] or 'chưa có giá'} | {entry['opening_hours'] or 'chưa có giờ'}")
        return 0
    candidates = [
        {"url": entry["image_source_url"], "alt": entry["name"], "title": entry["full_title"],
         "context": f"{entry['name']} | {entry['address']} | {entry['description'] or ''}"}
        for entry in entries if entry["image_source_url"]
    ]
    downloaded = download_page_images(candidates)
    persist_page_images(SOURCE_URL, candidates, downloaded)
    inserted = updated = 0
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT code,name FROM provinces WHERE name ILIKE '%Đà Nẵng%' LIMIT 1")
        province = cur.fetchone()
        if not province:
            raise RuntimeError("Không tìm thấy tỉnh/thành Đà Nẵng")
        province_code, province_name = province
        cur.execute("SELECT id,name,normalized_name,address,source_urls FROM place_crawl_staging WHERE province_code=%s", (province_code,))
        existing = [{"id": r[0], "name": r[1], "normalized_name": r[2], "address": r[3], "from_zalopay": SOURCE_URL in (r[4] or [])} for r in cur.fetchall()]
        used_norms = {row["normalized_name"] for row in existing}
        for entry in entries:
            image_url = downloaded.get(entry["image_source_url"], entry["image_source_url"])
            low, high = price_bounds(entry.get("price_text"))
            name = entry["name"]
            match = match_existing(entry, existing)
            if match:
                norm = match["normalized_name"]
                canonical_name = match["name"]
                updated += 1
            else:
                canonical_name = name
                norm = "".join(re.findall(r"[a-z0-9]+", plain(name)))
                if norm in used_norms:
                    suffix = "".join(re.findall(r"[a-z0-9]+", address_key(entry.get("address"))))
                    norm = f"{norm}{suffix}"
                used_norms.add(norm)
            cur.execute("""
                INSERT INTO html_crawled_places
                    (name,category,province_code,province_name,price_text,description,source_url,address,opening_hours,image_url,image_source_url)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (source_url,province_code,name) DO UPDATE SET
                    price_text=COALESCE(EXCLUDED.price_text,html_crawled_places.price_text),
                    description=COALESCE(EXCLUDED.description,html_crawled_places.description),
                    address=COALESCE(EXCLUDED.address,html_crawled_places.address),
                    opening_hours=COALESCE(EXCLUDED.opening_hours,html_crawled_places.opening_hours),
                    image_url=COALESCE(EXCLUDED.image_url,html_crawled_places.image_url),
                    image_source_url=COALESCE(EXCLUDED.image_source_url,html_crawled_places.image_source_url),
                    crawled_at=NOW()
                RETURNING id
            """, (canonical_name,"Ăn uống",province_code,province_name,entry.get("price_text"),entry.get("description"),SOURCE_URL,
                  entry.get("address"),entry.get("opening_hours"),image_url,entry.get("image_source_url")))
            source_id = cur.fetchone()[0]
            missing = [key for key, value in (("address",entry.get("address")),("price",entry.get("price_text")),
                        ("opening_hours",entry.get("opening_hours")),("image",image_url)) if not value]
            missing.append("coordinates")
            if match:
                cur.execute("""
                    UPDATE place_crawl_staging SET category='Ăn uống',price_text=COALESCE(%s,price_text),
                        price_min=COALESCE(%s,price_min),price_max=COALESCE(%s,price_max),currency=CASE WHEN %s IS NOT NULL THEN 'VND' ELSE currency END,
                        address=COALESCE(%s,address),opening_hours=COALESCE(%s,opening_hours),
                        image_url=COALESCE(%s,image_url),image_source_url=COALESCE(%s,image_source_url),image_source_name='zalopay.vn',
                        source_urls=ARRAY(SELECT DISTINCT unnest(source_urls||ARRAY[%s])),
                        source_record_ids=ARRAY(SELECT DISTINCT unnest(source_record_ids||ARRAY[%s])),
                        missing_fields=ARRAY(SELECT field FROM unnest(missing_fields) AS field WHERE field <> ALL(%s)),updated_at=NOW()
                    WHERE id=%s
                """, (entry.get("price_text"),low,high,entry.get("price_text"),entry.get("address"),entry.get("opening_hours"),
                      image_url,entry.get("image_source_url"),SOURCE_URL,source_id,missing,match["id"]))
            else:
                cur.execute("""
                    INSERT INTO place_crawl_staging
                        (province_code,province_name,name,normalized_name,category,price_text,price_min,price_max,currency,address,opening_hours,image_url,image_source_url,image_source_name,
                         source_urls,source_record_ids,missing_fields,status)
                    VALUES (%s,%s,%s,%s,'Ăn uống',%s,%s,%s,CASE WHEN %s IS NOT NULL THEN 'VND' END,%s,%s,%s,%s,'zalopay.vn',ARRAY[%s],ARRAY[%s],%s,'pending_enrichment')
                    ON CONFLICT (province_code,normalized_name) DO UPDATE SET
                        price_text=COALESCE(EXCLUDED.price_text,place_crawl_staging.price_text),
                        price_min=COALESCE(EXCLUDED.price_min,place_crawl_staging.price_min),price_max=COALESCE(EXCLUDED.price_max,place_crawl_staging.price_max),
                        address=COALESCE(EXCLUDED.address,place_crawl_staging.address),opening_hours=COALESCE(EXCLUDED.opening_hours,place_crawl_staging.opening_hours),
                        image_url=COALESCE(EXCLUDED.image_url,place_crawl_staging.image_url),image_source_url=COALESCE(EXCLUDED.image_source_url,place_crawl_staging.image_source_url),
                        image_source_name=EXCLUDED.image_source_name,source_urls=ARRAY(SELECT DISTINCT unnest(place_crawl_staging.source_urls||EXCLUDED.source_urls)),
                        source_record_ids=ARRAY(SELECT DISTINCT unnest(place_crawl_staging.source_record_ids||EXCLUDED.source_record_ids)),missing_fields=EXCLUDED.missing_fields,updated_at=NOW()
                """, (province_code,province_name,canonical_name,norm,entry.get("price_text"),low,high,entry.get("price_text"),entry.get("address"),
                      entry.get("opening_hours"),image_url,entry.get("image_source_url"),SOURCE_URL,source_id,missing))
                inserted += 1
        cur.execute("SELECT COUNT(*),COUNT(*) FILTER (WHERE price_text IS NOT NULL),COUNT(*) FILTER (WHERE image_url IS NOT NULL) FROM place_crawl_staging WHERE province_code=%s",(province_code,))
        totals=cur.fetchone()
    print(f"Đã nhập {len(entries)} mục Zalopay: {updated} cập nhật địa điểm cũ, {inserted} mục mới.")
    print(f"Đà Nẵng hiện có {totals[0]} mục; {totals[1]} có giá; {totals[2]} có ảnh.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
