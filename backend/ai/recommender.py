from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from math import radians, sin, cos, sqrt, atan2
import re


# ============================================================
# SAFE CONVERTERS
# ============================================================

def safe_float(value):
    if value is None:
        return None

    try:
        number = float(value)

        if number < 0:
            return None

        return number

    except (TypeError, ValueError):
        return None


def safe_int(value):
    if value is None:
        return None

    try:
        return int(float(value))

    except (TypeError, ValueError):
        return None


# ============================================================
# PLACE
# ============================================================

@dataclass
class Place:

    id: int

    name: str

    category: str

    subcategory: Optional[str] = None

    province_code: Optional[str] = None

    province_name: Optional[str] = None

    ward_id: Optional[int] = None

    ward_name: Optional[str] = None

    latitude: Optional[float] = None

    longitude: Optional[float] = None

    rating: Optional[float] = None

    review_count: Optional[int] = None

    description: Optional[str] = None

    price_min: Optional[float] = None

    price_max: Optional[float] = None

    price_source_url: Optional[str] = None

    price_source: Optional[str] = None

    price_checked_at: Optional[str] = None

    price_kind: Optional[str] = None

    opening_hours: Optional[str] = None

    # ========================================================
    # THÔNG TIN BỔ SUNG
    # ========================================================

    address: Optional[str] = None

    image_url: Optional[str] = None

    image_source_url: Optional[str] = None

    image_source_name: Optional[str] = None

    image_license: Optional[str] = None

    distance_km: Optional[float] = None

    district_name: Optional[str] = None

    directory_source_url: Optional[str] = None

    directory_phone: Optional[str] = None

    directory_website: Optional[str] = None


# ============================================================
# TOURISM RECOMMENDER
# ============================================================

