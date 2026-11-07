"""Integration smoke check against the live Docker API using only stdlib."""

import json
from urllib.request import Request, urlopen


BASE_URL = "http://127.0.0.1:8001"


def request_json(path, payload=None):
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        BASE_URL + path,
        data=body,
        headers={"Content-Type": "application/json"} if body is not None else {},
        method="POST" if body is not None else "GET",
    )
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


root = request_json("/")
assert root["status"] == "running", root
database = request_json("/api/test-db")
assert database["total_places"] > 0, database
provinces = request_json("/api/provinces")
assert len(provinces) == 34, f"Expected 34 provinces, got {len(provinces)}"
payload = request_json(
    "/api/recommend",
    {"province_code": "48", "categories": ["Cafe"], "limit": 3},
)
assert payload["success"], payload
assert payload["count"] > 0, payload
assert len(payload["data"]) <= 3, payload

hotels = request_json(
    "/api/recommend/hotels",
    {
        "province_code": "48",
        "days": 3,
        "people": 2,
        "accommodation_preference": "near_beach",
        "limit": 1,
    },
)
assert hotels["success"] and hotels["data"], hotels
hotel = hotels["data"][0]
plan_request = {
    "province_code": "48",
    "hotel_id": hotel["id"],
    "days": 3,
    "people": 2,
    "start_date": "2026-10-12",
    "preferences": ["Ăn uống", "Cafe", "Tham quan"],
    "radius_km": 8,
    "limit_per_day": 6,
}
plan = request_json("/api/plan", plan_request)
assert plan["success"] and len(plan["itinerary"]) == 3, plan
assert isinstance(plan["planning_logic"]["road_routing_available"], bool), plan["planning_logic"]
segments = [
    slot["travel_from_previous"]
    for day in plan["itinerary"]
    for slot in day["schedule"]
    if slot.get("place")
]
assert segments and all(
    item["distance_method"] in {"straight_line_haversine", "osrm_road_route"}
    and item["duration_method"] in {"rough_distance_estimate", "osrm_estimated_driving_duration"}
    for item in segments
), segments
chat = request_json(
    "/api/chat",
    {
        "message": "Đổi khách sạn gần biển, đi 4 người, thêm quán ăn và cafe ngày 2",
        "state": {
            **plan_request,
            "province_name": "Đà Nẵng",
            "hotel_name": hotel["name"],
            "accommodation_preference": "near_beach",
            "plan": plan,
            "itinerary": plan["itinerary"],
        },
        "history": [],
    },
)
assert chat["success"] and chat["action"] == "change_hotel", chat
updated = chat["data"]["plan"]
assert updated["people"] == 4 and len(updated["itinerary"]) == 3, updated
day_two = updated["itinerary"][1]
assert day_two["date"] == "2026-10-13", day_two
assert day_two["place_count"] == sum(bool(slot.get("place")) for slot in day_two["schedule"]), day_two
assert day_two["place_count"] >= 4, day_two
day_two_places = [slot["place"] for slot in day_two["schedule"] if slot.get("place")]
assert sum(place["category"] == "Ăn uống" for place in day_two_places) >= 2, day_two
assert any(place["category"] == "Cafe" for place in day_two_places), day_two
assert not any(slot.get("status") == "unavailable" for day in updated["itinerary"] for slot in day["schedule"])
day_preferences = chat["data"]["state"]["chat_day_preferences"]["2"]
assert "Ăn uống" in day_preferences and "Cafe" in day_preferences, day_preferences
place_ids = [
    slot["place"]["place_id"]
    for day in updated["itinerary"]
    for slot in day["schedule"]
    if slot.get("place")
]
assert len(place_ids) == len(set(place_ids)), place_ids
print(
    "API smoke test passed:",
    f"{database['total_places']} places,",
    f"{len(provinces)} provinces,",
    f"{payload['count']} recommendations,",
    "multi-intent chat kept a 3-day unique plan and added both requested venue categories to Day 2",
)
