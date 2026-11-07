"""Continuously refresh nationwide real venue data in polite-sized batches."""
from __future__ import annotations

import threading
import time
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import psycopg2

from crawl_real_tourism_data import (
    DB, collect_images, collect_prices, link_unambiguous_accommodations,
    unlink_confidently_mismatched_accommodations, clear_nonstay_hotel_prices,
)
from crawl_vietnamtourism_directory import CATEGORIES, crawl as crawl_directory
from migrate_postgres_to_mongodb import sync_recent_from_postgres


DIRECTORY_REFRESH = timedelta(days=7)
IMAGE_BATCH = 500
PRICE_BATCH = 250
PAUSE_BETWEEN_BATCHES = 300
DIRECTORY_RETRY_DELAY = timedelta(hours=1)


def refresh_directory(state: dict) -> None:
    """Refresh the public directory without blocking photo/price enrichment."""
    try:
        with psycopg2.connect(**DB) as directory_conn:
            print("[Danh bạ] Bắt đầu làm mới toàn quốc ở luồng riêng.", flush=True)
            result = crawl_directory(directory_conn, list(CATEGORIES), 1.5, None, None)
            state["last_success"] = datetime.now(timezone.utc)
            print(f"[Danh bạ] Hoàn tất: {result}.", flush=True)
    except Exception as exc:
        print(f"[Danh bạ] Lỗi làm mới: {type(exc).__name__}: {str(exc)[:300]}",
              file=sys.stderr, flush=True)


def run_enrichment_batch(collector, limit: int, delay: float):
    """Give each collector its own connection so real photos and prices run together."""
    with psycopg2.connect(**DB) as conn:
        return collector(conn, limit, delay, False)


def main() -> None:
    directory_state = {"last_success": None, "last_attempt": None}
    directory_thread = None
    while True:
        conn = None
        try:
            conn = psycopg2.connect(**DB)
            now = datetime.now(timezone.utc)
            directory_due = (directory_state["last_success"] is None or
                             now - directory_state["last_success"] >= DIRECTORY_REFRESH)
            retry_ready = (directory_state["last_attempt"] is None or
                           now - directory_state["last_attempt"] >= DIRECTORY_RETRY_DELAY)
            if (directory_thread is None or not directory_thread.is_alive()) and directory_due and retry_ready:
                directory_state["last_attempt"] = now
                directory_thread = threading.Thread(target=refresh_directory, args=(directory_state,),
                                                    name="vietnam-tourism-directory", daemon=True)
                directory_thread.start()
            unlinked = unlink_confidently_mismatched_accommodations(conn)
            if unlinked:
                print(f"[Khách sạn] Đã gỡ {unlinked} liên kết sai; bản gốc được lưu để khôi phục.", flush=True)
            cleared_places, cleared_directory = clear_nonstay_hotel_prices(conn)
            if cleared_places or cleared_directory:
                print(f"[Giá] Đã gỡ giá khách sạn lấy nhầm từ trang dịch vụ: {cleared_places} địa điểm, {cleared_directory} danh bạ.", flush=True)
            print("[Khách sạn] Nối bản ghi tên, đường và tỉnh khớp chắc chắn:",
                  link_unambiguous_accommodations(conn), flush=True)

            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="venue-enrichment") as pool:
                images_future = pool.submit(run_enrichment_batch, collect_images, IMAGE_BATCH, 1.2)
                prices_future = pool.submit(run_enrichment_batch, collect_prices, PRICE_BATCH, 4.0)
                checked_images, saved_images = images_future.result()
                checked_prices, saved_prices = prices_future.result()
            print(f"[Ảnh] Kiểm tra {checked_images}, lưu ảnh nội bộ {saved_images}.", flush=True)
            print(f"[Giá] Kiểm tra {checked_prices}, lưu giá công khai {saved_prices}.", flush=True)
            try:
                synced = sync_recent_from_postgres(conn)
                if synced["places"] or synced["directory"]:
                    print(f"[MongoDB] Đồng bộ {synced['places']} địa điểm, {synced['directory']} danh bạ.", flush=True)
            except Exception as sync_exc:
                print(f"[MongoDB] Chưa đồng bộ được: {type(sync_exc).__name__}: {str(sync_exc)[:250]}",
                      file=sys.stderr, flush=True)

        except Exception as exc:
            print(f"[Bộ thu thập] Lỗi tạm thời: {type(exc).__name__}: {str(exc)[:300]}",
                  file=sys.stderr, flush=True)
        finally:
            if conn is not None:
                conn.close()
        time.sleep(PAUSE_BETWEEN_BATCHES)


if __name__ == "__main__":
    main()
