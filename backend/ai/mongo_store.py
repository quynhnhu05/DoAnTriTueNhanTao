"""MongoDB connection helpers for the travel app's document store."""
from __future__ import annotations

import os
from functools import lru_cache

from pymongo import MongoClient


class MongoStore:
    """Small wrapper that mirrors the connection lifetime used by the app."""

    def __init__(self, database):
        self.database = database

    def __getitem__(self, collection: str):
        return self.database[collection]

    def close(self):
        # The pooled Mongo client is shared between requests.
        return None


@lru_cache(maxsize=1)
def _client() -> MongoClient:
    uri = os.getenv("MONGODB_URI", "").strip()
    if not uri:
        raise RuntimeError("Thiếu MONGODB_URI. Hãy cấu hình MongoDB Atlas trong file .env.")
    return MongoClient(uri, serverSelectionTimeoutMS=8000, connectTimeoutMS=8000)


def get_mongo_store() -> MongoStore:
    database_name = os.getenv("MONGODB_DATABASE", "travel_path").strip() or "travel_path"
    client = _client()
    client.admin.command("ping")
    return MongoStore(client[database_name])
