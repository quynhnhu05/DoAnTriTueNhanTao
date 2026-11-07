import json
import re
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values


# ============================================================
# 1. CẤU HÌNH
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

# Thư mục chứa 34 tỉnh và 3.321 xã/phường
WARDS_DIR = BASE_DIR / "data" / "temp_wards_check"

# PostgreSQL Docker
DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "database": "tourism_db",
    "user": "tourism_user",
    "password": "tourism_password",
}


# ============================================================
# 2. MAPPING THƯ MỤC DATASET → TÊN TỈNH THÀNH
# ============================================================

PROVINCE_FOLDER_MAP = {
    "1": "Hà Nội",
    "2": "Cao Bằng",
    "3": "Tuyên Quang",
    "4": "Lào Cai",
    "5": "Điện Biên",
    "6": "Lai Châu",
    "7": "Sơn La",
    "8": "Thái Nguyên",
    "9": "Lạng Sơn",
    "10": "Quảng Ninh",
    "11": "Phú Thọ",
    "12": "Bắc Ninh",
    "13": "Hải Phòng",
    "14": "Hưng Yên",
    "15": "Ninh Bình",
    "16": "Thanh Hóa",
    "17": "Nghệ An",
    "18": "Hà Tĩnh",
    "19": "Quảng Trị",
    "20": "Huế",
    "21": "Đà Nẵng",
    "22": "Quảng Ngãi",
    "23": "Khánh Hòa",
    "24": "Gia Lai",
    "25": "Đắk Lắk",
    "26": "Lâm Đồng",
    "27": "Tây Ninh",
    "28": "Đồng Nai",
    "29": "TP Hồ Chí Minh",
    "30": "Vĩnh Long",
    "31": "Đồng Tháp",
    "32": "An Giang",
    "33": "Cần Thơ",
    "34": "Cà Mau",
}


# ============================================================
# 3. TẠO BẢNG WARDS NẾU CHƯA CÓ
# ============================================================

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS wards (
    id SERIAL PRIMARY KEY,
    code VARCHAR(50) UNIQUE NOT NULL,
    name VARCHAR(150) NOT NULL,
    type VARCHAR(30),
    province_id INTEGER NOT NULL,

    geometry GEOMETRY(MULTIPOLYGON, 4326) NOT NULL,

    CONSTRAINT fk_wards_province
        FOREIGN KEY (province_id)
        REFERENCES provinces(id)
);
"""


CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_wards_geometry
ON wards
USING GIST (geometry);

CREATE INDEX IF NOT EXISTS idx_wards_province_id
ON wards(province_id);
"""


# ============================================================
# 4. ĐỌC PROVINCE ID TỪ DATABASE
# ============================================================

