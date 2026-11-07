"""Restore Vietnamese diacritics in imported ward boundary labels.

The local boundary import stores sanitized GeoJSON filenames as ward names.
This one-time reconciliation matches those labels by province and normalized
spelling, then replaces only the display name/type with the official form.
"""

import json
import re
import unicodedata
from pathlib import Path

import psycopg2


BASE_DIR = Path(__file__).resolve().parent
SOURCE_FILE = BASE_DIR / "ward_names_source.json"
DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "database": "tourism_db",
    "user": "tourism_user",
    "password": "tourism_password",
}


def normalize(value):
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(char for char in text if not unicodedata.combining(char)).replace("đ", "d")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def province_key(value):
    return re.sub(r"^(tinh|thanh pho|tp)\s+", "", normalize(value))


def imported_ward_key(value):
    text = re.sub(r"^\d+[_\s-]+", "", str(value or ""))
    text = re.sub(r"^(phường|xã|thị trấn|đặc khu)\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"_(phuong|xa|thi_tran|dac_khu)$", "", text)
    return normalize(text)


def official_ward_parts(value):
    name = " ".join(str(value or "").split()).strip()
    match = re.match(r"^(Phường|Xã|Thị trấn|Đặc khu)\s+(.+)$", name, re.IGNORECASE)
    if match:
        label = match.group(1).title()
        return label, name
    return None, name


def main():
    data = json.loads(SOURCE_FILE.read_text(encoding="utf-8"))
    official = {}
    for province in data:
        key = province_key(province.get("tentinhmoi"))
        for ward in province.get("phuongxa", []):
            display_type, display_name = official_ward_parts(ward.get("tenphuongxa"))
            name_only = re.sub(r"^(phường|xã|thị trấn|đặc khu)\s+", "", display_name, flags=re.IGNORECASE)
            official[(key, normalize(name_only))] = (display_name, display_type)

    connection = psycopg2.connect(**DB_CONFIG)
    updated = 0
    unmatched = 0
    try:
        with connection, connection.cursor() as cursor:
            cursor.execute("""
                SELECT w.id, w.name, p.name
                FROM wards AS w
                JOIN provinces AS p ON p.id = w.province_id
            """)
            rows = cursor.fetchall()
            for ward_id, imported_name, province_name in rows:
                match = official.get((province_key(province_name), imported_ward_key(imported_name)))
                if not match:
                    unmatched += 1
                    continue
                display_name, display_type = match
                cursor.execute(
                    "UPDATE wards SET name = %s, type = %s WHERE id = %s AND (name <> %s OR type IS DISTINCT FROM %s)",
                    (display_name, display_type, ward_id, display_name, display_type),
                )
                updated += cursor.rowcount
        print(f"Updated {updated} ward names; {unmatched} boundary labels were not present in the supplied name list.")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
