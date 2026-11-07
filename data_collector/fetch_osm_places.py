"""Import named Vietnamese tourism/service POIs from OpenStreetMap Overpass.

Fetches one province at a time from locally imported administrative polygons,
waits between requests, and upserts by OSM object type + id. It never invents
prices. Requires the existing gis_provinces boundaries and provinces table.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import psycopg2
from psycopg2.extras import execute_values


DB = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "dbname": os.getenv("DB_NAME", "tourism_db"),
    "user": os.getenv("DB_USER", "tourism_user"),
    "password": os.getenv("DB_PASSWORD", "tourism_password"),
}
OVERPASS = os.getenv("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
USER_AGENT = "DoAnTriTueNhanTao/1.0 (academic POI importer)"
WAIT_SECONDS = 10
LIMIT_TAGS = {
    "amenity": {
        "restaurant": ("Ăn uống", "Nhà hàng"), "fast_food": ("Ăn uống", "Đồ ăn nhanh"),
        "food_court": ("Ăn uống", "Khu ăn uống"), "cafe": ("Cafe", "Quán cafe"),
        "bar": ("Giải trí", "Quán bar"), "pub": ("Giải trí", "Quán pub"),
        "hospital": ("Y tế", "Bệnh viện"), "clinic": ("Y tế", "Phòng khám"),
        "pharmacy": ("Y tế", "Nhà thuốc"), "atm": ("Tài chính", "ATM"),
        "bank": ("Tài chính", "Ngân hàng"), "taxi": ("Di chuyển", "Taxi"),
        "bus_station": ("Di chuyển", "Bến xe"), "fuel": ("Di chuyển", "Trạm xăng"),
        "parking": ("Di chuyển", "Bãi đỗ xe"),
    },
    "tourism": {
        "hotel": ("Lưu trú", "Khách sạn"), "hostel": ("Lưu trú", "Hostel"),
        "guest_house": ("Lưu trú", "Nhà nghỉ"), "motel": ("Lưu trú", "Motel"),
        "resort": ("Lưu trú", "Resort"), "camp_site": ("Lưu trú", "Khu cắm trại"),
        "attraction": ("Tham quan", "Điểm tham quan"), "museum": ("Tham quan", "Bảo tàng"),
        "viewpoint": ("Tham quan", "Điểm ngắm cảnh"), "gallery": ("Tham quan", "Phòng trưng bày"),
        "theme_park": ("Vui chơi", "Công viên giải trí"),
        "zoo": ("Vui chơi", "Vườn thú"), "aquarium": ("Vui chơi", "Thủy cung"),
    },
    "shop": {
        "supermarket": ("Mua sắm", "Siêu thị"), "mall": ("Mua sắm", "Trung tâm thương mại"),
        "department_store": ("Mua sắm", "Cửa hàng bách hóa"), "market": ("Mua sắm", "Chợ"),
    },
    "leisure": {
        "park": ("Vui chơi", "Công viên"), "playground": ("Vui chơi", "Sân chơi"),
        "sports_centre": ("Vui chơi", "Trung tâm thể thao"), "swimming_pool": ("Vui chơi", "Hồ bơi"),
        "fitness_centre": ("Vui chơi", "Phòng tập"), "golf_course": ("Vui chơi", "Sân golf"),
        "nature_reserve": ("Thiên nhiên", "Khu bảo tồn thiên nhiên"),
    },
    "natural": {
        "beach": ("Thiên nhiên", "Bãi biển"), "waterfall": ("Thiên nhiên", "Thác nước"),
        "peak": ("Thiên nhiên", "Đỉnh núi"), "cave_entrance": ("Thiên nhiên", "Hang động"),
        "spring": ("Thiên nhiên", "Suối"),
    },
    "historic": {
        "archaeological_site": ("Tham quan", "Di tích khảo cổ"), "castle": ("Tham quan", "Di tích lịch sử"),
        "fort": ("Tham quan", "Di tích lịch sử"), "monument": ("Tham quan", "Di tích lịch sử"),
        "memorial": ("Tham quan", "Đài tưởng niệm"), "ruins": ("Tham quan", "Di tích lịch sử"),
    },
    "water": {
        "lake": ("Thiên nhiên", "Hồ"), "reservoir": ("Thiên nhiên", "Hồ chứa"),
    },
}
REGEXES = {key: "^(" + "|".join(values.keys()) + ")$" for key, values in LIMIT_TAGS.items()}


def category_for(tags):
    for key, values in LIMIT_TAGS.items():
        value = tags.get(key)
        if value in values:
            return values[value]
    return None


def address_for(tags):
    full = tags.get("addr:full")
    if full:
        return full
    parts = [tags.get("addr:housenumber"), tags.get("addr:street"), tags.get("addr:suburb"),
             tags.get("addr:district"), tags.get("addr:city"), tags.get("addr:province")]
    return ", ".join(str(part).strip() for part in parts if part)


def query_for(bounds):
    selectors = []
    for key, regex in REGEXES.items():
        for element in ("node", "way", "relation"):
            selectors.append("  %s[\"%s\"~\"%s\"](%s);" % (element, key, regex, bounds))
    return "[out:json][timeout:180];\n(\n%s\n);\nout center tags;" % "\n".join(selectors)


def fetch(query):
    payload = ("data=" + query).encode("utf-8")
    last_error = None
    for attempt in range(3):
        request = Request(OVERPASS, data=payload, headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "User-Agent": USER_AGENT,
        })
        try:
            with urlopen(request, timeout=240) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            last_error = exc
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise
            time.sleep(30 * (attempt + 1))
        except (URLError, TimeoutError) as exc:
            last_error = exc
            if attempt == 2:
                raise
            time.sleep(15 * (attempt + 1))
    raise last_error


def fetch_region(min_lat, min_lon, max_lat, max_lon, depth=0):
    """Retry large province extracts as smaller boxes after an Overpass timeout."""
    bounds = "%.6f,%.6f,%.6f,%.6f" % (min_lat, min_lon, max_lat, max_lon)
    try:
        return fetch(query_for(bounds))
    except HTTPError as exc:
        if exc.code not in {500, 502, 504} or depth >= 2:
            raise
        mid_lat = (min_lat + max_lat) / 2
        mid_lon = (min_lon + max_lon) / 2
        print("  Overpass quá tải; chia vùng truy vấn nhỏ hơn (lần %d)." % (depth + 1), flush=True)
        merged = {}
        boxes = [
            (min_lat, min_lon, mid_lat, mid_lon),
            (min_lat, mid_lon, mid_lat, max_lon),
            (mid_lat, min_lon, max_lat, mid_lon),
            (mid_lat, mid_lon, max_lat, max_lon),
        ]
        for box in boxes:
            part = fetch_region(*box, depth=depth + 1)
            for element in part.get("elements", []):
                merged[(element.get("type"), element.get("id"))] = element
        return {"elements": list(merged.values())}


def get_provinces(conn, code=None):
    with conn.cursor() as cur:
        sql = """SELECT p.code, p.name, ST_XMin(ST_Extent(g.geometry)) AS minx,
                         ST_YMin(ST_Extent(g.geometry)) AS miny,
                         ST_XMax(ST_Extent(g.geometry)) AS maxx,
                         ST_YMax(ST_Extent(g.geometry)) AS maxy
                  FROM provinces p JOIN gis_provinces g ON g.province_id = p.id"""
        args = []
        if code:
            sql += " WHERE p.code = %s"
            args.append(str(code))
        sql += " GROUP BY p.code, p.name ORDER BY p.code"
        cur.execute(sql, args)
        return cur.fetchall()


def persist(conn, province_code, elements):
    rows = []
    with conn.cursor() as cur:
        cur.execute("SELECT id, name FROM provinces WHERE code = %s", (province_code,))
        province = cur.fetchone()
        if not province:
            return 0
        province_id, province_name = province
        for element in elements:
            tags = element.get("tags") or {}
            name = tags.get("name") or tags.get("name:vi")
            category = category_for(tags)
            if not name or not category:
                continue
            if element.get("type") == "node":
                lat, lon = element.get("lat"), element.get("lon")
            else:
                center = element.get("center") or {}
                lat, lon = center.get("lat"), center.get("lon")
            if lat is None or lon is None:
                continue
            cur.execute("""SELECT p.name FROM gis_provinces g JOIN provinces p ON p.id = g.province_id
                          WHERE ST_Covers(g.geometry, ST_SetSRID(ST_Point(%s,%s),4326)) LIMIT 1""", (lon, lat))
            matched = cur.fetchone()
            if not matched or matched[0] != province_name:
                continue
            rows.append((name, category[0], category[1], province_id, province_name,
                         tags.get("addr:district") or tags.get("addr:city"),
                         tags.get("addr:ward"), address_for(tags), float(lat), float(lon),
                         float(lon), float(lat),
                         tags.get("phone"), tags.get("website") or tags.get("contact:website"),
                         tags.get("opening_hours"), "OpenStreetMap",
                         "https://www.openstreetmap.org/%s/%s" % (element["type"], element["id"]),
                         element["type"], int(element["id"]), tags.get("description")))
    if rows:
        with conn.cursor() as cur:
            execute_values(cur, """INSERT INTO places
                (name,category,subcategory,province_id,province,district,ward,address,latitude,longitude,location,
                 phone,website,opening_hours,source,source_url,osm_type,osm_id,description)
                VALUES %s ON CONFLICT (source,osm_type,osm_id)
                WHERE source='OpenStreetMap' AND osm_type IS NOT NULL AND osm_id IS NOT NULL
                DO UPDATE SET name=EXCLUDED.name, category=EXCLUDED.category,
                  subcategory=EXCLUDED.subcategory, province=EXCLUDED.province,
                  district=EXCLUDED.district, ward=EXCLUDED.ward, address=EXCLUDED.address,
                  latitude=EXCLUDED.latitude, longitude=EXCLUDED.longitude,
                  location=EXCLUDED.location, phone=EXCLUDED.phone, website=EXCLUDED.website,
                  opening_hours=EXCLUDED.opening_hours, source_url=EXCLUDED.source_url,
                  description=EXCLUDED.description, updated_at=CURRENT_TIMESTAMP""",
                rows,
                template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,ST_SetSRID(ST_Point(%s,%s),4326)::geography,%s,%s,%s,%s,%s,%s,%s,%s)",
                page_size=500)
        conn.commit()
    return len(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--province-code", help="Fetch one current province/city code only")
    parser.add_argument("--wait", type=int, default=WAIT_SECONDS,
                        help="Minimum seconds between Overpass requests (default: 10)")
    args = parser.parse_args()
    conn = psycopg2.connect(**DB)
    try:
        provinces = get_provinces(conn, args.province_code)
        if not provinces:
            raise RuntimeError("Không có ranh giới trong gis_provinces; hãy nhập 34 GeoJSON ranh giới trước.")
        print("Sẽ truy vấn %d tỉnh/thành; nghỉ ít nhất %d giây giữa các truy vấn." % (len(provinces), max(args.wait, 10)))
        for index, (code, name, minx, miny, maxx, maxy) in enumerate(provinces):
            bounds = "%.6f,%.6f,%.6f,%.6f" % (miny, minx, maxy, maxx)
            print("[%s] Đang lấy địa điểm: %s" % (code, name), flush=True)
            try:
                result = fetch_region(miny, minx, maxy, maxx)
                saved = persist(conn, code, result.get("elements", []))
                print("  Nhận %d đối tượng; lưu/cập nhật %d POI." % (len(result.get("elements", [])), saved))
            except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
                conn.rollback()
                print("  Lỗi truy vấn %s: %s" % (name, exc), file=sys.stderr)
            if index + 1 < len(provinces):
                time.sleep(max(args.wait, 10))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