def load_province_ids(conn):
    """
    Lấy province_id từ bảng provinces.

    Kết quả:
        {
            "Hà Nội": 1,
            "Cao Bằng": 2,
            ...
        }
    """

    province_ids = {}

    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, name
            FROM provinces
            ORDER BY id;
        """)

        rows = cur.fetchall()

    for province_id, province_name in rows:
        province_ids[province_name.strip()] = province_id

    return province_ids


# ============================================================
# 5. XÁC ĐỊNH TỈNH TỪ TÊN FOLDER
# ============================================================

def get_province_name_from_folder(folder_name):
    """
    Ví dụ:

        1_thu_đô_ha_noi
            -> Hà Nội

        21_đa_nang
            -> Đà Nẵng

        29_ho_chi_minh
            -> Thành phố Hồ Chí Minh
    """

    match = re.match(r"^(\d+)_", folder_name)

    if not match:
        raise ValueError(
            f"Không xác định được mã folder tỉnh: {folder_name}"
        )

    dataset_code = match.group(1)

    if dataset_code not in PROVINCE_FOLDER_MAP:
        raise ValueError(
            f"Không có mapping tỉnh cho folder: {folder_name}"
        )

    return PROVINCE_FOLDER_MAP[dataset_code]


# ============================================================
# 6. XÁC ĐỊNH TYPE CỦA XÃ/PHƯỜNG
# ============================================================

def detect_ward_type(name):
    """
    Dataset có thể không ghi rõ type.
    Ta suy ra từ tên nếu cần.

    Ví dụ:
        "Phường Bến Nghé" -> Phường
        "Xã An Bình"       -> Xã
        "Thị trấn ..."     -> Thị trấn
    """

    if not name:
        return None

    name_lower = name.strip().lower()

    if name_lower.startswith("phường "):
        return "Phường"

    if name_lower.startswith("xã "):
        return "Xã"

    if name_lower.startswith("thị trấn "):
        return "Thị trấn"

    return None


# ============================================================
# 7. ĐỌC MỘT FILE GEOJSON
# ============================================================

def read_geojson(file_path):
    """
    Đọc GeoJSON và trả về danh sách ward.
    """

    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if data.get("type") != "FeatureCollection":
        raise ValueError(
            f"File không phải FeatureCollection: {file_path}"
        )

    features = data.get("features", [])

    result = []

    for feature in features:

        properties = feature.get("properties") or {}
        geometry = feature.get("geometry")

        if not geometry:
            continue

        geometry_type = geometry.get("type")

        if geometry_type != "MultiPolygon":
            print(
                f"[WARNING] Geometry không phải MultiPolygon: "
                f"{file_path.name} -> {geometry_type}"
            )
            continue

        # ----------------------------------------------------
        # LẤY TÊN
        # ----------------------------------------------------

        name = (
            properties.get("name")
            or properties.get("ten")
            or properties.get("NAME")
            or properties.get("Name")
        )

        if not name:
            # Thử lấy từ tên file
            name = file_path.stem

        name = str(name).strip()

        # ----------------------------------------------------
        # LẤY CODE
        # ----------------------------------------------------

        ward_code = (
            properties.get("code")
            or properties.get("ma")
            or properties.get("ma_xa")
            or properties.get("maxa")
            or properties.get("matinhxa")
            or feature.get("id")
        )

        if ward_code is None:
            raise ValueError(
                f"Không tìm thấy code xã/phường: {file_path}"
            )

        ward_code = str(ward_code).strip()

        # ----------------------------------------------------
        # TYPE
        # ----------------------------------------------------

        ward_type = (
            properties.get("type")
            or properties.get("loai")
            or properties.get("loai_don_vi")
        )

        if ward_type:
            ward_type = str(ward_type).strip()
        else:
            ward_type = detect_ward_type(name)

        # ----------------------------------------------------
        # GIỮ NGUYÊN GEOMETRY
        # ----------------------------------------------------

        geometry_json = json.dumps(
            geometry,
            ensure_ascii=False
        )

        result.append({
            "code": ward_code,
            "name": name,
            "type": ward_type,
            "geometry": geometry_json
        })

    return result


# ============================================================
# 8. TÌM TOÀN BỘ FILE GEOJSON
# ============================================================

def find_geojson_files():
    """
    Tìm tất cả GeoJSON nằm bên trong:

        data/temp_wards_check/

    Ví dụ:

        1_thu_đô_ha_noi/wards/1_...geojson
        2_tinh_cao_bang/wards/1_...geojson
        ...
    """

    if not WARDS_DIR.exists():
        raise FileNotFoundError(
            f"Không tìm thấy thư mục:\n{WARDS_DIR}"
        )

    files = list(WARDS_DIR.rglob("*.geojson"))

    return files


# ============================================================
# 9. IMPORT VÀO DATABASE
# ============================================================

def import_wards():
    print("=" * 70)
    print("IMPORT 3.321 XÃ / PHƯỜNG VÀO POSTGRESQL")
    print("=" * 70)

    print(f"\n[1] Thư mục dữ liệu:")
    print(WARDS_DIR)

    # --------------------------------------------------------
    # Tìm file
    # --------------------------------------------------------

    geojson_files = find_geojson_files()

    print(
        f"\n[2] Tìm thấy {len(geojson_files)} file GeoJSON."
    )

    if len(geojson_files) == 0:
        raise RuntimeError(
            "Không tìm thấy file GeoJSON."
        )

    # --------------------------------------------------------
    # Kết nối DB
    # --------------------------------------------------------

    print("\n[3] Đang kết nối PostgreSQL...")

    conn = psycopg2.connect(**DB_CONFIG)

    print("    Kết nối thành công.")

    try:

        # ----------------------------------------------------
        # Tạo bảng
        # ----------------------------------------------------

        print("\n[4] Kiểm tra bảng wards...")

        with conn.cursor() as cur:
            cur.execute(CREATE_TABLE_SQL)
            cur.execute(CREATE_INDEX_SQL)

        conn.commit()

        print("    OK.")

        # ----------------------------------------------------
        # Load province IDs
        # ----------------------------------------------------

        print("\n[5] Đọc 34 tỉnh/thành từ bảng provinces...")

        province_ids = load_province_ids(conn)

        print(
            f"    Database đang có {len(province_ids)} tỉnh/thành."
        )

        if len(province_ids) != 34:
            raise RuntimeError(
                f"Bảng provinces phải có 34 tỉnh/thành, "
                f"nhưng hiện có {len(province_ids)}."
            )

        # ----------------------------------------------------
        # Kiểm tra mapping
        # ----------------------------------------------------

        print("\n[6] Kiểm tra mapping 34 folder...")

        for folder_code, province_name in PROVINCE_FOLDER_MAP.items():

            if province_name not in province_ids:
                raise RuntimeError(
                    f"Không tìm thấy tỉnh '{province_name}' "
                    f"trong bảng provinces."
                )

        print("    Mapping 34 tỉnh/thành: OK.")

        # ----------------------------------------------------
        # Đọc dữ liệu
        # ----------------------------------------------------

        print("\n[7] Đọc GeoJSON...")

        all_rows = []

        processed_files = 0
        processed_features = 0

        for file_path in geojson_files:

            province_folder = file_path.parent.parent.name

            province_name = get_province_name_from_folder(
                province_folder
            )

            province_id = province_ids[province_name]

            wards = read_geojson(file_path)

            for ward in wards:

                all_rows.append((
                    ward["code"],
                    ward["name"],
                    ward["type"],
                    province_id,
                    ward["geometry"],
                ))

            processed_files += 1
            processed_features += len(wards)

            if processed_files % 500 == 0:
                print(
                    f"    Đã đọc {processed_files}/"
                    f"{len(geojson_files)} files | "
                    f"{processed_features} wards"
                )

        print(
            f"\n    Tổng file: {processed_files}"
        )

        print(
            f"    Tổng xã/phường đọc được: "
            f"{processed_features}"
        )

        # ----------------------------------------------------
        # Kiểm tra số lượng
        # ----------------------------------------------------

        if processed_features != 3321:
            print(
                "\n[WARNING]"
                f"\nDataset đọc được {processed_features} xã/phường."
                f"\nKỳ vọng: 3321."
            )

        # ----------------------------------------------------
        # Xóa dữ liệu cũ
        # ----------------------------------------------------

        print("\n[8] Xóa dữ liệu wards cũ...")

        with conn.cursor() as cur:
            cur.execute("DELETE FROM wards;")

        conn.commit()

        print("    OK.")

        # ----------------------------------------------------
        # INSERT BATCH
        # ----------------------------------------------------

        print("\n[9] Import vào PostgreSQL...")

        insert_sql = """
            INSERT INTO wards
            (
                code,
                name,
                type,
                province_id,
                geometry
            )
            VALUES %s
        """

        BATCH_SIZE = 500

        total_inserted = 0

        for start in range(
            0,
            len(all_rows),
            BATCH_SIZE
        ):

            batch = all_rows[
                start:start + BATCH_SIZE
            ]

            values_sql = []

            for code, name, ward_type, province_id, geometry_json in batch:

                values_sql.append((
                    code,
                    name,
                    ward_type,
                    province_id,
                    geometry_json
                ))

            execute_values(
                conn.cursor(),
                insert_sql,
                values_sql,
                template="""
                    (
                        %s,
                        %s,
                        %s,
                        %s,
                        ST_SetSRID(
                            ST_GeomFromGeoJSON(%s),
                            4326
                        )
                    )
                """
            )

            conn.commit()

            total_inserted += len(batch)

            print(
                f"    Imported: "
                f"{total_inserted}/{len(all_rows)}"
            )

        # ----------------------------------------------------
        # KIỂM TRA
        # ----------------------------------------------------

        print("\n[10] Kiểm tra database...")

        with conn.cursor() as cur:

            cur.execute("""
                SELECT COUNT(*)
                FROM wards;
            """)

            total = cur.fetchone()[0]

            cur.execute("""
                SELECT COUNT(*)
                FROM wards
                WHERE geometry IS NOT NULL;
            """)

            geometry_count = cur.fetchone()[0]

            cur.execute("""
                SELECT COUNT(*)
                FROM wards
                WHERE ST_SRID(geometry) = 4326;
            """)

            srid_count = cur.fetchone()[0]

            cur.execute("""
                SELECT COUNT(*)
                FROM wards
                WHERE ST_IsValid(geometry);
            """)

            valid_count = cur.fetchone()[0]

            cur.execute("""
                SELECT COUNT(DISTINCT province_id)
                FROM wards;
            """)

            province_count = cur.fetchone()[0]

        print()
        print("-" * 70)
        print("KẾT QUẢ")
        print("-" * 70)

        print(f"wards                 : {total}")
        print(f"có geometry           : {geometry_count}")
        print(f"SRID = 4326           : {srid_count}")
        print(f"geometry hợp lệ       : {valid_count}")
        print(f"tỉnh/thành có dữ liệu : {province_count}")

        print("-" * 70)

        if total == 3321:
            print("✓ ĐỦ 3.321 XÃ/PHƯỜNG")
        else:
            print(
                f"⚠ Số lượng hiện tại: {total}, "
                f"không phải 3321."
            )

        if geometry_count == total:
            print("✓ Tất cả đều có geometry")

        if srid_count == total:
            print("✓ Tất cả geometry là SRID 4326")

        if valid_count == total:
            print("✓ Tất cả geometry hợp lệ")

        if province_count == 34:
            print("✓ Đủ dữ liệu của 34 tỉnh/thành")

        print("-" * 70)

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()

    print("\nDONE.")


# ============================================================
# 10. MAIN
# ============================================================

if __name__ == "__main__":
    import_wards()