class TourismRecommender:

    def __init__(
        self,
        places: list[Place],
    ):

        self.places = places

    # ========================================================
    # GET PLACE BY ID
    # ========================================================

    def get_place_by_id(
        self,
        place_id,
    ):

        if place_id is None:
            return None

        try:
            target_id = int(place_id)

        except (TypeError, ValueError):
            return None

        for place in self.places:

            try:
                current_id = int(place.id)

            except (TypeError, ValueError):
                continue

            if current_id == target_id:
                return place

        return None

    # ========================================================
    # HAVERSINE DISTANCE
    # ========================================================

    @staticmethod
    def _distance_km(
        latitude1,
        longitude1,
        latitude2,
        longitude2,
    ):

        try:

            lat1 = float(latitude1)
            lon1 = float(longitude1)

            lat2 = float(latitude2)
            lon2 = float(longitude2)

        except (TypeError, ValueError):

            return None

        # ---------------------------------------------
        # Convert degrees -> radians
        # ---------------------------------------------

        lat1 = radians(lat1)
        lon1 = radians(lon1)

        lat2 = radians(lat2)
        lon2 = radians(lon2)

        # ---------------------------------------------
        # Haversine
        # ---------------------------------------------

        dlat = lat2 - lat1
        dlon = lon2 - lon1

        a = (
            sin(dlat / 2) ** 2
            +
            cos(lat1)
            * cos(lat2)
            * sin(dlon / 2) ** 2
        )

        c = 2 * atan2(
            sqrt(a),
            sqrt(1 - a),
        )

        earth_radius_km = 6371.0

        return earth_radius_km * c

    # ========================================================
    # RECOMMEND NEARBY
    # ========================================================

    def recommend_nearby(
        self,
        *,
        latitude,
        longitude,
        province_code=None,
        interests=None,
        limit=20,
        radius_km=5,
        exclude_id=None,
    ):

        interests = interests or []

        interests = [
            str(item)
            .strip()
            .lower()
            for item in interests
            if item
        ]

        # ----------------------------------------------------
        # Categories suitable for tourism activities
        # ----------------------------------------------------

        tourism_categories = {

            "tham quan",

            "ăn uống",

            "cafe",

            "thiên nhiên",

            "giải trí",

            "vui chơi",

            "mua sắm",

        }

        candidates = []

        # ====================================================
        # LOOP PLACES
        # ====================================================

        for place in self.places:

            # ------------------------------------------------
            # Exclude selected hotel
            # ------------------------------------------------

            if exclude_id is not None:

                try:

                    if int(place.id) == int(exclude_id):
                        continue

                except (TypeError, ValueError):

                    pass

            # ------------------------------------------------
            # Province
            # ------------------------------------------------

            if province_code:

                current_province = str(
                    place.province_code or ""
                ).strip()

                requested_province = str(
                    province_code
                ).strip()

                if current_province != requested_province:
                    continue

            # ------------------------------------------------
            # Coordinates
            # ------------------------------------------------

            if (
                place.latitude is None
                or place.longitude is None
            ):
                continue

            # ------------------------------------------------
            # Category
            # ------------------------------------------------

            category = str(
                place.category or ""
            ).strip().lower()

            subcategory = str(
                place.subcategory or ""
            ).strip().lower()

            # ------------------------------------------------
            # Do not use accommodation as an activity
            # ------------------------------------------------

            accommodation_keywords = [
                "lưu trú",
                "khách sạn",
                "hotel",
                "hostel",
                "resort",
                "motel",
                "homestay",
                "nhà nghỉ",
            ]

            if any(
                keyword in category
                or keyword in subcategory
                for keyword in accommodation_keywords
            ):
                continue

            # ------------------------------------------------
            # Only tourism-related categories
            # ------------------------------------------------

            is_tourism_category = False

            for tourism_category in tourism_categories:

                if (
                    tourism_category in category
                    or tourism_category in subcategory
                ):
                    is_tourism_category = True
                    break

            # ------------------------------------------------
            # If user selected preferences,
            # only prioritize matching activities.
            #
            # But if there is no match, keep the place
            # only when it is a tourism category.
            # ------------------------------------------------

            preference_score = (
                self._preference_score(
                    category,
                    subcategory,
                    interests,
                )
            )

            if interests:

                matched_interest = False

                for interest in interests:

                    if (
                        interest in category
                        or interest in subcategory
                    ):
                        matched_interest = True
                        break

                if not matched_interest:
                    continue

            else:

                if not is_tourism_category:
                    continue

            # ------------------------------------------------
            # Distance
            # ------------------------------------------------

            distance = self._distance_km(
                latitude,
                longitude,
                place.latitude,
                place.longitude,
            )

            if distance is None:
                continue

            # ------------------------------------------------
            # Radius
            # ------------------------------------------------

            if distance > radius_km:
                continue

            # ------------------------------------------------
            # Scores
            # ------------------------------------------------

            rating_score = self._rating_score(
                place.rating
            )

            review_score = self._review_score(
                place.review_count
            )

            # ------------------------------------------------
            # Small radius keeps the plan near the hotel. A wider radius is
            # an explicit request to explore farther, so rank outward places
            # higher instead of silently clustering around the hotel.
            # ------------------------------------------------

            if radius_km <= 0:

                distance_score = 0.0

            else:

                distance_ratio = min(1.0, distance / radius_km)
                distance_score = (
                    distance_ratio if radius_km >= 15
                    else max(0.0, 1.0 - distance_ratio)
                )

            # ------------------------------------------------
            # Tourism category bonus
            # ------------------------------------------------

            category_score = (
                1.0
                if is_tourism_category
                else 0.3
            )

            # ------------------------------------------------
            # Final score
            # ------------------------------------------------

            prominence_score = self._prominence_score(place)
            if radius_km >= 15:
                final_score = (
                    0.25 * preference_score
                    + 0.10 * rating_score
                    + 0.05 * review_score
                    + 0.28 * distance_score
                    + 0.12 * category_score
                    + 0.20 * prominence_score
                )
            else:
                final_score = (
                    0.30 * preference_score
                    + 0.20 * rating_score
                    + 0.10 * review_score
                    + 0.25 * distance_score
                    + 0.10 * category_score
                    + 0.05 * prominence_score
                )

            candidates.append(
                (
                    final_score,
                    distance,
                    place,
                )
            )

        # ====================================================
        # SORT
        # ====================================================

        candidates.sort(
            key=lambda item: (
                item[0],
                -item[1],
            ),
            reverse=True,
        )

        # ====================================================
        # RETURN
        # ====================================================

        result = []

        for score, distance, place in candidates[:limit]:

            # ----------------------------------------------
            # Gắn khoảng cách vào object
            # ----------------------------------------------

            place.distance_km = round(
                distance,
                2,
            )

            result.append(place)

        return result

    @staticmethod
    def _prominence_score(place):
        """Map feature types and recognizable landmarks substitute for missing review data."""
        import unicodedata

        def fold(value):
            value = unicodedata.normalize("NFD", str(value or "").lower())
            value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
            return value.replace("đ", "d")

        name = fold(getattr(place, "name", ""))
        subcategory = fold(getattr(place, "subcategory", ""))
        # City names often appear in gym/fitness business names. They should
        # not outrank actual travel stops just because they include a region.
        non_sightseeing = (
            "phong tap", "trung tam the thao", "the thao", "gym", "fitness",
            "yoga", "martial arts", "khach san", "hotel", "hostel", "resort",
            "homestay", "guest house", "villa", "spa",
        )
        if any(term in subcategory or term in name for term in non_sightseeing):
            return 0.40
        # Recognize well-known destinations across all 34 current provinces.
        # Province mergers made many former inter-provincial day trips local
        # to one large province (e.g. Da Nang–Hoi An–Tam Ky); names are kept
        # province-agnostic so the ranking works with any selected province.
        landmark_names = (
            # Northern mountains and Red River Delta
            "hoan kiem", "ho guom", "van mieu", "ho chi minh mausoleum", "lang chu tich ho chi minh",
            "chua mot cot", "nha tu hoa lo", "quang truong ba dinh", "ba vi national park", "ban gioc",
            "poc bo", "nguom ngao", "dong van karst", "lung cu flag tower", "lo lo chai", "ma pi leng",
            "a pa chai", "dien bien phu battlefield", "o quy ho pass", "sin ho plateau", "ta xua",
            "fansipan", "y ty", "mau son mountain", "chi lang pass", "ba be lake", "dinh hoa at",
            "ha long bay", "yen tu pagoda", "co to island", "cat ba national park", "lan ha bay", "do son beach", "pho hien",
            "but thap", "do temple", "hung kings", "den hung", "tam dao", "xuan son",
            "trang an", "tam coc", "cuc phuong", "van long", "sam son beach", "pu luong",
            "lam kinh", "que phong", "pu mat", "cua lo", "thien cam",
            "ke go", "dong loc", "phong nha", "son doong", "vinh moc", "la vang",
            # Central Coast and Central Highlands
            "ba na", "hoi an ancient town", "ancient town hoi an", "chua cau", "japanese covered bridge",
            "my son", "phu ninh lake", "ngu hanh son", "son tra peninsula", "cau vang",
            "cho con", "cho han", "cho dem helio", "helio center", "cau rong", "cau tinh yeu",
            "bien my khe", "my khe beach", "linh ung pagoda", "hai van pass", "hai van quan",
            "asia park", "sun wheel", "thanh dia my son",
            "ly son island", "vinwonders", "dray nur", "dray sap", "xuan huong lake", "langbiang",
            # Quy Nhơn and the former Bình Định area are now part of Gia Lai.
            # Keep their headline coastal and Cham heritage sites discoverable
            # after the 2025 administrative merger.
            "ky co", "eo gio", "hon kho", "hon kho island", "ghenh rang", "tien sa",
            "thap doi", "doi cham towers", "thap banh it", "banh it cham towers",
            "thap duong long", "bao tang quang trung", "quang trung museum",
            "dam thi nai", "cu lao xanh", "tinh xa ngoc hoa", "ky co beach",
            "ke ga lighthouse", "ta cu mountain", "cat tien national park",
            "tri an lake", "cu chi tunnels", "can gio biosphere", "ho may park",
            "christ of vung tau", "nui ba den", "dau tieng lake", "sa dec flower village",
            "cai be floating market", "ao ba om", "con chim islet", "sam mountain",
            "phu quoc vinwonders", "cai rang floating market", "dat mui cape",
            "um minh ha national park",
        )
        # A place name must match whole normalized words and belong to an
        # attraction/nature/entertainment category. Plain substring matching
        # used to mark restaurants like "My Son Valley Restaurant" and shops
        # like "Ba Nam" as famous landmarks because they contain those letters.
        landmark_categories = {"tham quan", "thien nhien", "vui choi"}
        category = fold(getattr(place, "category", ""))
        is_visit_category = category in landmark_categories
        recognized_market = category == "mua sam" and any(
            re.search(r"\b" + re.escape(term) + r"\b", name)
            for term in ("cho con", "cho han", "cho dem helio", "helio center", "cai rang floating market")
        )
        if (is_visit_category and any(
            re.search(r"\b" + re.escape(term) + r"\b", name)
            for term in landmark_names
        )) or recognized_market:
            return 1.0
        # Province or city names can be useful evidence of a regional stop,
        # but are scored just below a named landmark so a famous monument,
        # park, lake, or historic site wins when both fit the selected radius.
        regional_names_by_province = {
            "01": ("hoan kiem", "ho guom", "van mieu", "chua mot cot", "nha tu hoa lo", "ba dinh"),
            "04": ("ban gioc", "poc bo", "nguom ngao"),
            "08": ("na hang", "tan trao", "dong van", "lung cu", "ma pi leng"),
            "11": ("dien bien phu", "a pa chai"),
            "12": ("o quy ho", "sin ho"),
            "14": ("moc chau", "ta xua"),
            "15": ("fansipan", "sa pa", "y ty"),
            "19": ("ba be", "dinh hoa"),
            "20": ("mau son", "chi lang"),
            "22": ("ha long", "yen tu", "co to"),
            "24": ("but thap", "do temple"),
            "25": ("den hung", "hung kings", "tam dao", "xuan son"),
            "31": ("cat ba", "lan ha", "do son"),
            "33": ("pho hien",),
            "37": ("trang an", "tam coc", "cuc phuong", "van long"),
            "38": ("sam son", "pu luong", "lam kinh"),
            "40": ("kim lien", "pu mat", "cua lo"),
            "42": ("thien cam", "ke go", "dong loc"),
            "44": ("phong nha", "son doong", "vinh moc", "la vang"),
            "46": ("thien mu", "dai noi hue"),
            "48": ("ba na", "hoi an", "my son", "phu ninh", "tam ky", "ngu hanh son", "son tra", "cau vang", "cho con", "cau rong", "my khe", "helio"),
            "51": ("ly son", "sa huynh"),
            "52": (
                "mang den", "bien ho", "thac pa sy", "ky co", "eo gio", "hon kho",
                "ghenh rang", "thap doi", "thap banh it", "thap duong long",
                "bao tang quang trung", "dam thi nai", "cu lao xanh", "tinh xa ngoc hoa",
            ),
            "56": ("nha trang", "vinwonders", "ninh chu", "vinh hy"),
            "66": ("buon don", "lak lake", "ho lak", "dray nur"),
            "68": ("da lat", "xuan huong", "langbiang", "mui ne", "ke ga", "ta cu"),
            "75": ("cat tien", "tri an"),
            "79": ("cu chi", "can gio", "vung tau"),
            "80": ("nui ba den", "dau tieng"),
            "82": ("tram chim", "xeo quit", "sa dec"),
            "86": ("cai be", "ao ba om", "con chim"),
            "91": ("chau doc", "sam mountain", "phu quoc"),
            "92": ("cai rang",),
            "96": ("dat mui", "um minh ha"),
        }
        province_code = str(getattr(place, "province_code", "") or "").strip()
        if is_visit_category and any(
            re.search(r"\b" + re.escape(term) + r"\b", name)
            for term in regional_names_by_province.get(province_code, ())
        ):
            return 0.92

        notable_types = (
            "cong vien giai tri", "vuon thu", "thuy cung", "diem tham quan", "diem ngam canh",
            "bao tang", "di tich", "thac nuoc", "bai bien", "khu bao ton",
        )
        if any(term in subcategory for term in notable_types):
            return 0.85
        if fold(getattr(place, "category", "")) in {"tham quan", "thien nhien"}:
            return 0.65
        return 0.40

    # ========================================================
    # NORMAL RECOMMEND
    # ========================================================

    def recommend(
        self,
        province_code: Optional[str] = None,
        categories: Optional[list[str]] = None,
        interests: Optional[list[str]] = None,
        budget_min: Optional[float] = None,
        budget_max: Optional[float] = None,
        limit: int = 20,
    ):

        categories = categories or []
        interests = interests or []

        categories = [
            str(x).strip().lower()
            for x in categories
            if x
        ]

        interests = [
            str(x).strip().lower()
            for x in interests
            if x
        ]

        candidates = []

        # ====================================================
        # LOOP
        # ====================================================

        for place in self.places:

            # ------------------------------------------------
            # Province
            # ------------------------------------------------

            if province_code:

                if (
                    str(
                        place.province_code or ""
                    ).strip()
                    !=
                    str(
                        province_code
                    ).strip()
                ):
                    continue

            category = (
                place.category or ""
            ).lower()

            subcategory = (
                place.subcategory or ""
            ).lower()

            # ------------------------------------------------
            # Category filter
            # ------------------------------------------------

            if categories:

                matched = False

                for item in categories:

                    if item in category:
                        matched = True
                        break

                    if item in subcategory:
                        matched = True
                        break

                if not matched:
                    continue

            # ------------------------------------------------
            # Preference
            # ------------------------------------------------

            preference_score = (
                self._preference_score(
                    category,
                    subcategory,
                    interests,
                )
            )

            # ------------------------------------------------
            # Rating
            # ------------------------------------------------

            rating_score = (
                self._rating_score(
                    place.rating
                )
            )

            # ------------------------------------------------
            # Reviews
            # ------------------------------------------------

            review_score = (
                self._review_score(
                    place.review_count
                )
            )

            # ------------------------------------------------
            # Budget
            # ------------------------------------------------

            budget_score = (
                self._budget_score(
                    place.price_min,
                    place.price_max,
                    budget_min,
                    budget_max,
                )
            )

            # ------------------------------------------------
            # Final
            # ------------------------------------------------

            final_score = (

                0.40
                * preference_score

                +

                0.25
                * rating_score

                +

                0.15
                * review_score

                +

                0.20
                * budget_score
            )

            candidates.append(
                (
                    final_score,
                    place,
                )
            )

        # ====================================================
        # SORT
        # ====================================================

        candidates.sort(
            key=lambda item: item[0],
            reverse=True,
        )

        return [
            place
            for _, place
            in candidates[:limit]
        ]

    # ========================================================
    # SERIALIZE
    # ========================================================

    def serialize(
        self,
        place: Place,
        score=0,
        distance=None,
        explanation=None,
    ):

        if distance is None:

            distance = getattr(
                place,
                "distance_km",
                None,
            )

        return {

            "id":
                place.id,

            "name":
                place.name,

            "category":
                place.category,

            "subcategory":
                place.subcategory,

            "province_code":
                place.province_code,

            "province":
                place.province_name,

            "ward":
                place.ward_name,

            "address":
                getattr(
                    place,
                    "address",
                    None,
                ),

            "latitude":
                place.latitude,

            "longitude":
                place.longitude,

            "rating":
                place.rating,

            "review_count":
                place.review_count,

            "directory_source_url": getattr(place, "directory_source_url", None),

            "directory_phone": getattr(place, "directory_phone", None),

            "directory_website": getattr(place, "directory_website", None),

            "price_min":
                place.price_min,

            "price_max":
                place.price_max,

            "image_url":
                getattr(
                    place,
                    "image_url",
                    None,
                ),

            "image_source_url": getattr(place, "image_source_url", None),

            "image_source_name": getattr(place, "image_source_name", None),

            "image_license": getattr(place, "image_license", None),

            "score":
                round(
                    score,
                    4,
                ),

            "distance_km":
                round(
                    distance,
                    2,
                )
                if distance is not None
                else None,

            "explanation":
                explanation or [],
        }

    # ========================================================
    # PREFERENCE SCORE
    # ========================================================

    @staticmethod
    def _preference_score(
        category,
        subcategory,
        interests,
    ):

        if not interests:
            return 0.5

        for interest in interests:

            interest = str(
                interest
            ).strip().lower()

            if interest in category:
                return 1.0

            if interest in subcategory:
                return 0.9

            # ----------------------------------------------
            # Một số mapping tiếng Việt
            # ----------------------------------------------

            aliases = {

                "food": "ăn uống",

                "restaurant": "ăn uống",

                "restaurants": "ăn uống",

                "cafe": "cafe",

                "coffee": "cafe",

                "sightseeing": "tham quan",

                "attraction": "tham quan",

                "nature": "thiên nhiên",

                "entertainment": "giải trí",

                "shopping": "mua sắm",

                "play": "vui chơi",
            }

            mapped = aliases.get(
                interest
            )

            if mapped:

                if mapped in category:
                    return 1.0

                if mapped in subcategory:
                    return 0.9

        return 0.2

    # ========================================================
    # RATING SCORE
    # ========================================================

    @staticmethod
    def _rating_score(
        rating,
    ):

        rating = safe_float(
            rating
        )

        if rating is None:
            return 0.5

        return min(
            max(
                rating / 5.0,
                0.0,
            ),
            1.0,
        )

    # ========================================================
    # REVIEW SCORE
    # ========================================================

    @staticmethod
    def _review_score(
        review_count,
    ):

        review_count = safe_int(
            review_count
        )

        if review_count is None:
            return 0.5

        if review_count >= 1000:
            return 1.0

        if review_count >= 500:
            return 0.9

        if review_count >= 100:
            return 0.8

        if review_count >= 50:
            return 0.7

        if review_count >= 10:
            return 0.6

        return 0.5

    # ========================================================
    # BUDGET SCORE
    # ========================================================

    @staticmethod
    def _budget_score(
        price_min,
        price_max,
        budget_min,
        budget_max,
    ):

        if (
            budget_min is None
            and budget_max is None
        ):
            return 0.5

        price_min = safe_float(
            price_min
        )

        price_max = safe_float(
            price_max
        )

        # ----------------------------------------------------
        # Prefer minimum known price
        # ----------------------------------------------------

        price = price_min

        if price is None:
            price = price_max

        if price is None:
            return 0.5

        # ----------------------------------------------------
        # Below minimum
        # ----------------------------------------------------

        if (
            budget_min is not None
            and price < budget_min
        ):
            return 0.3

        # ----------------------------------------------------
        # Above maximum
        # ----------------------------------------------------

        if (
            budget_max is not None
            and price > budget_max
        ):
            return 0.2

        return 1.0
