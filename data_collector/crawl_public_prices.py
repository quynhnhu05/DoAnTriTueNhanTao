"""Collect explicit VND prices from Schema.org offers and linked public menus.

Only crawls venue websites already listed in the database. Honors robots.txt,
waits between distinct websites, records URL/time, and never estimates a missing
value. A listed offer is not treated as a date-specific hotel quote.
"""
from __future__ import annotations

import argparse
from io import BytesIO
import json
import os
import re
import socket
import sys
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
from http.client import HTTPException
from ipaddress import ip_address
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

import psycopg2
from pypdf import PdfReader


DB = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "dbname": os.getenv("DB_NAME", "tourism_db"),
    "user": os.getenv("DB_USER", "tourism_user"),
    "password": os.getenv("DB_PASSWORD", "tourism_password"),
}
USER_AGENT = "DoAnTriTueNhanTao/1.0 (public price metadata collector)"
MIN_DELAY = 4
MAX_HTML_BYTES = 3_000_000
MAX_PDF_BYTES = 12_000_000
MAX_PDF_PAGES = 25
LOCATION_SLUGS = (
    "hanoi", "hue", "laichau", "dienbien", "sonla", "langson", "quangninh",
    "thanhhoa", "nghean", "hatinh", "caobang", "tuyenquang", "laocai",
    "thainguyen", "phutho", "bacninh", "hungyen", "haiphong", "ninhbinh",
    "quangtri", "danang", "quangngai", "gialai", "daklak", "khanhhoa",
    "lamdong", "dongnai", "hochiminh", "tayninh", "dongthap", "vinhlong",
    "cantho", "angiang", "camau", "binhduong", "hoian", "saigon", "nhatrang",
    "dalat", "sapa", "phuquoc", "vungtau", "halong", "catba", "muine",
)


class JsonLdParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_jsonld = False
        self.in_hidden = False
        self.parts = []
        self.blocks = []
        self.visible_text = []
        self.links = []
        self.anchor_href = None
        self.anchor_text = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "script":
            attrs = dict(attrs)
            if attrs.get("type", "").split(";")[0].strip().lower() == "application/ld+json":
                self.in_jsonld = True
                self.parts = []
            else:
                self.in_hidden = True
        elif tag.lower() in {"style", "noscript", "svg"}:
            self.in_hidden = True
        elif tag.lower() == "a":
            self.anchor_href = dict(attrs).get("href")
            self.anchor_text = []

    def handle_data(self, data):
        if self.in_jsonld:
            self.parts.append(data)
        elif not self.in_hidden:
            self.visible_text.append(data)
            if self.anchor_href:
                self.anchor_text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "script" and self.in_jsonld:
            self.blocks.append("".join(self.parts))
            self.in_jsonld = False
        elif tag.lower() in {"script", "style", "noscript", "svg"}:
            self.in_hidden = False
        elif tag.lower() == "a":
            if self.anchor_href:
                self.links.append((self.anchor_href, " ".join(self.anchor_text)))
            self.anchor_href = None
            self.anchor_text = []


def public_http_url(value):
    parsed = urlparse(value or "")
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
        return bool(addresses) and all(not ip_address(item[4][0]).is_private and
                                       not ip_address(item[4][0]).is_loopback and
                                       not ip_address(item[4][0]).is_link_local and
                                       not ip_address(item[4][0]).is_reserved for item in addresses)
    except (OSError, ValueError):
        return False


