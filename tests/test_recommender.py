from datetime import date, datetime, timezone

from ai.planner import TripPlanner
from ai.recommender import Place, TourismRecommender
from ai.repository import display_ward_name


def make_place(identifier, category, rating, description):
    place = Place(identifier, f"Place {identifier}", category, None, "48", "Đà Nẵng", None, "Phường Hải Châu",
                   16.05, 108.2, rating, 10, description, None, None)
    place.address = f"{identifier} Đường thử nghiệm"
    place.image_url = f"/media/places/test-{identifier}.jpg"
    return place


def test_recommend_nearby_respects_category_and_radius():
    model = TourismRecommender([make_place(1, "Cafe", 4.8, "coffee biển"), make_place(2, "Ăn uống", 5, "hải sản")])
    results = model.recommend_nearby(
        latitude=16.05,
        longitude=108.2,
        province_code="48",
        interests=["Cafe"],
        radius_km=1,
        limit=10,
    )
    assert [item.id for item in results] == [1]


def test_recommend_nearby_excludes_selected_hotel_or_place():
    model = TourismRecommender([make_place(1, "Cafe", 4.8, "coffee biển"), make_place(2, "Cafe", 4.5, "coffee biển")])
    results = model.recommend_nearby(
        latitude=16.05,
        longitude=108.2,
        province_code="48",
        interests=["Cafe"],
        radius_km=1,
        limit=10,
        exclude_id=1,
    )
    assert [item.id for item in results] == [2]


def test_landmark_score_ignores_restaurants_and_ba_nam_shops():
    restaurant = Place(1, "My Son Valley Restaurant", "Ăn uống", province_code="48")
    shop = Place(2, "Vựa Mía Dừa Bà Nam", "Mua sắm", province_code="48")
    ba_na = Place(3, "Sun World Bà Nà Hills", "Vui chơi", province_code="48")
    cho_con = Place(4, "Chợ Cồn", "Tham quan", province_code="48")
    helio = Place(5, "Trung tâm Giải trí Phức hợp Helio Center", "Vui chơi", province_code="48")
    scorer = TourismRecommender([restaurant, shop, ba_na])
    assert scorer._prominence_score(restaurant) < 0.85
    assert scorer._prominence_score(shop) < 0.85
    assert scorer._prominence_score(ba_na) == 1.0
    assert scorer._prominence_score(cho_con) == 1.0
    assert scorer._prominence_score(helio) == 1.0


def test_trip_budget_prefers_places_with_recent_public_prices():
    priced = make_place(1, "Ăn uống", 4.5, "restaurant")
    priced.price_min = 15_000
    priced.price_max = 30_000
    priced.price_kind = "published_offer"
    priced.price_source_url = "https://restaurant.example/menu"
    priced.price_checked_at = datetime.now(timezone.utc)
    unpriced = make_place(2, "Ăn uống", 4.5, "restaurant")

    priced_penalty = TripPlanner._budget_route_adjustment(priced, 100_000)
    unpriced_penalty = TripPlanner._budget_route_adjustment(unpriced, 100_000)

    assert priced_penalty < unpriced_penalty


def test_wide_da_nang_exploration_prioritizes_ba_na_over_nearer_stop():
    hotel = make_place(100, "Lưu trú", 4.8, "hotel")
    hotel.latitude, hotel.longitude = 16.05, 108.20
    ba_na = Place(1, "Sun World Bà Nà Hills", "Vui chơi", province_code="48", latitude=16.0, longitude=108.0, address="Đường thử nghiệm")
    nearer = Place(2, "Ngũ Hành Sơn", "Tham quan", province_code="48", latitude=16.04, longitude=108.22, address="Đường thử nghiệm")
    ba_na.image_url = nearer.image_url = "/media/places/test.jpg"
    planner = TripPlanner(TourismRecommender([hotel, ba_na, nearer]))
    selected = planner._select_next_place(
        pools={"__sightseeing__": [nearer, ba_na]},
        preferred_category="__sightseeing__", used_ids={100}, used_places=[],
        previous_lat=hotel.latitude, previous_lon=hotel.longitude,
        start_time="09:00", end_time="11:00", prioritize_exploration=True,
    )
    assert selected is ba_na


