"""Stable class-project visuals and opening hours for sparse POI records."""

from __future__ import annotations

import base64
import hashlib
import re
import unicodedata
from pathlib import Path


def _fold(value: str) -> str:
    value = unicodedata.normalize("NFD", str(value or "").casefold())
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", value.replace("đ", "d")).strip()


def _seed(place) -> int:
    identity = getattr(place, "id", None) or getattr(place, "name", "venue")
    return int.from_bytes(hashlib.blake2b(str(identity).encode("utf-8"), digest_size=8).digest(), "big")


def demo_opening_hours(place) -> str:
    """Return stable, category-plausible demo hours without changing DB data."""
    category = _fold(" ".join(str(getattr(place, key, "") or "") for key in ("category", "subcategory", "name")))
    seed = _seed(place)
    if any(word in category for word in ("khach san", "luu tru", "hotel", "hostel", "resort", "homestay", "motel", "nha nghi")):
        return "Mở cửa 24/7 · nhận phòng từ 14:00 · trả phòng trước 12:00"
    if any(word in category for word in ("cafe", "ca phe", "coffee", "tra sua")):
        starts = ("06:30", "07:00", "07:30", "08:00")
        ends = ("21:00", "21:30", "22:00", "22:30")
    elif any(word in category for word in ("an uong", "nha hang", "restaurant", "food", "am thuc")):
        starts = ("07:00", "08:00", "09:00", "10:00")
        ends = ("21:00", "21:30", "22:00", "22:30")
    elif any(word in category for word in ("mua sam", "shopping", "sieu thi", "supermarket")):
        starts, ends = ("08:00", "08:30", "09:00", "09:30"), ("21:00", "21:30", "22:00", "22:30")
    elif any(word in category for word in ("vui choi", "giai tri", "tham quan", "thien nhien", "sightseeing", "nature")):
        starts, ends = ("07:00", "07:30", "08:00", "08:30"), ("17:00", "17:30", "18:00", "18:30")
    else:
        starts, ends = ("07:00", "08:00", "09:00", "10:00"), ("18:00", "19:00", "20:00", "21:00")
    return f"Hằng ngày {starts[seed % len(starts)]}–{ends[(seed >> 8) % len(ends)]}"


def demo_image_data_uri(place) -> str:
    """Create a distinct, lightweight illustration for records with no image."""
    seed = _seed(place)
    category = _fold(" ".join(str(getattr(place, key, "") or "") for key in ("category", "subcategory", "name")))
    if any(word in category for word in ("khach san", "luu tru", "hotel", "hostel", "resort", "homestay", "motel", "nha nghi")):
        symbol = '<path d="M55 150V69l49-33 49 33v81h-28v-43H83v43z"/><path d="M93 150v-27h23v27"/>'
    elif any(word in category for word in ("cafe", "ca phe", "coffee", "tra sua")):
        symbol = '<path d="M68 64h69v48a34 34 0 0 1-69 0z"/><path d="M137 76h17a17 17 0 0 1 0 34h-17M62 151h88M83 46q-10-13 0-25m24 25q-10-13 0-25m24 25q-10-13 0-25"/>'
    elif any(word in category for word in ("an uong", "nha hang", "restaurant", "food", "am thuc")):
        symbol = '<path d="M60 94h105a52 52 0 0 1-105 0zM55 94h115M80 66l12 14m19-19 7 17m25-15-9 15"/><path d="M83 154h60"/>'
    elif any(word in category for word in ("mua sam", "shopping", "sieu thi", "supermarket")):
        symbol = '<path d="M58 77h111l-11 78H69zM49 77h129l-13-34H63zM85 43v112m57-112v112"/>'
    else:
        symbol = '<path d="M51 150h111M62 150V88l49-41 49 41v62M80 150v-39h62v39M47 88h126M74 88V70m25 18V62m24 26V70m25 18V62"/>'
    hue_a, hue_b = seed % 360, (seed >> 12) % 360
    windows = ''.join(f'<rect x="{78 + 22*x}" y="{84 + 20*y}" width="9" height="10" rx="2"/>' for x in range(3) for y in range(2))
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 220 180"><defs><linearGradient id="g" x2="1" y2="1"><stop stop-color="hsl({hue_a},58%,82%)"/><stop offset="1" stop-color="hsl({hue_b},48%,68%)"/></linearGradient></defs><rect width="220" height="180" rx="24" fill="url(#g)"/><circle cx="177" cy="38" r="19" fill="#fff2bf" opacity=".9"/><path d="M0 143Q58 117 110 145T220 135V180H0z" fill="#fff" opacity=".22"/><g fill="#fff" opacity=".8">{windows}</g><g fill="none" stroke="#fff" stroke-width="7" stroke-linecap="round" stroke-linejoin="round">{symbol}</g><path d="M0 166h220" stroke="#fff" stroke-opacity=".5" stroke-width="2"/></svg>'''
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")


_PHOTO_DIR = Path(__file__).resolve().parents[1] / "assets" / "generated_photos"
_PHOTO_GROUPS = {
    "stay": ("stay_photo.jpg", "stay_pool.jpg"),
    "food": ("food_photo.jpg", "food_pool.jpg"),
    "cafe": ("cafe_photo.jpg", "cafe_pool.jpg"),
    "shopping": ("shopping_pool.jpg",),
    "nature": ("nature_photo.jpg", "nature_pool.jpg"),
    "attraction": ("oldtown_photo.jpg", "oldtown_pool.jpg"),
}


def demo_photo_url(place, exclude: set[str] | None = None) -> str | None:
    """Choose a locally bundled photo-style fallback suited to a venue type."""
    category = _fold(" ".join(str(getattr(place, key, "") or "") for key in ("category", "subcategory", "name")))
    if any(word in category for word in ("khach san", "luu tru", "hotel", "hostel", "resort", "homestay", "motel", "nha nghi")):
        group = "stay"
    elif any(word in category for word in ("cafe", "ca phe", "coffee", "tra sua")):
        group = "cafe"
    elif any(word in category for word in ("an uong", "nha hang", "restaurant", "food", "am thuc")):
        group = "food"
    elif any(word in category for word in ("mua sam", "shopping", "sieu thi", "supermarket")):
        group = "shopping"
    elif any(word in category for word in ("thien nhien", "nature", "bien", "beach", "vui choi", "giai tri", "entertainment")):
        group = "nature"
    else:
        group = "attraction"

    names = _PHOTO_GROUPS[group]
    seed = _seed(place)
    ordered = names[seed % len(names):] + names[:seed % len(names)]
    for name in ordered:
        path = _PHOTO_DIR / name
        url = f"/media/places/generated_photos/{name}"
        if path.is_file() and url not in (exclude or set()):
            return url
    return None


def displayable_image_url(place) -> str:
    """Return only a photo saved for this place, never a category fallback."""
    url = str(getattr(place, "image_url", "") or "").strip()
    source = str(getattr(place, "image_source_url", "") or "").strip()
    if re.match(r"^/media/places/[a-z0-9/_-]+\.(?:jpe?g|png|webp|gif)$", url, re.I):
        return url
    if url.startswith("https://") and source.startswith("https://"):
        return url
    return ""
