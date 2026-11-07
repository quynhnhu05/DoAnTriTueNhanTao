import os
import osmium
import psycopg2
from psycopg2.extras import execute_values


# ============================================================
# CONFIG
# ============================================================

BASE_DIR = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)

OSM_FILE = os.path.join(
    BASE_DIR,
    "data",
    "raw",
    "vietnam-260908.osm.pbf"
)

DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "database": "tourism_db",
    "user": "tourism_user",
    "password": "tourism_password",
}

BATCH_SIZE = 1000


# ============================================================
# CATEGORIES
# ============================================================

AMENITY_TYPES = {
    "restaurant": ("Ăn uống", "Nhà hàng"),
    "fast_food": ("Ăn uống", "Đồ ăn nhanh"),
    "food_court": ("Ăn uống", "Khu ăn uống"),

    "cafe": ("Cafe", "Quán cafe"),

    "bar": ("Giải trí", "Quán bar"),
    "pub": ("Giải trí", "Quán pub"),

    "hospital": ("Y tế", "Bệnh viện"),
    "clinic": ("Y tế", "Phòng khám"),
    "pharmacy": ("Y tế", "Nhà thuốc"),

    "atm": ("Tài chính", "ATM"),
    "bank": ("Tài chính", "Ngân hàng"),

    "taxi": ("Di chuyển", "Taxi"),
    "bus_station": ("Di chuyển", "Bến xe"),
    "fuel": ("Di chuyển", "Trạm xăng"),
    "parking": ("Di chuyển", "Bãi đỗ xe"),
}


TOURISM_TYPES = {
    "hotel": ("Lưu trú", "Khách sạn"),
    "hostel": ("Lưu trú", "Hostel"),
    "guest_house": ("Lưu trú", "Nhà nghỉ"),
    "motel": ("Lưu trú", "Motel"),
    "resort": ("Lưu trú", "Resort"),
    "chalet": ("Lưu trú", "Chalet"),
    "camp_site": ("Lưu trú", "Khu cắm trại"),
    "caravan_site": ("Lưu trú", "Khu caravan"),

    "attraction": ("Tham quan", "Điểm tham quan"),
    "museum": ("Tham quan", "Bảo tàng"),
    "viewpoint": ("Tham quan", "Điểm ngắm cảnh"),
    "gallery": ("Tham quan", "Phòng trưng bày"),
    "monument": ("Tham quan", "Tượng đài"),
    "memorial": ("Tham quan", "Đài tưởng niệm"),
    "ruins": ("Tham quan", "Di tích"),

    "theme_park": ("Vui chơi", "Công viên giải trí"),
    "zoo": ("Vui chơi", "Vườn thú"),
    "aquarium": ("Vui chơi", "Thủy cung"),
}


SHOP_TYPES = {
    "supermarket": ("Mua sắm", "Siêu thị"),
    "convenience": ("Mua sắm", "Cửa hàng tiện lợi"),
    "mall": ("Mua sắm", "Trung tâm thương mại"),
    "department_store": ("Mua sắm", "Cửa hàng bách hóa"),
    "market": ("Mua sắm", "Chợ"),
}


LEISURE_TYPES = {
    "park": ("Vui chơi", "Công viên"),
    "playground": ("Vui chơi", "Sân chơi"),
    "stadium": ("Vui chơi", "Sân vận động"),
    "sports_centre": ("Vui chơi", "Trung tâm thể thao"),
    "swimming_pool": ("Vui chơi", "Hồ bơi"),
    "fitness_centre": ("Vui chơi", "Trung tâm thể thao"),
    "golf_course": ("Vui chơi", "Sân golf"),
    "marina": ("Vui chơi", "Bến du thuyền"),
}


NATURAL_TYPES = {
    "beach": ("Thiên nhiên", "Bãi biển"),
    "waterfall": ("Thiên nhiên", "Thác nước"),
    "peak": ("Thiên nhiên", "Đỉnh núi"),
    "cave_entrance": ("Thiên nhiên", "Hang động"),
    "spring": ("Thiên nhiên", "Suối"),
}


# ============================================================
# HANDLER
# ============================================================

