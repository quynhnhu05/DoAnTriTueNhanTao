"""Stable, explicitly internal demo prices for venues without sourced rates.

These values are generated from a place identity so they do not change between
requests. They are kept separate from the database's published-price fields.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata


def _category(place) -> str:
    value = " ".join(str(getattr(place, key, "") or "") for key in ("category", "subcategory", "name"))
    value = unicodedata.normalize("NFD", value.casefold())
    value = "".join(char for char in value if unicodedata.category(char) != "Mn").replace("đ", "d")
    return re.sub(r"\s+", " ", value)


def _seed(place) -> int:
    identity = getattr(place, "id", None) or getattr(place, "name", "venue")
    return int.from_bytes(hashlib.blake2b(str(identity).encode("utf-8"), digest_size=8).digest(), "big")


def demo_price(place) -> dict:
    """Return a repeatable demo range and unit without changing sourced data."""
    category = _category(place)
    seed = _seed(place)
    if any(word in category for word in ("khach san", "luu tru", "hotel", "hostel", "resort", "homestay", "motel")):
        low, step, unit, count = 280_000, 85_000, "per_night", 27
    elif any(word in category for word in ("cafe", "ca phe", "coffee", "tra sua")):
        low, step, unit, count = 25_000, 5_000, "per_person", 14
    elif any(word in category for word in ("an uong", "nha hang", "restaurant", "food", "am thuc")):
        low, step, unit, count = 45_000, 10_000, "per_person", 20
    elif any(word in category for word in ("mua sam", "shopping", "sieu thi", "supermarket")):
        low, step, unit, count = 80_000, 20_000, "per_visit", 21
    elif any(word in category for word in ("vui choi", "giai tri", "amusement", "entertainment")):
        low, step, unit, count = 60_000, 20_000, "per_person", 25
    elif any(word in category for word in ("tham quan", "thien nhien", "sightseeing", "nature")):
        low, step, unit, count = 20_000, 10_000, "per_person", 17
    else:
        low, step, unit, count = 40_000, 10_000, "per_person", 18

    offset = seed % count
    minimum = low + offset * step
    spread = (1 + ((seed >> 13) % 5)) * step
    maximum = minimum + spread
    return {"min": minimum, "max": maximum, "unit": unit}


def demo_per_person_cost(place) -> int:
    """A stable itinerary budget amount, separate from a venue's public price."""
    price = demo_price(place)
    amount = (price["min"] + price["max"]) / 2
    return int(round(amount / 5_000) * 5_000)
