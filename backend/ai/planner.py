from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from math import ceil
import json
import os
import re
import unicodedata
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen
from .cost_estimator import CostEstimator, CostEstimate
from .demo_pricing import demo_per_person_cost, demo_price
from .demo_data import demo_opening_hours, displayable_image_url


def days_without_sightseeing(itinerary: list[dict[str, Any]]) -> list[int]:
    """Find days with no real attraction, recreation or nature stop."""
    gaps = []
    for index, day in enumerate(itinerary, start=1):
        has_outing = False
        for slot in day.get("schedule", []):
            place = slot.get("place") or {}
            raw_category = " ".join(str(place.get(key) or "") for key in ("category", "subcategory", "type"))
            normalized = "".join(
                char for char in unicodedata.normalize("NFKD", raw_category.casefold())
                if not unicodedata.combining(char)
            ).replace("đ", "d")
            if any(term in normalized for term in ("tham quan", "vui choi", "thien nhien", "giai tri")):
                has_outing = True
                break
        if not has_outing:
            gaps.append(int(day.get("day") or index))
    return gaps


def plan_quality_warnings(itinerary: list[dict[str, Any]], missing_slots: int = 0) -> list[str]:
    warnings = []
    if missing_slots:
        warnings.append(
            "%d hoạt động chưa thể xếp vì thiếu địa điểm mới phù hợp; hãy mở rộng bán kính hoặc bổ sung dữ liệu POI." % missing_slots
        )
    gaps = days_without_sightseeing(itinerary)
    if gaps:
        day_labels = ", ".join(str(day) for day in gaps)
        warnings.append(
            f"Ngày {day_labels} chưa có điểm tham quan, vui chơi hoặc thiên nhiên phù hợp với bộ lọc dữ liệu hiện tại. Hãy thử mở rộng phạm vi hoặc chọn sở thích khác."
        )
    return warnings


