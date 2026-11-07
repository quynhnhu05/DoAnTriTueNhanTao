import json
from pathlib import Path

import psycopg2


BASE_DIR = Path(r"D:\DoAnTriTueNhanTao")
GEOJSON_DIR = BASE_DIR / "data" / "boundaries" / "provinces"

DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "dbname": "tourism_db",
    "user": "tourism_user",
    "password": "tourism_password",
}


def main():
    files = sorted(GEOJSON_DIR.glob("*.geojson"))

    print(f"Tìm thấy {len(files)} file GeoJSON")

    if len(files) != 34:
        raise Exception(
            f"Phải có đúng 34 file GeoJSON, nhưng hiện có {len(files)}"
        )

    conn = psycopg2.connect(**DB_CONFIG)

    try:
        with conn.cursor() as cur:
            imported = 0
            skipped = 0

            for file_path in files:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                features = data.get("features", [])

                if len(features) != 1:
                    raise Exception(
                        f"{file_path.name}: expected 1 feature, "
                        f"nhưng có {len(features)}"
                    )

                feature = features[0]

                properties = feature.get("properties", {})
                geometry = feature.get("geometry")

                province_code = str(properties.get("code", "")).strip()
                province_name = properties.get("name")

                if not province_code:
                    raise Exception(
                        f"{file_path.name}: thiếu province code"
                    )

                if not province_name:
                    raise Exception(
                        f"{file_path.name}: thiếu province name"
                    )

                if not geometry:
                    raise Exception(
                        f"{file_path.name}: thiếu geometry"
                    )

                if geometry.get("type") != "MultiPolygon":
                    raise Exception(
                        f"{file_path.name}: geometry không phải MultiPolygon"
                    )

                # Kiểm tra province code có tồn tại trong bảng provinces
                cur.execute(
                    """
                    SELECT id, name
                    FROM provinces
                    WHERE code = %s
                    """,
                    (province_code,)
                )

                province = cur.fetchone()

                if province is None:
                    raise Exception(
                        f"{file_path.name}: code {province_code} "
                        f"không tồn tại trong bảng provinces"
                    )

                province_id = province[0]

                # Nếu đã import rồi thì bỏ qua
                cur.execute(
                    """
                    SELECT 1
                    FROM gis_provinces
                    WHERE province_code = %s
                    """,
                    (province_code,)
                )

                if cur.fetchone():
                    print(
                        f"[SKIP] {province_code} - {province_name}"
                    )
                    skipped += 1
                    continue

                geometry_json = json.dumps(
                    geometry,
                    ensure_ascii=False
                )

                cur.execute(
                    """
                    INSERT INTO gis_provinces (
                        province_id,
                        province_code,
                        province_name,
                        geometry
                    )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        ST_Multi(
                            ST_SetSRID(
                                ST_GeomFromGeoJSON(%s),
                                4326
                            )
                        )
                    )
                    """,
                    (
                        province_id,
                        province_code,
                        province_name,
                        geometry_json,
                    )
                )

                print(
                    f"[OK]   {province_code} - {province_name}"
                )

                imported += 1

        conn.commit()

        print()
        print("================================")
        print("IMPORT HOÀN TẤT")
        print("================================")
        print(f"Imported : {imported}")
        print(f"Skipped  : {skipped}")
        print(f"Total    : {imported + skipped}")

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


if __name__ == "__main__":
    main()