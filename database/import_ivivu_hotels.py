"""Import Da Nang hotel cards from iVIVU's public listing into crawl tables.

Hotel prices on the listing are package prices (for example flight + breakfast),
so they are kept verbatim in price_text and never converted to nightly rates.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urljoin

import psycopg2
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database.crawll import load_project_env, prepare_certificates

SOURCE_URL = "https://www.ivivu.com/khach-san-da-nang"
USER_AGENT = "TravelPathStudentResearch/1.0 (public hotel listing crawl)"


def connect_db():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "5432")),
        dbname=os.getenv("DB_NAME", "tourism_db"),
        user=os.getenv("DB_USER", "tourism_user"),
        password=os.getenv("DB_PASSWORD", "tourism_password"),
        connect_timeout=8,
    )


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    load_project_env()
    ca_bundle = prepare_certificates()
    try:
        response = requests.get(
            SOURCE_URL,
            headers={"User-Agent": USER_AGENT},
            timeout=(10, 35),
            verify=ca_bundle,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"Không tải được danh sách khách sạn: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    soup = BeautifulSoup(response.content, "html.parser")
    cards = []
    seen_images: set[str] = set()
    for name_node in soup.select(".pdv__hotel--name"):
        card = name_node.find_parent(class_="pdv__content-box")
        if not card:
            continue
        name = name_node.get_text(" ", strip=True)
        address_node = card.select_one(".pdv__location-name")
        price_node = card.select_one(".pdv__badget-text")
        photo = card.find("img")
        if not (name and address_node and price_node and photo):
            continue
        address = address_node.get_text(" ", strip=True)
        package = price_node.get_text(" ", strip=True)
        image_url = urljoin(response.url, photo.get("src") or photo.get("data-src") or "")
        if not image_url or image_url in seen_images:
            continue
        seen_images.add(image_url)
        cards.append((name, address, f"Combo: {package}", image_url))

    if not cards:
        print("Trang không trả về thẻ khách sạn có đủ tên, địa chỉ, giá và ảnh.", file=sys.stderr)
        return 1

    try:
        with connect_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT code, name FROM provinces WHERE name = %s LIMIT 1", ("Đà Nẵng",))
                province = cur.fetchone()
                if not province:
                    print("Không tìm thấy tỉnh Đà Nẵng trong bảng provinces.", file=sys.stderr)
                    return 1
                province_code, province_name = province

                saved = 0
                with_image = 0
                for name, address, package_price, image_source_url in cards:
                    cur.execute("""
                        SELECT image_local_path
                        FROM html_crawled_page_images
                        WHERE source_url = %s AND image_source_url = %s
                        LIMIT 1
                    """, (SOURCE_URL, image_source_url))
                    image_url = cur.fetchone()
                    if not image_url or not image_url[0]:
                        continue
                    local_image = image_url[0]

                    cur.execute("""
                        INSERT INTO html_crawled_places
                            (name, category, province_code, province_name, price_text,
                             description, source_url, address, opening_hours, image_url, image_source_url)
                        VALUES (%s, 'Lưu trú', %s, %s, %s,
                                'Giá combo được giữ nguyên theo nội dung thẻ khách sạn trên trang nguồn.',
                                %s, %s, NULL, %s, %s)
                        ON CONFLICT (source_url, province_code, name) DO UPDATE SET
                            category = EXCLUDED.category,
                            province_name = EXCLUDED.province_name,
                            price_text = EXCLUDED.price_text,
                            description = EXCLUDED.description,
                            address = EXCLUDED.address,
                            image_url = EXCLUDED.image_url,
                            image_source_url = EXCLUDED.image_source_url,
                            crawled_at = NOW()
                        RETURNING id
                    """, (name, province_code, province_name, package_price,
                          SOURCE_URL, address, local_image, image_source_url))
                    source_record_id = cur.fetchone()[0]
                    normalized_name = "".join(ch for ch in name.casefold() if ch.isalnum())
                    cur.execute("""
                        INSERT INTO place_crawl_staging (
                            province_code, province_name, name, normalized_name, category,
                            price_text, address, image_url, image_source_url, image_source_name,
                            source_urls, source_record_ids, missing_fields, status
                        ) VALUES (
                            %s, %s, %s, %s, 'Lưu trú', %s, %s, %s, %s, 'iVIVU',
                            ARRAY[%s], ARRAY[%s], ARRAY['coordinates', 'opening_hours'], 'pending_enrichment'
                        )
                        ON CONFLICT (province_code, normalized_name) DO UPDATE SET
                            name = EXCLUDED.name,
                            category = EXCLUDED.category,
                            price_text = EXCLUDED.price_text,
                            address = EXCLUDED.address,
                            image_url = EXCLUDED.image_url,
                            image_source_url = EXCLUDED.image_source_url,
                            image_source_name = EXCLUDED.image_source_name,
                            source_urls = ARRAY(SELECT DISTINCT unnest(place_crawl_staging.source_urls || EXCLUDED.source_urls)),
                            source_record_ids = ARRAY(SELECT DISTINCT unnest(place_crawl_staging.source_record_ids || EXCLUDED.source_record_ids)),
                            missing_fields = ARRAY['coordinates', 'opening_hours'],
                            status = 'pending_enrichment',
                            crawled_at = NOW(), updated_at = NOW()
                    """, (province_code, province_name, name, normalized_name,
                          package_price, address, local_image, image_source_url,
                          SOURCE_URL, source_record_id))
                    saved += 1
                    with_image += 1

                cur.execute("""
                    SELECT COUNT(*)
                    FROM html_crawled_places
                    WHERE source_url = %s AND category = 'Lưu trú'
                """, (SOURCE_URL,))
                total = cur.fetchone()[0]
    except psycopg2.Error as exc:
        print(f"Lỗi khi lưu khách sạn vào PostgreSQL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(f"Khách sạn lấy được trên trang: {len(cards)}; đã lưu/cập nhật vào DB có ảnh riêng: {saved}.")
    print(f"Tổng thẻ khách sạn của nguồn này trong html_crawled_places: {total}.")
    print("Địa chỉ và giá combo được giữ theo trang; trạng thái chờ tọa độ và giờ nhận/trả phòng.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