class TripPlanner:
    """
    Tạo lịch trình theo timeline thay vì chỉ chia N địa điểm vào từng ngày.

    Mỗi ngày ưu tiên:
      1. Ăn sáng
      2. Tham quan buổi sáng
      3. Ăn trưa
      4. Nghỉ trưa
      5. Tham quan / mua sắm buổi chiều
      6. Cafe / ăn nhẹ
      7. Ăn tối
      8. Vui chơi / tham quan buổi tối

    Ngày đầy đủ nhắm tới 4 hoạt động buổi sáng, 3 buổi chiều, 2 buổi tối;
    các bữa ăn, nghỉ và cà phê được xếp riêng. Ngày khởi hành được giảm tải.
    Vẫn giữ nguyên interface create_plan(...) để main.py không cần đổi.
    """

    # Outing opportunities are placed across the usable day, with meals,
    # rest and coffee breaks in between. They are a scheduling canvas, not a
    # user-facing maximum: routing and opening hours decide what fits.
    DAILY_SLOTS = [
        ("07:00", "07:45", "Ăn sáng", "Ăn uống", 45, "meal"),
        ("08:00", "08:45", "Khám phá buổi sáng", "Tham quan", 45, "outing_morning"),
        ("08:55", "09:40", "Khám phá buổi sáng", "Tham quan", 45, "outing_morning"),
        ("09:45", "10:15", "Cafe buổi sáng", "Cafe", 30, "cafe"),
        ("10:25", "11:10", "Khám phá buổi sáng", "Tham quan", 45, "outing_morning"),
        ("11:20", "12:05", "Khám phá buổi sáng", "Tham quan", 45, "outing_morning"),
        ("12:30", "13:15", "Ăn trưa", "Ăn uống", 45, "meal_lunch"),
        ("13:15", "13:30", "Nghỉ trưa tại khu vực tham quan", None, 15, "rest"),
        ("13:30", "14:15", "Khám phá buổi chiều", "Tham quan", 45, "outing_afternoon"),
        ("14:25", "15:10", "Khám phá buổi chiều", "Tham quan", 45, "outing_afternoon"),
        ("15:10", "15:40", "Cafe / ăn nhẹ", "Cafe", 30, "cafe"),
        ("16:00", "16:45", "Khám phá cuối buổi chiều", "Tham quan", 45, "outing_afternoon"),
        ("17:00", "18:30", "Về khách sạn tắm rửa và nghỉ trước bữa tối", None, 90, "hotel_break"),
        ("18:30", "19:30", "Ăn tối", "Ăn uống", 60, "meal_dinner"),
        ("19:45", "20:30", "Khám phá buổi tối", "Vui chơi", 45, "outing_evening"),
        ("20:40", "21:25", "Khám phá buổi tối", "Vui chơi", 45, "outing_evening"),
    ]
    ACTIVITY_SLOT_TARGETS = {
        "morning": ({1, 2, 4, 5}, 4),
        "afternoon": ({8, 9, 11}, 3),
        "evening": ({14, 15}, 2),
    }

    # Historical province names folded into the current 34 administrative
    # units. A venue linked to Da Nang may legitimately say Quang Nam, but a
    # Saigon address must never be scheduled as a Da Nang business.
    PROVINCE_ADDRESS_ALIASES = {
        "01": ("Ha Noi",), "04": ("Cao Bang",), "08": ("Tuyen Quang", "Ha Giang"),
        "11": ("Dien Bien",), "12": ("Lai Chau",), "14": ("Son La",),
        "15": ("Lao Cai", "Yen Bai"), "19": ("Thai Nguyen", "Bac Kan"),
        "20": ("Lang Son",), "22": ("Quang Ninh",), "24": ("Bac Ninh", "Bac Giang"),
        "25": ("Phu Tho", "Vinh Phuc", "Hoa Binh"), "31": ("Hai Phong", "Hai Duong"),
        "33": ("Hung Yen", "Thai Binh"), "37": ("Ninh Binh", "Ha Nam", "Nam Dinh"),
        "38": ("Thanh Hoa",), "40": ("Nghe An",), "42": ("Ha Tinh",),
        "44": ("Quang Tri", "Quang Binh"), "46": ("Hue", "Thua Thien Hue"),
        "48": ("Da Nang", "Quang Nam"), "51": ("Quang Ngai", "Kon Tum"),
        "52": ("Gia Lai", "Binh Dinh"), "56": ("Khanh Hoa", "Ninh Thuan"),
        "66": ("Dak Lak", "Phu Yen"), "68": ("Lam Dong", "Binh Thuan", "Dak Nong"),
        "75": ("Dong Nai", "Binh Duong"),
        "79": ("Ho Chi Minh", "Ba Ria Vung Tau", "Binh Duong", "Sai Gon"),
        "80": ("Tay Ninh", "Long An"), "82": ("Dong Thap", "Tien Giang"),
        "86": ("Vinh Long", "Ben Tre", "Tra Vinh"), "91": ("An Giang", "Kien Giang"),
        "92": ("Can Tho", "Soc Trang", "Hau Giang"), "96": ("Ca Mau", "Bac Lieu"),
    }
    ALL_PROVINCE_ADDRESS_NAMES = tuple({
        "Ha Noi", "Cao Bang", "Tuyen Quang", "Ha Giang", "Dien Bien", "Lai Chau", "Son La",
        "Lao Cai", "Yen Bai", "Thai Nguyen", "Bac Kan", "Lang Son", "Quang Ninh", "Bac Ninh",
        "Bac Giang", "Phu Tho", "Vinh Phuc", "Hoa Binh", "Hai Phong", "Hai Duong", "Hung Yen",
        "Thai Binh", "Ninh Binh", "Ha Nam", "Nam Dinh", "Thanh Hoa", "Nghe An", "Ha Tinh",
        "Quang Tri", "Quang Binh", "Hue", "Thua Thien Hue", "Da Nang", "Quang Nam", "Quang Ngai",
        "Kon Tum", "Gia Lai", "Binh Dinh", "Khanh Hoa", "Ninh Thuan", "Dak Lak", "Phu Yen",
        "Lam Dong", "Binh Thuan", "Dak Nong", "Dong Nai", "Binh Duong", "Ho Chi Minh",
        "Ba Ria Vung Tau", "Sai Gon", "Tay Ninh", "Long An", "Dong Thap", "Tien Giang",
        "Vinh Long", "Ben Tre", "Tra Vinh", "An Giang", "Kien Giang", "Can Tho", "Soc Trang",
        "Hau Giang", "Ca Mau", "Bac Lieu",
    })

    # Nếu không tìm được "Vui chơi", fallback sang "Tham quan".
    FALLBACK_CATEGORY = {
        "Vui chơi": "Tham quan",
    }
    SLOT_FALLBACKS = {
        "Ăn uống": [],
        "Cafe": [],
        "Tham quan": ["Vui chơi", "Thiên nhiên", "Mua sắm"],
        "Vui chơi": ["Tham quan", "Thiên nhiên", "Mua sắm"],
        "Thiên nhiên": ["Tham quan", "Vui chơi"],
        "Mua sắm": ["Tham quan", "Vui chơi"],
    }

    def __init__(self, recommender):
        self.recommender = recommender
        self.cost_estimator = CostEstimator()

    @staticmethod
    def _full_address(place):
        """Return a clean street, current ward, and province address."""
        normalize = lambda value: "".join(
            char for char in unicodedata.normalize("NFKD", str(value or "").casefold())
            if not unicodedata.combining(char)
        ).replace("đ", "d")
        strip_level = lambda value: re.sub(
            r"^(phường|phuong|xã|xa|thị trấn|thi tran|đặc khu|dac khu|"
            r"quận|quan|huyện|huyen|thị xã|thi xa|thành phố|thanh pho|tỉnh|tinh)\s+",
            "", str(value or "").strip(), flags=re.IGNORECASE,
        )
        locality_keys = [normalize(strip_level(value)) for value in (
            getattr(place, "ward_name", None),
            getattr(place, "district_name", None),
            getattr(place, "province_name", None),
        ) if value]
        street_parts = []
        for component in (part.strip(" ,") for part in str(getattr(place, "address", None) or "").split(",")):
            if not component:
                continue
            key = normalize(strip_level(component))
            is_admin = (
                any(key == known or (len(min(key, known)) >= 5 and (key in known or known in key))
                    for known in locality_keys)
                or any(normalize(legacy) == key for legacy in TripPlanner.ALL_PROVINCE_ADDRESS_NAMES)
                or bool(re.match(r"^(phường|phuong|xã|xa|quận|quan|huyện|huyen|"
                                 r"thị trấn|thi tran|thành phố|thanh pho|tỉnh|tinh)\b", component, re.I))
            )
            if is_admin:
                continue
            # Source addresses usually put the house number and street in
            # separate comma-delimited pieces; keep at most those two pieces.
            if len(street_parts) < 2:
                street_parts.append(component)
        if len(street_parts) == 2 and re.fullmatch(r"\d+[A-Za-z]?", street_parts[0]):
            street_parts = [f"{street_parts[0]} {street_parts[1]}"]
        parts = street_parts
        # The current ward and province are authoritative; legacy district
        # names are omitted so an address cannot mix old and new boundaries.
        for value in (getattr(place, "ward_name", None), getattr(place, "province_name", None)):
            text = " ".join(str(value or "").split()).strip(" ,")
            key = normalize(strip_level(text))
            if text and all(key != normalize(strip_level(existing)) for existing in parts):
                parts.append(text)
        return ", ".join(parts) or None

    # ========================================================
    # CREATE PLAN
    # ========================================================

    def create_plan(
        self,
        *,
        province_code: str,
        days: int,
        people: int,
        budget=None,
        preferences=None,
        day_preferences=None,
        start_date=None,
        hotel_id=None,
        radius_km=50,
        limit_per_day=None,
        avoid_place_ids=None,
        excluded_categories=None,
        day_excluded_categories=None,
    ):
        preferences = preferences or []
        day_preferences = day_preferences or {}
        excluded_categories = {
            self._normalize_category(value) for value in (excluded_categories or []) if value
        }
        day_excluded_categories = day_excluded_categories or {}
        avoid_place_ids = {int(x) for x in (avoid_place_ids or []) if str(x).isdigit()}

        if days <= 0:
            raise ValueError("Số ngày phải lớn hơn 0")

        if people <= 0:
            raise ValueError("Số người phải lớn hơn 0")

        if not hotel_id:
            raise ValueError("Phải chọn khách sạn trước khi tạo lịch trình")

        # Compatibility argument for old clients; actual pace is time and
        # route based rather than limited by a selected number of stops.

        radius_km = float(radius_km or 50)
        if radius_km < 1 or radius_km > 100:
            raise ValueError("Phạm vi lịch trình phải từ 1 đến 100 km")

        if isinstance(start_date, str) and start_date.strip():
            try:
                start_date = date.fromisoformat(start_date.strip())
            except ValueError as exc:
                raise ValueError("Ngày khởi hành không hợp lệ") from exc
        elif isinstance(start_date, datetime):
            start_date = start_date.date()
        if start_date is not None and not isinstance(start_date, date):
            raise ValueError("Ngày khởi hành không hợp lệ")

        hotel = self.recommender.get_place_by_id(hotel_id)

        if hotel is None:
            raise ValueError(f"Không tìm thấy khách sạn có id={hotel_id}")

        hotel_lat = hotel.latitude
        hotel_lon = hotel.longitude

        if hotel_lat is None or hotel_lon is None:
            raise ValueError("Khách sạn chưa có tọa độ")

        # ----------------------------------------------------
        # Lấy pool theo từng nhóm.
        # Không dùng một danh sách chung rồi cắt [:5] nữa.
        # ----------------------------------------------------
        pool_size = max(days * 12, days + 12, len(avoid_place_ids) + days * 12) if avoid_place_ids else max(days * 12, days + 12)

        pools = {
            "Ăn uống": self._get_category_pool(
                province_code, hotel_lat, hotel_lon,
                "Ăn uống", pool_size, hotel_id, radius_km, avoid_place_ids
            ),
            "Cafe": self._get_category_pool(
                province_code, hotel_lat, hotel_lon,
                "Cafe", pool_size, hotel_id, radius_km, avoid_place_ids
            ),
            "Tham quan": self._get_category_pool(
                province_code, hotel_lat, hotel_lon,
                "Tham quan", pool_size, hotel_id, radius_km, avoid_place_ids
            ),
            "Vui chơi": self._get_category_pool(
                province_code, hotel_lat, hotel_lon,
                "Vui chơi", pool_size, hotel_id, radius_km, avoid_place_ids
            ),
            "Mua sắm": self._get_category_pool(
                province_code, hotel_lat, hotel_lon,
                "Mua sắm", pool_size, hotel_id, radius_km, avoid_place_ids
            ),
            "__all__": self._get_category_pool(
                province_code, hotel_lat, hotel_lon,
                None, pool_size * 3, hotel_id, radius_km, avoid_place_ids
            ),
        }

        # Nếu preference có các nhóm khác, lấy thêm pool để không làm
        # mất tính cá nhân hóa của recommender hiện tại.
        for preference in preferences:
            normalized = self._normalize_category(preference)
            if normalized and normalized not in pools:
                pools[normalized] = self._get_category_pool(
                    province_code, hotel_lat, hotel_lon,
                    preference, pool_size, hotel_id, radius_km, avoid_place_ids
                )

        if excluded_categories:
            pools = {
                group: [
                    place for place in candidates
                    if self._normalize_category(self._effective_category(place)) not in excluded_categories
                ]
                for group, candidates in pools.items()
            }

        # In a wide-radius trip, an iconic theme park, beach, viewpoint, or
        # natural site should be eligible beside formally tagged attractions.
        # Otherwise a full category of ordinary museums can hide these stops.
        if radius_km >= 15:
            sightseeing_candidates = []
            sightseeing_ids = set()
            for group in ("Tham quan", "Vui chơi", "Thiên nhiên", "Mua sắm"):
                for place in pools.get(group, []):
                    try:
                        place_id = int(place.id)
                    except (TypeError, ValueError):
                        continue
                    if place_id not in sightseeing_ids:
                        sightseeing_ids.add(place_id)
                        sightseeing_candidates.append(place)
            pools["__sightseeing__"] = sightseeing_candidates

        evening_candidates = []
        evening_ids = set()
        # Evening is normally for a second activity (mall, cafe, show, etc.).
        # A second restaurant is only allowed when the user explicitly asks.
        for group in ("Vui chơi", "Giải trí", "Ăn uống", "Cafe", "Mua sắm", "Tham quan"):
            for place in pools.get(group, []):
                try:
                    place_id = int(place.id)
                except (TypeError, ValueError):
                    continue
                if place_id not in evening_ids:
                    evening_ids.add(place_id)
                    evening_candidates.append(place)
        pools["__evening__"] = evening_candidates

        if not any(pools.values()):
            raise ValueError(
                "Không tìm thấy địa điểm du lịch phù hợp xung quanh khách sạn."
            )

        # ----------------------------------------------------
        # Tạo lịch từng ngày.
        # Ngày đầy đủ có tối đa 5 điểm hoạt động; ngày về được giảm tải.
        # Bữa ăn, cà phê, nghỉ trưa và quãng đường được xếp riêng theo timeline.
        # ----------------------------------------------------
        itinerary = []
        total_real_cost = 0.0
        total_estimated_cost = 0.0
        unpriced_places = 0
        activity_count = 0
        published_price_places = 0
        used_ids = {int(hotel_id)}
        # During a one-day rebuild, avoid_place_ids contains the current trip's
        # stops. Resolve them back to POIs so _same_place can also reject
        # duplicate database rows for the same named/nearby venue.
        used_places = []
        if avoid_place_ids:
            for place in getattr(self.recommender, "places", []):
                try:
                    if int(place.id) in avoid_place_ids:
                        used_places.append(place)
                except (AttributeError, TypeError, ValueError):
                    continue
        exploration_preference_cursor = 0
        shopping_preferred = any(
            self._normalize_category(value) == "Mua sắm"
            for value in preferences if value
        )
        shopping_preference_days = {
            int(day_number)
            for day_number, values in day_preferences.items()
            if str(day_number).isdigit()
            and any(self._normalize_category(value) == "Mua sắm" for value in (values or []) if value)
        }
        # Shopping is optional and only enters the itinerary when it is one
        # of the traveler's interests. Keep it occasional on longer trips.
        shopping_stop_limit = (
            max(1, (days + 2) // 3) if shopping_preferred else len(shopping_preference_days)
        )
        shopping_stops_scheduled = 0

        # A target budget is used as a soft affordability signal while
        # selecting stops. Published prices for a menu item are not a full
        # meal bill, so they must never force a route or make a cost claim.
        target_budget_per_person_day = (
            float(budget) / (days * people)
            if budget is not None and float(budget) > 0 else None
        )

        missing_total = 0

        for day in range(1, days + 1):
            final_departure_day = day == days and days > 1
            trip_weekday = (start_date + timedelta(days=day - 1)).weekday() if start_date else None
            schedule = []
            used_ids_today = {int(hotel_id)}
            used_places_today = []
            selected_today = 0
            sightseeing_selected_today = False
            day_cost = 0.0
            day_unpriced = 0
            day_missing = 0
            day_activity_count = 0
            day_published_price_places = 0
            previous_lat = hotel_lat
            previous_lon = hotel_lon
            extra_preferences = day_preferences.get(day, day_preferences.get(str(day), [])) or []
            normalized_extra_preferences = [self._normalize_category(x) for x in extra_preferences if x]
            extra_preferred = set(normalized_extra_preferences)
            preferred = {self._normalize_category(x) for x in [*preferences, *extra_preferences] if x}
            full_day_ba_na = False
            full_day_ba_na_location = None
            full_day_ba_na_end_time = None
            balanced_exploration_categories = []
            for value in [*preferences, *extra_preferences]:
                normalized = self._normalize_category(value)
                if normalized in {"Tham quan", "Vui chơi", "Thiên nhiên", "Mua sắm"} and normalized not in balanced_exploration_categories:
                    balanced_exploration_categories.append(normalized)

            # Day one includes the real-world arrival task. The user must
            # confirm early luggage drop/check-in with the hotel; do not imply
            # that a room is guaranteed before the property's check-in time.
            if day == 1:
                schedule.append({
                    "start_time": "06:30",
                    "end_time": "07:00",
                    "type": "Gửi hành lý / làm thủ tục lưu trú",
                    "duration_minutes": 30,
                    "description": "Đến khách sạn gửi hành lý; nhận phòng sớm chỉ khi khách sạn xác nhận. Nếu giờ đến khác, hãy điều chỉnh khung này.",
                    "description_en": "Leave your luggage at the hotel. Early check-in is subject to hotel confirmation; adjust this block if you arrive later.",
                    "location": hotel.name,
                    "location_address": self._full_address(hotel),
                    "place": None,
                    "return_to_hotel": False,
                })
            if final_departure_day:
                schedule.append({
                    "start_time": "08:05",
                    "end_time": "08:20",
                    "type": "Trả phòng / gửi hành lý",
                    "duration_minutes": 15,
                    "description": "Làm thủ tục trả phòng và gửi hành lý tại khách sạn; xác nhận trước thời gian giữ hành lý.",
                    "description_en": "Check out and leave your luggage at the hotel; confirm the property's luggage-storage policy.",
                    "location": hotel.name,
                    "location_address": self._full_address(hotel),
                    "place": None,
                    "return_to_hotel": False,
                })
            for slot_index, (
                start_time,
                end_time,
                slot_type,
                category,
                duration_minutes,
                slot_role,
            ) in enumerate(self.DAILY_SLOTS):
                shopping_requested_today = shopping_preferred or day in shopping_preference_days
                if not shopping_requested_today or shopping_stops_scheduled >= shopping_stop_limit:
                    eligible_pools = {
                        group: [place for place in candidates if self._effective_category(place) != "Mua sắm"]
                        for group, candidates in pools.items()
                    }
                else:
                    eligible_pools = pools
                excluded_today = {
                    self._normalize_category(value)
                    for value in day_excluded_categories.get(day, day_excluded_categories.get(str(day), [])) or []
                    if value
                }
                if excluded_today:
                    eligible_pools = {
                        group: [
                            place for place in candidates
                            if self._normalize_category(self._effective_category(place)) not in excluded_today
                        ]
                        for group, candidates in eligible_pools.items()
                    }
                # Keep the departure day light: breakfast, one nearby morning
                # attraction, a cafe stop, then return for luggage and depart.
                # Lunch and dinner belong on full travel days, not before a
                # morning departure.
                is_outing = slot_role.startswith("outing_")
                evening_slot = slot_role == "outing_evening"
                if final_departure_day and slot_role not in {
                    "meal", "outing_morning", "outing_afternoon", "cafe",
                    "meal_lunch", "meal_dinner",
                }:
                    continue
                if final_departure_day and slot_role == "cafe" and slot_index != 10:
                    continue
                if final_departure_day and slot_role == "outing_morning" and selected_today >= 1:
                    continue
                if final_departure_day and slot_role == "outing_afternoon" and selected_today >= 2:
                    continue
                if full_day_ba_na and slot_role in {"meal_lunch", "rest", "cafe", "outing_morning", "outing_afternoon"}:
                    # Lunch/rest/afternoon/cafe are covered by the full-day
                    # Bà Nà visit; keep dinner and evening as separate stops.
                    if slot_role == "meal_lunch":
                        return_start = full_day_ba_na_end_time or "15:30"
                        schedule.append({
                            "start_time": return_start,
                            "end_time": "17:30",
                            "type": "Trở về trung tâm và nghỉ ngơi",
                            "duration_minutes": 75,
                            "description": "Thời gian rời Bà Nà, về khu vực lưu trú, tắm rửa và nghỉ trước bữa tối.",
                            "description_en": "Travel back from Bà Nà to your accommodation area, freshen up, and rest before dinner.",
                            "slot_index": slot_index,
                            "place": None,
                            "travel_from_previous": None,
                            "return_to_hotel": True,
                            "location": hotel.name,
                            "location_address": self._full_address(hotel),
                        })
                        # The next stop is planned from the hotel area. The
                        # map retains the Bà Nà → hotel → dinner route.
                        previous_lat, previous_lon = hotel_lat, hotel_lon
                    continue
                if is_outing and not evening_slot and balanced_exploration_categories:
                    preference_index = exploration_preference_cursor % len(balanced_exploration_categories)
                    category = balanced_exploration_categories[preference_index]
                    exploration_preference_cursor += 1
                    daypart = "tối" if evening_slot else ("sáng" if slot_role == "outing_morning" else "chiều")
                    slot_type = f"{category} buổi {daypart}"

                # Nghỉ trưa không cần địa điểm.
                if category is None:
                    # Keep the midday break near lunch. Returning to a distant
                    # hotel after a day trip can push a "lunch break" into the
                    # late afternoon and waste an extra drive.
                    return_to_hotel = slot_role == "hotel_break"
                    schedule.append({
                        "start_time": start_time,
                        "end_time": end_time,
                        "type": slot_type,
                        "duration_minutes": duration_minutes,
                        "slot_index": slot_index,
                        "place": None,
                        "travel_from_previous": None,
                        "return_to_hotel": return_to_hotel,
                        **({
                            "description": "Nghỉ ngắn sau bữa trưa tại khu vực gần đó để tránh nắng; không cần quay lại khách sạn giữa ngày.",
                            "description_en": "Take a short post-lunch rest nearby to avoid the heat; there is no need to return to the hotel mid-day.",
                        } if slot_role == "rest" else {
                            "description": "Về khách sạn tắm rửa, nghỉ ngắn trước bữa tối.",
                            "description_en": "Return to the hotel to freshen up and rest briefly before dinner.",
                            "location": hotel.name,
                            "location_address": self._full_address(hotel),
                        } if return_to_hotel else {
                            "description": "Nghỉ ngắn, uống nước hoặc ăn nhẹ trước khi quay về khách sạn chuẩn bị cho buổi tối.",
                            "description_en": "Take a short break, drink water or have a snack before returning to the hotel to get ready for the evening.",
                        }),
                    })
                    if return_to_hotel:
                        previous_lat, previous_lon = hotel_lat, hotel_lon
                    continue


                # Meals must resolve to a named food venue; cafes and shops are
                # never used as substitutes. Move the search after the preceding
                # planned block so long visits do not leave lunch behind.
                if slot_role in {"meal", "meal_lunch", "meal_dinner"} and schedule:
                    try:
                        previous_planned_end = max(
                            self._minutes(item.get("end_time", start_time)) for item in schedule
                        )
                        meal_buffer = 0 if slot_role == "meal_lunch" else 15
                        start_time = max(start_time, self._format_minutes(previous_planned_end + meal_buffer))
                        end_time = self._format_minutes(self._minutes(start_time) + duration_minutes)
                    except (TypeError, ValueError):
                        pass

                # Ưu tiên đúng loại hoạt động; chỉ đổi sang loại gần nghĩa nếu
                # kho hiện tại không còn địa điểm mới cho khung này.
                # Không bao giờ thay hoạt động tối bằng điểm tham quan/thiên nhiên.
                # Danh thắng xa như Bà Nà cần được xếp ban ngày và phải có giờ mở cửa.
                # A restaurant is not a coffee stop. If the mapped dataset
                # has no actual cafe for this break, leave the optional break
                # out rather than relabeling a meal venue as a cafe.
                fallback_categories = [] if evening_slot or slot_role == "cafe" or slot_role in {"meal", "meal_lunch", "meal_dinner"} else self.SLOT_FALLBACKS.get(category, [])
                if category in self.FALLBACK_CATEGORY:
                    fallback_categories = [self.FALLBACK_CATEGORY[category], *fallback_categories]
                slot_used_ids = used_ids | used_ids_today
                slot_used_places = [*used_places, *used_places_today]
                select_category = (
                    "__evening__" if evening_slot else
                    "__sightseeing__"
                    if radius_km >= 15 and category == "Tham quan"
                    else category
                )
                # Keep each cluster together. A broad provincial radius may
                # admit a famous excursion (up to 45 km from the hotel), but
                # it must not turn into 35–50 km zigzags between consecutive
                # stops. Daytime points after the first stop stay within 15 km;
                # afternoon attractions stay close enough to return for dinner.
                near_hotel = (self._distance(previous_lat, previous_lon, hotel_lat, hotel_lon) or 0) <= 0.8
                outing_leg_limit = 25 if near_hotel else 15
                selected = self._select_next_place(
                    pools=eligible_pools,
                    preferred_category=select_category,
                    used_ids=slot_used_ids,
                    used_places=slot_used_places,
                    previous_lat=previous_lat,
                    previous_lon=previous_lon,
                    weekday=trip_weekday,
                    start_time=start_time,
                    end_time=end_time,
                    requested_visit_duration_minutes=duration_minutes if is_outing else None,
                    allowed_categories={category} if category in {"Ăn uống", "Cafe"} else None,
                    extra_exclude_ids=avoid_place_ids,
                    avoid_nearby_to=full_day_ba_na_location if full_day_ba_na and slot_role == "meal_dinner" else None,
                    min_distance_from_avoid_km=8 if full_day_ba_na and slot_role == "meal_dinner" else 0,
                    allow_evening_food="Ăn uống" in extra_preferred,
                    target_budget_per_person_day=target_budget_per_person_day,
                    prioritize_exploration=(
                        radius_km >= 15
                        and category in {"Tham quan", "Vui chơi", "Thiên nhiên"}
                        and not sightseeing_selected_today
                    ),
                    evening_only=evening_slot,
                    preferred_evening_categories=[*normalized_extra_preferences, *balanced_exploration_categories],
                    max_visit_minutes=None,
                    max_distance_from_previous_km=(
                        10
                        if category in {"Ăn uống", "Cafe"}
                        else min(float(radius_km), 25.0) if final_departure_day and is_outing
                        else outing_leg_limit if is_outing
                        else (10 if final_departure_day else None)
                    ),
                    max_distance_from_hotel_km=(
                        12 if category == "Mua sắm"
                        else 5 if final_departure_day and slot_role == "cafe"
                        else 15 if slot_role == "outing_afternoon"
                        else None
                    ),
                    hotel_latitude=hotel_lat,
                    hotel_longitude=hotel_lon,
                )
                actual_category = self._effective_category(selected) if selected is not None else category
                for fallback_category in fallback_categories:
                    if selected is not None:
                        break
                    selected = self._select_next_place(
                        pools=eligible_pools,
                        preferred_category=fallback_category,
                        used_ids=used_ids | used_ids_today,
                        used_places=[*used_places, *used_places_today],
                        previous_lat=previous_lat,
                        previous_lon=previous_lon,
                        weekday=trip_weekday,
                        start_time=start_time,
                        end_time=end_time,
                        requested_visit_duration_minutes=duration_minutes if is_outing else None,
                        allowed_categories=(
                            {"Tham quan", "Vui chơi", "Giải trí", "Thiên nhiên", "Mua sắm"}
                            if is_outing else {fallback_category}
                            if fallback_category in {"Ăn uống", "Cafe"} else None
                        ),
                        extra_exclude_ids=avoid_place_ids,
                        avoid_nearby_to=full_day_ba_na_location if full_day_ba_na and slot_role == "meal_dinner" else None,
                        min_distance_from_avoid_km=8 if full_day_ba_na and slot_role == "meal_dinner" else 0,
                        allow_evening_food="Ăn uống" in extra_preferred,
                        target_budget_per_person_day=target_budget_per_person_day,
                        evening_only=evening_slot,
                        preferred_evening_categories=[*normalized_extra_preferences, *balanced_exploration_categories],
                        max_visit_minutes=None,
                        max_distance_from_previous_km=(
                            10
                            if fallback_category in {"Ăn uống", "Cafe"}
                            else outing_leg_limit if is_outing else None
                        ),
                    )
                    if selected is not None:
                        actual_category = fallback_category

                # Never repeat a restaurant or cafe during the trip. If the
                # verified venue pool is exhausted, leave the meal unassigned
                # and report the shortage instead of duplicating a stop.
                if selected is None and category in {"Ăn uống", "Cafe"}:
                    if category == "Ăn uống":
                        local_food = self._nearby_food_candidates(
                            previous_lat, previous_lon, province_code,
                            radius_km=20 if slot_role == "meal_lunch" else 15,
                        )
                        present_food_ids = {
                            int(existing.id) for existing in eligible_pools.get("Ăn uống", [])
                            if str(getattr(existing, "id", "")).isdigit()
                        }
                        meal_search_pools = {
                            **eligible_pools,
                            "Ăn uống": list(eligible_pools.get("Ăn uống", [])) + [
                                place for place in local_food
                                if int(place.id) not in present_food_ids
                            ],
                        }
                    else:
                        meal_search_pools = eligible_pools
                    retry_delays = (0, 30, 60, 90, 120) if slot_role in {"meal", "meal_lunch", "meal_dinner"} else (0,)
                    meal_distance_limits = (
                        (10, 15, 20) if slot_role == "meal_lunch" else (10, 15)
                    ) if slot_role in {"meal", "meal_lunch", "meal_dinner"} else (10,)
                    for delay in retry_delays:
                        candidate_start = self._format_minutes(self._minutes(start_time) + delay)
                        candidate_end = self._format_minutes(self._minutes(candidate_start) + duration_minutes)
                        for meal_distance_limit in meal_distance_limits:
                            selected = self._select_next_place(
                                pools=meal_search_pools,
                                preferred_category=category,
                                used_ids=used_ids | used_ids_today,
                                used_places=[*used_places, *used_places_today],
                                previous_lat=previous_lat,
                                previous_lon=previous_lon,
                                weekday=trip_weekday,
                                start_time=candidate_start,
                                end_time=candidate_end,
                                requested_visit_duration_minutes=None,
                                extra_exclude_ids=avoid_place_ids,
                                max_distance_from_previous_km=meal_distance_limit,
                                hotel_latitude=hotel_lat,
                                hotel_longitude=hotel_lon,
                                target_budget_per_person_day=target_budget_per_person_day,
                            )
                            if selected is not None:
                                start_time, end_time = candidate_start, candidate_end
                                actual_category = category
                                break
                        if selected is not None:
                            break

                if selected is not None and evening_slot:
                    actual_category = self._effective_category(selected)
                    slot_type = {
                        "Vui chơi": "Vui chơi buổi tối",
                        "Giải trí": "Giải trí buổi tối",
                        "Ăn uống": "Ẩm thực buổi tối",
                        "Cafe": "Cafe buổi tối",
                        "Mua sắm": "Mua sắm buổi tối",
                        "Tham quan": "Tham quan buổi tối",
                    }.get(actual_category, "Hoạt động buổi tối")
                    if actual_category == "Cafe":
                        duration_minutes = 90
                        end_time = self._format_minutes(self._minutes(start_time) + duration_minutes)

                if selected is None and not evening_slot:
                    # Never fill a meal or coffee stop with an unrelated shop
                    # or attraction. A missing nearby restaurant is safer than
                    # a cross-region detour disguised as a lunch break.
                    allow_generic_fallback = category not in {"Ăn uống", "Cafe"}
                    selected = self._select_next_place(
                        pools=eligible_pools,
                        preferred_category="__all__",
                        used_ids=used_ids | used_ids_today,
                        used_places=[*used_places, *used_places_today],
                        previous_lat=previous_lat,
                        previous_lon=previous_lon,
                        weekday=trip_weekday,
                        start_time=start_time,
                        end_time=end_time,
                        requested_visit_duration_minutes=duration_minutes if is_outing else None,
                        allowed_categories={"Tham quan", "Vui chơi", "Giải trí", "Thiên nhiên", "Mua sắm"} if is_outing else None,
                        extra_exclude_ids=avoid_place_ids,
                        target_budget_per_person_day=target_budget_per_person_day,
                        max_visit_minutes=None,
                        max_distance_from_previous_km=12,
                    ) if allow_generic_fallback else None
                    actual_category = None

                if selected is None:
                    if slot_role in {"meal", "meal_lunch", "meal_dinner"}:
                        # A meal card must identify a real venue; do not show a
                        # vague instruction card as if it were a restaurant.
                        continue
                    if slot_role in {"cafe", "outing_morning", "outing_afternoon", "outing_evening"}:
                        continue
                    # Never render an empty meal/activity as though it were a stop.
                    continue

                place = selected
                actual_category = self._effective_category(selected)
                if actual_category == "Mua sắm":
                    shopping_stops_scheduled += 1
                if is_outing and (actual_category != category or select_category == "__sightseeing__"):
                    # When a notable place is stored under another tag, label
                    # the activity by the venue's actual database category.
                    daypart = "tối" if evening_slot else ("sáng" if slot_role == "outing_morning" else "chiều")
                    slot_type = {
                        "Tham quan": f"Tham quan buổi {daypart}",
                        "Vui chơi": f"Vui chơi buổi {daypart}",
                        "Thiên nhiên": f"Thiên nhiên buổi {daypart}",
                        "Mua sắm": f"Mua sắm buổi {daypart}",
                    }.get(actual_category, slot_type)
                is_full_day_ba_na = self._is_full_day_ba_na(place)
                if is_full_day_ba_na:
                    duration_minutes = 390
                    end_time = self._format_minutes(self._minutes(start_time) + duration_minutes)
                    slot_type = "Khám phá Bà Nà cả ngày"
                    full_day_ba_na = True
                    full_day_ba_na_location = (place.latitude, place.longitude)
                    full_day_ba_na_end_time = end_time
                elif is_outing:
                    duration_minutes = self._recommended_visit_duration(place, duration_minutes)
                    if final_departure_day:
                        duration_minutes = min(duration_minutes, 60)
                    end_time = self._format_minutes(self._minutes(start_time) + duration_minutes)
                if select_category == "__sightseeing__":
                    sightseeing_selected_today = True
                if is_outing:
                    selected_today += 1
                used_ids_today.add(int(place.id))
                used_places_today.append(place)
                used_ids.add(int(place.id))
                used_places.append(place)
                activity_count += 1
                day_activity_count += 1
                if self._has_current_published_price(place):
                    published_price_places += 1
                    day_published_price_places += 1

                distance = self._distance(
                    previous_lat,
                    previous_lon,
                    place.latitude,
                    place.longitude,
                )

                travel_minutes = self._travel_minutes(distance)

                activity, total, estimated = self._build_activity(
                    place=place,
                    people=people,
                    distance_km=distance,
                    travel_minutes=travel_minutes,
                    start_time=start_time,
                    end_time=end_time,
                    slot_type=slot_type,
                )
                if is_full_day_ba_na:
                    onsite_restaurants = self._nearby_food_candidates(
                        place.latitude, place.longitude, province_code,
                        radius_km=3, require_address=True,
                    )
                    onsite_restaurant = onsite_restaurants[0] if onsite_restaurants else None
                    activity["onsite_breaks"] = [{
                        "start_time": "12:00",
                        "end_time": "13:00",
                        "type": (
                            f"Ăn trưa tại {onsite_restaurant.name} trong khu Sun World Bà Nà Hills"
                            if onsite_restaurant else
                            "Ăn trưa tại khu ẩm thực Sun World Bà Nà Hills"
                        ),
                    }]

                schedule.append({
                    "start_time": start_time,
                    "end_time": end_time,
                    "type": slot_type,
                    "duration_minutes": duration_minutes,
                    "slot_index": slot_index,
                    "latest_start_time": self._format_minutes(
                        (17 * 60) if slot_index == 11 and slot_role == "outing_afternoon" else
                        (11 * 45) if slot_role == "outing_morning" else
                        (16 * 60) if slot_role == "meal_lunch" else
                        (22 * 60 + 15) if slot_role == "meal_dinner" else
                        (10 * 60) if slot_role == "meal" else
                        self._minutes(start_time) + (
                            90 if slot_role.startswith("outing_")
                            else 60 if slot_role in {"meal", "meal_lunch", "meal_dinner"}
                            else 35
                        )
                    ),
                    "status": "scheduled",
                    "requested_category": category,
                    "activity_period": (
                        {"outing_morning": "morning", "outing_afternoon": "afternoon", "outing_evening": "evening"}.get(slot_role)
                        if is_outing else None
                    ),
                    "category_substitution": actual_category != category,
                    "travel_from_previous": {
                        "distance_km": round(distance or 0, 2),
                        "estimated_minutes": travel_minutes,
                        "distance_method": "straight_line_haversine",
                        "duration_method": "rough_distance_estimate",
                    },
                    "place": activity,
                })

                if total is None:
                    unpriced_places += 1
                    day_unpriced += 1
                else:
                    day_cost += total
                    if estimated:
                        total_estimated_cost += total
                    else:
                        total_real_cost += total

                previous_lat = place.latitude
                previous_lon = place.longitude

            # A practical end-of-day buffer is included in the route, so the
            # line and estimated return leg finish at the accommodation.
            schedule.append({
                "start_time": "20:15" if final_departure_day else "22:00",
                "end_time": "20:45" if final_departure_day else "22:15",
                "type": "Nhận hành lý tại khách sạn và khởi hành" if final_departure_day else "Về khách sạn nghỉ ngơi",
                "duration_minutes": 30 if final_departure_day else 15,
                "description": (
                    "Quay lại khách sạn nhận hành lý rồi khởi hành; thời gian là khung gợi ý cho ngày cuối, hãy chỉnh theo giờ xe/tàu/máy bay đã đặt."
                    if final_departure_day else "Kết thúc hoạt động trong ngày và nghỉ tại khách sạn."
                ),
                "description_en": (
                    "Return to the hotel to collect your luggage and depart. This is a suggested final-day window; adjust it to your booked bus, train, or flight."
                    if final_departure_day else "Finish the day's activities and rest at the hotel."
                ),
                "place": None,
                "return_to_hotel": True,
                "location": hotel.name,
                "location_address": getattr(hotel, "address", None),
            })

            route_info = self._route_and_schedule_day(schedule, hotel_lat, hotel_lon)
            # Routing can invalidate a venue when an opening-hours window or
            # day-end limit is missed. Remove that failed card from the public
            # itinerary instead of showing an empty stop and alarming warning.
            schedule[:] = [item for item in schedule if item.get("status") != "unavailable"]
            for item in schedule:
                item.pop("slot_index", None)
            daypart_counts = {part: 0 for part in self.ACTIVITY_SLOT_TARGETS}
            for item in schedule:
                part = item.get("activity_period")
                if item.get("place") and part in daypart_counts:
                    daypart_counts[part] += 1

            # Nếu ngày nào đó không đủ địa điểm, vẫn trả lịch hợp lệ.
            itinerary.append({
                "day": day,
                "date": (start_date + timedelta(days=day - 1)).isoformat() if start_date else None,
                "title": self._day_title(day),
                "schedule": schedule,
                "route": route_info,
                "estimated_day_cost": None if day_unpriced else round(day_cost, 0),
                "unpriced_places": day_unpriced,
                "activity_count": day_activity_count,
                "published_price_places": day_published_price_places,
                "activities_without_public_price": day_activity_count - day_published_price_places,
                "place_count": len([
                    x for x in schedule if x.get("place") is not None
                ]),
                "missing_slots": day_missing,
                "activity_distribution": daypart_counts,
            })

        # Recount after travel-time validation may remove stops that would make
        # the day run past a meal window or the end of the day.
        total_real_cost = total_estimated_cost = 0.0
        unpriced_places = published_price_places = activity_count = 0
        missing_total = 0
        for day in itinerary:
            real_day = estimated_day = 0.0
            day_unpriced = day_published = day_activities = day_missing = 0
            for item in day["schedule"]:
                place = item.get("place")
                if item.get("status") == "unavailable":
                    day_missing += 1
                if not place:
                    continue
                day_activities += 1
                if place.get("cost_type") == "unavailable" or place.get("total_cost") is None:
                    day_unpriced += 1
                else:
                    amount = float(place["total_cost"])
                    if place.get("cost_type") == "estimated":
                        estimated_day += amount
                    elif place.get("cost_type") == "real":
                        real_day += amount
                if place.get("price_kind") == "published_offer" and place.get("price_source_url") and place.get("price_checked_at") and (place.get("price_min") is not None or place.get("price_max") is not None):
                    day_published += 1
            day["activity_count"] = day_activities
            day["published_price_places"] = day_published
            day["activities_without_public_price"] = day_activities - day_published
            day["unpriced_places"] = day_unpriced
            day["place_count"] = day_activities
            day["missing_slots"] = day_missing
            day["estimated_day_cost"] = None if day_unpriced else round(real_day + estimated_day, 0)
            total_real_cost += real_day
            total_estimated_cost += estimated_day
            unpriced_places += day_unpriced
            published_price_places += day_published
            activity_count += day_activities
            missing_total += day_missing

        total_cost = total_real_cost + total_estimated_cost

        planning_warnings = plan_quality_warnings(itinerary, missing_total)
        planning_warnings.extend(self._activity_distribution_warnings(itinerary))

        return {
            "success": True,
            "province_code": province_code,
            "days": days,
            "start_date": start_date.isoformat() if start_date else None,
            "people": people,
            "budget": budget,
            "radius_km": radius_km,

            "hotel": {
                "id": hotel.id,
                "name": hotel.name,
                "category": hotel.category,
                "subcategory": hotel.subcategory,
                "province": getattr(hotel, "province_name", None),
                "ward": getattr(hotel, "ward_name", None),
                "address": self._full_address(hotel),
                "latitude": hotel.latitude,
                "longitude": hotel.longitude,
                "rating": hotel.rating,
                "review_count": hotel.review_count,
                "image_url": getattr(hotel, "image_url", None),
                "image_source_url": getattr(hotel, "image_source_url", None),
                "image_source_name": getattr(hotel, "image_source_name", None),
                "image_license": getattr(hotel, "image_license", None),
            },

            "hotel_id": hotel_id,

            "planning_logic": {
                "hotel_as_base": True,
                "time_based": True,
                "meals_per_day": 3,
                "food_priority": True,
                "rest_time": True,
                "travel_time": True,
                "travel_distance_method": "straight_line_haversine",
                "travel_time_method": "osrm_estimated_driving_duration_or_fallback",
                "road_routing_available": any(day.get("route", {}).get("source") == "OSRM" for day in itinerary),
                "routing_disclosure": "Tuyến đường và thời gian lái xe được ước tính từ OSRM/OpenStreetMap khi dịch vụ có sẵn; không có dữ liệu giao thông trực tiếp. Nếu dịch vụ không phản hồi, khoảng cách hiển thị là đường chim bay và được ghi rõ.",
                "avoid_duplicate_places": True,
                "radius_km": radius_km,
                "activity_targets_per_full_day": {"morning": 4, "afternoon": 3, "evening": 2},
                "message": (
                    "Lịch trình được xây dựng quanh khách sạn, "
                    "chia theo khung giờ và ưu tiên ăn uống."
                ),
            },

            "itinerary": itinerary,
            "complete": missing_total == 0 and len(itinerary) == days and not days_without_sightseeing(itinerary),
            "missing_slots": missing_total,
            "planning_warnings": planning_warnings,

            "cost_summary": {
                "real_cost": round(total_real_cost, 0),
                "estimated_cost": round(total_estimated_cost, 0),
                "total_estimated_trip_cost": None if unpriced_places else round(total_cost, 0),
                "known_cost": round(total_cost, 0),
                "unpriced_places": unpriced_places,
                "activity_count": activity_count,
                "published_price_places": published_price_places,
                "activities_without_public_price": activity_count - published_price_places,
                "budget": budget,
            "radius_km": radius_km,
                "within_budget": (
                    total_cost <= budget if budget is not None and unpriced_places == 0 else None
                ),
            },
        }

    def _activity_distribution_warnings(self, itinerary):
        """Disclose when verified POIs and realistic timing cannot fill the requested daypart targets."""
        warnings = []
        for index, day in enumerate(itinerary, start=1):
            meal_names = {"Ăn sáng", "Ăn trưa", "Ăn tối"}
            served_meals = {
                slot.get("type") for slot in day.get("schedule", [])
                if slot.get("place") and slot.get("type") in meal_names
            }
            if len(served_meals) < 3:
                missing_meals = ", ".join(sorted(meal_names - served_meals))
                warnings.append(
                    f"Ngày {index} chưa xếp đủ 3 bữa tại quán có ảnh thật và địa chỉ rõ ràng"
                    + (f" (thiếu: {missing_meals})." if missing_meals else ".")
                )
            # Daily distribution is shown in each day header. Avoid a separate
            # warning banner that repeats the same counts and dominates the plan.
        return warnings

    # ========================================================
    # CATEGORY POOL
    # ========================================================

    def _get_category_pool(
        self,
        province_code,
        latitude,
        longitude,
        category,
        limit,
        hotel_id,
        radius_km=50,
        avoid_place_ids=None,
    ):
        """
        Lấy riêng candidate cho từng nhóm.
        recommend_nearby hiện tại đã xử lý province, radius,
        preference và distance.
        """
        result = []
        try:
            result = self.recommender.recommend_nearby(
                latitude=latitude,
                longitude=longitude,
                province_code=province_code,
                interests=[category] if category else [],
                # Keep enough nearby alternatives so meals are not chosen from a small hotel-ranked shortlist.
                limit=max(limit * 6, 60),
                radius_km=radius_km,
                exclude_id=hotel_id,
            )
        except Exception:
            result = []

        avoid_place_ids = {int(x) for x in (avoid_place_ids or []) if str(x).isdigit()}

        # Loại duplicate ID trong chính pool và loại những địa điểm
        # người dùng đã yêu cầu tránh khi tạo một lịch mới.
        unique = []
        seen = set()

        for place in result:
            try:
                pid = int(place.id)
            except (TypeError, ValueError):
                continue

            if pid in seen or pid in avoid_place_ids:
                continue
            if (not self._has_usable_address(place)
                    or not self._is_tourism_candidate(place)):
                continue
            if self._is_generic_stop_placeholder(place, category):
                continue

            seen.add(pid)
            unique.append(place)

        # The general recommender may truncate a large province to a small
        # ranking window before planning. Keep published-price venues available;
        # for wide-radius exploration, also include notable attractions from
        # the full local cache so a popular landmark cannot disappear behind
        # many nearby ordinary venues.
        if category:
            province_code = str(province_code or "").strip()
            wide_sightseeing = radius_km >= 15 and category in {
                "Tham quan", "Vui chơi", "Thiên nhiên"
            }
            for place in getattr(self.recommender, "places", []):
                try:
                    pid = int(place.id)
                except (TypeError, ValueError):
                    continue
                if (pid in seen or pid in avoid_place_ids or pid == int(hotel_id)
                        or str(getattr(place, "province_code", "") or "").strip() != province_code
                        or str(getattr(place, "category", "") or "").strip() != str(category).strip()):
                    continue
                if (not self._has_usable_address(place)
                        or not self._is_tourism_candidate(place)):
                    continue
                if self._is_generic_stop_placeholder(place, category):
                    continue
                distance = self._distance(latitude, longitude, place.latitude, place.longitude)
                if distance is None or distance > radius_km:
                    continue
                seen.add(pid)
                unique.append(place)

        return unique

    def _nearby_food_candidates(
        self, latitude, longitude, province_code, radius_km=15,
        require_address=True, require_image=True,
    ):
        """Find real food venues near the current stop, not just near the hotel."""
        try:
            candidates = self.recommender.recommend_nearby(
                latitude=latitude,
                longitude=longitude,
                province_code=province_code,
                interests=["Ăn uống"],
                limit=100,
                radius_km=radius_km,
            )
        except Exception:
            return []
        result = []
        seen = set()
        for place in candidates or []:
            try:
                place_id = int(place.id)
            except (AttributeError, TypeError, ValueError):
                continue
            if place_id in seen or self._effective_category(place) != "Ăn uống":
                continue
            if ((require_address and not self._has_usable_address(place))
                    or not self._is_tourism_candidate(place)):
                continue
            if not str(getattr(place, "name", "") or "").strip():
                continue
            if not getattr(place, "latitude", None) or not getattr(place, "longitude", None):
                continue
            seen.add(place_id)
            result.append(place)
        result.sort(key=lambda place: self._distance(latitude, longitude, place.latitude, place.longitude) or 999)
        return result

    @staticmethod
    def _is_generic_stop_placeholder(place, category):
        if str(category or "").strip() != "Mua sắm":
            return False
        name = unicodedata.normalize("NFD", str(getattr(place, "name", "") or "").casefold())
        name = "".join(ch for ch in name if not unicodedata.combining(ch)).replace("đ", "d")
        name = re.sub(r"[^a-z0-9]+", " ", name).strip()
        generic_stops = {"diem dung chan", "tram dung chan", "rest area", "stopover"}
        original_name = str(getattr(place, "name", "") or "").casefold().replace(" ", "")
        everyday_retail = (
            "tap hoa", "grocery", "convenience", "mini mart", "minimart",
            "general store", "bach hoa xanh", "circle k", "family mart", "ministop",
            "cua hang tien loi", "cua hang tap hoa", "winmart", "vinmart",
            "coopmart", "co op mart", "lotte mart", "big c", "aeon",
            "mega market", "supermarket", "brg mart", "fuji mart", "tops market",
        )
        return (
            name in generic_stops
            or original_name.endswith("+")
            or any(term in name for term in everyday_retail)
        )

    @staticmethod
    def _normalized_place_name(place):
        name = unicodedata.normalize("NFD", str(getattr(place, "name", "") or "").casefold())
        name = "".join(ch for ch in name if not unicodedata.combining(ch)).replace("đ", "d")
        return re.sub(r"[^a-z0-9]+", " ", name).strip()

    @staticmethod
    def _normalized_text(value):
        value = unicodedata.normalize("NFD", str(value or "").casefold())
        value = "".join(ch for ch in value if not unicodedata.combining(ch)).replace("đ", "d")
        return re.sub(r"[^a-z0-9]+", " ", value).strip()

    @staticmethod
    def _has_usable_address(place):
        """Require a street address or usable GPS coordinates for a demo POI."""
        def key(value):
            value = unicodedata.normalize("NFD", str(value or "").casefold())
            value = "".join(ch for ch in value if not unicodedata.combining(ch)).replace("đ", "d")
            return re.sub(r"[^a-z0-9]+", " ", value).strip()

        address = str(getattr(place, "address", "") or "").strip()
        if not address:
            try:
                lat = float(getattr(place, "latitude", None))
                lon = float(getattr(place, "longitude", None))
                code = str(getattr(place, "province_code", "") or "").strip()
                return bool(code and -90 <= lat <= 90 and -180 <= lon <= 180)
            except (TypeError, ValueError):
                return False
        province_code = str(getattr(place, "province_code", "") or "").strip()
        normalized_address = TripPlanner._normalized_text(address)
        allowed_aliases = {
            TripPlanner._normalized_text(value)
            for value in TripPlanner.PROVINCE_ADDRESS_ALIASES.get(province_code, ())
        }
        for province_name in TripPlanner.ALL_PROVINCE_ADDRESS_NAMES:
            normalized_name = TripPlanner._normalized_text(province_name)
            if (normalized_name in normalized_address
                    and normalized_name not in allowed_aliases):
                return False
        admin_prefix = re.compile(
            r"^(?:phuong|xa|thi tran|dac khu|quan|huyen|thi xa|thanh pho|tinh|district|city|province)\s+"
        )
        locality_values = set()
        for field in ("ward_name", "district_name", "province_name"):
            value = key(getattr(place, field, ""))
            if value:
                locality_values.add(value)
                locality_values.add(admin_prefix.sub("", value).strip())
        place_name = key(getattr(place, "name", ""))
        for component in address.split(","):
            component_key = key(component)
            if not component_key:
                continue
            without_prefix = admin_prefix.sub("", component_key).strip()
            if not without_prefix or without_prefix in locality_values:
                continue
            if component_key == place_name or without_prefix == place_name:
                continue
            # A component that is just an administrative area is not a wayfinding
            # address, even when the source omitted its administrative prefix.
            if component_key in locality_values:
                continue
            return True
        return False

    @staticmethod
    def _has_local_verified_image(place):
        """Require a verified photo downloaded into the app's local media."""
        return str(getattr(place, "image_url", "") or "").strip().startswith("/media/places/")

    @classmethod
    def _has_verified_photo_and_price(cls, place):
        return cls._has_local_verified_image(place) and cls._has_current_published_price(place)

    @classmethod
    def _is_tourism_candidate(cls, place):
        category = cls._normalize_category(cls._effective_category(place))
        if category not in {"Ăn uống", "Cafe", "Tham quan", "Vui chơi", "Giải trí", "Thiên nhiên", "Mua sắm"}:
            return False
        name = cls._normalized_place_name(place)
        descriptor = " ".join(filter(None, (
            name,
            cls._normalized_text(getattr(place, "category", "")),
            cls._normalized_text(getattr(place, "subcategory", "")),
        )))
        excluded = (
            "hospital", "clinic", "pharmacy", "atm", "bank", "office", "factory",
            "warehouse", "repair shop", "gas station", "petrol station", "car wash",
            "gym", "fitness", "yoga school", "sports club", "sports center", "sports centre",
            "stadium", "swim club", "swimming club", "swimming pool", "football field",
            "trung tam van hoa the thao", "trung tam the thao", "nha van hoa",
            "spa", "massage", "pool and spa", "pool & spa",
            "tennis court", "badminton", "martial arts", "dojo", "supermarket", "convenience store",
            "winmart", "vinmart", "coopmart", "co op mart", "big c", "aeon supermarket",
            "clb boi", "san van dong", "nha tap luyen", "phong tap",
        )
        if any(term in descriptor for term in excluded):
            return False
        subcategory = unicodedata.normalize("NFD", str(getattr(place, "subcategory", "") or "").casefold())
        subcategory = "".join(ch for ch in subcategory if not unicodedata.combining(ch)).replace("đ", "d")
        subcategory = re.sub(r"[^a-z0-9]+", " ", subcategory).strip()
        if name in {"phim", "cang", "beer"}:
            return False
        if category == "Ăn uống":
            # The source map often stores pubs and beer halls in the same
            # broad category as restaurants. They are not safe meal picks,
            # especially for breakfast, so keep them out of every meal plan.
            food_subcategory = cls._normalized_text(getattr(place, "subcategory", ""))
            meal_only = f" {name} {food_subcategory} "
            food_exclusions = (
                " bia hoi ", " beer hall ", " beer house ", " beer pub ",
                " quan nhau ", " beer garden ", " cocktail bar ",
                " karaoke ", " night club ", " nightclub ",
            )
            if any(term in meal_only for term in food_exclusions):
                return False
            if re.search(r"\b(bar|pub|lounge|club)\b", meal_only):
                return False
        if category in {"Tham quan", "Vui chơi", "Thiên nhiên"} and any(
            term in subcategory for term in ("quan bar", "bar", "pub", "karaoke", "nightclub")
        ):
            return False
        if category == "Mua sắm":
            # OSM's shop/mall taxonomy also labels ordinary errands (grocery,
            # convenience and unclassified shops) as shopping. Keep markets,
            # destination malls and craft stops, not supermarket errands.
            tourist_shopping_terms = (
                "cho ", "night market",
                "walking street", "pho di bo", "phố đi bộ", "souvenir", "handicraft",
                "craft village", "lang nghe", "artisan",
            )
            destination_mall_names = (
                "vincom plaza", "vincom center", "lotte mall", "helio center",
                "aeon mall", "gigamall", "thiso mall", "crescent mall",
            )
            subcategory = unicodedata.normalize("NFD", str(getattr(place, "subcategory", "") or "").casefold())
            subcategory = "".join(ch for ch in subcategory if not unicodedata.combining(ch)).replace("đ", "d")
            subcategory = re.sub(r"[^a-z0-9]+", " ", subcategory).strip()
            small_shop_types = (
                "cua hang tien loi", "cua hang bach hoa", "cua hang tap hoa",
                "cua hang tam hoa", "convenience store", "grocery store",
            )
            if any(term in subcategory for term in small_shop_types):
                return False
            if any(term in name for term in ("dien may", "thu cung", "pet shop", "electronics")):
                return False
            if (not cls._is_notable_shopping_stop(place)
                    and not any(term in name for term in tourist_shopping_terms)
                    and not any(term in name for term in destination_mall_names)):
                return False
            if cls._is_generic_stop_placeholder(place, category):
                return False
        return not cls._is_generic_stop_placeholder(place, category)

    @classmethod
    def _is_night_market(cls, place):
        name = cls._normalized_place_name(place)
        return "cho dem" in name or "night market" in name

    @classmethod
    def _effective_category(cls, place):
        """Correct common OSM category mistakes for itinerary labels."""
        if place is None:
            return ""
        name = cls._normalized_place_name(place)
        if cls._is_night_market(place):
            return "Mua sắm"
        if any(term in name for term in (
            "cau rong", "cau tran thi ly", "cau tinh yeu", "cau vang",
            "japanese covered bridge", "bridge of love",
        )):
            return "Tham quan"
        if any(term in name for term in (
            "sun world", "ba na hills", "vinwonders", "asia park", "helio center",
        )):
            return "Vui chơi"
        return str(getattr(place, "category", "") or "").strip()

    @classmethod
    def _is_notable_shopping_stop(cls, place):
        name = cls._normalized_place_name(place)
        return any(term in name for term in (
            "cho con", "cho han", "cho dem", "night market", "helio",
            "hoi an ancient town", "phố cổ", "pho co", "walking street",
        ))

    # ========================================================
    # SELECT NEXT PLACE
    # ========================================================

    def _select_next_place(
        self,
        *,
        pools,
        preferred_category,
        used_ids,
        used_places,
        previous_lat,
        previous_lon,
        weekday=None,
        start_time=None,
        end_time=None,
        requested_visit_duration_minutes=None,
        allowed_categories=None,
        extra_exclude_ids=None,
        avoid_nearby_to=None,
        min_distance_from_avoid_km=0,
        allow_evening_food=False,
        prioritize_exploration=False,
        evening_only=False,
        max_visit_minutes=None,
        preferred_evening_categories=None,
        max_distance_from_previous_km=None,
        max_distance_from_hotel_km=None,
        hotel_latitude=None,
        hotel_longitude=None,
        target_budget_per_person_day=None,
    ):
        pool = pools.get(preferred_category, [])

        candidates = []
        extra_exclude_ids = extra_exclude_ids or set()
        allowed_categories = set(allowed_categories or [])

        for place in pool:
            try:
                pid = int(place.id)
            except (TypeError, ValueError):
                continue

            if (not self._has_usable_address(place)
                    or not self._is_tourism_candidate(place)):
                continue
            if allowed_categories and self._effective_category(place) not in allowed_categories:
                continue

            if pid in used_ids or pid in extra_exclude_ids:
                continue

            if any(self._same_place(place, previous) for previous in used_places):
                continue

            visit_duration = int(requested_visit_duration_minutes or self._duration_minutes(start_time, end_time))
            visit_duration = self._recommended_visit_duration(place, visit_duration)
            candidate_end = self._format_minutes(self._minutes(start_time) + visit_duration)
            if not self._open_during_slot(place, start_time, candidate_end, weekday):
                continue
            if max_visit_minutes is not None and self._recommended_visit_duration(place, 90) > max_visit_minutes:
                continue

            night_market = self._is_night_market(place)
            subcategory = unicodedata.normalize("NFD", str(getattr(place, "subcategory", "") or "").casefold())
            subcategory = "".join(ch for ch in subcategory if not unicodedata.combining(ch)).replace("đ", "d")
            subcategory = re.sub(r"[^a-z0-9]+", " ", subcategory).strip()
            # Bars, pubs, karaoke and nightclubs belong only in an evening
            # slot. OSM often mislabels them as general entertainment, which
            # previously put nightlife stops into a morning sightseeing plan.
            if (not evening_only and self._effective_category(place) in {
                "Tham quan", "Vui chơi", "Giải trí", "Thiên nhiên", "Mua sắm"
            } and any(term in subcategory for term in (
                "bar", "pub", "karaoke", "nightclub", "night club", "sports bar",
            ))):
                continue
            if night_market and not evening_only:
                # A night market is not a morning sightseeing stop even when
                # its OSM category is merely tagged as a general attraction.
                continue

            if evening_only:
                # Only schedule a night stop when both its category and
                # published opening hours support an evening visit. Keep it
                # close to the previous stop so the day can return safely.
                allowed_evening_categories = {"Vui chơi", "Giải trí", "Cafe", "Mua sắm"}
                if night_market:
                    allowed_evening_categories.add("Tham quan")
                if allow_evening_food:
                    allowed_evening_categories.add("Ăn uống")
                if str(getattr(place, "category", "") or "").strip() not in allowed_evening_categories:
                    continue
                normalized_name = self._normalized_place_name(place)
                if any(term in normalized_name for term in ("gym", "fitness center", "fitness centre", "sports hall")):
                    continue
                if distance := self._distance(previous_lat, previous_lon, place.latitude, place.longitude):
                    if distance > 10:
                        continue
                else:
                    continue

            distance = self._distance(
                previous_lat,
                previous_lon,
                place.latitude,
                place.longitude,
            )

            if distance is None:
                continue

            # The selected province radius is for sightseeing, not for
            # breakfast, coffee, or routine shopping detours.
            if max_distance_from_previous_km is not None and distance > float(max_distance_from_previous_km):
                # A province-wide trip may need one signature day excursion
                # (for example Da Nang to Ba Na or Hoi An). Allow it only as
                # the first notable daytime stop, keep it within 45 km of the
                # hotel, and leave the route engine to validate road time.
                headline_day_trip = False
                if prioritize_exploration and not evening_only and hotel_latitude is not None and hotel_longitude is not None:
                    origin_from_hotel = self._distance(
                        hotel_latitude, hotel_longitude, previous_lat, previous_lon,
                    )
                    target_from_hotel = self._distance(
                        hotel_latitude, hotel_longitude, place.latitude, place.longitude,
                    )
                    if origin_from_hotel is not None and origin_from_hotel <= 3 and target_from_hotel is not None and target_from_hotel <= 45:
                        try:
                            headline_day_trip = self.recommender._prominence_score(place) >= 0.9
                        except (AttributeError, TypeError, ValueError):
                            headline_day_trip = False
                if not headline_day_trip:
                    continue

            if max_distance_from_hotel_km is not None:
                from_hotel = self._distance(
                    hotel_latitude, hotel_longitude, place.latitude, place.longitude,
                )
                if from_hotel is None or from_hotel > float(max_distance_from_hotel_km):
                    if not self._is_notable_shopping_stop(place):
                        continue

            # After a full-day Bà Nà visit the itinerary must return to the
            # city for dinner instead of selecting another POI inside the
            # resort grounds (which appears as a 0 km transfer).
            if avoid_nearby_to and min_distance_from_avoid_km > 0:
                from_avoid = self._distance(
                    avoid_nearby_to[0], avoid_nearby_to[1],
                    place.latitude, place.longitude,
                )
                if from_avoid is None or from_avoid < min_distance_from_avoid_km:
                    continue

            budget_adjustment = self._budget_route_adjustment(
                place, target_budget_per_person_day
            )
            route_score = distance + budget_adjustment - (
                0.75 if self._has_current_published_price(place) else 0.0
            )
            if evening_only:
                category = str(getattr(place, "category", "") or "").strip()
                effective_category = self._effective_category(place)
                normalized_evening_preferences = {
                    self._normalize_category(value) for value in (preferred_evening_categories or [])
                }
                preference_rank = 0 if effective_category in normalized_evening_preferences else 1
                evening_priority = (
                    0 if night_market or self._is_notable_shopping_stop(place)
                    or category in {"Vui chơi", "Giải trí"}
                    else 1 if category == "Mua sắm"
                    else 2 if category == "Cafe"
                    else 3
                )
                candidates.append((preference_rank * 10 + evening_priority, route_score, route_score, distance, place))
                continue

            # Favor a sourced price for a modest detour (up to 750 metres).
            # Actual distance is retained as the tie-breaker, so the route
            # still stays compact and prices never override the requested type.
            if preferred_category == "__sightseeing__" or prioritize_exploration:
                # In a wide-radius trip, preserve the recommender's landmark
                # priority during route construction. Previously this pool was
                # reduced to the nearest next stop, which hid famous places
                # outside the immediate hotel neighbourhood.
                try:
                    prominence = self.recommender._prominence_score(place)
                except (AttributeError, TypeError, ValueError):
                    prominence = 0.0
                name = unicodedata.normalize("NFD", str(getattr(place, "name", "") or "").lower())
                name = "".join(ch for ch in name if unicodedata.category(ch) != "Mn").replace("đ", "d")
                province_code = str(getattr(place, "province_code", "") or "").strip()
                # Bà Nà is a signature full-day attraction in the merged
                # Đà Nẵng–Quảng Nam province. Put it ahead of nearby generic
                # sightseeing stops when the user chose a broad exploration
                # radius, while keeping all other provinces data-driven.
                headline_priority = int(
                    province_code == "48"
                    and getattr(place, "category", "") in {"Vui chơi", "Tham quan", "Thiên nhiên"}
                    and re.search(r"\bsun world ba na\b|\bba na hills\b", name) is not None
                )
                # Explore a genuinely notable stop first, then break ties by
                # compact route distance. The old -distance tie-break chose
                # the farthest landmark (e.g. My Son) ahead of Ba Na even
                # when both had the same prominence.
                candidates.append((-prominence, -headline_priority, route_score, distance, place))
            else:
                candidates.append((0.0, route_score, route_score, distance, place))

        if not candidates:
            return None

        # Ưu tiên gần điểm trước đó để lịch trình không chạy lòng vòng.
        candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
        return candidates[0][4]

    @classmethod
    def _budget_route_adjustment(cls, place, budget_per_person_day):
        """Small route-score penalty for prices that exceed a target budget.

        Only recent prices with a source URL can influence selection. The
        amount is a planning hint: menus list individual dishes and attraction
        offers can have conditions, so it is not used as a meal/visit total.
        """
        if budget_per_person_day is None or budget_per_person_day <= 0:
            return 0.0
        category = cls._normalize_category(cls._effective_category(place))
        category_share = {
            "Ăn uống": 0.20,
            "Cafe": 0.10,
            "Tham quan": 0.16,
            "Vui chơi": 0.20,
            "Giải trí": 0.16,
            "Thiên nhiên": 0.08,
        }.get(category)
        if category_share is None:
            return 0.0

        try:
            if not cls._has_current_published_price(place):
                price = demo_per_person_cost(place)
                values = [price]
            else:
                values = [
                    float(value)
                    for value in (
                        getattr(place, "price_min", None),
                        getattr(place, "price_max", None),
                    )
                    if value is not None and float(value) > 0
                ]
            price = sum(values) / len(values) if values else None
        except (TypeError, ValueError):
            return 0.15
        if price is None or price <= 0:
            return 0.15

        if category == "Ăn uống":
            price *= 2

        allowance = budget_per_person_day * category_share
        ratio = price / allowance if allowance > 0 else 0.0
        # A light preference for affordable known prices; travel time still
        # matters, and expensive venues remain eligible when options are few.
        if ratio <= 1:
            return 0.0
        if ratio <= 1.5:
            return 0.20
        if ratio <= 2:
            return 0.45
        return 0.75

    @staticmethod
    def _minutes(value):
        hour, minute = (int(part) for part in str(value).split(":", 1))
        if hour < 0 or hour > 24 or minute < 0 or minute > 59 or (hour == 24 and minute != 0):
            raise ValueError("invalid time")
        return hour * 60 + minute

    @staticmethod
    def weekday_for_day(day, trip_start_date=None):
        """Resolve the weekday for an itinerary day, falling back to trip date."""
        raw_date = day.get("date") if isinstance(day, dict) else None
        try:
            if raw_date:
                return date.fromisoformat(str(raw_date)).weekday()
        except (TypeError, ValueError):
            pass
        try:
            raw_start = date.fromisoformat(str(trip_start_date)) if trip_start_date else None
            day_number = int(day.get("day")) if isinstance(day, dict) else 0
            if raw_start and day_number > 0:
                return (raw_start + timedelta(days=day_number - 1)).weekday()
        except (TypeError, ValueError):
            return None
        return None

    @staticmethod
    def _open_during_slot(place, start_time, end_time, weekday=None):
        """Exclude a venue only when its published hours clearly miss the slot."""
        hours = str(getattr(place, "opening_hours", "") or demo_opening_hours(place)).strip()
        if not hours or not start_time or not end_time:
            return True
        normalized = hours.casefold().replace(" ", "")
        if normalized in {"24/7", "24x7", "daily24hours"} or "open24hours" in normalized:
            return True

        try:
            slot_start = TripPlanner._minutes(start_time)
            slot_end = TripPlanner._minutes(end_time)
        except (TypeError, ValueError):
            return True

        day_names = {"Mo": 0, "Tu": 1, "We": 2, "Th": 3, "Fr": 4, "Sa": 5, "Su": 6}
        range_pattern = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})\s*[-–]\s*(\d{1,2}):(\d{2})(?!\d)")

        def expand_days(spec):
            result = set()
            for part in spec.split(","):
                endpoints = part.split("-", 1)
                if not endpoints or endpoints[0] not in day_names:
                    continue
                first = day_names[endpoints[0]]
                last = day_names[endpoints[-1]] if len(endpoints) == 2 and endpoints[-1] in day_names else first
                current = first
                while True:
                    result.add(current)
                    if current == last:
                        break
                    current = (current + 1) % 7
            return result

        applicable = []
        saw_day_rule = False
        for clause in re.split(r"[;\n]+", hours):
            clause = clause.strip()
            if not clause:
                continue
            day_match = re.match(r"((?:Mo|Tu|We|Th|Fr|Sa|Su)(?:-(?:Mo|Tu|We|Th|Fr|Sa|Su))?(?:,(?:Mo|Tu|We|Th|Fr|Sa|Su)(?:-(?:Mo|Tu|We|Th|Fr|Sa|Su))?)*)\s+", clause)
            clause_days = expand_days(day_match.group(1)) if day_match else set()
            if day_match:
                saw_day_rule = True
                clause = clause[day_match.end():]
                if weekday is not None and weekday not in clause_days:
                    continue
            elif weekday is not None and saw_day_rule:
                # An unqualified time range applies every day.
                pass

            if re.match(r"(?:off|closed|close)\b", clause, re.IGNORECASE):
                continue
            for open_hour, open_minute, close_hour, close_minute in range_pattern.findall(clause):
                opened = int(open_hour) * 60 + int(open_minute)
                closed = int(close_hour) * 60 + int(close_minute)
                if opened < 0 or opened >= 24 * 60 or closed < 0 or closed > 24 * 60:
                    continue
                if closed <= opened:
                    closed += 24 * 60
                applicable.append((opened, closed))

        if not applicable:
            # Plain `off` is an explicit closed day only when it carries a day rule.
            return not saw_day_rule
        return any(slot_start >= opened and slot_end <= closed for opened, closed in applicable)

    @staticmethod
    def _has_current_published_price(place):
        if (getattr(place, "price_kind", None) != "published_offer"
                or str(getattr(place, "price_source", "") or "").strip().casefold() == "estimated"
                or not getattr(place, "price_source_url", None)
                or getattr(place, "price_min", None) is None and getattr(place, "price_max", None) is None):
            return False
        checked_at = getattr(place, "price_checked_at", None)
        if not checked_at:
            return False
        try:
            if isinstance(checked_at, str):
                checked_at = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
            if checked_at.tzinfo is None:
                checked_at = checked_at.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - checked_at.astimezone(timezone.utc)
            return timedelta(minutes=-5) <= age <= timedelta(days=45)
        except (TypeError, ValueError, OverflowError):
            return False

    @staticmethod
    def _same_place(first, second):
        def folded(value):
            text = unicodedata.normalize("NFKD", str(value or "").casefold())
            return re.sub(r"[^a-z0-9]+", " ", "".join(ch for ch in text if not unicodedata.combining(ch))).strip()

        distance = TripPlanner._distance(first.latitude, first.longitude, second.latitude, second.longitude)
        if distance is None:
            return False
        first_name, second_name = folded(first.name), folded(second.name)
        if first_name == second_name and distance <= 0.15:
            return True
        if distance <= 1.25:
            stop_words = {
                "a", "an", "the", "diem", "tham", "quan", "du", "lich", "khu",
                "cong", "vien", "center", "centre", "city", "danang", "da", "nang",
            }
            first_tokens = {token for token in first_name.split() if len(token) > 1 and token not in stop_words}
            second_tokens = {token for token in second_name.split() if len(token) > 1 and token not in stop_words}
            # Distinct OSM labels for a single landmark area (for example
            # Hải Vân Quan and Đèo Hải Vân) should not consume two itinerary
            # stops when their coordinates are essentially the same.
            if len(first_tokens & second_tokens) >= 2:
                return True
        # OSM often stores one destination under several names/categories
        # (e.g. "Sun World Bà Nà Hills" and "Núi Bà Nà"). Do not schedule
        # these aliases on separate days as if they were different trips.
        ba_na_alias = re.compile(r"\bba\s+na\b")
        return (
            ba_na_alias.search(first_name) is not None
            and ba_na_alias.search(second_name) is not None
            and distance <= 3
        )

    @staticmethod
    def _is_full_day_ba_na(place):
        if str(getattr(place, "province_code", "") or "").strip() != "48":
            return False
        if str(getattr(place, "category", "") or "").strip() not in {"Vui chơi", "Tham quan", "Thiên nhiên"}:
            return False
        name = unicodedata.normalize("NFD", str(getattr(place, "name", "") or "").casefold())
        name = "".join(ch for ch in name if not unicodedata.combining(ch)).replace("đ", "d")
        return re.search(r"\b(?:sun\s+world\s+)?ba\s+na(?:\s+hills)?\b", name) is not None

    @staticmethod
    def _recommended_visit_duration(place, default_minutes):
        """Choose a daytime stay length from the attraction's OSM type/name."""
        fold = lambda value: "".join(
            ch for ch in unicodedata.normalize("NFD", str(value or "").casefold())
            if not unicodedata.combining(ch)
        ).replace("đ", "d")
        category = fold(getattr(place, "category", ""))
        subcategory = fold(getattr(place, "subcategory", ""))
        name = fold(getattr(place, "name", ""))
        feature = f"{subcategory} {name}"
        if any(term in feature for term in ("ky co", "hon kho", "cu lao xanh")):
            # Beaches and islands need a real visit window, including the
            # boat/transfer time that short default stops used to omit.
            return 180
        if any(term in feature for term in ("eo gio", "ghenh rang", "thap doi", "thap banh it")):
            return 90
        if any(term in feature for term in ("bao tang", "museum", "trien lam")):
            return 90
        if any(term in feature for term in ("chua ", "pagoda", "den tho", "temple", "di tich")):
            return 90
        if category == "vui choi" and any(term in subcategory for term in ("cong vien giai tri", "amusement park", "center", "complex")):
            return 180
        return int(default_minutes)

    # ========================================================
    # BUILD ACTIVITY
    # ========================================================

    def _build_activity(
        self,
        *,
        place,
        people,
        distance_km,
        travel_minutes,
        start_time,
        end_time,
        slot_type,
    ):
        # A website's lowest menu item or a generic service Offer is a real
        # listed price, but not a trustworthy per-person trip cost. Only an
        # explicitly classified per_person_cost can enter the trip total.
        verified_price = self._has_current_published_price(place)
        demo = not verified_price or getattr(place, "price_kind", None) != "per_person_cost"
        if not demo:
            estimated = self.cost_estimator.estimate_place_cost(
                category=place.category,
                price_min=place.price_min,
                price_max=place.price_max,
            )
        else:
            estimated = CostEstimate(
                demo_per_person_cost(place), True, "Mức giá tham khảo"
            )

        if self._effective_category(place) == "Ăn uống" and estimated.amount is not None:
            estimated = CostEstimate(
                estimated.amount * 2,
                estimated.is_estimated,
                "Giá món ăn dự kiến ×2",
            )

        cost_per_person = estimated.amount
        total = cost_per_person * people if cost_per_person is not None else None

        activity = {
            "place_id": place.id,
            "name": place.name,
            "category": self._effective_category(place),
            "subcategory": place.subcategory,
            "province": getattr(place, "province_name", None),
            "district": getattr(place, "district_name", None),
            "ward": getattr(place, "ward_name", None),
            "address": self._full_address(place),
            "rating": place.rating,
            "review_count": place.review_count,
            "latitude": place.latitude,
            "longitude": place.longitude,
            "opening_hours": getattr(place, "opening_hours", None) or demo_opening_hours(place),
            "opening_hours_unverified": self._is_night_market(place) and not bool(getattr(place, "opening_hours", None)),
            "image_url": displayable_image_url(place),
            "image_source_url": getattr(place, "image_source_url", None),
            "image_source_name": getattr(place, "image_source_name", None),
            "image_license": getattr(place, "image_license", None),

            "distance_km": round(distance_km or 0, 2),
            "travel_time_minutes": travel_minutes,

            "start_time": start_time,
            "end_time": end_time,
            "duration_minutes": self._duration_minutes(
                start_time,
                end_time,
            ),
            "activity_type": slot_type,

            "price_min": place.price_min if verified_price else None,
            "price_max": place.price_max if verified_price else None,
            "price_source_url": getattr(place, "price_source_url", None) if verified_price else None,
            "price_source": getattr(place, "price_source", None) if verified_price else None,
            "price_checked_at": getattr(place, "price_checked_at", None) if verified_price else None,
            "price_kind": getattr(place, "price_kind", None) if verified_price else None,
            "demo_price_min": demo_price(place)["min"] if demo else None,
            "demo_price_max": demo_price(place)["max"] if demo else None,
            "demo_price_unit": demo_price(place)["unit"] if demo else None,

            "cost_per_person": round(cost_per_person, 0) if cost_per_person is not None else None,
            "total_cost": round(total, 0) if total is not None else None,
            "cost_type": "unavailable" if cost_per_person is None else ("estimated" if estimated.is_estimated else "real"),
            "cost_label": estimated.label,
        }

        return activity, total, estimated.is_estimated

    # ========================================================
    # DISTANCE / TIME
    # ========================================================

    @staticmethod
    def _distance(lat1, lon1, lat2, lon2):
        """
        Dùng recommender's Haversine nếu có.
        """
        if (
            lat1 is None
            or lon1 is None
            or lat2 is None
            or lon2 is None
        ):
            return None

        try:
            return float(
                TripPlanner._haversine(
                    float(lat1),
                    float(lon1),
                    float(lat2),
                    float(lon2),
                )
            )
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _haversine(lat1, lon1, lat2, lon2):
        from math import radians, sin, cos, sqrt, atan2

        earth_radius_km = 6371.0

        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)

        a = (
            sin(dlat / 2) ** 2
            + cos(radians(lat1))
            * cos(radians(lat2))
            * sin(dlon / 2) ** 2
        )

        c = 2 * atan2(sqrt(a), sqrt(max(0, 1 - a)))
        return earth_radius_km * c

    @staticmethod
    def _travel_minutes(distance_km):
        """Rough fallback only; OSRM road durations replace this when available."""
        if distance_km is None:
            return None

        return max(3, ceil((distance_km / 30.0) * 60))

    @staticmethod
    def _format_minutes(value):
        return f"{int(value) // 60:02d}:{int(value) % 60:02d}"

    @classmethod
    def _fetch_osrm_route(cls, points):
        """Request one road route for the day's ordered stops, if configured."""
        base_url = str(os.getenv("OSRM_URL", "")).strip().rstrip("/")
        if not base_url or len(points) < 2:
            return None
        coordinates = ";".join(f"{float(lon):.6f},{float(lat):.6f}" for lat, lon in points)
        url = f"{base_url}/route/v1/driving/{coordinates}?overview=full&geometries=geojson&steps=false"
        request = Request(url, headers={"User-Agent": "AISmartTravelPlanner/1.0", "Accept": "application/json"})
        try:
            with urlopen(request, timeout=2.5) as response:
                payload = json.loads(response.read(2_000_000).decode("utf-8"))
            route = (payload.get("routes") or [None])[0]
            if payload.get("code") != "Ok" or not route:
                return None
            legs = route.get("legs") or []
            geometry = route.get("geometry") or {}
            coordinates = geometry.get("coordinates") if isinstance(geometry, dict) else None
            if len(legs) != len(points) - 1 or not coordinates:
                return None
            return {
                "legs": [
                    {"distance_km": float(leg["distance"]) / 1000, "minutes": max(1, ceil(float(leg["duration"]) / 60))}
                    for leg in legs
                ],
                "geometry": coordinates,
            }
        except (OSError, URLError, TimeoutError, ValueError, TypeError, KeyError):
            return None

    def _route_and_schedule_day(self, schedule, hotel_lat, hotel_lon):
        """Attach road legs/map geometry and move stops to times that allow travel."""
        def places_in_day():
            return [slot for slot in schedule if slot.get("place")]

        def route_current_stops():
            points = [(hotel_lat, hotel_lon)]
            place_leg_refs = []
            hotel_return_refs = []
            for slot in schedule:
                if slot.get("place"):
                    place_leg_refs.append((slot, len(points) - 1))
                    points.append((slot["place"].get("latitude"), slot["place"].get("longitude")))
                if slot.get("return_to_hotel") and points[-1] != (hotel_lat, hotel_lon):
                    hotel_return_refs.append((slot, len(points) - 1))
                    points.append((hotel_lat, hotel_lon))
            if any(lat is None or lon is None for lat, lon in points):
                return None, place_leg_refs, hotel_return_refs, points
            if len(points) > 1 and points[-1] != (hotel_lat, hotel_lon):
                points.append((hotel_lat, hotel_lon))
            return self._fetch_osrm_route(points), place_leg_refs, hotel_return_refs, points

        route, place_leg_refs, hotel_return_refs, waypoints = route_current_stops()
        source = "OSRM" if route else "straight_line_estimate"
        legs = []
        geometry = []
        stops = places_in_day()
        removed = False
        if route:
            legs = route["legs"]
            geometry = route["geometry"]
            # A broad search radius is useful for planned day trips, but it
            # must not send an ordinary stop on a 25+ km road detour. Keep a
            # long transfer only for a recognized headline attraction; the
            # next route calculation then fills the day from the remaining
            # nearby choices.
            long_detours = []
            for slot, leg_index in place_leg_refs:
                leg = legs[leg_index]
                if slot.get("type") in {"Ăn sáng", "Ăn trưa", "Ăn tối"}:
                    # Keep a named meal at the chosen day-trip area. Removing
                    # it with the long sightseeing leg leaves guests without
                    # a real place to eat; the route is recalculated after
                    # distant sightseeing stops are removed.
                    continue
                if float(leg.get("distance_km") or 0) <= 15:
                    continue
                item = slot.get("place") or {}
                destination = self.recommender.get_place_by_id(item.get("place_id"))
                try:
                    prominence = self.recommender._prominence_score(destination) if destination else 0.0
                except (AttributeError, TypeError, ValueError):
                    prominence = 0.0
                if prominence < 0.9:
                    long_detours.append(slot)
            if long_detours:
                for slot in long_detours:
                    slot["place"] = None
                    slot["status"] = "unavailable"
                    slot["reason"] = "Điểm này tạo quãng đường bộ quá dài so với lịch trong ngày; hệ thống đã bỏ để giữ tuyến đi thực tế."
                    slot["duration_minutes"] = 0
                    slot["travel_from_previous"] = None
                removed = True
                route, place_leg_refs, hotel_return_refs, waypoints = route_current_stops()
                source = "OSRM" if route else "straight_line_estimate"
                stops = places_in_day()
                legs = route["legs"] if route else []
                geometry = route["geometry"] if route else []
            for slot, leg_index in place_leg_refs:
                leg = legs[leg_index]
                slot["travel_from_previous"] = {
                    "distance_km": round(leg["distance_km"], 2),
                    "estimated_minutes": leg["minutes"],
                    "distance_method": "osrm_road_route",
                    "duration_method": "osrm_estimated_driving_duration",
                }
                place = slot["place"]
                place["distance_km"] = round(leg["distance_km"], 2)
                place["travel_time_minutes"] = leg["minutes"]
            for slot, leg_index in hotel_return_refs:
                leg = legs[leg_index]
                slot["travel_to_hotel"] = {
                    "distance_km": round(leg["distance_km"], 2),
                    "estimated_minutes": leg["minutes"],
                    "distance_method": "osrm_road_route",
                    "duration_method": "osrm_estimated_driving_duration",
                }
        else:
            for first, second in zip(waypoints, waypoints[1:]):
                distance = self._distance(*first, *second) or 0
                legs.append({"distance_km": distance, "minutes": self._travel_minutes(distance)})
            for slot, leg_index in place_leg_refs:
                leg = legs[leg_index]
                slot["travel_from_previous"] = {
                    "distance_km": round(leg["distance_km"], 2),
                    "estimated_minutes": leg["minutes"],
                    "distance_method": "straight_line_haversine",
                    "duration_method": "rough_distance_estimate",
                }
                slot["place"]["distance_km"] = round(leg["distance_km"], 2)
                slot["place"]["travel_time_minutes"] = leg["minutes"]
            for slot, leg_index in hotel_return_refs:
                leg = legs[leg_index]
                slot["travel_to_hotel"] = {
                    "distance_km": round(leg["distance_km"], 2),
                    "estimated_minutes": leg["minutes"],
                    "distance_method": "straight_line_haversine",
                    "duration_method": "rough_distance_estimate",
                }
            geometry = []

        # A daily schedule includes travel plus a short arrival buffer. If this
        # pushes a stop outside its realistic daypart, leave that slot open.
        previous_end = min(self._minutes(schedule[0].get("start_time", "07:00")), 7 * 60) if schedule else 7 * 60
        place_index = 0
        for slot in schedule:
            desired = self._minutes(slot["start_time"])
            if slot.get("type") == "Ăn trưa":
                # Do not carry a stale lunch delay from sightseeing stops
                # that the route validator removed. Keep lunch in the
                # requested 12:30–13:30 midday window, adding only the actual
                # travel time needed from the last retained stop.
                desired = min(desired, 12 * 60 + 30)
            duration = int(slot.get("duration_minutes") or 0)
            place = slot.get("place")
            travel = 0
            if place:
                travel = int((slot.get("travel_from_previous") or {}).get("estimated_minutes") or 0)
            elif slot.get("return_to_hotel"):
                travel = int((slot.get("travel_to_hotel") or {}).get("estimated_minutes") or 0)
            start = max(desired, previous_end + travel + (10 if place or slot.get("return_to_hotel") else 0))
            allowed = self._minutes(slot["latest_start_time"]) if slot.get("latest_start_time") else None
            if place and ((allowed is not None and start > allowed) or start + duration > 22 * 60 + 30):
                if slot.get("type") == "Ăn trưa":
                    # A long morning excursion can run through the nominal
                    # lunch window. Preserve lunch immediately after the
                    # excursion, but do not claim a restaurant is open at the
                    # shifted arrival time when its hours were checked for
                    # noon. The traveler can choose a currently open venue
                    # near the attraction.
                    slot["place"] = None
                    slot["status"] = "flexible"
                    slot["description"] = "Chuyến tham quan buổi sáng kéo dài nên khung ăn trưa đã được dời theo giờ kết thúc thực tế. Chưa xác nhận được quán đang mở gần điểm dừng; hãy chọn quán tại khu vực trước khi đi."
                    slot["description_en"] = "The morning visit ran long, so lunch has moved to follow its actual end time. No nearby restaurant could be confirmed open; choose a venue in the area before setting out."
                    slot["travel_from_previous"] = None
                    removed = True
                    place = None
                    start = max(desired, previous_end)
                elif slot.get("type") == "Ăn tối":
                    # If a late afternoon stop pushes a restaurant past its
                    # realistic meal window, preserve dinner as an honest,
                    # venue-free block instead of silently deleting the meal
                    # or showing a restaurant that may already be closed.
                    slot["place"] = None
                    slot["status"] = "flexible"
                    slot["description"] = "Dành thời gian ăn tối. Nhà hàng đã chọn không còn phù hợp với giờ đến; hãy chọn một quán đang mở gần khách sạn hoặc trên tuyến về."
                    slot["description_en"] = "Time for dinner. The selected restaurant no longer fits the arrival time; choose an open venue near the hotel or along the return route."
                    slot["travel_from_previous"] = None
                    removed = True
                    place = None
                    start = max(desired, previous_end)
                else:
                    slot["place"] = None
                    slot["status"] = "unavailable"
                    slot["reason"] = "Thời gian di chuyển khiến điểm này không còn phù hợp với khung giờ; lịch giữ trống thay vì xếp quá muộn."
                    slot["travel_from_previous"] = None
                    slot["duration_minutes"] = 0
                    duration = 0
                    removed = True
                    place = None
                    start = max(desired, previous_end)
            end = start + duration
            slot["start_time"] = self._format_minutes(start)
            slot["end_time"] = self._format_minutes(end)
            if place:
                place["start_time"] = slot["start_time"]
                place["end_time"] = slot["end_time"]
                place["duration_minutes"] = duration
                slot["travel_from_previous"]["distance_method"] = ("osrm_road_route" if route else "straight_line_haversine")
                place_index += 1
            # An unavailable venue still occupies its planned window; using
            # only its start time caused later hotel/rest blocks to overlap it.
            previous_end = end

        if removed:
            # Recompute route geometry and leg values without any omitted stops.
            route, place_leg_refs, hotel_return_refs, waypoints = route_current_stops()
            source = "OSRM" if route else "straight_line_estimate"
            stops = places_in_day()
            if route:
                legs, geometry = route["legs"], route["geometry"]
            else:
                geometry = []
                legs = []
                for first, second in zip(waypoints, waypoints[1:]):
                    distance = self._distance(*first, *second) or 0
                    legs.append({"distance_km": distance, "minutes": self._travel_minutes(distance)})
                geometry = []
            # Update only route metrics on remaining stops. Times stay shifted
            # conservatively from the first, longer route estimate.
            for slot, leg_index in place_leg_refs:
                leg = legs[leg_index]
                slot["travel_from_previous"] = {
                    "distance_km": round(leg["distance_km"], 2),
                    "estimated_minutes": leg["minutes"],
                    "distance_method": "osrm_road_route" if route else "straight_line_haversine",
                    "duration_method": "osrm_estimated_driving_duration" if route else "rough_distance_estimate",
                }
                slot["place"]["distance_km"] = round(leg["distance_km"], 2)
                slot["place"]["travel_time_minutes"] = leg["minutes"]
            for slot, leg_index in hotel_return_refs:
                leg = legs[leg_index]
                slot["travel_to_hotel"] = {
                    "distance_km": round(leg["distance_km"], 2),
                    "estimated_minutes": leg["minutes"],
                    "distance_method": "osrm_road_route" if route else "straight_line_haversine",
                    "duration_method": "osrm_estimated_driving_duration" if route else "rough_distance_estimate",
                }

        return_leg = legs[-1] if len(legs) > 0 and waypoints[-1] == (hotel_lat, hotel_lon) else None
        return_minutes = int((return_leg or {}).get("minutes") or 0)
        last_end = self._minutes(stops[-1].get("end_time", "00:00")) if stops else 0
        return {
            "source": source,
            "geometry": geometry,
            "stop_count": len(places_in_day()),
            "return_to_hotel": ({
                "distance_km": round(float(return_leg["distance_km"]), 2),
                "estimated_minutes": return_minutes,
                "estimated_arrival": self._format_minutes(last_end + return_minutes),
                "distance_method": "osrm_road_route" if source == "OSRM" else "straight_line_haversine",
            } if return_leg else None),
            "duration_note": "Ước tính tuyến lái xe, không có giao thông trực tiếp." if source == "OSRM" else "Chưa có tuyến đường bộ; khoảng cách là đường chim bay, thời gian chỉ ước tính.",
        }

    @staticmethod
    def _duration_minutes(start_time, end_time):
        sh, sm = [int(x) for x in start_time.split(":")]
        eh, em = [int(x) for x in end_time.split(":")]

        return (eh * 60 + em) - (sh * 60 + sm)

    # ========================================================
    # HELPERS
    # ========================================================

    @staticmethod
    def _normalize_category(value):
        if value is None:
            return ""

        text = str(value).strip().lower()

        aliases = {
            "food": "Ăn uống",
            "restaurant": "Ăn uống",
            "restaurants": "Ăn uống",
            "ăn": "Ăn uống",
            "ăn uống": "Ăn uống",

            "coffee": "Cafe",
            "cafe": "Cafe",

            "sightseeing": "Tham quan",
            "attraction": "Tham quan",
            "tham quan": "Tham quan",

            "entertainment": "Giải trí",
            "giải trí": "Giải trí",

            "play": "Vui chơi",
            "vui chơi": "Vui chơi",

            "shopping": "Mua sắm",
            "mua sắm": "Mua sắm",

            "nature": "Thiên nhiên",
            "thiên nhiên": "Thiên nhiên",
        }

        return aliases.get(text, value)

    @staticmethod
    def _day_title(day):
        titles = {
            1: "Khám phá và ẩm thực",
            2: "Trải nghiệm địa phương",
            3: "Ẩm thực và vui chơi",
        }

        return titles.get(day, f"Khám phá ngày {day}")