def normalize_http_url(value):
    """Percent-encode Unicode in public URLs before urllib builds a request."""
    try:
        parsed = urlparse(str(value or "").strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return ""
        path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
        query = quote(parsed.query, safe="/%?:@!$&'()*+,;=-._~")
        fragment = quote(parsed.fragment, safe="/%?:@!$&'()*+,;=-._~")
        return urlunparse(parsed._replace(path=path, query=query, fragment=fragment))
    except (TypeError, ValueError):
        return ""


def _fold_compact(value):
    value = str(value or "").replace("đ", "d").replace("Đ", "D")
    value = unicodedata.normalize("NFD", str(value or ""))
    value = "".join(char for char in value if unicodedata.category(char) != "Mn")
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _fold_words(value):
    value = str(value or "").replace("đ", "d").replace("Đ", "D")
    value = unicodedata.normalize("NFD", str(value or ""))
    value = "".join(char for char in value if unicodedata.category(char) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def page_location_matches_places(page_url, place_context):
    """Reject a branch-specific linked rate/menu page for other DB locations."""
    page = _fold_compact(urlparse(page_url or "").path)
    target = _fold_compact(place_context)
    return not any(location in page and location not in target for location in LOCATION_SLUGS)


def _editorial_dated_url(page_url):
    """Dated editorial articles are not current venue menus or rate cards."""
    path = (urlparse(page_url or "").path or "").lower()
    if not re.search(r"/(?:19|20)\d{2}/\d{1,2}/\d{1,2}/", path):
        return False
    return not any(token in path for token in ("menu", "thuc-don", "price", "pricing", "rates", "bang-gia"))


_NAME_STOPWORDS = {
    "restaurant", "restaurants", "eatery", "cafe", "coffee", "hotel", "hotels",
    "homestay", "resort", "kitchen", "food", "foods", "vietnam", "vietnamese",
    "da", "nang", "hoi", "an", "ha", "noi", "the", "and", "by", "of", "at",
    "inc", "llc", "company", "co", "limited", "menu", "menus", "official",
}


def matching_place_ids(page_url, page_text, places):
    """Map a menu to the matching branch/name, not every venue sharing a domain."""
    page_path = _fold_compact(urlparse(page_url or "").path)
    page_words = set(_fold_words((page_url or "") + " " + (page_text or "")).split())
    location_tokens = [slug for slug in LOCATION_SLUGS if slug in page_path]
    candidates = []
    for place_id, name, address, province in places:
        context = _fold_compact(" ".join(str(v or "") for v in (name, address, province)))
        if any(token not in context for token in location_tokens):
            continue
        name_tokens = {t for t in _fold_words(name).split()
                       if len(t) >= 3 and t not in _NAME_STOPWORDS}
        score = len(name_tokens & page_words)
        candidates.append((place_id, score))
    if not candidates:
        return []
    best = max(score for _, score in candidates)
    # A URL stored on a place can be stale or wrong (for example an unrelated
    # restaurant domain). Never attach its prices to every place on that site
    # when neither the page text nor URL mentions any candidate place name.
    if best <= 0:
        return []
    if sum(1 for _, score in candidates if score == best) == 1:
        return [place_id for place_id, score in candidates if score == best]
    return [place_id for place_id, _ in candidates]


def robots_allow(url):
    url = normalize_http_url(url)
    if not url:
        return False
    parsed = urlparse(url)
    robots_url = "%s://%s/robots.txt" % (parsed.scheme, parsed.netloc)
    if not public_http_url(robots_url):
        return False
    try:
        request = Request(robots_url, headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=12) as response:
            body = response.read(256_000).decode("utf-8", errors="replace")
        robot = RobotFileParser()
        robot.set_url(robots_url)
        robot.parse(body.splitlines())
        return robot.can_fetch(USER_AGENT, url)
    except HTTPError as exc:
        return exc.code in (404, 410)
    except (OSError, URLError, TimeoutError):
        return False


def page_jsonld(url):
    url = normalize_http_url(url)
    if not public_http_url(url) or not robots_allow(url):
        return None
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/pdf;q=0.8"})
    with urlopen(request, timeout=20) as response:
        final_url = normalize_http_url(response.geturl())
        if not public_http_url(final_url):
            return None
        if urlparse(final_url).netloc.lower() != urlparse(url).netloc.lower() and not robots_allow(final_url):
            return None
        mime = response.headers.get_content_type()
        is_pdf = mime in {"application/pdf", "application/x-pdf"} or urlparse(final_url).path.lower().endswith(".pdf")
        if is_pdf:
            pdf = response.read(MAX_PDF_BYTES + 1)
            if len(pdf) > MAX_PDF_BYTES:
                return None
        elif mime in {"text/html", "application/xhtml+xml"}:
            html = response.read(MAX_HTML_BYTES + 1)
            if len(html) > MAX_HTML_BYTES:
                return None
        else:
            return None
    if is_pdf:
        try:
            reader = PdfReader(BytesIO(pdf), strict=False)
            if reader.is_encrypted:
                return None
            pages = []
            for page in reader.pages[:MAX_PDF_PAGES]:
                pages.append(page.extract_text() or "")
            visible_text = "\n".join(pages)
        except Exception:
            return None
        # Image-only/scanned menus need OCR; do not invent prices from them.
        if len(visible_text.strip()) < 20:
            return None
        return final_url, [], visible_text, []
    parser = JsonLdParser()
    parser.feed(html.decode("utf-8", errors="replace"))
    docs = []
    for block in parser.blocks:
        try:
            docs.append(json.loads(block))
        except (ValueError, TypeError):
            continue
    return final_url, docs, " ".join(parser.visible_text), parser.links


def walk_json(value):
    if isinstance(value, dict):
        yield value
        graph = value.get("@graph")
        if graph is not None:
            yield from walk_json(graph)
        for key, item in value.items():
            if key != "@graph":
                yield from walk_json(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk_json(item)


def _price_context_matches(category, context, page_url=""):
    text = context.lower()
    path = (urlparse(page_url).path or "").lower()
    if category == "Lưu trú":
        non_stay = r"green.?sm|di chuyển|đi lại|taxi|transport|voucher|thẻ quà|gift card|meeting room|conference|ballroom|wedding|banquet|dining|restaurant|menu|spa|massage|treatment|pool|fitness|gym"
        if re.search(r"dining|restaurant|menu|spa|massage|treatment|pool|fitness|gym|transport|meeting|banquet", path):
            return False
        return bool(re.search(r"ph[oò]ng|room|per night|/night|mỗi đêm|mỗi ngày|overnight|booking|đặt phòng", text)) and not bool(re.search(non_stay, text))
    if category in {"Ăn uống", "Cafe"}:
        if re.search(r"/san-pham|product", path) or re.search(r"thêm vào giỏ|add to cart|nguyên hạt|\b\d+\s?g\b|\b\d+\s?kg\b", text):
            return False
        return bool(re.search(r"menu|thực đơn|món ăn|đồ uống|cà phê|coffee|espresso|latte|bún|phở|cơm|bánh|tea|trà|restaurant|cafe|café|food", text))
    if category in {"Tham quan", "Vui chơi", "Giải trí"}:
        if re.search(r"hotel|hostel|guest.?house|room|ph[oò]ng|accommodation", path):
            return False
        return bool(re.search(r"vé|ticket|admission|entrance|tham quan|người lớn|trẻ em|giá vào cửa|công viên|khu vui chơi", text))
    if category == "Di chuyển":
        return bool(re.search(r"vé|ticket|fare|cước|giá/km|taxi|bus|xe buýt|thuê xe", text))
    if category == "Mua sắm":
        return bool(re.search(r"sản phẩm|product|store|cửa hàng|price", text))
    return False


def _has_expired_public_offer(text, today=None):
    """Detect an offer whose explicit end date has passed; ignore article dates."""
    today = today or datetime.now(timezone.utc).date()
    markers = re.compile(
        r"(?:đến hết ngày|đến ngày|hết hạn(?: vào ngày)?|valid until|valid through|ends? on)"
        r"[^0-9]{0,24}(\d{1,2})[./-](\d{1,2})[./-](20\d{2})",
        re.IGNORECASE,
    )
    for match in markers.finditer(text or ""):
        try:
            end_date = datetime(int(match.group(3)), int(match.group(2)), int(match.group(1))).date()
        except ValueError:
            continue
        if end_date < today:
            return True
    return False


def _has_stale_dated_rate_url(page_url, today=None):
    """Reject rate/menu pages explicitly dated to an earlier year."""
    today = today or datetime.now(timezone.utc).date()
    path = urlparse(page_url or "").path.lower()
    if not re.search(r"bang[-_]?gia|price|pricing|rate|menu", path):
        return False
    years = [int(year) for year in re.findall(r"20\d{2}", path)]
    return any(year < today.year for year in years)


def explicit_vnd_prices(docs, visible_text="", category="", page_url=""):
    found = []
    seen = set()
    path = (urlparse(page_url).path or "").lower()
    minimum_price = 5_000 if category in {"Ăn uống", "Cafe"} else 1_000
    for doc in docs:
        for obj in walk_json(doc):
            if "Offer" not in str(obj.get("@type", "")):
                continue
            currency = str(obj.get("priceCurrency") or "").upper()
            raw = obj.get("price")
            if currency != "VND" or raw is None:
                continue
            try:
                normalized = str(raw).replace(",", "").replace(" ", "")
                # JSON numbers like 40.000 are often a thousands separator in VN,
                # while small fractions are not useful VND amounts here.
                price = float(normalized)
            except ValueError:
                continue
            offer_context = json.dumps(obj, ensure_ascii=False)
            if (minimum_price <= price <= 100_000_000 and
                    _price_context_matches(category, offer_context, page_url) and price not in seen):
                found.append(price)
                seen.add(price)
    # Bắt các giá hiển thị bằng tiền Việt trên trang chính thức khi website
    # không khai JSON-LD. Không suy đoán từ số trần như số điện thoại.
    amount_pattern = re.compile(
        r"(?<![\w/])(?:(?:VND|VNĐ)[ \t]*)?"
        r"((?:\d{1,3}(?:[., \t]\d{3})+)|\d{4,10})[ \t]*"
        r"(?:₫|đồng|vnđ|vnd)(?!\w)", re.IGNORECASE
    )
    source_text = visible_text or ""
    for match in amount_pattern.finditer(source_text):
        raw = re.sub(r"[^0-9]", "", match.group(1))
        try:
            price = float(raw)
        except ValueError:
            continue
        if price == 1_000 and re.search(r"(?:×|x)\s*$", source_text[max(0, match.start(1) - 8):match.start(1)], re.IGNORECASE):
            # Do not treat the multiplier declaration itself as a menu item.
            continue
        context = source_text[max(0, match.start() - 140):min(len(source_text), match.end() + 140)]
        if (minimum_price <= price <= 100_000_000 and
                _price_context_matches(category, context, page_url) and price not in seen):
            found.append(price)
            seen.add(price)

    # Some official menus show item prices in thousands (for example,
    # "Eggs on Toast – 80" followed by "All prices ×1,000 VND"). Interpret
    # those values only when the page explicitly declares the multiplier and
    # the category is food or cafe; never infer a multiplier from bare numbers.
    multiplier = re.search(
        r"\ball\s+prices\b.{0,80}?(?:×|x)\s*1\s*[,.]?\s*000\s*(?:vnd|vnđ|đồng)\b",
        source_text,
        re.IGNORECASE | re.DOTALL,
    )
    if multiplier and category in {"Ăn uống", "Cafe"}:
        item_pattern = re.compile(r"^\s*(.{2,90}?)\s+[–—-]\s*(\d{1,3})\b", re.IGNORECASE)
        for line in source_text.splitlines():
            match = item_pattern.search(line)
            if not match:
                continue
            label = match.group(1).strip()
            if not re.search(r"[A-Za-zÀ-ỹ]{2,}", label) or re.search(r"\d{4}", label):
                continue
            price = float(match.group(2)) * 1_000
            # Descriptions may mention package weights (for example 200g).
            # Validate the item title and the explicit scale note separately.
            context = label + " menu item; all prices are multiplied by 1,000 VND"
            if (minimum_price <= price <= 100_000_000 and
                    _price_context_matches(category, context, page_url) and price not in seen):
                found.append(price)
                seen.add(price)

    # Vietnamese menus commonly abbreviate thousands of VND as "k" (e.g.
    # 690k). Accept this only on an explicit menu/food page and in matching
    # food/cafe categories; a bare suffix elsewhere is not a price signal.
    if category in {"Ăn uống", "Cafe"}:
        k_pattern = re.compile(r"(?<![\w/])(\d{1,3}(?:[.,]\d{3})*|\d{1,4})\s*k\b", re.IGNORECASE)
        is_menu_path = bool(re.search(r"menu|thuc.?don|food|dining", path))
        if is_menu_path or re.search(r"menu|thực đơn|món ăn|đồ uống|coffee|restaurant", source_text, re.IGNORECASE):
            for match in k_pattern.finditer(source_text):
                raw = match.group(1).replace(".", "").replace(",", "")
                try:
                    price = float(raw) * 1_000
                except ValueError:
                    continue
                context = source_text[max(0, match.start() - 90):min(len(source_text), match.end() + 90)]
                if (minimum_price <= price <= 100_000_000 and
                        (_price_context_matches(category, context, page_url) or is_menu_path) and
                        price not in seen):
                    found.append(price)
                    seen.add(price)
    return found


def price_page_links(home_url, links, limit=3):
    """Pick a few same-site pages whose links suggest prices, menus, or room rates."""
    host = (urlparse(home_url).hostname or "").lower()
    terms = re.compile(
        r"menu|thuc.?don|bang.?gia|price|pricing|rate|room|phong|dich.?vu|"
        r"service|restaurant|dining|food|gia.?phong|san.?pham",
        re.IGNORECASE,
    )
    ranked = {}
    for href, label in links:
        candidate = urljoin(home_url, href or "")
        parsed = urlparse(candidate)
        if parsed.scheme not in {"http", "https"}:
            continue
        clue = (parsed.path + " " + (label or "")).lower()
        if not terms.search(clue):
            continue
        linked_pdf = parsed.path.lower().endswith(".pdf")
        if (parsed.hostname or "").lower() != host and not linked_pdf:
            continue
        priority = 0 if re.search(r"menu|thuc.?don|bang.?gia|pricing|price", clue) else 1
        ranked[candidate] = min(priority, ranked.get(candidate, priority))
    return [url for url, _ in sorted(ranked.items(), key=lambda item: (item[1], item[0]))[:max(1, limit)]]


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=100, help="Maximum distinct websites this run (default: 100)")
    parser.add_argument("--delay", type=int, default=MIN_DELAY, help="Minimum seconds between requests to one host")
    parser.add_argument("--workers", type=int, default=8, help="Concurrent independent hosts (default: 8)")
    parser.add_argument("--retry-errors", action="store_true",
                        help="Retry websites whose previous crawl failed, even if checked less than 30 days ago")
    parser.add_argument("--refresh-all", action="store_true",
                        help="Recrawl every eligible website, regardless of its previous crawl time or status")
    parser.add_argument("--category", action="append", choices=("Ăn uống", "Lưu trú", "Cafe", "Vui chơi", "Giải trí", "Tham quan", "Mua sắm", "Di chuyển"), help="Limit this run to one or more database categories")
    args = parser.parse_args()
    delay = max(args.delay, MIN_DELAY)
    conn = psycopg2.connect(**DB)
    try:
        with conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS public_price_crawl_sites (
                site_key TEXT PRIMARY KEY,
                last_checked_at TIMESTAMPTZ NOT NULL,
                status VARCHAR(24) NOT NULL,
                note TEXT
            )""")
            cur.execute("""WITH eligible_places AS (
                SELECT pl.*,
                    COALESCE(NULLIF(TRIM(pl.website), ''), NULLIF(TRIM(directory.website), '')) AS crawl_website
                FROM places pl
                LEFT JOIN LATERAL (
                    SELECT td.website FROM tourism_directory_records td
                    WHERE td.place_id = pl.id AND NULLIF(TRIM(td.website), '') IS NOT NULL
                    ORDER BY td.last_seen_at DESC, td.id DESC LIMIT 1
                ) directory ON TRUE
            )
                SELECT LOWER(CASE WHEN pl.crawl_website ~* '^https?://'
                    THEN TRIM(pl.crawl_website) ELSE 'https://' || TRIM(pl.crawl_website) END) AS site_key,
                    MIN(CASE WHEN pl.crawl_website ~* '^https?://'
                    THEN TRIM(pl.crawl_website) ELSE 'https://' || TRIM(pl.crawl_website) END) AS website,
                    ARRAY_AGG(DISTINCT pl.category) AS categories, s.status,
                    STRING_AGG(DISTINCT CONCAT_WS(' ', pl.name, pl.address, pl.province), ' ') AS place_context,
                    JSONB_AGG(DISTINCT JSONB_BUILD_OBJECT('id', pl.id, 'name', pl.name,
                        'address', pl.address, 'province', pl.province, 'category', pl.category)) AS site_places
                FROM eligible_places pl
                LEFT JOIN public_price_crawl_sites s ON s.site_key = LOWER(CASE WHEN pl.crawl_website ~* '^https?://'
                    THEN TRIM(pl.crawl_website) ELSE 'https://' || TRIM(pl.crawl_website) END)
                WHERE pl.crawl_website IS NOT NULL AND TRIM(pl.crawl_website) <> ''
                  AND (pl.crawl_website ~* '^https?://[^/[:space:]]+[.][^/[:space:]]+'
                    OR pl.crawl_website ~* '^[A-Za-z0-9.-]+[.][A-Za-z]{2,}(/.*)?$')
                  AND pl.crawl_website !~* '(^|//)(www[.])?facebook[.]com(/|$)'
                  AND COALESCE(pl.source,'') <> 'Demo seed'
                  AND pl.category IN ('Ăn uống','Lưu trú','Cafe','Vui chơi','Giải trí','Tham quan','Mua sắm','Di chuyển')
                  AND (CARDINALITY(%s::text[]) = 0 OR pl.category = ANY(%s::text[]))
                GROUP BY LOWER(CASE WHEN pl.crawl_website ~* '^https?://'
                    THEN TRIM(pl.crawl_website) ELSE 'https://' || TRIM(pl.crawl_website) END), s.last_checked_at, s.status
                HAVING %s OR s.last_checked_at IS NULL OR s.last_checked_at < NOW() - INTERVAL '30 days'
                    OR (%s AND s.status = 'fetch_error')
                ORDER BY s.last_checked_at NULLS FIRST, site_key LIMIT %s""",
                (args.category or [], args.category or [], args.refresh_all,
                 args.retry_errors, max(1, min(args.limit, 5000))))
            places = cur.fetchall()
        conn.commit()
    finally:
        conn.close()

    print("Số website đủ điều kiện trong lượt này: %d" % len(places), flush=True)
    by_host = {}
    for site_key, website, categories, _previous_status, place_context, site_places in places:
        host = (urlparse(website).netloc or "").lower()
        by_host.setdefault(host, []).append((site_key, website, categories, place_context, site_places))
    total = len(places)
    progress = {"done": 0}
    progress_lock = threading.Lock()

    def crawl_host(host_and_sites):
        _, host_sites = host_and_sites
        db = psycopg2.connect(**DB)
        try:
            for site_key, website, categories, place_context, site_places in host_sites:
                status, note, category_prices, category_targets, final_url = "no_public_price", None, {}, {}, None
                places_by_category = {
                    category: [(int(item["id"]), item.get("name"), item.get("address"), item.get("province"))
                               for item in site_places if item.get("category") == category]
                    for category in categories
                }
                try:
                    try:
                        result = page_jsonld(website)
                    except HTTPError as exc:
                        parsed_website = urlparse(website)
                        has_deep_path = (parsed_website.path or "").strip("/") or parsed_website.query
                        if exc.code not in (404, 410) or not has_deep_path:
                            raise
                        # Venue records often contain an old deep link even though
                        # the same official domain still serves a current menu/homepage.
                        root_url = urlunparse(parsed_website._replace(
                            path="/", params="", query="", fragment=""
                        ))
                        time.sleep(delay)
                        try:
                            result = page_jsonld(root_url)
                        except (HTTPError, URLError, HTTPException, TimeoutError, OSError, ValueError):
                            raise exc
                        if result:
                            note = "URL trang con trả 404/410; đã thử trang chủ cùng domain."
                        else:
                            raise exc
                    if result:
                        final_url, docs, visible_text, links = result
                        category_prices = {category: [] for category in categories}
                        category_targets = {}
                        if (not _has_expired_public_offer(visible_text)
                                and not _has_stale_dated_rate_url(final_url)
                                and not _editorial_dated_url(final_url)):
                            for category in categories:
                                category_prices[category] = explicit_vnd_prices(docs, visible_text, category, final_url)
                        if any(category_prices.values()):
                            category_targets = {
                                category: matching_place_ids(final_url, visible_text, places_by_category[category])
                                for category, prices in category_prices.items() if prices
                            }
                            for category, prices in list(category_prices.items()):
                                if prices and not category_targets.get(category):
                                    category_prices[category] = []
                                    note = "Trang có giá nhưng không xác nhận được tên địa điểm trong DB"
                        if not any(category_prices.values()):
                            for price_url in price_page_links(final_url, links, limit=3):
                                time.sleep(delay)
                                price_page = page_jsonld(price_url)
                                if not price_page:
                                    continue
                                page_url, docs, page_text, _ = price_page
                                if (_has_expired_public_offer(page_text) or _has_stale_dated_rate_url(page_url)
                                        or _editorial_dated_url(page_url)):
                                    final_url = page_url
                                    continue
                                page_prices = {
                                    category: explicit_vnd_prices(docs, page_text, category, page_url)
                                    for category in categories
                                }
                                page_targets = {
                                    category: matching_place_ids(page_url, page_text, places_by_category[category])
                                    for category, prices in page_prices.items() if prices
                                }
                                accepted_prices = {
                                    category: prices for category, prices in page_prices.items()
                                    if prices and page_targets.get(category)
                                }
                                if any(accepted_prices.values()) and page_location_matches_places(page_url, place_context):
                                    final_url = page_url
                                    category_prices = accepted_prices
                                    category_targets = {
                                        category: page_targets[category] for category in accepted_prices
                                    }
                                    break
                                if any(page_prices.values()):
                                    note = "Trang giá/menu không xác nhận tên địa điểm hoặc tỉnh/thành trong DB"
                        status = "published_vnd_price" if any(category_prices.values()) else "no_public_price"
                    else:
                        status, note = "skipped", "URL không công khai hoặc robots.txt không cho phép"
                except (HTTPError, URLError, HTTPException, TimeoutError, OSError, ValueError) as exc:
                    status, note = "fetch_error", str(exc)[:500]

                checked = datetime.now(timezone.utc)
                with db.cursor() as cur:
                    if final_url and status not in {"fetch_error", "skipped"}:
                        for category, prices in category_prices.items():
                            if prices:
                                target_ids = category_targets.get(category, [])
                                if not target_ids:
                                    site_category_ids = [place[0] for place in places_by_category[category]]
                                    if not site_category_ids:
                                        continue
                                    cur.execute("""UPDATE places SET price_min=NULL, price_max=NULL, currency=NULL,
                                        price_source_url=NULL, price_checked_at=NULL, price_kind=NULL,
                                        updated_at=CURRENT_TIMESTAMP WHERE id = ANY(%s::bigint[])
                                        AND category=%s AND price_kind='published_offer'""", (site_category_ids, category))
                                    continue
                                cur.execute("""UPDATE places SET price_min=%s, price_max=%s, currency='VND',
                                    price_source_url=%s, price_checked_at=%s, price_kind='published_offer',
                                    updated_at=CURRENT_TIMESTAMP WHERE id = ANY(%s::bigint[])""",
                                    (min(prices), max(prices), final_url, checked, target_ids))
                                sibling_ids = [item[0] for item in places_by_category[category] if item[0] not in target_ids]
                                if sibling_ids:
                                    cur.execute("""UPDATE places SET price_min=NULL, price_max=NULL, currency=NULL,
                                    price_source_url=NULL, price_checked_at=NULL, price_kind=NULL,
                                    updated_at=CURRENT_TIMESTAMP WHERE id = ANY(%s::bigint[])
                                    AND price_kind='published_offer'""", (sibling_ids,))
                            else:
                                site_category_ids = [place[0] for place in places_by_category[category]]
                                if not site_category_ids:
                                    continue
                                cur.execute("""UPDATE places SET price_min=NULL, price_max=NULL, currency=NULL,
                                    price_source_url=NULL, price_checked_at=NULL, price_kind=NULL,
                                    updated_at=CURRENT_TIMESTAMP WHERE id = ANY(%s::bigint[]) AND category=%s
                                    AND price_kind='published_offer'""", (site_category_ids, category))
                    cur.execute("""INSERT INTO public_price_crawl_sites(site_key,last_checked_at,status,note)
                        VALUES(%s,%s,%s,%s) ON CONFLICT(site_key) DO UPDATE SET
                        last_checked_at=EXCLUDED.last_checked_at,status=EXCLUDED.status,note=EXCLUDED.note""",
                        (site_key, checked, status, note))
                db.commit()
                with progress_lock:
                    progress["done"] += 1
                    done = progress["done"]
                all_prices = [price for values in category_prices.values() for price in values]
                print("[%d/%d] %s: %s%s" % (done, total, site_key, status,
                      (" (%s–%s VND)" % (int(min(all_prices)), int(max(all_prices)))) if all_prices else ""), flush=True)
                if len(host_sites) > 1:
                    time.sleep(delay)
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 12))) as executor:
        list(executor.map(crawl_host, by_host.items()))


if __name__ == "__main__":
    main()
