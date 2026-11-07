"""Collect public venue details from registered first-party HTML websites.

The crawler coordinates four HTML tools by job: Requests/BeautifulSoup reads
the home page, Scrapy discovers a small number of same-site price/menu pages,
HTTPX fetches those pages concurrently, and Playwright renders a thin JS shell
only when an ordinary successful HTML response contains almost no content.
It stores only venue-matched, explicit VND prices and first-party page images.

It is intentionally not a search-engine/social-network scraper. It starts only
from websites already associated with a venue in the app database, stays on
that hostname, and obeys robots.txt. A 403/429 response is not retried using a
different engine.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
import json
import logging
import os
import re
import sys
import threading
import time
from urllib.parse import urljoin, urlparse, urldefrag, urlunparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.robotparser import RobotFileParser

import psycopg2
import requests
from bs4 import BeautifulSoup
import httpx

from crawl_public_prices import (
    _fold_words,
    _NAME_STOPWORDS,
    explicit_vnd_prices,
    page_location_matches_places,
    public_http_url,
)
from crawl_real_tourism_data import listing_matches_page
from media_store import materialize_image


DB = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "dbname": os.getenv("DB_NAME", "tourism_db"),
    "user": os.getenv("DB_USER", "tourism_user"),
    "password": os.getenv("DB_PASSWORD", "tourism_password"),
}
USER_AGENT = "AI-Smart-Travel-Research/1.0 (public venue website HTML collector)"
PRICE_HINT = re.compile(r"menu|thuc.?don|bang.?gia|gia.?phong|price|pricing|rate|room|food|dining|restaurant|hotel", re.I)
BAD_EXT = re.compile(r"\.(?:pdf|jpe?g|png|gif|webp|svg|zip|mp4|mp3|css|js)(?:$|\?)", re.I)
SUPERMARKET_NAME = re.compile(
    r"\b(?:winmart|win\s*mart|co\s*\.\s*op\s*mart|coop\s*mart|lotte\s*mart|aeon\s*mall|aeon\s*citimart|go!|big\s*c|bigc|mm\s*mega\s*market|emart|tops\s*market|satra\s*mart)\b",
    re.I,
)
MAX_HTML_BYTES = 2_000_000
_HOST_LOCKS: dict[str, threading.Lock] = defaultdict(threading.Lock)
_HOST_LAST: dict[str, float] = {}


def forbidden_host(url: str) -> bool:
    host = host_key(url)
    return host == "tripadvisor.com" or host.endswith(".tripadvisor.com") or host == "tripadvisor.com.vn" or host.endswith(".tripadvisor.com.vn")


@lru_cache(maxsize=512)
def robots_policy(origin: str):
    """Read robots.txt once per origin for the non-Scrapy engines."""
    parsed = urlparse(origin)
    robots_url = urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", ""))
    if not public_http_url(robots_url):
        return None
    try:
        request = Request(robots_url, headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=12) as response:
            body = response.read(256_000).decode("utf-8", "replace")
    except HTTPError as exc:
        if exc.code in (404, 410):
            parser = RobotFileParser()
            parser.parse([])
            return parser
        return None
    except (OSError, URLError, TimeoutError, ValueError):
        return None
    parser = RobotFileParser()
    parser.set_url(robots_url)
    parser.parse(body.splitlines())
    return parser


def can_crawl(url: str) -> bool:
    if forbidden_host(url) or not public_http_url(url):
        return False
    parsed = urlparse(url)
    parser = robots_policy(f"{parsed.scheme}://{parsed.netloc}")
    return bool(parser and parser.can_fetch(USER_AGENT, url))


def pace_sync(host: str, delay: float):
    with _HOST_LOCKS[host]:
        wait = delay - (time.monotonic() - _HOST_LAST.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        _HOST_LAST[host] = time.monotonic()


@dataclass
class Page:
    url: str
    html: str
    engine: str


def host_key(url: str) -> str:
    return (urlparse(url).hostname or "").casefold().rstrip(".")


def normalized_site(url: str) -> str | None:
    value = str(url or "").strip()
    if not value:
        return None
    if not re.match(r"^https?://", value, re.I):
        value = "https://" + value
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or not public_http_url(value) or forbidden_host(value):
        return None
    return value


def page_parts(html: str, page_url: str):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        if tag.name != "script" or tag.get("type", "").casefold() != "application/ld+json":
            tag.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))[:250_000]
    jsonld = []
    for tag in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        try:
            jsonld.append(json.loads(tag.string or tag.get_text()))
        except (TypeError, json.JSONDecodeError):
            continue
    images = []
    for selector, attr in (("meta[property='og:image']", "content"),
                           ("meta[name='twitter:image']", "content"),
                           ("link[rel='image_src']", "href")):
        for node in soup.select(selector):
            value = node.get(attr)
            if value:
                image = urljoin(page_url, value.strip())
                if urlparse(image).scheme == "https" and public_http_url(image):
                    images.append(image)
    links = []
    root_host = host_key(page_url)
    for node in soup.find_all("a", href=True):
        href = urldefrag(urljoin(page_url, node["href"].strip()))[0]
        parsed = urlparse(href)
        if (parsed.scheme == "https" and host_key(href) == root_host
                and not BAD_EXT.search(parsed.path)
                and PRICE_HINT.search((parsed.path or "") + " " + node.get_text(" ", strip=True))):
            links.append(href)
    return soup, text, jsonld, list(dict.fromkeys(images)), list(dict.fromkeys(links))


def structured_address(jsonld: list, venue_name: str) -> str | None:
    """Find a street address on a matching Schema.org business entity."""
    wanted = {t for t in _fold_words(venue_name).split()
              if len(t) >= 3 and t not in _NAME_STOPWORDS}
    stack = list(jsonld)
    while stack:
        item = stack.pop()
        if isinstance(item, list):
            stack.extend(item)
            continue
        if not isinstance(item, dict):
            continue
        stack.extend(v for k, v in item.items() if k in {"@graph", "mainEntity", "itemListElement"})
        name = item.get("name")
        if isinstance(name, dict):
            name = name.get("name")
        tokens = {t for t in _fold_words(name).split()
                  if len(t) >= 3 and t not in _NAME_STOPWORDS}
        address = item.get("address")
        if not (wanted and tokens and wanted <= tokens) or not isinstance(address, (str, dict)):
            continue
        if isinstance(address, str):
            result = address.strip()
        else:
            result = ", ".join(str(address.get(k)).strip() for k in
                               ("streetAddress", "addressLocality", "addressRegion", "postalCode")
                               if address.get(k))
        if result and re.search(r"\d|đường|phố|street|road|avenue|highway", result, re.I):
            return result[:500]
    return None


def fetch_requests(url: str) -> Page | None:
    if not public_http_url(url) or not can_crawl(url):
        return None
    try:
        pace_sync(host_key(url), 2.0)
        response = requests.get(url, headers={"User-Agent": USER_AGENT,
                                              "Accept": "text/html,application/xhtml+xml"},
                                timeout=(8, 20), allow_redirects=True,
                                stream=True)
        if response.status_code in (403, 429):
            return None
        response.raise_for_status()
        if "html" not in response.headers.get("content-type", "").casefold():
            return None
        final = response.url
        if host_key(final) != host_key(url) or not public_http_url(final):
            return None
        body = response.raw.read(MAX_HTML_BYTES + 1, decode_content=True)
        if len(body) > MAX_HTML_BYTES:
            return None
        return Page(final, body.decode(response.encoding or "utf-8", "replace"), "requests+bs4")
    except requests.RequestException:
        return None


async def fetch_httpx(urls: list[str], delay: float, concurrency: int) -> list[Page]:
    pages: list[Page] = []
    per_host: dict[str, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(1))
    last_request: dict[str, float] = {}
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT,
                                          "Accept": "text/html,application/xhtml+xml"},
                                 timeout=httpx.Timeout(18, connect=8), follow_redirects=False,
                                 limits=httpx.Limits(max_connections=max(2, concurrency),
                                                     max_keepalive_connections=max(1, concurrency // 2))) as client:
        async def one(url: str):
            host = host_key(url)
            if not host or not public_http_url(url) or not can_crawl(url):
                return
            async with per_host[host]:
                wait = delay - (time.monotonic() - last_request.get(host, 0))
                if wait > 0:
                    await asyncio.sleep(wait)
                last_request[host] = time.monotonic()
                try:
                    response = await client.get(url)
                    # Do not switch engines after a site explicitly blocks access.
                    if response.status_code in (403, 429):
                        return
                    if response.status_code in (301, 302, 303, 307, 308):
                        target = urljoin(url, response.headers.get("location", ""))
                        if host_key(target) != host or not public_http_url(target) or not can_crawl(target):
                            return
                        response = await client.get(target)
                    response.raise_for_status()
                    if "html" not in response.headers.get("content-type", "").casefold():
                        return
                    if host_key(str(response.url)) != host or len(response.content) > MAX_HTML_BYTES:
                        return
                    pages.append(Page(str(response.url), response.text, "httpx+asyncio"))
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code not in (403, 429):
                        logging.info("HTTPX bỏ qua %s: HTTP %s", url, exc.response.status_code)
                except httpx.HTTPError as exc:
                    logging.info("HTTPX bỏ qua %s: %s", url, exc)
        await asyncio.gather(*(one(url) for url in urls))
    return pages


def discover_with_scrapy(sites: list[str], pages_per_site: int) -> dict[str, list[str]]:
    """Discover same-site links with robots enforcement and conservative delay."""
    if not sites:
        return {}
    try:
        import scrapy
        from scrapy.crawler import CrawlerProcess
        from scrapy.linkextractors import LinkExtractor
    except ImportError:
        logging.warning("Scrapy chưa được cài; vẫn tiếp tục bằng Requests/HTTPX/Playwright.")
        return {}
    found: dict[str, list[str]] = defaultdict(list)

    class VenueLinkSpider(scrapy.Spider):
        name = "registered_venue_html_links"

        def __init__(self, start_url: str, page_cap: int, **kwargs):
            super().__init__(**kwargs)
            self.start_urls = [start_url]
            self.allowed_domains = [host_key(start_url)]
            self.page_cap = page_cap
            self.seen_count = 0
            self.scheduled_count = 1
            self.extractor = LinkExtractor(allow_domains=self.allowed_domains,
                                           deny_extensions=("pdf", "jpg", "jpeg", "png", "gif", "webp", "svg", "zip", "mp4", "mp3"),
                                           unique=True)

        def parse(self, response):
            if response.status in (403, 429):
                return
            if response.status < 200 or response.status >= 300:
                return
            self.seen_count += 1
            root = self.start_urls[0]
            for link in self.extractor.extract_links(response):
                target = urldefrag(link.url)[0]
                parsed = urlparse(target)
                if (parsed.scheme == "https" and host_key(target) == host_key(root)
                        and PRICE_HINT.search((parsed.path or "") + " " + link.text)):
                    if target not in found[root]:
                        found[root].append(target)
            if response.meta.get("depth", 0) < 1 and self.seen_count < self.page_cap:
                for link in self.extractor.extract_links(response):
                    target = urldefrag(link.url)[0]
                    if (self.scheduled_count < self.page_cap and host_key(target) == host_key(root)
                            and not BAD_EXT.search(urlparse(target).path)
                            and response.url != target):
                        self.scheduled_count += 1
                        yield response.follow(target, callback=self.parse, dont_filter=False)

    settings = {
        "ROBOTSTXT_OBEY": True,
        "USER_AGENT": USER_AGENT,
        "DOWNLOAD_DELAY": 2.0,
        "CONCURRENT_REQUESTS": 12,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_START_DELAY": 2.0,
        "AUTOTHROTTLE_MAX_DELAY": 10.0,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 0.5,
        "DEPTH_LIMIT": 1,
        "DOWNLOAD_TIMEOUT": 20,
        "RETRY_ENABLED": False,
        "LOG_LEVEL": "ERROR",
        "LOG_ENABLED": False,
        "TELNETCONSOLE_ENABLED": False,
    }
    process = CrawlerProcess(settings)
    for site in sites:
        if public_http_url(site) and can_crawl(site):
            process.crawl(VenueLinkSpider, start_url=site, page_cap=pages_per_site)
    process.start(stop_after_crawl=True, install_signal_handlers=False)
    return found


async def fetch_playwright(url: str) -> Page | None:
    if not public_http_url(url) or not can_crawl(url):
        return None
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(user_agent=USER_AGENT)
            response = await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            if response and response.status in (403, 429):
                await browser.close()
                return None
            if response and response.status >= 400:
                await browser.close()
                return None
            await page.wait_for_timeout(1500)
            final = page.url
            html = await page.content()
            await browser.close()
            if host_key(final) != host_key(url) or not public_http_url(final) or len(html) > MAX_HTML_BYTES:
                return None
            return Page(final, html, "playwright")
    except Exception as exc:
        logging.info("Playwright không khả dụng cho %s: %s", url, str(exc)[:160])
        return None


def load_sites(conn, limit: int, province: str | None, category: str | None,
               target_per_group: int = 200):
    with conn.cursor() as cur:
        cur.execute("""WITH eligible AS (
            SELECT pl.id, pl.name, pl.category, pl.address,
                   COALESCE(pl.ward,'') AS ward, COALESCE(pl.district,'') AS district,
                   COALESCE(p.name, pl.province,'') AS province,
                   COALESCE(NULLIF(BTRIM(pl.website),''), NULLIF(BTRIM(td.website),'')) AS website,
                   pl.image_url, pl.price_source_url, pl.image_source_url,
                   ROW_NUMBER() OVER (
                     PARTITION BY pl.province_id, CASE
                       WHEN pl.category IN ('Tham quan','Vui chơi','Giải trí','Thiên nhiên')
                         THEN 'Tham quan/vui chơi'
                       WHEN pl.category='Mua sắm' THEN 'Siêu thị'
                       WHEN pl.category='Ăn uống' THEN 'Nhà hàng/quán ăn'
                       ELSE pl.category END
                     ORDER BY (pl.image_url IS NULL) DESC, (pl.price_source_url IS NULL) DESC, pl.id
                   ) AS quota_rank
            FROM places pl
            LEFT JOIN provinces p ON p.id=pl.province_id
            LEFT JOIN LATERAL (
              SELECT website FROM tourism_directory_records d
              WHERE d.place_id=pl.id AND NULLIF(BTRIM(d.website),'') IS NOT NULL
              ORDER BY d.last_seen_at DESC, d.id DESC LIMIT 1
            ) td ON TRUE
            WHERE COALESCE(pl.source,'') <> 'Demo seed'
              AND NULLIF(BTRIM(pl.address),'') IS NOT NULL
              AND (pl.image_url IS NULL OR pl.image_source_url IS NULL
                   OR pl.price_source_url IS NULL OR pl.price_min IS NULL)
              AND (
                pl.category IN ('Lưu trú','Ăn uống','Cafe','Tham quan','Vui chơi','Giải trí','Thiên nhiên')
                OR (pl.category='Mua sắm' AND pl.name ~* %s)
              )
              AND COALESCE(NULLIF(BTRIM(pl.website),''), NULLIF(BTRIM(td.website),'')) IS NOT NULL
              AND (%s IS NULL OR p.name ILIKE %s OR pl.province ILIKE %s)
              AND (%s IS NULL OR pl.category=%s)
            )
            SELECT id, name, category, address, COALESCE(ward,''), COALESCE(district,''),
                   COALESCE(province,''), website, image_url, price_source_url, image_source_url
            FROM eligible
            WHERE quota_rank <= %s
            ORDER BY id
            LIMIT %s""", (province, f"%{province}%" if province else None,
                         f"%{province}%" if province else None,
                         SUPERMARKET_NAME.pattern,
                         category, category, max(1, min(target_per_group, 20_000)),
                         max(1, min(limit, 20_000)))
        )
        rows = cur.fetchall()
    venues = []
    for row in rows:
        site = normalized_site(row[7])
        if not site:
            continue
        venues.append({"id": int(row[0]), "name": row[1], "category": row[2],
                       "address": row[3], "ward": row[4], "district": row[5],
                       "province": row[6], "site": site, "image_url": row[8],
                       "price_source_url": row[9], "image_source_url": row[10]})
    return venues


def persist_page(conn, venue: dict, page: Page):
    _, text, jsonld, images, _ = page_parts(page.html, page.url)
    if not listing_matches_page(venue["name"], page.url, text, jsonld):
        return False, False
    location_context = " ".join(str(venue.get(k) or "") for k in ("address", "ward", "district", "province"))
    if not page_location_matches_places(page.url, location_context):
        return False, False
    prices = explicit_vnd_prices(jsonld, text, venue["category"], page.url)
    full_address = structured_address(jsonld, venue["name"])
    image_path = None
    if (not venue.get("image_url") or not venue.get("image_source_url")) and images:
        image_path = materialize_image(images[0], venue["name"])
    updated_price = bool(prices)
    # Refresh only from an explicit current price/address/image in a matching
    # first-party HTML page; absence of a value never clears existing data.
    with conn.cursor() as cur:
        if updated_price:
            cur.execute("""UPDATE places SET price_min=%s, price_max=%s, currency='VND',
                price_source='Website công khai', price_source_url=%s, price_checked_at=NOW(),
                price_kind='published_offer', updated_at=CURRENT_TIMESTAMP WHERE id=%s""",
                        (min(prices), max(prices), page.url, venue["id"]))
        if full_address and len(full_address) > len(str(venue.get("address") or "")):
            cur.execute("""UPDATE places SET address=%s, updated_at=CURRENT_TIMESTAMP
                WHERE id=%s AND (address IS NULL OR LENGTH(BTRIM(address)) < LENGTH(%s))""",
                        (full_address, venue["id"], full_address))
        if image_path:
            cur.execute("""UPDATE places SET image_url=%s, image_source_url=%s,
                image_source_name='Website chính thức của cơ sở', image_license=NULL,
                image_checked_at=NOW(), updated_at=CURRENT_TIMESTAMP
                WHERE id=%s AND (image_url IS NULL OR image_source_url IS NULL)""",
                        (image_path, page.url, venue["id"]))
    conn.commit()
    return updated_price, bool(image_path)


async def run(args):
    conn = psycopg2.connect(**DB)
    try:
        venues = load_sites(conn, args.limit, args.province, args.category,
                            args.target_per_group)
        if not venues:
            print("Không có website công khai gắn với địa điểm đủ điều kiện trong DB.")
            return
        # A website is crawled once, even if several branches refer to it.
        by_site: dict[str, list[dict]] = defaultdict(list)
        for venue in venues:
            by_site[venue["site"]].append(venue)
        sites = list(by_site)
        print(f"Địa điểm: {len(venues)} | website duy nhất: {len(sites)}", flush=True)

        # Engine 1: static first-party landing pages.
        roots = []
        with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 8))) as pool:
            for site, page in zip(sites, pool.map(fetch_requests, sites)):
                if page:
                    roots.append(page)

        # Engine 2: Scrapy follows only same-site links and is configured to obey robots.txt.
        scrapy_sites = sites[:max(0, min(args.scrapy_sites, len(sites)))]
        discovered = await asyncio.to_thread(discover_with_scrapy, scrapy_sites, args.scrapy_pages)
        targets = []
        for site, links in discovered.items():
            for link in links[:args.links_per_site]:
                if link != site and host_key(link) == host_key(site):
                    targets.append(link)

        # Engine 3: async fetching for discovered menu/rate/about pages.
        linked_pages = await fetch_httpx(targets, args.delay, args.workers)

        # Engine 4: render only a successful, unusually thin JS shell; never bypass 403/429.
        rendered = []
        for page in roots:
            _, text, _, _, _ = page_parts(page.html, page.url)
            if len(text) < 180 and re.search(r"javascript|enable js|loading|__next|ng-version", page.html, re.I):
                result = await fetch_playwright(page.url)
                if result:
                    rendered.append(result)

        counts = {"giá": 0, "ảnh": 0, "trang khớp": 0}
        for page in [*roots, *linked_pages, *rendered]:
            site = next((root for root in sites if host_key(root) == host_key(page.url)), None)
            if not site:
                continue
            for venue in by_site[site]:
                try:
                    price, image = persist_page(conn, venue, page)
                    if price or image:
                        counts["trang khớp"] += 1
                    counts["giá"] += int(price)
                    counts["ảnh"] += int(image)
                    if image:
                        venue["image_url"] = "saved"
                    if price:
                        venue["price_source_url"] = page.url
                except Exception as exc:
                    logging.warning("Bỏ qua %s (%s): %s", venue["name"], page.url, str(exc)[:160])
        print("Kết quả lượt này: %s" % " | ".join(f"{key}: {value}" for key, value in counts.items()), flush=True)
        print("Trang lấy được: Requests=%d, Scrapy phát hiện URL=%d, HTTPX=%d, Playwright=%d" %
              (len(roots), len(targets), len(linked_pages), len(rendered)), flush=True)
    finally:
        conn.close()


def main():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=100, help="Tổng số địa điểm tối đa trong lượt (mặc định 100)")
    parser.add_argument("--target-per-group", type=int, default=200,
                        help="Tối đa địa điểm mỗi tỉnh và nhóm; mặc định 200")
    parser.add_argument("--workers", type=int, default=6, help="Số kết nối song song có giới hạn")
    parser.add_argument("--delay", type=float, default=3.0, help="Khoảng chờ tối thiểu mỗi hostname")
    parser.add_argument("--scrapy-sites", type=int, default=30, help="Số website dùng Scrapy trong lượt")
    parser.add_argument("--scrapy-pages", type=int, default=8, help="Giới hạn trang nội bộ mỗi website")
    parser.add_argument("--links-per-site", type=int, default=3, help="Trang giá/menu tối đa mỗi website")
    parser.add_argument("--province", help="Lọc tỉnh/thành theo tên")
    parser.add_argument("--category", help="Lọc nhóm: Lưu trú, Ăn uống, Cafe, Tham quan...")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
