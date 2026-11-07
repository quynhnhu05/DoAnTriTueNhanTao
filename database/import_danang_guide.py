"""Extract non-lodging Da Nang records from the linked public travel guide.

The source article has irregular markup, so this importer uses its section and
subheading structure and copies only address, price, and hours stated in the
surrounding article text. Images are chosen from that page's downloaded image
inventory and assigned uniquely per record.
"""

from __future__ import annotations

import os
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import urljoin, urlparse

import psycopg2
import requests
from bs4 import BeautifulSoup, Tag
from database.price_extraction import backfill_prices

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database.crawll import (  # noqa: E402
    extract_image_candidates,
    load_project_env,
    persist_page_images,
    prepare_certificates,
    download_page_images,
)

SOURCE_URL = "https://inxyz.us/da-nang-travel-guide-2026-cam-nang-du-lich-tu-tuc-tu-a-z/"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"


def db_connect():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"), port=int(os.getenv("DB_PORT", "5432")),
        dbname=os.getenv("DB_NAME", "tourism_db"), user=os.getenv("DB_USER", "tourism_user"),
        password=os.getenv("DB_PASSWORD", "tourism_password"), connect_timeout=10,
    )


def plain(value: str) -> str:
    value = unicodedata.normalize("NFKD", (value or "").casefold().replace("đ", "d"))
    value = "".join(c for c in value if not unicodedata.combining(c))
    return " ".join(re.findall(r"[a-z0-9]+", value))


def norm_name(value: str) -> str:
    value = re.sub(r"^\s*(?:\*\s*)?(?:\d+\s*[.)-]\s*)+", "", value.strip())
    value = re.sub(r"^[\W_]+", "", value, flags=re.UNICODE)
    value = re.sub(r"\s+", " ", value).strip(" :-–—")
    return value


def get_text(node: Tag) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()


def read_entry_content(soup: BeautifulSoup) -> Tag | None:
    return (soup.select_one("article .entry-content") or soup.select_one(".entry-content")
            or soup.select_one("[itemprop='articleBody']") or soup.select_one("main article"))


def section_key(text: str) -> str | None:
    t = plain(text)
    if "khach san" in t or "homestay" in t or "luu tru" in t:
        return "skip"
    if "dia diem tham quan noi bat" in t:
        return "attractions"
    if "am thuc da nang" in t or "an gi ngon" in t:
        return "food"
    if "quan cafe dep" in t or "quan ca phe dep" in t:
        return "cafe"
    if "dia diem chup anh dep" in t:
        return "photos"
    if "top trai nghiem nen thu" in t:
        return "experiences"
    if "le hoi truyen thong" in t:
        return "festivals"
    return None


def infer_subcategory(text: str) -> str | None:
    t = plain(text)
    if any(k in t for k in ("van hoa", "lich su", "tam linh", "cau bieu tuong", "cho noi tieng",
                            "bai bien dep", "bien dep", "lang nghe truyen thong", "thien nhien",
                            "sinh thai", "nghi duong")):
        return "group"
    return None


def collect_blocks(heading: Tag) -> tuple[list[str], list[tuple[str, str]]]:
    """Text and image URLs between a venue heading and the next peer heading."""
    level = int(heading.name[1])
    texts: list[str] = []
    images: list[tuple[str, str]] = []
    for node in heading.find_all_next(True):
        if node is heading:
            continue
        if node.name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            next_level = int(node.name[1])
            if next_level <= level:
                break
        if node.name in {"p", "li"}:
            value = get_text(node)
            if value and value not in texts:
                texts.append(value)
        elif node.name == "img":
            raw = (node.get("data-src") or node.get("data-lazy-src") or node.get("data-original")
                   or node.get("src") or "")
            if raw.startswith("data:") and node.get("data-src"):
                raw = node["data-src"]
            if raw and not raw.startswith("data:"):
                images.append((urljoin(SOURCE_URL, raw), node.get("alt", "")))
    return texts, images


def pull_field(texts: list[str], labels: tuple[str, ...]) -> str | None:
    for text in texts:
        normalized = plain(text)
        for label in labels:
            if normalized.startswith(plain(label)):
                value = re.sub(r"^[^:：]{1,45}[:：]\s*", "", text).strip()
                return value or None
    return None


