from __future__ import annotations

import re
import unicodedata
from typing import Optional

from .recommender import Place, safe_float, safe_int


def display_ward_name(name, ward_type=None):
    """Clean imported ward filename slugs into readable address labels."""
    value = " ".join(str(name or "").split()).strip(" ,")
    if not value:
        return None

    folded = unicodedata.normalize("NFKD", value.casefold())
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    folded = folded.replace("đ", "d")
    label = None
    existing_prefix = re.match(r"^(phường|phuong|xã|xa|thị trấn|thi tran|đặc khu|dac khu|ward|commune|town)\s+", value, re.IGNORECASE)
    if existing_prefix:
        prefix_folded = unicodedata.normalize("NFKD", existing_prefix.group(1).casefold())
        prefix_folded = "".join(char for char in prefix_folded if not unicodedata.combining(char)).replace("đ", "d")
        label = {
            "phuong": "Phường", "xa": "Xã", "thi tran": "Thị trấn",
            "dac khu": "Đặc khu", "ward": "Phường", "commune": "Xã", "town": "Thị trấn",
        }.get(prefix_folded)
        value = value[existing_prefix.end():].strip()
        folded = folded[existing_prefix.end():]
    suffixes = (
        ("_thi_tran", "Thị trấn"), ("_dac_khu", "Đặc khu"),
        ("_phuong", "Phường"), ("_xa", "Xã"),
    )
    for suffix, prefix in suffixes:
        if folded.endswith(suffix):
            value = value[: -len(suffix)].rstrip(" _-")
            label = prefix
            break

    # The ward dataset prefixes each label with its numeric record number.
    value = re.sub(r"^\s*\d+[_\s-]+", "", value).replace("_", " ").strip()
    value = " ".join(value.split()).title()
    if not value:
        return None

    raw_type = " ".join(str(ward_type or "").split()).strip()
    type_folded = unicodedata.normalize("NFKD", raw_type.casefold())
    type_folded = "".join(char for char in type_folded if not unicodedata.combining(char)).replace("đ", "d")
    type_labels = {
        "phuong": "Phường", "xa": "Xã", "thi tran": "Thị trấn",
        "dac khu": "Đặc khu", "town": "Thị trấn", "commune": "Xã", "ward": "Phường",
    }
    label = type_labels.get(type_folded, label)
    if label and not value.casefold().startswith(label.casefold() + " "):
        return f"{label} {value}"
    return value