def test_ba_na_aliases_are_not_scheduled_as_two_destinations():
    sun_world = Place(1, "Sun World Bà Nà Hills", "Vui chơi", province_code="48", latitude=15.9956, longitude=107.9897)
    mountain = Place(2, "Núi Bà Nà", "Thiên nhiên", province_code="48", latitude=15.9982, longitude=107.9856)
    assert TripPlanner._same_place(sun_world, mountain)


def test_same_landmark_under_local_area_names_is_not_scheduled_twice():
    hai_van_gate = Place(1, "Hải Vân Quan", "Tham quan", latitude=16.19, longitude=108.13)
    hai_van_pass = Place(2, "Đèo Hải Vân", "Tham quan", latitude=16.1905, longitude=108.1304)
    assert TripPlanner._same_place(hai_van_gate, hai_van_pass)


def test_practical_day_has_four_daytime_stops_and_one_evening_stop():
    outing_roles = [slot[5] for slot in TripPlanner.DAILY_SLOTS if slot[5].startswith("outing_")]
    assert outing_roles.count("outing_morning") == 2
    assert outing_roles.count("outing_afternoon") == 2
    assert outing_roles.count("outing_evening") == 1


def test_road_route_duration_is_reserved_before_the_next_visit(monkeypatch):
    hotel = Place(100, "Hotel", "Lưu trú", latitude=0, longitude=0)
    first = Place(1, "First attraction", "Tham quan", latitude=1, longitude=1)
    second = Place(2, "Second attraction", "Tham quan", latitude=2, longitude=2)
    planner = TripPlanner(TourismRecommender([hotel, first, second]))
    monkeypatch.setattr(TripPlanner, "_fetch_osrm_route", lambda cls, points: {
        "legs": [
            {"distance_km": 10, "minutes": 20},
            {"distance_km": 66.5, "minutes": 62},
            {"distance_km": 10, "minutes": 20},
        ],
        "geometry": [[0, 0], [1, 1], [2, 2], [0, 0]],
    })
    schedule = [
        {"start_time": "08:00", "end_time": "09:30", "duration_minutes": 90,
         "latest_start_time": "09:30", "place": {"latitude": 1, "longitude": 1}},
        {"start_time": "10:00", "end_time": "11:30", "duration_minutes": 90,
         "latest_start_time": "10:45", "place": {"latitude": 2, "longitude": 2}},
    ]
    planner._route_and_schedule_day(schedule, 0, 0)
    assert schedule[1]["start_time"] == "10:42"


def test_attraction_type_changes_recommended_visit_duration():
    museum = Place(1, "Bảo tàng Chăm", "Tham quan", subcategory="Bảo tàng")
    theme_park = Place(2, "Helio Center", "Vui chơi", subcategory="Công viên giải trí")
    assert TripPlanner._recommended_visit_duration(museum, 120) == 90
    assert TripPlanner._recommended_visit_duration(theme_park, 120) == 180


def test_mall_category_does_not_use_generic_rest_stop_as_a_shop():
    stop = Place(1, "Điểm dừng chân", "Mua sắm", province_code="48")
    assert TripPlanner._is_generic_stop_placeholder(stop, "Mua sắm")


