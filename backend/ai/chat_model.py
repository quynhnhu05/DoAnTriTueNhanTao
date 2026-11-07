"""Local language-model interpretation for the trip assistant.

The model only translates free-form requests into a safe, bounded command for
the existing trip planner. It does not invent prices or modify the database.
"""
from __future__ import annotations

import json
import os
from urllib.error import URLError
from urllib.request import Request, urlopen


OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5:4b")
OLLAMA_TIMEOUT = max(3.0, float(os.getenv("OLLAMA_TIMEOUT", "75")))


def chat_model_status() -> dict:
    """Report whether the configured local model is installed and reachable."""
    request = Request(OLLAMA_URL + "/api/tags", headers={"Accept": "application/json"}, method="GET")
    try:
        with urlopen(request, timeout=2) as response:
            result = json.loads(response.read().decode("utf-8"))
        models = result.get("models", [])
        available = any(
            str(item.get("name", "")).split(":")[0] == OLLAMA_MODEL.split(":")[0]
            and str(item.get("name", "")).endswith(":" + OLLAMA_MODEL.split(":")[-1])
            for item in models
        )
        return {"mode": "local_ai" if available else "rules", "model": OLLAMA_MODEL, "available": available}
    except (OSError, URLError, TimeoutError, ValueError, KeyError, TypeError):
        return {"mode": "rules", "model": OLLAMA_MODEL, "available": False}


def _trip_context(state: dict) -> str:
    plan = state.get("itinerary") or []
    schedule = []
    for day in plan[:10]:
        for slot in (day.get("schedule") or [])[:10]:
            place = slot.get("place") or {}
            schedule.append({
                "day": day.get("day"),
                "date": day.get("date"),
                "from": slot.get("start_time"),
                "to": slot.get("end_time"),
                "activity": slot.get("type"),
                "place": place.get("name"),
                "category": place.get("category"),
                "address": place.get("address") or place.get("ward"),
                "duration_minutes": slot.get("duration_minutes"),
                "travel_km": (slot.get("travel_from_previous") or {}).get("distance_km"),
            })
    context = {
        "destination": state.get("province_name"), "days": state.get("days"),
        "travelers": state.get("people"), "budget_vnd": state.get("budget"),
        "stay": state.get("hotel_name"), "radius_km": state.get("radius_km"),
        "stops_per_day": state.get("limit_per_day"), "interests": state.get("preferences") or [],
        "day_preferences": state.get("chat_day_preferences") or {},
        "scheduled_stops": schedule,
    }
    return json.dumps(context, ensure_ascii=False)