class PlaceRepository:
    """Read places from MongoDB documents or the legacy PostgreSQL store.

    Lưu ý quan trọng: thứ tự cột SELECT phải khớp 1-1 với Place.
    Bản cũ đọc pl.address vào description nên address bị mất và giá bị lệch cột.
    """

    def __init__(self, connection):
        self.connection = connection

    def get_places(
        self,
        province_code: Optional[str] = None,
        limit: int = 100000,
    ) -> list[Place]:
        if hasattr(self.connection, "database"):
            return self._get_mongo_places(province_code=province_code, limit=limit)

        cursor = self.connection.cursor()
        query = """
            SELECT
                pl.id,
                pl.name,
                pl.category,
                pl.subcategory,
                p.code AS province_code,
                p.name AS province_name,
                COALESCE(w.name, pl.ward) AS ward_name,
                pl.latitude,
                pl.longitude,
                pl.rating,
                pl.review_count,
                pl.address,
                CASE WHEN pl.price_source_url IS NOT NULL
                           AND pl.price_checked_at >= NOW() - INTERVAL '45 days'
                           AND LOWER(COALESCE(pl.price_source,'')) <> 'estimated'
                     THEN pl.price_min ELSE NULL END AS price_min,
                CASE WHEN pl.price_source_url IS NOT NULL
                           AND pl.price_checked_at >= NOW() - INTERVAL '45 days'
                           AND LOWER(COALESCE(pl.price_source,'')) <> 'estimated'
                     THEN pl.price_max ELSE NULL END AS price_max,
                pl.price_source_url,
                pl.price_checked_at,
                pl.price_kind,
                pl.opening_hours,
                pl.district,
                directory.source_url,
                directory.phone,
                directory.website,
                w.id AS matched_ward_id,
                w.type AS matched_ward_type,
                pl.image_url,
                pl.image_source_url,
                pl.image_source_name,
                pl.image_license,
                pl.price_source
            FROM places pl
            LEFT JOIN provinces p
                ON p.id = pl.province_id
            LEFT JOIN LATERAL (
                SELECT td.source_url, td.phone, td.website
                FROM tourism_directory_records td
                WHERE td.place_id = pl.id
                ORDER BY td.last_seen_at DESC, td.id DESC
                LIMIT 1
            ) directory ON TRUE
            LEFT JOIN wards w ON w.id = pl.ward_id
            WHERE pl.latitude IS NOT NULL
              AND pl.longitude IS NOT NULL
        """

        params: list[object] = []
        if province_code:
            query += " AND p.code = %s"
            params.append(str(province_code).strip())

        query += " ORDER BY pl.id LIMIT %s"
        params.append(max(1, int(limit)))

        cursor.execute(query, params)
        rows = cursor.fetchall()
        cursor.close()

        places: list[Place] = []
        for row in rows:
            places.append(
                Place(
                    id=safe_int(row[0]) or 0,
                    name=str(row[1] or ""),
                    category=str(row[2] or ""),
                    subcategory=str(row[3]) if row[3] is not None else None,
                    province_code=str(row[4]) if row[4] is not None else None,
                    province_name=str(row[5]) if row[5] is not None else None,
                    ward_id=safe_int(row[22]),
                    ward_name=display_ward_name(row[6], row[23]),
                    image_url=str(row[24]) if row[24] is not None else None,
                    image_source_url=str(row[25]) if row[25] is not None else None,
                    image_source_name=str(row[26]) if row[26] is not None else None,
                    image_license=str(row[27]) if row[27] is not None else None,
                    latitude=safe_float(row[7]),
                    longitude=safe_float(row[8]),
                    rating=safe_float(row[9]),
                    review_count=safe_int(row[10]),
                    # ĐÚNG: row[11] là address, không phải description.
                    address=str(row[11]) if row[11] is not None else None,
                    description=None,
                    price_min=safe_float(row[12]),
                    price_max=safe_float(row[13]),
                    price_source_url=str(row[14]) if row[14] is not None else None,
                    price_source=str(row[28]) if row[28] is not None else None,
                    price_checked_at=row[15].isoformat() if row[15] is not None else None,
                    price_kind=str(row[16]) if row[16] is not None else None,
                    opening_hours=str(row[17]) if row[17] is not None else None,
                    district_name=str(row[18]) if row[18] is not None else None,
                    directory_source_url=str(row[19]) if row[19] is not None else None,
                    directory_phone=str(row[20]) if row[20] is not None else None,
                    directory_website=str(row[21]) if row[21] is not None else None,
                )
            )

        return places

    def _get_mongo_places(self, province_code: Optional[str], limit: int) -> list[Place]:
        query: dict[str, object] = {
            "latitude": {"$ne": None},
            "longitude": {"$ne": None},
        }
        if province_code:
            query["province_code"] = str(province_code).strip()
        docs = self.connection["places"].find(query).sort("id", 1).limit(max(1, int(limit)))
        places: list[Place] = []
        for doc in docs:
            price_checked_at = doc.get("price_checked_at")
            if hasattr(price_checked_at, "isoformat"):
                price_checked_at = price_checked_at.isoformat()
            places.append(Place(
                id=safe_int(doc.get("id")) or 0,
                name=str(doc.get("name") or ""),
                category=str(doc.get("category") or ""),
                subcategory=doc.get("subcategory"),
                province_code=str(doc.get("province_code")) if doc.get("province_code") is not None else None,
                province_name=doc.get("province_name"),
                ward_id=safe_int(doc.get("ward_id")),
                ward_name=display_ward_name(doc.get("ward_name") or doc.get("ward"), doc.get("ward_type")),
                latitude=safe_float(doc.get("latitude")),
                longitude=safe_float(doc.get("longitude")),
                rating=safe_float(doc.get("rating")),
                review_count=safe_int(doc.get("review_count")),
                description=doc.get("description"),
                price_min=safe_float(doc.get("price_min")),
                price_max=safe_float(doc.get("price_max")),
                price_source_url=doc.get("price_source_url"),
                price_source=doc.get("price_source"),
                price_checked_at=price_checked_at,
                price_kind=doc.get("price_kind"),
                opening_hours=doc.get("opening_hours"),
                address=doc.get("address"),
                image_url=doc.get("image_url"),
                image_source_url=doc.get("image_source_url"),
                image_source_name=doc.get("image_source_name"),
                image_license=doc.get("image_license"),
                district_name=doc.get("district"),
                directory_source_url=doc.get("directory_source_url"),
                directory_phone=doc.get("phone"),
                directory_website=doc.get("website"),
            ))
        return places
