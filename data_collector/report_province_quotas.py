"""Audit place inventory against a per-province collection quota.

Counts are database records with a street/address field; they are not a claim
that a current price or photo has been independently verified. Those fields
are reported separately so collection gaps stay visible.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys

import psycopg2


DB = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "dbname": os.getenv("DB_NAME", "tourism_db"),
    "user": os.getenv("DB_USER", "tourism_user"),
    "password": os.getenv("DB_PASSWORD", "tourism_password"),
}

# Large grocery/supermarket chains only. Do not count convenience stores,
# general shops, or small groceries just because their category is Mua sắm.
SUPERMARKET_RE = re.compile(
    r"(?:win\s*mart|winmart|co\s*\.?\s*op\s*mart|coop\s*mart|"
    r"lotte\s*mart|aeon(?:\s*(?:mall|cit[iy]mart))?|"
    r"mm\s*mega\s*market|emart|tops\s*market|satra\s*mart|"
    r"big\s*c|bigc|(?:^|\W)go!(?:$|\W))",
    re.I,
)


def query_rows(conn):
    with conn.cursor() as cur:
        cur.execute(
            """WITH raw AS (
                 SELECT p.id AS province_id, p.name AS province,
                   CASE
                     WHEN pl.category='Ăn uống' THEN 'Nhà hàng/quán ăn'
                     WHEN pl.category='Lưu trú' THEN 'Khách sạn'
                     WHEN pl.category='Cafe' THEN 'Quán cà phê'
                     WHEN pl.category IN ('Tham quan','Vui chơi','Giải trí','Thiên nhiên')
                       THEN 'Tham quan/vui chơi'
                     WHEN pl.category='Mua sắm' AND (
                       pl.name ~* %s OR COALESCE(pl.subcategory,'') ~* '(supermarket|hypermarket|department_store|mall)'
                     ) THEN 'Siêu thị'
                   END AS group_name,
                   lower(regexp_replace(COALESCE(pl.name,''), '[^[:alnum:]]+', '', 'g')) AS norm_name,
                   lower(regexp_replace(COALESCE(pl.address,''), '[^[:alnum:]]+', '', 'g')) AS norm_address,
                   NULLIF(BTRIM(pl.address),'') IS NOT NULL AS has_address,
                   NULLIF(BTRIM(pl.website),'') IS NOT NULL AS has_website,
                   (NULLIF(BTRIM(pl.image_url),'') IS NOT NULL
                    AND NULLIF(BTRIM(pl.image_source_url),'') IS NOT NULL) AS has_image,
                   (pl.price_min IS NOT NULL OR pl.price_max IS NOT NULL) AS has_price,
                   ((pl.price_min IS NOT NULL OR pl.price_max IS NOT NULL)
                    AND NULLIF(BTRIM(pl.price_source_url),'') IS NOT NULL
                    AND pl.price_kind='published_offer') AS has_price_source
                 FROM places pl
                 LEFT JOIN provinces p ON p.id=pl.province_id
                 WHERE p.id IS NOT NULL AND COALESCE(pl.source,'') <> 'Demo seed'
               ), dedup AS (
                 SELECT province_id, province, group_name, norm_name, norm_address,
                   bool_or(has_address) AS has_address,
                   bool_or(has_website) AS has_website,
                   bool_or(has_image) AS has_image,
                   bool_or(has_price) AS has_price,
                   bool_or(has_price_source) AS has_price_source
                 FROM raw WHERE group_name IS NOT NULL AND norm_name <> ''
                 GROUP BY province_id, province, group_name, norm_name, norm_address
               )
               SELECT p.id, p.name, g.group_name,
                 count(d.group_name) AS records,
                 count(*) FILTER (WHERE d.has_address) AS with_address,
                 count(*) FILTER (WHERE d.has_image) AS with_image,
                 count(*) FILTER (WHERE d.has_price) AS with_price,
                 count(*) FILTER (WHERE d.has_price_source) AS with_price_source,
                 count(*) FILTER (WHERE d.has_website) AS with_website,
                 count(*) FILTER (WHERE d.has_address AND d.has_image AND d.has_price
                                   AND d.has_price_source) AS complete
               FROM provinces p
               CROSS JOIN (VALUES ('Nhà hàng/quán ăn'),('Khách sạn'),('Quán cà phê'),
                                 ('Tham quan/vui chơi'),('Siêu thị')) AS g(group_name)
               LEFT JOIN dedup d ON d.province_id=p.id AND d.group_name=g.group_name
               GROUP BY p.id,p.name,g.group_name
               ORDER BY p.name, CASE g.group_name
                 WHEN 'Khách sạn' THEN 1 WHEN 'Nhà hàng/quán ăn' THEN 2
                 WHEN 'Quán cà phê' THEN 3 WHEN 'Tham quan/vui chơi' THEN 4 ELSE 5 END""",
            (SUPERMARKET_RE.pattern,),
        )
        return cur.fetchall()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=int, default=200, help="Mục tiêu mỗi nhóm ở mỗi tỉnh (mặc định 200)")
    parser.add_argument("--csv", help="Ghi thêm báo cáo CSV vào đường dẫn chỉ định")
    args = parser.parse_args()
    if args.target < 1:
        parser.error("--target phải lớn hơn 0")

    conn = psycopg2.connect(**DB)
    try:
        rows = query_rows(conn)
    finally:
        conn.close()

    headers = ["Tỉnh/thành", "Nhóm", "Mục tiêu", "Bản ghi địa điểm", "Thiếu địa điểm đủ ảnh và giá nguồn",
               "Có địa chỉ", "Có ảnh", "Có giá", "Giá có nguồn", "Có website", "Đủ địa chỉ+ảnh+giá nguồn"]
    output = []
    for row in rows:
        _, province, group_name, records, address, image, price, price_source, website, complete = row
        output.append([province, group_name, args.target, records, max(0, args.target - complete),
                       address, image, price, price_source, website, complete])

    provinces = len({r[0] for r in output})
    group_sums = {}
    for record in output:
        group_sums.setdefault(record[1], [0, 0, 0, 0, 0, 0, 0])
        s = group_sums[record[1]]
        # totals: records, address, image, price, price source, website,
        # full address+image+price-source completeness (skip gap column).
        for index, value in enumerate((record[3], *record[5:11])):
            s[index] += value

    print(f"Hạn mức: {args.target} địa điểm × 5 nhóm × {provinces} tỉnh/thành = {args.target * 5 * provinces:,} địa điểm.")
    print("Ảnh phải có nguồn ảnh; giá phải là giá niêm yết có nguồn. Đủ điều kiện cần cùng một bản ghi có địa chỉ + ảnh + giá.")
    print("Nhóm | DB địa điểm | Có địa chỉ | Ảnh có nguồn | Có giá | Giá niêm yết có nguồn | Có website | Đủ cả 3 | Thiếu tới mục tiêu")
    for group, (records, address, image, price, source, website, complete) in group_sums.items():
        gap = sum(row[4] for row in output if row[1] == group)
        print(f"{group} | {records:,} | {address:,} | {image:,} | {price:,} | {source:,} | {website:,} | {complete:,} | {gap:,}")
    if args.csv:
        os.makedirs(os.path.dirname(os.path.abspath(args.csv)), exist_ok=True)
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as file:
            writer = csv.writer(file)
            writer.writerow(headers)
            writer.writerows(output)
        print(f"Đã ghi bảng chi tiết: {args.csv}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