def test_ba_na_gets_a_day_visit_with_lunch_break_and_evening_meal():
    hotel = Place(100, "Hotel", "Lưu trú", province_code="48", latitude=16.0487, longitude=108.2424)
    breakfast = Place(101, "Breakfast cafe", "Ăn uống", province_code="48", latitude=16.049, longitude=108.243, address="Đường thử nghiệm")
    ba_na = Place(102, "Sun World Bà Nà Hills", "Vui chơi", province_code="48", latitude=15.9956, longitude=107.9897, address="Đường thử nghiệm")
    dinner = Place(103, "Dinner restaurant", "Ăn uống", province_code="48", latitude=16.05, longitude=108.244, address="Đường thử nghiệm")
    night = Place(104, "Night market", "Mua sắm", province_code="48", latitude=16.05, longitude=108.245, opening_hours="Mo-Su 19:00-23:59", address="Đường thử nghiệm")
    onsite_restaurant = Place(105, "Bà Nà onsite restaurant", "Ăn uống", province_code="48", latitude=15.996, longitude=107.990, rating=5, address="Đường thử nghiệm")
    for item in (breakfast, ba_na, dinner, night, onsite_restaurant):
        item.image_url = f"/media/places/test-{item.id}.jpg"
    planner = TripPlanner(TourismRecommender([hotel, breakfast, ba_na, dinner, night, onsite_restaurant]))
    result = planner.create_plan(
        province_code="48", days=1, people=2, hotel_id=hotel.id,
        radius_km=100, preferences=["Tham quan", "Mua sắm"],
    )
    schedule = result["itinerary"][0]["schedule"]
    ba_na_slot = next(slot for slot in schedule if (slot.get("place") or {}).get("name") == ba_na.name)
    assert ba_na_slot["type"] == "Khám phá Bà Nà cả ngày"
    assert ba_na_slot["duration_minutes"] == 390
    assert ba_na_slot["place"]["onsite_breaks"][0]["type"] == "Ăn trưa và nghỉ tại khu du lịch"
    assert any(slot["type"] == "Trở về trung tâm và nghỉ ngơi" for slot in schedule)
    assert not any(slot.get("type") == "Ăn trưa" for slot in schedule)
    dinner_slot = next(slot for slot in schedule if slot.get("type") == "Ăn tối")
    assert dinner_slot.get("place", {}).get("name") == dinner.name
    assert TripPlanner._distance(
        ba_na.latitude, ba_na.longitude,
        dinner_slot["place"]["latitude"], dinner_slot["place"]["longitude"],
    ) >= 8
    assert any(slot.get("type") == "Mua sắm buổi tối" and (slot.get("place") or {}).get("name") == night.name for slot in schedule)
    assert not any(slot.get("type") == "Ẩm thực buổi tối" for slot in schedule)

    without_shopping_preference = planner.create_plan(
        province_code="48", days=1, people=2, hotel_id=hotel.id,
        radius_km=100, preferences=["Tham quan"],
    )
    assert not any(
        (slot.get("place") or {}).get("category") == "Mua sắm"
        or slot.get("type", "").startswith("Mua sắm")
        for slot in without_shopping_preference["itinerary"][0]["schedule"]
    )


