"""Read-only integration smoke check against the live Docker API."""

import json
from io import BytesIO
from urllib.request import Request, urlopen
from pypdf import PdfReader


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
assert database["available_places"] > 0, database
provinces = request_json("/api/provinces")
assert len(provinces) == 34, f"Expected 34 provinces, got {len(provinces)}"
staged = request_json("/api/chat/places?limit=500")
assert staged, "No ready-for-use places returned by the chat data source"
hotels = [place for place in staged if place.get("category") == "Lưu trú"]
assert hotels, "No ready-for-use hotel returned by the chat data source"
hotel = hotels[0]
province_code = hotel["province_code"]
plan = request_json("/api/chat-plan", {
    "province_code": province_code,
    "hotel_id": hotel["id"],
    "days": 3,
    "people": 2,
    "budget": 50000000,
    "preferences": ["Ăn uống", "Cafe", "Tham quan"],
})
assert plan["success"] and len(plan["data"]["itinerary"]) == 3, plan
assert isinstance(plan["data"]["cost_summary"]["total_estimated_trip_cost"], (int, float)), plan
for day in plan["data"]["itinerary"]:
    non_stay_places = [
        slot["place"] for slot in day["schedule"]
        if slot.get("place") and slot["place"].get("category") != "Lưu trú"
    ]
    assert len(non_stay_places) <= 8, f"Day {day['day']} is overloaded: {len(non_stay_places)} stops"

pdf_request = Request(
    BASE_URL + "/api/chat-plan/pdf",
    data=json.dumps({"plan": plan["data"]}, ensure_ascii=False).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urlopen(pdf_request, timeout=60) as response:
    pdf = response.read()
    assert response.headers.get_content_type() == "application/pdf"
assert pdf.startswith(b"%PDF-") and len(pdf) > 1000, "PDF response is empty or invalid"
pdf_reader = PdfReader(BytesIO(pdf))
pdf_images = sum(len(page.images) for page in pdf_reader.pages)
assert pdf_images > 0, "PDF was generated without any place photos"

chat = request_json(
    "/api/chat",
    {
        "message": "Xin chào",
        "state": {},
        "history": [],
    },
)
assert chat.get("action") == "need_context" and chat.get("message"), chat
print(
    "API smoke test passed:",
    f"{database['available_places']} available places ({database['total_places']} published, {database['ready_staged_places']} ready),",
    f"{len(provinces)} provinces,",
    f"{len(staged)} chat records,",
    f"3-day plan, valid PDF with {pdf_images} embedded photos, chat response",
)
