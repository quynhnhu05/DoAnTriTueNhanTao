"""Copy the travel application's PostgreSQL data into MongoDB Atlas.

Run from the backend environment after setting MONGODB_URI. This is an
idempotent upsert: it preserves source rows and never drops either database.
"""
from __future__ import annotations

import os
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor
from pymongo import MongoClient, ReplaceOne


PG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "dbname": os.getenv("DB_NAME", "tourism_db"),
    "user": os.getenv("DB_USER", "tourism_user"),
    "password": os.getenv("DB_PASSWORD", "tourism_password"),
}
MONGO_URI = os.getenv("MONGODB_URI", "").strip()
MONGO_DATABASE = os.getenv("MONGODB_DATABASE", "travel_path").strip() or "travel_path"
BATCH_SIZE = max(100, int(os.getenv("MIGRATION_BATCH_SIZE", "1000")))


def plain(value: Any) -> Any:
    if hasattr(value, "__float__") and value.__class__.__name__ == "Decimal":
        return float(value)
    return value


def upsert_batches(collection, documents):
    batch = []
    count = 0
    for source in documents:
        doc = {key: plain(value) for key, value in dict(source).items()}
        doc["_id"] = int(doc["id"])
        batch.append(ReplaceOne({"_id": doc["_id"]}, doc, upsert=True))
        if len(batch) >= BATCH_SIZE:
            collection.bulk_write(batch, ordered=False)
            count += len(batch)
            print(f"{collection.name}: copied {count}")
            batch.clear()
    if batch:
        collection.bulk_write(batch, ordered=False)
        count += len(batch)
    return count


def main():
    if not MONGO_URI:
        raise SystemExit("Set MONGODB_URI to the MongoDB Atlas connection string first.")

    mongo = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
    mongo.admin.command("ping")
    db = mongo[MONGO_DATABASE]
    pg = psycopg2.connect(**PG, cursor_factory=RealDictCursor)
    try:
        with pg.cursor(name="travel_provinces_cursor") as cur:
            cur.execute("SELECT id, code, name, type FROM provinces ORDER BY id")
            provinces = upsert_batches(db["provinces"], cur)

        places_sql = """
            SELECT pl.id, pl.name, pl.category, pl.subcategory,
                   p.code AS province_code, p.name AS province_name,
                   COALESCE(w.name, pl.ward) AS ward_name, w.id AS ward_id,
                   w.type AS ward_type, pl.latitude, pl.longitude,
                   pl.rating, pl.review_count, pl.address, pl.description,
                   pl.price_min, pl.price_max, pl.price_source_url,
                   pl.price_source, pl.price_checked_at, pl.price_kind,
                   pl.opening_hours, pl.district,
                   pl.image_url, pl.image_source_url, pl.image_source_name,
                   pl.image_license, pl.image_checked_at,
                   directory.source_url AS directory_source_url,
                   directory.phone, directory.website
            FROM places pl
            LEFT JOIN provinces p ON p.id = pl.province_id
            LEFT JOIN wards w ON w.id = pl.ward_id
            LEFT JOIN LATERAL (
                SELECT td.source_url, td.phone, td.website
                FROM tourism_directory_records td
                WHERE td.place_id = pl.id
                ORDER BY td.last_seen_at DESC, td.id DESC
                LIMIT 1
            ) directory ON TRUE
            ORDER BY pl.id
        """
        with pg.cursor(name="travel_places_cursor") as cur:
            cur.execute(places_sql)
            places = upsert_batches(db["places"], cur)

        with pg.cursor(name="travel_directory_cursor") as cur:
            cur.execute("SELECT * FROM tourism_directory_records ORDER BY id")
            directory = upsert_batches(db["tourism_directory_records"], cur)

        db["places"].create_index([("province_code", 1), ("category", 1)])
        db["places"].create_index([("name", "text"), ("address", "text")])
        db["places"].create_index([("image_url", 1)])
        db["provinces"].create_index([("code", 1)], unique=True)
        db["tourism_directory_records"].create_index([("place_id", 1), ("last_seen_at", -1)])

        with pg.cursor() as cur:
            cur.execute("SELECT CURRENT_TIMESTAMP AS watermark")
            watermark = cur.fetchone()["watermark"]
        db["sync_state"].replace_one(
            {"_id": "places"}, {"_id": "places", "last_synced_at": watermark}, upsert=True
        )
        db["sync_state"].replace_one(
            {"_id": "tourism_directory_records"},
            {"_id": "tourism_directory_records", "last_synced_at": watermark}, upsert=True,
        )

        print({
            "database": MONGO_DATABASE,
            "provinces_copied": provinces,
            "places_copied": places,
            "directory_records_copied": directory,
            "mongo_places": db["places"].estimated_document_count(),
            "mongo_provinces": db["provinces"].estimated_document_count(),
        })
    finally:
        pg.close()
        mongo.close()