def clean_description(texts: list[str]) -> str | None:
    useful = []
    for text in texts:
        if plain(text).startswith(("dia chi", "gia ve", "gia tham khao", "gia:", "gio mo cua", "gio hoat dong")):
            continue
        text = re.sub(r"\s+", " ", text)
        if text and text not in useful:
            useful.append(text)
        if sum(map(len, useful)) >= 1500:
            break
    return " ".join(useful)[:3000] or None


def extract_items(root: Tag) -> list[dict[str, str | None]]:
    items: list[dict[str, str | None]] = []
    current_section: str | None = None
    skip_until_next_top = False
    current_group = ""
    seen: set[tuple[str, str]] = set()
    for heading in root.select("h2,h3,h4,h5"):
        level = int(heading.name[1])
        title = get_text(heading)
        if level <= 3:
            new_section = section_key(title)
            current_section = new_section
            skip_until_next_top = new_section == "skip"
            current_group = ""
            continue
        if not current_section or skip_until_next_top:
            continue
        cleaned = norm_name(title)
        if not cleaned or len(cleaned) < 3:
            continue
        if any(marker in plain(title) for marker in ("dac biet le hoi hoa", "thong tin ve tour")):
            continue
        group = infer_subcategory(title)
        if level == 4 and group:
            current_group = cleaned
            continue
        # h5s under sightseeing are named locations; h4s under other sections
        # are named dishes, cafes, photo spots, activities, or festivals.
        if current_section == "attractions" and level != 5:
            continue
        if current_section != "attractions" and level not in (4, 5):
            continue
        if any(word in plain(cleaned) for word in (
            "ngay ", "phuong tien", "gia ve tham quan", "thoi gian", "dia chi", "khach san", "homestay"
        )):
            continue
        key = (current_section, plain(cleaned))
        if key in seen:
            continue
        seen.add(key)
        texts, image_refs = collect_blocks(heading)
        combined = " ".join(texts)
        if current_section == "food":
            category = "Ăn uống"
        elif current_section == "cafe":
            category = "Cafe"
        elif current_section == "experiences":
            category = "Vui chơi"
        elif current_section == "festivals":
            category = "Vui chơi"
        else:
            category = "Tham quan"
        items.append({
            "name": cleaned,
            "category": category,
            "description": clean_description(texts),
            "address": pull_field(texts, ("Địa chỉ", "Địa điểm")),
            "price": pull_field(texts, ("Giá vé tham quan", "Giá vé", "Giá tham khảo", "Giá")),
            "hours": pull_field(texts, ("Giờ mở cửa", "Giờ hoạt động", "Thời gian mở cửa")),
            "combined": combined,
            "image_refs": image_refs,
            "group": current_group or None,
        })

    # The article lists restaurant name, signature dishes, and street address
    # as numbered paragraphs beneath this subsection rather than as headings.
    restaurant_heading = next((h for h in root.select("h2,h3,h4,h5")
                               if "7 quán ăn ở Đà Nẵng" in get_text(h)), None)
    if restaurant_heading:
        for node in restaurant_heading.find_all_next(["p", "li", "h2", "h3", "h4", "h5"], limit=80):
            if node.name in {"h2", "h3", "h4", "h5"} and node is not restaurant_heading:
                break
            text = get_text(node)
            match = re.match(r"^\s*\d+\s*[.)-]\s*(.+?)\s*[–—-]\s*Địa chỉ\s*[:：]\s*(.+?)\s*[.]?\s*$", text, re.IGNORECASE)
            if not match:
                continue
            raw_name, address = match.groups()
            # Keep dish information from parentheses as the venue description.
            name = re.sub(r"\s*\([^)]*\)\s*", " ", raw_name).strip()
            key = ("restaurants", plain(name))
            if key in seen:
                continue
            seen.add(key)
            items.append({
                "name": name,
                "category": "Ăn uống",
                "description": raw_name.strip(),
                "address": address.strip(),
                "price": None,
                "hours": None,
                "combined": text,
                "image_refs": [],
                "group": "Quán ăn được Michelin gợi ý",
            })
    return items


