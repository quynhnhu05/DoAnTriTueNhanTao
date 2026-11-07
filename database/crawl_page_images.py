"""Download and catalog page photos without calling a language model."""

from __future__ import annotations

import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database.crawll import (
    DEFAULT_URL,
    download_page_images,
    extract_image_candidates,
    load_project_env,
    persist_page_images,
    prepare_certificates,
)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    load_project_env()
    ca_bundle = prepare_certificates()
    urls = sys.argv[1:] or [DEFAULT_URL]
    for url in urls:
        try:
            response = requests.get(
                url,
                headers={"User-Agent": "TravelPathStudentResearch/1.0"},
                timeout=(10, 40),
                verify=ca_bundle,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            print(f"Không tải được {url}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        candidates = extract_image_candidates(BeautifulSoup(response.content, "html.parser"), url)
        article_count = sum(item.get("is_article_image") == "true" for item in candidates)
        print(f"{url}: tìm thấy {len(candidates)} ảnh, {article_count} ảnh trong nội dung bài.")
        downloaded = download_page_images(candidates)
        persist_page_images(url, candidates, downloaded)
        print(f"Đã tải và lưu {len(downloaded)} ảnh.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
