import main
from main import ChatRequest, chat, classify_requested_categories, find_requested_poi, requested_time_of_day
from ai.recommender import Place, TourismRecommender


def test_chat_parser_keeps_multiple_requested_categories():
    assert classify_requested_categories("thêm quán ăn và cafe ngày 2") == ["Ăn uống", "Cafe"]


def test_chat_parser_keeps_multiple_experience_preferences():
    assert classify_requested_categories("ưu tiên thiên nhiên, tham quan và mua sắm") == [
        "Tham quan",
        "Mua sắm",
        "Thiên nhiên",
    ]


def test_chat_understands_move_ba_na_to_morning():
    ba_na = Place(1, "Sun World Bà Nà Hills", "Vui chơi", province_code="48", latitude=16.0, longitude=108.0, rating=4.8)
    model = TourismRecommender([ba_na])
    assert requested_time_of_day("tôi muốn đi bà nà vào buổi sáng") == "morning"
    assert find_requested_poi("tôi muốn đi bà nà vào buổi sáng", model, []) is ba_na


def test_chat_does_not_match_ba_na_to_ba_nam():
    ba_nam = Place(2, "Vựa Mía Dừa Trái Cây Nhiệt Đới Bà Nam", "Mua sắm", province_code="48")
    model = TourismRecommender([ba_nam])
    assert find_requested_poi("tôi muốn đi bà nà vào buổi sáng", model, []) is None


def test_chat_moves_named_place_into_requested_daypart(monkeypatch):
    hotel = Place(100, "Hotel", "Lưu trú", province_code="48", latitude=16.05, longitude=108.2)
    breakfast = Place(1, "Breakfast cafe", "Ăn uống", province_code="48", latitude=16.051, longitude=108.201, opening_hours="Mo-Su 06:00-22:00")
    old_morning = Place(2, "Museum", "Tham quan", province_code="48", latitude=16.052, longitude=108.202)
    dinner = Place(3, "Dinner restaurant", "Ăn uống", province_code="48", latitude=16.053, longitude=108.203, opening_hours="Mo-Su 06:00-22:00")
    ba_na = Place(4, "Sun World Bà Nà Hills", "Vui chơi", province_code="48", latitude=16.054, longitude=108.204, opening_hours="Mo-Su 08:00-17:00")
    model = TourismRecommender([hotel, breakfast, old_morning, dinner, ba_na])
    monkeypatch.setattr(main, "get_system", lambda: (model, main.TripPlanner(model)))
    itinerary = [{"day": 1, "schedule": [
        {"start_time": "07:30", "end_time": "08:15", "type": "Ăn sáng", "duration_minutes": 45, "place": {"place_id": 1, "name": breakfast.name, "category": breakfast.category, "latitude": breakfast.latitude, "longitude": breakfast.longitude, "cost_type": "unavailable"}},
        {"start_time": "09:00", "end_time": "11:00", "type": "Tham quan buổi sáng", "duration_minutes": 120, "place": {"place_id": 2, "name": old_morning.name, "category": old_morning.category, "latitude": old_morning.latitude, "longitude": old_morning.longitude, "cost_type": "unavailable"}},
        {"start_time": "18:00", "end_time": "19:00", "type": "Ăn tối", "duration_minutes": 60, "place": {"place_id": 3, "name": dinner.name, "category": dinner.category, "latitude": dinner.latitude, "longitude": dinner.longitude, "cost_type": "unavailable"}},
        {"start_time": "19:15", "end_time": "20:45", "type": "Vui chơi buổi tối", "duration_minutes": 90, "place": {"place_id": 4, "name": ba_na.name, "category": ba_na.category, "latitude": ba_na.latitude, "longitude": ba_na.longitude, "cost_type": "unavailable"}},
    ]}]
    hotel_data = {"id": 100, "name": hotel.name, "latitude": hotel.latitude, "longitude": hotel.longitude}
    plan = {"hotel": hotel_data, "itinerary": itinerary}
    state = {"province_code": "48", "hotel_id": 100, "hotel_name": hotel.name, "days": 1, "people": 2, "radius_km": 8, "plan": plan, "itinerary": itinerary}

    response = chat(ChatRequest(message="tôi muốn đi bà nà vào buổi sáng", state=state))

    assert response["success"] is True
    assert response["action"] == "move_place_time"
    updated = response["data"]["itinerary"][0]["schedule"]
    assert next(slot for slot in updated if slot["type"] == "Tham quan buổi sáng")["place"]["name"] == ba_na.name
    assert next(slot for slot in updated if slot["type"] == "Vui chơi buổi tối")["place"] is None


def test_chat_adds_named_daytime_place_by_replacing_a_compatible_stop(monkeypatch):
    hotel = Place(100, "Hotel", "Lưu trú", province_code="48", latitude=16.05, longitude=108.2)
    cho_con = Place(101, "Chợ Cồn", "Tham quan", province_code="48", latitude=16.068, longitude=108.214)
    museum = Place(102, "Local Museum", "Tham quan", province_code="48", latitude=16.051, longitude=108.201)
    model = TourismRecommender([hotel, cho_con, museum])
    monkeypatch.setattr(main, "get_system", lambda: (model, main.TripPlanner(model)))
    hotel_data = {"id": hotel.id, "name": hotel.name, "latitude": hotel.latitude, "longitude": hotel.longitude}
    itinerary = [{"day": 1, "schedule": [
        {"start_time": "09:00", "end_time": "11:00", "type": "Tham quan buổi sáng", "duration_minutes": 120,
         "place": {"place_id": museum.id, "name": museum.name, "category": museum.category,
                   "latitude": museum.latitude, "longitude": museum.longitude, "cost_type": "unavailable"}},
    ]}]
    plan = {"hotel": hotel_data, "itinerary": itinerary}
    state = {"province_code": "48", "hotel_id": hotel.id, "hotel_name": hotel.name, "days": 1, "people": 2,
             "radius_km": 8, "plan": plan, "itinerary": itinerary}

    response = chat(ChatRequest(message="thêm Chợ Cồn vào ngày 1", state=state))

    assert response["action"] == "add_named_place"
    slot = response["data"]["itinerary"][0]["schedule"][0]
    assert slot["place"]["name"] == cho_con.name
    assert "Local Museum" in response["message"]