class TourismHandler(osmium.SimpleHandler):

    def __init__(self, connection):
        super().__init__()

        self.connection = connection
        self.cursor = connection.cursor()

        self.batch = []

        self.objects_processed = 0
        self.places_found = 0
        self.places_saved = 0

    # ========================================================
    # ADDRESS
    # ========================================================

    def get_address(self, tags):

        address = tags.get("addr:full", "")

        if address:
            return address

        parts = [
            tags.get("addr:housenumber", ""),
            tags.get("addr:street", ""),
            tags.get("addr:ward", ""),
            tags.get("addr:suburb", ""),
            tags.get("addr:district", ""),
            tags.get("addr:city", ""),
            tags.get("addr:province", ""),
        ]

        return ", ".join(
            x for x in parts if x
        )

    # ========================================================
    # NUMBER
    # ========================================================

    def to_number(self, value):

        if not value:
            return None

        try:
            return float(value)
        except Exception:
            return None

    # ========================================================
    # ADD BATCH
    # ========================================================

    def add_to_batch(
        self,
        osm_id,
        name,
        category,
        subcategory,
        latitude,
        longitude,
        tags
    ):

        province = tags.get(
            "addr:province",
            ""
        )

        city = tags.get(
            "addr:city",
            ""
        )

        if not province and city:
            province = city

        district = tags.get(
            "addr:district",
            ""
        )

        ward = tags.get(
            "addr:ward",
            ""
        )

        address = self.get_address(tags)

        price_min = self.to_number(
            tags.get("minprice")
        )

        price_max = self.to_number(
            tags.get("maxprice")
        )

        row = (
            name,
            category,
            subcategory,

            province,
            district,
            ward,

            address,

            latitude,
            longitude,

            longitude,
            latitude,

            tags.get("phone", ""),
            tags.get("website", ""),
            tags.get("opening_hours", ""),

            tags.get("price", ""),
            price_min,
            price_max,
            tags.get("currency", ""),

            None,
            None,

            tags.get("description", ""),

            "OpenStreetMap",
            None,

            osm_id,
        )

        self.batch.append(row)

        self.places_found += 1

        if len(self.batch) >= BATCH_SIZE:
            self.save_batch()

    # ========================================================
    # SAVE BATCH
    # ========================================================

    def save_batch(self):

        if not self.batch:
            return

        sql = """
            INSERT INTO places (
                name,
                category,
                subcategory,

                province,
                district,
                ward,

                address,

                latitude,
                longitude,

                location,

                phone,
                website,
                opening_hours,

                price,
                price_min,
                price_max,
                currency,

                rating,
                review_count,

                description,

                source,
                source_url,

                osm_id
            )

            VALUES %s

            ON CONFLICT DO NOTHING
        """

        template = """
        (
            %s,
            %s,
            %s,

            %s,
            %s,
            %s,

            %s,

            %s,
            %s,

            ST_SetSRID(
                ST_MakePoint(%s, %s),
                4326
            )::geography,

            %s,
            %s,
            %s,

            %s,
            %s,
            %s,
            %s,

            %s,
            %s,

            %s,

            %s,
            %s,

            %s
        )
        """

        try:

            execute_values(
                self.cursor,
                sql,
                self.batch,
                template=template,
                page_size=BATCH_SIZE
            )

            self.connection.commit()

            self.places_saved += len(
                self.batch
            )

            print(
                f"💾 Đã lưu: "
                f"{self.places_saved:,} địa điểm"
            )

            self.batch.clear()

        except Exception as e:

            print()
            print(
                "❌ Lỗi khi lưu batch:"
            )
            print(e)

            self.connection.rollback()

            self.batch.clear()

    # ========================================================
    # PROCESS
    # ========================================================

    def process_object(
        self,
        obj,
        latitude=None,
        longitude=None
    ):

        self.objects_processed += 1

        tags = dict(obj.tags)

        name = tags.get("name")

        if not name:
            return

        category = None
        subcategory = None

        # ----------------------------------------------------
        # AMENITY
        # ----------------------------------------------------

        amenity = tags.get("amenity")

        if amenity in AMENITY_TYPES:

            category, subcategory = AMENITY_TYPES[
                amenity
            ]

        # ----------------------------------------------------
        # TOURISM
        # ----------------------------------------------------

        elif tags.get("tourism") in TOURISM_TYPES:

            tourism = tags.get("tourism")

            category, subcategory = TOURISM_TYPES[
                tourism
            ]

        # ----------------------------------------------------
        # SHOP
        # ----------------------------------------------------

        elif tags.get("shop") in SHOP_TYPES:

            shop = tags.get("shop")

            category, subcategory = SHOP_TYPES[
                shop
            ]

        # ----------------------------------------------------
        # LEISURE
        # ----------------------------------------------------

        elif tags.get("leisure") in LEISURE_TYPES:

            leisure = tags.get("leisure")

            category, subcategory = LEISURE_TYPES[
                leisure
            ]

        # ----------------------------------------------------
        # NATURAL
        # ----------------------------------------------------

        elif tags.get("natural") in NATURAL_TYPES:

            natural = tags.get("natural")

            category, subcategory = NATURAL_TYPES[
                natural
            ]

        # ----------------------------------------------------
        # KHÔNG PHẢI ĐỊA ĐIỂM CẦN LẤY
        # ----------------------------------------------------

        if not category:
            return

        self.add_to_batch(
            obj.id,
            name,
            category,
            subcategory,
            latitude,
            longitude,
            tags
        )

    # ========================================================
    # NODE
    # ========================================================

    def node(self, n):

        latitude = None
        longitude = None

        try:

            if n.location.valid():

                latitude = n.location.lat
                longitude = n.location.lon

        except Exception:
            pass

        self.process_object(
            n,
            latitude,
            longitude
        )

    # ========================================================
    # WAY
    # ========================================================

    def way(self, w):

        latitudes = []
        longitudes = []

        try:

            for node in w.nodes:

                try:

                    if node.location.valid():

                        latitudes.append(
                            node.location.lat
                        )

                        longitudes.append(
                            node.location.lon
                        )

                except Exception:
                    pass

        except Exception:
            pass

        latitude = None
        longitude = None

        if latitudes:

            latitude = sum(
                latitudes
            ) / len(latitudes)

            longitude = sum(
                longitudes
            ) / len(longitudes)

        self.process_object(
            w,
            latitude,
            longitude
        )

    # ========================================================
    # FINISH
    # ========================================================

    def finish(self):

        self.save_batch()

        self.cursor.close()


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print("OSM → POSTGRESQL / POSTGIS")
    print("DU LỊCH VIỆT NAM")
    print("=" * 70)

    print()

    if not os.path.exists(OSM_FILE):

        print("❌ Không tìm thấy file:")
        print(OSM_FILE)

        return

    # --------------------------------------------------------
    # CONNECT
    # --------------------------------------------------------

    print("🔌 Đang kết nối PostgreSQL...")

    try:

        connection = psycopg2.connect(
            **DB_CONFIG
        )

        print(
            "✅ Kết nối PostgreSQL thành công!"
        )

    except Exception as e:

        print(
            "❌ Không kết nối được PostgreSQL:"
        )

        print(e)

        return

    # --------------------------------------------------------
    # HANDLER
    # --------------------------------------------------------

    handler = TourismHandler(
        connection
    )

    print()
    print("📂 File OSM:")
    print(OSM_FILE)

    print()
    print("🚀 Bắt đầu đọc OSM...")
    print(
        f"📦 Batch: {BATCH_SIZE:,} địa điểm"
    )

    print()
    print(
        "⚠️ Không đóng Terminal trong lúc chạy."
    )

    print()

    try:

        handler.apply_file(
            OSM_FILE,
            locations=True
        )

        # Lưu batch cuối cùng
        handler.finish()

        print()

        print("=" * 70)
        print("✅ HOÀN THÀNH")
        print("=" * 70)

    except KeyboardInterrupt:

        print()
        print(
            "⚠️ Người dùng dừng chương trình."
        )

        # Cực kỳ quan trọng:
        # lưu batch hiện tại trước khi thoát
        handler.save_batch()

        handler.cursor.close()

    except Exception as e:

        print()
        print(
            "❌ LỖI:"
        )

        print(e)

        handler.connection.rollback()

        handler.cursor.close()

    finally:

        connection.close()

    print()

    print(
        f"🔎 Object đã xử lý: "
        f"{handler.objects_processed:,}"
    )

    print(
        f"📍 Địa điểm tìm thấy: "
        f"{handler.places_found:,}"
    )

    print(
        f"💾 Đã lưu Database: "
        f"{handler.places_saved:,}"
    )

    print()

    print(
        "Database: tourism_db"
    )

    print(
        "Table: places"
    )


if __name__ == "__main__":
    main()