def image_score(item: dict, photo: dict) -> int:
    name_words = set(re.findall(r"[a-z0-9]+", plain(str(item["name"]))))
    photo_text = plain(" ".join(str(photo.get(k) or "") for k in ("alt_text", "title_text", "surrounding_text")))
    photo_words = set(re.findall(r"[a-z0-9]+", photo_text))
    score = len(name_words & photo_words) * 30
    if plain(str(item.get("group") or "")) and plain(str(item["group"])) in photo_text:
        score += 20
    category = str(item["category"])
    terms = {
        "Ăn uống": {"mon", "an", "am", "thuc", "bun", "mi", "banh", "che", "quan", "hai", "san"},
        "Cafe": {"cafe", "coffee", "ca", "phe"},
        "Tham quan": {"cau", "chua", "bao", "tang", "bien", "bai", "son", "tra", "lang", "dao", "ho"},
        "Vui chơi": {"trai", "nghiem", "du", "thuyen", "truc", "thang", "sung", "sup", "luot", "le", "hoi", "cau", "ngu", "phao", "hoa", "an", "hai", "tuy", "loan"},
    }
    score += min(3, len(photo_words & terms.get(category, set()))) * 4
    path = urlparse(str(photo.get("image_source_url") or "")).path.lower()
    if any(k in path for k in ("logo", "banner", "avatar", "icon", "loading", "placeholder", "ad-")):
        score -= 100
    return score


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_project_env()
    ca_bundle = prepare_certificates()
    try:
        response = requests.get(SOURCE_URL, headers={"User-Agent": UA}, timeout=(10, 55), verify=ca_bundle)
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"Không tải được bài hướng dẫn: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    soup = BeautifulSoup(response.content, "html.parser")
    root = read_entry_content(soup)
    if not root:
        print("Không xác định được nội dung chính của bài viết.", file=sys.stderr)
        return 1

    image_candidates = extract_image_candidates(soup, SOURCE_URL)
    downloaded = download_page_images(image_candidates)
    persist_page_images(SOURCE_URL, image_candidates, downloaded)
    items = extract_items(root)

    try:
        with db_connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT code, name FROM provinces WHERE name ILIKE '%Đà Nẵng%' LIMIT 1")
            province = cur.fetchone()
            if not province:
                raise RuntimeError("Không tìm thấy tỉnh Đà Nẵng trong bảng provinces")
            province_code, province_name = province
            cur.execute("""
                SELECT image_source_url, image_local_path, alt_text, title_text, surrounding_text
                FROM html_crawled_page_images
                WHERE source_url=%s AND image_local_path IS NOT NULL ORDER BY id
            """, (SOURCE_URL,))
            photos = [dict(zip(("image_source_url", "image_local_path", "alt_text", "title_text", "surrounding_text"), row))
                      for row in cur.fetchall()]
            photos = [p for p in photos if not any(w in urlparse(p["image_source_url"]).path.lower()
                                                    for w in ("logo", "banner", "avatar", "icon", "loading", "placeholder"))]
            used: set[str] = set()
            saved = 0
            with_image = 0
            for item in items:
                preferred = [ref[0] for ref in item["image_refs"]]
                candidate_pool = [p for p in photos if p["image_source_url"] not in used]
                candidate_pool.sort(key=lambda p: (
                    p["image_source_url"] not in preferred,
                    -image_score(item, p),
                    p["image_source_url"],
                ))
                photo = candidate_pool[0] if candidate_pool else None
                image_url = photo["image_local_path"] if photo else None
                image_source_url = photo["image_source_url"] if photo else None
                if photo:
                    used.add(image_source_url)
                    with_image += 1
                cur.execute("""
                    INSERT INTO html_crawled_places
                        (name,category,province_code,province_name,price_text,description,source_url,address,opening_hours,image_url,image_source_url)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (source_url,province_code,name) DO UPDATE SET
                        category=EXCLUDED.category, province_name=EXCLUDED.province_name,
                        price_text=COALESCE(EXCLUDED.price_text,html_crawled_places.price_text),
                        description=COALESCE(EXCLUDED.description,html_crawled_places.description),
                        address=COALESCE(EXCLUDED.address,html_crawled_places.address),
                        opening_hours=COALESCE(EXCLUDED.opening_hours,html_crawled_places.opening_hours),
                        image_url=COALESCE(EXCLUDED.image_url,html_crawled_places.image_url),
                        image_source_url=COALESCE(EXCLUDED.image_source_url,html_crawled_places.image_source_url), crawled_at=NOW()
                    RETURNING id
                """, (item["name"],item["category"],province_code,province_name,item["price"],item["description"],
                      SOURCE_URL,item["address"],item["hours"],image_url,image_source_url))
                record_id=cur.fetchone()[0]
                missing=[]
                if not item["address"]: missing.append("address")
                if not item["price"]: missing.append("price")
                if not item["hours"]: missing.append("opening_hours")
                if not image_url: missing.append("image")
                missing.append("coordinates")
                normalized=plain(str(item["name"]))
                normalized="".join(re.findall(r"[a-z0-9]+",normalized))
                cur.execute("""
                    INSERT INTO place_crawl_staging
                        (province_code,province_name,name,normalized_name,category,price_text,address,opening_hours,image_url,image_source_url,image_source_name,
                         source_urls,source_record_ids,missing_fields,status)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'inxyz.us',ARRAY[%s],ARRAY[%s],%s,'pending_enrichment')
                    ON CONFLICT (province_code,normalized_name) DO UPDATE SET
                        name=EXCLUDED.name, category=EXCLUDED.category,
                        price_text=COALESCE(EXCLUDED.price_text,place_crawl_staging.price_text),
                        address=COALESCE(EXCLUDED.address,place_crawl_staging.address),
                        opening_hours=COALESCE(EXCLUDED.opening_hours,place_crawl_staging.opening_hours),
                        image_url=COALESCE(EXCLUDED.image_url,place_crawl_staging.image_url),
                        image_source_url=COALESCE(EXCLUDED.image_source_url,place_crawl_staging.image_source_url),
                        source_urls=ARRAY(SELECT DISTINCT unnest(place_crawl_staging.source_urls||EXCLUDED.source_urls)),
                        source_record_ids=ARRAY(SELECT DISTINCT unnest(place_crawl_staging.source_record_ids||EXCLUDED.source_record_ids)),
                        missing_fields=EXCLUDED.missing_fields, status='pending_enrichment', updated_at=NOW()
                """, (province_code,province_name,item["name"],normalized,item["category"],item["price"],item["address"],item["hours"],
                      image_url,image_source_url,SOURCE_URL,record_id,missing))
                saved += 1
            prices_added = backfill_prices(cur, province_code)
            cur.execute("""
                SELECT category, COUNT(*), COUNT(*) FILTER (WHERE image_url IS NOT NULL),
                       COUNT(*) FILTER (WHERE address IS NOT NULL), COUNT(*) FILTER (WHERE price_text IS NOT NULL)
                FROM html_crawled_places WHERE source_url=%s AND province_code=%s GROUP BY category ORDER BY category
            """, (SOURCE_URL,province_code))
            summary=cur.fetchall()
            cur.execute("""
                SELECT COUNT(*) FROM html_crawled_page_images WHERE source_url=%s AND image_local_path IS NOT NULL
            """, (SOURCE_URL,))
            image_total=cur.fetchone()[0]
    except Exception as exc:
        print(f"Lỗi khi nhập dữ liệu bài hướng dẫn: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(f"Đã nhập/cập nhật {saved} mục địa điểm và trải nghiệm, ghép {with_image} ảnh riêng; không lấy khách sạn.")
    print(f"Đã gắn thêm giá vào {prices_added} địa điểm khớp địa chỉ trong nội dung crawl.")
    print(f"Ảnh tải được từ trang: {image_total}.")
    for row in summary:
        print(f"{row[0]}: {row[1]} mục; {row[2]} có ảnh; {row[3]} có địa chỉ; {row[4]} có giá được ghi trên trang.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
