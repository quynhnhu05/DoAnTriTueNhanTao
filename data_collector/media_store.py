"""Fetch validated public venue images into the app's shared local media store."""
from __future__ import annotations

import hashlib
import os
import re
import socket
from ipaddress import ip_address
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse, urlunparse
from urllib.request import Request, urlopen


MAX_IMAGE_BYTES = 15 * 1024 * 1024
MEDIA_ROOT = Path(os.getenv("MEDIA_DIR") or (Path(__file__).resolve().parents[1] / "data" / "media"))
EXTENSIONS = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif",
}


def _public_remote_url(url: str, allow_http: bool = False) -> bool:
    parsed = urlparse(url or "")
    allowed_schemes = {"https", "http"} if allow_http else {"https"}
    if parsed.scheme not in allowed_schemes or not parsed.hostname:
        return False
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443)
        return bool(addresses) and all(
            not (ip_address(item[4][0]).is_private or ip_address(item[4][0]).is_loopback
                 or ip_address(item[4][0]).is_link_local or ip_address(item[4][0]).is_reserved)
            for item in addresses
        )
    except (OSError, ValueError):
        return False


def materialize_image(
    url: str,
    venue_name: str,
    *,
    collection: str = "places",
    allow_http: bool = False,
) -> str | None:
    """Download a public photo and return its app-relative static path."""
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme == "https" and parsed.hostname:
        url = urlunparse(parsed._replace(
            path=quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~"),
            query=quote(parsed.query, safe="/%?:@!$&'()*+,;=-._~"),
        ))
    if not _public_remote_url(url, allow_http=allow_http):
        return None
    parsed = urlparse(url)
    collection = re.sub(r"[^a-z0-9_-]+", "-", collection.casefold()).strip("-_") or "places"
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    slug = re.sub(r"[^a-z0-9]+", "-", (venue_name or "place").casefold()).strip("-")[:40] or "place"
    static_dir = "places" if collection == "places" else f"{collection}/places"
    directory = MEDIA_ROOT / static_dir
    directory.mkdir(parents=True, exist_ok=True)
    for content_type, extension in EXTENSIONS.items():
        existing = directory / f"{slug}-{digest}{extension}"
        if existing.is_file():
            return f"/media/{static_dir}/{existing.name}"

    request = Request(url, headers={
        "User-Agent": "AI-Smart-Travel/1.0 (academic travel itinerary; image attribution retained)",
        "Accept": "image/avif,image/webp,image/png,image/jpeg,image/gif,*/*;q=0.5",
    })
    try:
        with urlopen(request, timeout=25) as response:
            final_url = response.geturl()
            if not _public_remote_url(final_url, allow_http=allow_http):
                return None
            content_type = response.headers.get_content_type().casefold()
            extension = EXTENSIONS.get(content_type)
            if not extension:
                return None
            body = response.read(MAX_IMAGE_BYTES + 1)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, socket.timeout):
        return None
    if not body or len(body) > MAX_IMAGE_BYTES:
        return None
    if content_type == "image/jpeg" and not body.startswith(b"\xff\xd8\xff"):
        return None
    if content_type == "image/png" and not body.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    if content_type == "image/webp" and not (body.startswith(b"RIFF") and body[8:12] == b"WEBP"):
        return None
    if content_type == "image/gif" and not body.startswith((b"GIF87a", b"GIF89a")):
        return None

    filename = f"{slug}-{digest}{extension}"
    target = directory / filename
    temporary = directory / f".{filename}.tmp"
    temporary.write_bytes(body)
    temporary.replace(target)
    return f"/media/{static_dir}/{filename}"