def sync_recent_from_postgres(pg_connection=None) -> dict[str, int]:
    """Mirror changed crawler records into MongoDB after an initial import.

    The crawler retains PostgreSQL as its extraction staging store; this sync
    makes MongoDB the live read database without rescanning 56k unchanged POIs
    after each small image/price crawl batch.
    """
    if not MONGO_URI:
        return {"places": 0, "directory": 0}
    mongo = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    mongo.admin.command("ping")
    db = mongo[MONGO_DATABASE]
    owned_connection = pg_connection is None
    pg = pg_connection or psycopg2.connect(**PG, cursor_factory=RealDictCursor)
    try:
        with pg.cursor() as cur:
            cur.execute("SELECT CURRENT_TIMESTAMP AS watermark")
            watermark = cur.fetchone()["watermark"]
        results = {}
        for name, query in (
            ("places", """
                SELECT pl.id, pl.name, pl.category, pl.subcategory,
                       p.code AS province_code, p.name AS province_name,
                       COALESCE(w.name, pl.ward) AS ward_name, w.id AS ward_id,
                       w.type AS ward_type, pl.latitude, pl.longitude, pl.rating,
                       pl.review_count, pl.address, pl.description, pl.price_min,
                       pl.price_max, pl.price_source_url, pl.price_source,
                       pl.price_checked_at, pl.price_kind, pl.opening_hours,
                       pl.district, pl.image_url, pl.image_source_url,
                       pl.image_source_name, pl.image_license, pl.image_checked_at,
                       directory.source_url AS directory_source_url,
                       directory.phone, directory.website
                FROM places pl
                LEFT JOIN provinces p ON p.id = pl.province_id
                LEFT JOIN wards w ON w.id = pl.ward_id
                LEFT JOIN LATERAL (
                    SELECT td.source_url, td.phone, td.website
                    FROM tourism_directory_records td WHERE td.place_id = pl.id
                    ORDER BY td.last_seen_at DESC, td.id DESC LIMIT 1
                ) directory ON TRUE
                WHERE pl.updated_at > %s AND pl.updated_at <= %s
                ORDER BY pl.id
            """),
            ("tourism_directory_records", """
                SELECT * FROM tourism_directory_records
                WHERE last_seen_at > %s AND last_seen_at <= %s
                ORDER BY id
            """),
        ):
            state = db["sync_state"].find_one({"_id": name})
            if not state or not state.get("last_synced_at"):
                raise RuntimeError(
                    f"Chưa nhập dữ liệu ban đầu cho {name}; hãy chạy migrate_postgres_to_mongodb.py trước."
                )
            with pg.cursor(name=f"mongo_sync_{name}") as cur:
                cur.execute(query, (state["last_synced_at"], watermark))
                results[name] = upsert_batches(db[name], cur)
            db["sync_state"].replace_one(
                {"_id": name}, {"_id": name, "last_synced_at": watermark}, upsert=True
            )
        return {"places": results["places"], "directory": results["tourism_directory_records"]}
    finally:
        if owned_connection:
            pg.close()
        mongo.close()


if __name__ == "__main__":
    main()