def interpret_trip_message(message: str, state: dict, history: list[dict] | None = None) -> dict | None:
    """Return a safe canonical planner command, answer, or clarification.

    Returns None when Ollama is unavailable; callers can use the deterministic
    command parser as a fallback.
    """
    safe_history = [
        {"role": "assistant" if item.get("role") == "bot" else "user", "content": str(item.get("text", ""))[:200]}
        for item in (history or [])[-3:]
        if item.get("role") in {"bot", "user"} and item.get("text")
    ]
    system = (
        "Bạn là trợ lý lập chuyến du lịch Việt Nam, hiểu tiếng Việt tự nhiên có dấu/không dấu và tiếng Anh. "
        "Đọc cả lịch sử gần nhất để hiểu đại từ như 'đổi nó', 'ngày đó', 'chỗ này', nhưng ưu tiên tin nhắn mới nhất. "
        "Trả đúng một JSON theo một trong các dạng: {\"mode\":\"command\",\"command\":\"...\"}, "
        "{\"mode\":\"commands\",\"commands\":[\"...\",\"...\"]}, "
        "{\"mode\":\"answer\",\"reply\":\"...\"}, hoặc {\"mode\":\"clarify\",\"reply\":\"...\"}. "
        "Nếu người dùng yêu cầu hành động thì tạo command ngắn, rõ, giữ nguyên tên địa điểm, số ngày, số người, giờ, "
        "ngân sách, bán kính, số điểm mỗi ngày và TẤT CẢ yêu cầu trong câu ghép. Không được bỏ ý phụ. "
        "Hỗ trợ hỏi/sửa lịch hiện có; thêm, bớt, thay, chuyển điểm theo ngày/buổi; tạo lịch mới; đổi khách sạn; "
        "điều chỉnh nhịp độ, nhóm ăn uống/cafe/tham quan/thiên nhiên/mua sắm/vui chơi/giải trí và ràng buộc trong chuyến đi. "
        "Với câu có nhiều hành động độc lập, bắt buộc trả mode=commands và tách thành các lệnh theo đúng thứ tự; "
        "mỗi lệnh chỉ chứa một thay đổi, tối đa 4 lệnh. Không được gộp rồi làm rơi ý, ngày, buổi hoặc phủ định. "
        "Ví dụ: 'đổi khách sạn gần biển, ngày 2 thêm quán hải sản và bỏ mua sắm ngày 1' → commands gồm "
        "'đổi khách sạn gần biển', 'ngày 2 thêm quán ăn hải sản', 'ngày 1 bỏ mua sắm'. "
        "Ví dụ: 'ngày 2 thêm quán ăn ngon, 4 người' → command giữ cả ngày 2, ăn uống, 4 người. "
        "'đi với trẻ nhỏ, đừng đi xa, ưu tiên biển và ăn hải sản' → giữ đủ ba điều kiện trong command. "
        "'đưa Chợ Cồn vào buổi sáng ngày 2' → giữ nguyên Chợ Cồn, buổi sáng, ngày 2. "
        "'khách sạn gần biển, ngân sách 3 triệu' → giữ gần biển và 3 triệu. "
        "Nếu hỏi thông tin thì trả lời từ lịch trình/điểm đến trong ngữ cảnh, không tự bịa giá, giờ mở cửa hoặc dữ liệu. "
        "Hiểu cách nói đời thường, câu thiếu dấu, lỗi gõ, góp ý và câu ghép; diễn giải theo mục tiêu chuyến đi chứ không chỉ dò từ khóa. "
        "Ví dụ: 'lịch chán quá, tối đi đâu chơi thêm' là yêu cầu làm mới/tăng vui chơi buổi tối; "
        "'ngày nào cũng mua sắm, bỏ bớt' là yêu cầu giảm hoặc bỏ nhóm mua sắm; "
        "'đi du lịch phải có chỗ nổi tiếng, thêm Chợ Cồn ngày 2 buổi sáng' phải giữ điểm nổi tiếng, ngày 2 và buổi sáng; "
        "'đi nhẹ thôi, mẹ tôi lớn tuổi, vẫn muốn xem nhiều nơi' cần cân bằng quãng đường, thời lượng và số điểm; "
        "'ngày 1 có gì, thêm quán hải sản rồi đổi khách sạn gần biển' gồm cả câu hỏi và hai hành động. "
        "Lệnh command phải viết thành câu ngắn mà bộ lập lịch có thể thực hiện trực tiếp: giữ nguyên địa danh, số ngày, "
        "ngày cụ thể, buổi và phủ định. Ví dụ 'tối nào cũng có mall, không thích nữa' → 'bỏ mua sắm khỏi toàn bộ chuyến đi'; "
        "'ngày 2 bỏ cafe' → 'bỏ cafe khỏi ngày 2'; 'đưa chỗ đó sang sáng' chỉ được dùng tên điểm từ lịch sử gần nhất; "
        "'thêm 1 ngày nữa' phải là tăng thời lượng hiện tại thêm một ngày, còn 'đi 4 ngày' là đặt tổng cộng 4 ngày. "
        "Khi nói lịch quá dồn/quá ít điểm/tối kết thúc sớm, hãy điều chỉnh nhịp độ theo thời gian mở cửa và đường đi, "
        "giữ bữa ăn và thời gian nghỉ thực tế; không nhồi địa điểm hoặc tự thêm mua sắm nếu người dùng không muốn. "
        "Nếu câu lệnh không thể biểu diễn bằng một hành động mà hệ thống hỗ trợ, hoặc thiếu lựa chọn cần thiết, hãy hỏi một câu làm rõ; "
        "đừng nói đã cập nhật nếu chưa có command thực hiện được. Giữ câu trả lời ngắn, thân thiện và bằng ngôn ngữ người dùng."
    )
    body = {
        "model": OLLAMA_MODEL,
        "stream": False,
        # Qwen3.5 defaults to an internal thinking trace which can consume
        # the whole small response budget before emitting the JSON command.
        # Ollama exposes this as a top-level chat option for this model.
        "think": False,
        "format": "json",
        "options": {"temperature": 0.1, "num_predict": 320},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": "Ngữ cảnh: " + _trip_context(state)},
            *safe_history,
            {"role": "user", "content": "Tin nhắn hiện tại: " + message},
        ],
    }
    request = Request(
        OLLAMA_URL + "/api/chat",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=OLLAMA_TIMEOUT) as response:
            result = json.loads(response.read().decode("utf-8"))
        content = result.get("message", {}).get("content", "")
        parsed = json.loads(content)
        mode = parsed.get("mode")
        if mode == "commands" and isinstance(parsed.get("commands"), list):
            commands = [str(command).strip()[:400] for command in parsed["commands"] if str(command).strip()]
            if 1 < len(commands) <= 4:
                return {"mode": "commands", "commands": commands}
        if mode == "command" and isinstance(parsed.get("command"), str):
            command = parsed["command"].strip()[:500]
            if command:
                return {"mode": "command", "command": command}
        if mode in {"answer", "clarify"} and isinstance(parsed.get("reply"), str):
            reply = parsed["reply"].strip()[:1200]
            if reply:
                return {"mode": mode, "reply": reply}
    except (OSError, URLError, TimeoutError, ValueError, KeyError, TypeError):
        return None
    return None