def test_trip_plan_keeps_a_practical_daily_sightseeing_pace():
    hotel = make_place(100, "Hotel", 4.8, "hotel")
    places = [hotel]
    next_id = 1
    for category in ("Tham quan", "Vui chơi", "Thiên nhiên"):
        for offset in range(8):
            place = make_place(next_id, category, 4.8, category)
            place.name = f"{category} {offset}"
            place.category = category
            place.latitude = 16.05 + (offset % 3) * 0.002
            place.longitude = 108.20 + (offset // 3) * 0.002
            places.append(place)
            next_id += 1
    planner = TripPlanner(TourismRecommender(places))
    result = planner.create_plan(
        province_code="48", days=3, people=2, hotel_id=hotel.id,
        radius_km=100, preferences=["Tham quan", "Vui chơi", "Thiên nhiên"],
    )
    daily_sightseeing = [
        sum(1 for slot in day["schedule"] if slot.get("place") and slot.get("type", "").startswith(("Tham quan", "Vui chơi", "Thiên nhiên")))
        for day in result["itinerary"]
    ]
    # Four daytime visits on full days, then a deliberately lighter departure day.
    assert daily_sightseeing == [4, 4, 2]


def test_plan_address_includes_known_ward_commune_and_province():
    hotel = Place(
        1, "Khách sạn thử nghiệm", "Lưu trú", province_name="Đà Nẵng",
        district_name="Hải Châu", ward_name="Phường Hải Châu 1",
        address="12 Đường thử nghiệm, Đà Nẵng, Đà Nẵng",
    )
    assert TripPlanner(TourismRecommender([hotel]))._full_address(hotel) == (
        "12 Đường thử nghiệm, Đà Nẵng, Phường Hải Châu 1"
    )
    assert display_ward_name("33_hoi_an_phuong") == "Phường Hoi An"
    assert display_ward_name("xa An Bình", "Xã") == "Xã An Bình"


def test_planner_builds_unique_daily_stops_and_uses_start_date():
    hotel = make_place(100, "Lưu trú", 4.8, "hotel")
    hotel.latitude, hotel.longitude = 16.05, 108.2
    places = [hotel]
    for identifier, longitude in ((1, 108.201), (2, 108.202)):
        place = make_place(identifier, "Ăn uống", 4.8, "nhà hàng")
        place.latitude, place.longitude = 16.05, longitude
        place.opening_hours = "Mo-Su 06:00-22:00"
        places.append(place)

    planner = TripPlanner(TourismRecommender(places))
    result = planner.create_plan(
        province_code="48",
        days=2,
        people=2,
        hotel_id=100,
        radius_km=8,
        limit_per_day=1,
        start_date=date(2026, 10, 11),
    )

    assert len(result["itinerary"]) == 2
    assert [day["date"] for day in result["itinerary"]] == ["2026-10-11", "2026-10-12"]
    ids = [slot["place"]["place_id"] for day in result["itinerary"] for slot in day["schedule"] if slot.get("place")]
    assert len(set(ids)) == 2
    for day in result["itinerary"]:
        daily_ids = [slot["place"]["place_id"] for slot in day["schedule"] if slot.get("place")]
        assert len(daily_ids) == len(set(daily_ids))
    assert result["planning_logic"]["road_routing_available"] is False
    travel_segments = [
        slot["travel_from_previous"]
        for day in result["itinerary"]
        for slot in day["schedule"]
        if slot.get("place")
    ]
    assert all(segment["distance_method"] == "straight_line_haversine" for segment in travel_segments)
    assert all(segment["duration_method"] == "rough_distance_estimate" for segment in travel_segments)


def test_published_opening_hours_are_checked_for_trip_weekday():
    place = make_place(1, "Tham quan", 4.8, "museum")
    place.opening_hours = "Mo-Fr 09:00-17:00; Sa 10:00-14:00; Su off"

    assert TripPlanner._open_during_slot(place, "09:15", "11:15", weekday=0)
    assert not TripPlanner._open_during_slot(place, "09:15", "11:15", weekday=6)
    assert not TripPlanner._open_during_slot(place, "14:30", "16:30", weekday=5)


def test_weekday_for_each_itinerary_day_uses_its_own_date():
    # 11 Oct 2026 is Sunday; the following day is Monday.
    assert TripPlanner.weekday_for_day({"day": 1, "date": "2026-10-11"}) == 6
    assert TripPlanner.weekday_for_day({"day": 2, "date": "2026-10-12"}) == 0
    assert TripPlanner.weekday_for_day({"day": 2}, "2026-10-11") == 0
    assert TripPlanner.weekday_for_day({"day": 2, "date": "bad"}, "2026-10-11") == 0


def test_day_specific_food_request_adds_evening_food_slot():
    hotel = make_place(100, "Lưu trú", 4.8, "hotel")
    hotel.latitude, hotel.longitude = 16.05, 108.2
    places = [hotel]
    for identifier, longitude in ((1, 108.201), (2, 108.202), (3, 108.203), (4, 108.204), (7, 108.207)):
        place = make_place(identifier, "Ăn uống", 4.8, "nhà hàng")
        place.latitude, place.longitude = 16.05, longitude
        place.opening_hours = "Mo-Su 06:00-22:00"
        places.append(place)
    cafe = make_place(5, "Cafe", 4.8, "coffee")
    cafe.latitude, cafe.longitude = 16.05, 108.205
    cafe.opening_hours = "Mo-Su 07:00-22:00"
    attraction = make_place(6, "Tham quan", 4.8, "museum")
    attraction.latitude, attraction.longitude = 16.05, 108.206
    attraction.opening_hours = "Mo-Su 07:00-22:00"
    attraction_two = make_place(8, "Tham quan", 4.8, "temple")
    attraction_two.latitude, attraction_two.longitude = 16.05, 108.208
    attraction_two.opening_hours = "Mo-Su 07:00-22:00"
    places.extend([cafe, attraction, attraction_two])

    result = TripPlanner(TourismRecommender(places)).create_plan(
        province_code="48",
        days=1,
        people=2,
        hotel_id=100,
        radius_km=8,
        limit_per_day=7,
        day_preferences={1: ["Ăn uống"]},
    )
    evening = next(
        slot for slot in result["itinerary"][0]["schedule"]
        if slot["type"] == "Ẩm thực buổi tối"
    )

    assert evening["type"] == "Ẩm thực buổi tối"
    assert evening["place"]["category"] == "Ăn uống"
    assert evening["start_time"] >= "19:15"
    assert evening["end_time"] <= "22:30"


def test_plan_has_arrival_departure_and_hotel_rest_blocks():
    hotel = make_place(100, "Hotel", 4.8, "hotel")
    breakfast = make_place(101, "Breakfast", 4.8, "restaurant")
    breakfast.category = "Ăn uống"
    breakfast.latitude, breakfast.longitude = hotel.latitude, hotel.longitude
    planner = TripPlanner(TourismRecommender([hotel, breakfast]))
    result = planner.create_plan(
        province_code="48", days=2, people=2, hotel_id=hotel.id,
        radius_km=8, limit_per_day=1,
    )
    first = result["itinerary"][0]["schedule"]
    last = result["itinerary"][-1]["schedule"]
    assert first[0]["type"] == "Gửi hành lý / làm thủ tục lưu trú"
    assert any(slot["type"] == "Về khách sạn nghỉ trưa" and slot.get("return_to_hotel") for slot in first)
    assert any(slot["type"] == "Trả phòng / gửi hành lý" for slot in last)
    assert any(slot["type"] == "Về khách sạn tắm rửa và nghỉ trước bữa tối" for slot in first)
    assert last[-1]["type"] == "Về khách sạn nhận hành lý và khởi hành"
    assert not any(slot.get("type") == "Vui chơi buổi tối" for slot in last)


def test_tourist_shopping_keeps_supermarkets_but_ignores_small_groceries():
    planner = TripPlanner(TourismRecommender([]))
    assert planner._is_generic_stop_placeholder(Place(1, "WinMart+", "Mua sắm"), "Mua sắm")
    assert planner._is_generic_stop_placeholder(Place(14, "Cửa hàng WinMart+ Vĩnh Thịnh", "Mua sắm"), "Mua sắm")
    assert not planner._is_generic_stop_placeholder(Place(2, "GO! Đà Nẵng", "Mua sắm"), "Mua sắm")
    assert not planner._is_generic_stop_placeholder(Place(3, "Chợ Cồn", "Mua sắm"), "Mua sắm")
    assert not planner._is_tourism_candidate(Place(4, "Tạp hóa Cô Tâm", "Mua sắm"))
    assert not planner._is_tourism_candidate(Place(5, "Cô Tâm", "Mua sắm"))
    assert not planner._is_tourism_candidate(Place(11, "tạp hóa", "Mua sắm", subcategory="Cửa hàng tiện lợi"))
    assert not planner._is_tourism_candidate(Place(12, "Nobipet - Siêu Thị Thú Cưng", "Mua sắm", subcategory="Siêu thị"))
    assert not planner._is_tourism_candidate(Place(10, "Shoping Mall", "Mua sắm"))
    assert planner._is_tourism_candidate(Place(6, "Chợ Cồn", "Mua sắm"))
    assert planner._is_tourism_candidate(Place(7, "Vincom Plaza Đà Nẵng", "Mua sắm"))
    assert planner._is_tourism_candidate(Place(8, "GO! Đà Nẵng", "Mua sắm"))
    assert planner._is_tourism_candidate(Place(9, "Co.opmart Đà Nẵng", "Mua sắm"))
    assert planner._is_tourism_candidate(Place(13, "BRG Mart", "Mua sắm", subcategory="Siêu thị"))


def test_meal_planning_excludes_bars_and_beer_halls():
    planner = TripPlanner(TourismRecommender([]))
    assert not planner._is_tourism_candidate(Place(1, "Phố Cổ Bia Hơi Hà Nội", "Ăn uống"))
    assert not planner._is_tourism_candidate(Place(2, "Cocktail Bar Da Nang", "Ăn uống"))
    assert planner._is_tourism_candidate(Place(3, "Quán Phở An", "Ăn uống"))


def test_itinerary_candidate_without_a_local_photo_is_hidden():
    planner = TripPlanner(TourismRecommender([]))
    missing_photo = make_place(4, "Tham quan", 4.5, "museum")
    missing_photo.image_url = "https://example.com/photo.jpg"
    assert not planner._has_local_verified_image(missing_photo)
    assert planner._has_local_verified_image(make_place(5, "Tham quan", 4.5, "museum"))
    assert not planner._has_verified_photo_and_price(make_place(6, "Tham quan", 4.5, "museum"))
    priced = make_place(7, "Tham quan", 4.5, "museum")
    priced.price_kind = "published_offer"
    priced.price_source_url = "https://venue.example/prices"
    priced.price_min = 25_000
    priced.price_checked_at = datetime.now(timezone.utc)
    assert planner._has_verified_photo_and_price(priced)


def test_planner_does_not_treat_sports_facilities_as_tourist_attractions():
    planner = TripPlanner(TourismRecommender([]))
    yoga = Place(1, "Jade Private Fitness", "Vui chơi", address="123 Đường A")
    swim_club = Place(2, "CLB Bơi lội Đà Nẵng", "Vui chơi", address="123 Đường A")
    helio = Place(3, "Helio Center", "Vui chơi", address="123 Đường A")
    assert not planner._is_tourism_candidate(yoga)
    assert not planner._is_tourism_candidate(swim_club)
    assert planner._is_tourism_candidate(helio)


def test_shopping_stops_stay_local_unless_they_are_tourist_markets():
    hotel = make_place(100, "Hotel", 4.8, "hotel")
    hotel.latitude, hotel.longitude = 16.05, 108.20
    supermarket = Place(1, "Blue Ms. Tam Tam", "Mua sắm", province_code="48", latitude=15.88, longitude=108.32)
    market = Place(2, "Chợ Cồn", "Mua sắm", province_code="48", latitude=16.06, longitude=108.21, address="Đường thử nghiệm")
    market.image_url = "/media/places/test-market.jpg"
    planner = TripPlanner(TourismRecommender([hotel, supermarket, market]))
    selected = planner._select_next_place(
        pools={"Mua sắm": [supermarket, market]}, preferred_category="Mua sắm",
        used_ids={hotel.id}, used_places=[], previous_lat=hotel.latitude,
        previous_lon=hotel.longitude, hotel_latitude=hotel.latitude,
        hotel_longitude=hotel.longitude, max_distance_from_hotel_km=12,
        start_time="14:30", end_time="16:30",
    )
    assert selected is market


def test_night_markets_are_not_scheduled_as_morning_sightseeing():
    hotel = make_place(100, "Hotel", 4.8, "hotel")
    night_market = Place(1, "Chợ đêm Helio", "Tham quan", province_code="48", latitude=16.05, longitude=108.201)
    planner = TripPlanner(TourismRecommender([hotel, night_market]))
    assert planner._is_night_market(night_market)
    assert planner._select_next_place(
        pools={"Tham quan": [night_market]}, preferred_category="Tham quan",
        used_ids={hotel.id}, used_places=[], previous_lat=hotel.latitude,
        previous_lon=hotel.longitude, start_time="08:15", end_time="10:15",
    ) is None


def test_common_landmark_osm_mistags_are_shown_with_a_useful_activity_label():
    bridge = Place(1, "Cầu Trần Thị Lý", "Mua sắm")
    assert TripPlanner._effective_category(bridge) == "Tham quan"
    night_market = Place(2, "Chợ đêm Helio", "Tham quan")
    assert TripPlanner._effective_category(night_market) == "Mua sắm"


def test_evening_slot_never_falls_back_to_distant_daytime_landmark():
    hotel = make_place(100, "Lưu trú", 4.8, "hotel")
    hotel.latitude, hotel.longitude = 16.05, 108.2
    landmark = make_place(1, "Vui chơi", 5.0, "Sun World Bà Nà Hills")
    landmark.latitude, landmark.longitude = 16.20, 108.00
    landmark.opening_hours = "Mo-Su 08:00-17:00"
    result = TripPlanner(TourismRecommender([hotel, landmark])).create_plan(
        province_code="48", days=1, people=2, hotel_id=100,
        radius_km=100, limit_per_day=7,
    )
    evening_stops = [slot for slot in result["itinerary"][0]["schedule"] if "buổi tối" in slot["type"]]
    assert all(slot.get("place") is not None for slot in evening_stops)
    assert not any((slot.get("place") or {}).get("name") == landmark.name for slot in evening_stops)
