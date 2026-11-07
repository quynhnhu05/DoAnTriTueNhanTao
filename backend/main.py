from __future__ import annotations

import os
import re
import hashlib
import hmac
import json
import secrets
import smtplib
import threading
import time
import unicodedata
import html
from email.message import EmailMessage
from urllib.parse import unquote
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from statistics import median
from types import SimpleNamespace
from typing import Any, Optional

import psycopg2
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from ai.chat_model import chat_model_status, interpret_trip_message
from ai.repository import PlaceRepository
from ai.mongo_store import get_mongo_store
from ai.system import build_system
from ai.planner import TripPlanner, days_without_sightseeing, plan_quality_warnings
from ai.recommender import Place, safe_float, safe_int
from ai.demo_pricing import demo_price
from ai.demo_data import demo_opening_hours, displayable_image_url


DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "tourism_db")
DB_USER = os.getenv("DB_USER", "tourism_user")
DB_PASSWORD = os.getenv("DB_PASSWORD", "tourism_password")
DATA_BACKEND = os.getenv("DATA_BACKEND", "postgres").strip().lower()

app = FastAPI(
    title="AI Smart Travel Assistant",
    description=f"API trợ lý du lịch dùng {DATA_BACKEND.upper()} + recommender + TripPlanner.",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5500",
        "http://localhost:5500",
        "http://127.0.0.1:5501",
        "http://localhost:5501",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RecommendRequest(BaseModel):
    province_code: Optional[str] = None
    categories: list[str] = Field(default_factory=list)
    interests: list[str] = Field(default_factory=list)
    budget_min: Optional[float] = None
    budget_max: Optional[float] = None
    limit: int = Field(default=20, ge=1, le=100)


class HotelRecommendRequest(BaseModel):
    province_code: str
    days: int = Field(default=3, ge=1, le=30)
    people: int = Field(default=2, ge=1, le=100)
    budget: Optional[float] = Field(default=None, ge=0)
    budget_min: Optional[float] = Field(default=None, ge=0)
    budget_max: Optional[float] = Field(default=None, ge=0)
    accommodation_preference: str = "near_center"
    limit: int = Field(default=12, ge=1, le=100)


class PlanRequest(BaseModel):
    province_code: str
    days: int = Field(default=3, ge=1, le=30)
    people: int = Field(default=2, ge=1, le=100)
    budget: Optional[float] = Field(default=None, ge=0)
    preferences: list[str] = Field(default_factory=list)
    start_date: Optional[date] = None
    hotel_id: int
    radius_km: float = Field(default=50, ge=1, le=100)
    # Kept for clients that still send it; the planner now fits the day to
    # travel, opening hours, meals and the traveler's interests automatically.
    limit_per_day: Optional[int] = None


class ChatPlanRequest(BaseModel):
    province_code: str
    days: int = Field(default=3, ge=1, le=30)
    people: int = Field(default=2, ge=1, le=100)
    budget: Optional[float] = Field(default=None, ge=0)
    preferences: list[str] = Field(default_factory=list)
    avoid_hotel_ids: list[int] = Field(default_factory=list)
    variation_seed: int = 0
    hotel_id: Optional[int] = None
    avoid_place_ids: list[int] = Field(default_factory=list)
    required_place_ids: list[int] = Field(default_factory=list)
    strict_budget: bool = False


class ChatPlanPDFRequest(BaseModel):
    plan: dict[str, Any]


class ChangeChatHotelRequest(BaseModel):
    province_code: str
    days: int = Field(default=3, ge=1, le=30)
    people: int = Field(default=2, ge=1, le=100)
    budget: Optional[float] = Field(default=None, ge=0)
    avoid_hotel_ids: list[int] = Field(default_factory=list)
    current_plan: dict[str, Any] = Field(default_factory=dict)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=1000)
    state: dict[str, Any] = Field(default_factory=dict)
    history: list[dict[str, str]] = Field(default_factory=list)
    allow_ai_interpretation: bool = True


class RegisterRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=254)
    username: str = Field(..., min_length=3, max_length=40)
    password: str = Field(..., min_length=8, max_length=256)
    password_confirm: str = Field(..., min_length=8, max_length=256)


class VerifyRegistrationRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=254)
    otp: str = Field(..., min_length=6, max_length=6)


class ResetPasswordRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=254)
    otp: str = Field(..., min_length=6, max_length=6)
    new_password: str = Field(..., min_length=8, max_length=256)
    password_confirm: str = Field(..., min_length=8, max_length=256)


class PasswordResetOtpRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=254)


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=254)
    password: str = Field(..., min_length=1, max_length=256)


class SavePlanRequest(BaseModel):
    plan: dict[str, Any]
    title: Optional[str] = Field(default=None, max_length=180)
    plan_id: Optional[int] = Field(default=None, ge=1)


SYSTEM_CACHE: dict[str, Any] = {"system": None, "loaded_at": 0.0}
SYSTEM_CACHE_LOCK = threading.Lock()
SYSTEM_CACHE_TTL_SECONDS = max(30, int(os.getenv("SYSTEM_CACHE_TTL_SECONDS", "300")))


def get_connection():
    if DATA_BACKEND == "mongodb":
        return get_mongo_store()
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )


def get_system(force_reload: bool = False):
    now = time.monotonic()
    if (SYSTEM_CACHE["system"] is not None and not force_reload
            and now - SYSTEM_CACHE["loaded_at"] < SYSTEM_CACHE_TTL_SECONDS):
        return SYSTEM_CACHE["system"]

    with SYSTEM_CACHE_LOCK:
        now = time.monotonic()
        if (SYSTEM_CACHE["system"] is not None and not force_reload
                and now - SYSTEM_CACHE["loaded_at"] < SYSTEM_CACHE_TTL_SECONDS):
            return SYSTEM_CACHE["system"]

        conn = get_connection()
        try:
            repo = PlaceRepository(conn)
            # OSM expansion can exceed the former 50k cap; load every active
            # POI so no province gets silently truncated after import.
            places = repo.get_places(limit=100000)
        finally:
            conn.close()

        system = build_system(places)
        SYSTEM_CACHE["system"] = system
        SYSTEM_CACHE["loaded_at"] = time.monotonic()
        return system


def invalidate_system_cache():
    with SYSTEM_CACHE_LOCK:
        SYSTEM_CACHE["system"] = None
        SYSTEM_CACHE["loaded_at"] = 0.0


AUTH_PASSWORD_ITERATIONS = 310_000
AUTH_SESSION_COOKIE = "travel_session"


def _auth_connection():
    if DATA_BACKEND != "postgres":
        raise HTTPException(status_code=503, detail="Tài khoản hiện cần cơ sở dữ liệu PostgreSQL.")
    return get_connection()


def _password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, AUTH_PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${AUTH_PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def _password_matches(password: str, encoded: str) -> bool:
    try:
        scheme, rounds, salt_hex, digest_hex = encoded.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def _session_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _optional_current_user(request: Request):
    token = request.cookies.get(AUTH_SESSION_COOKIE)
    if not token:
        return None
    conn = _auth_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT u.id, u.email, u.display_name, u.role
               FROM user_sessions s JOIN users u ON u.id = s.user_id
               WHERE s.session_hash = %s AND s.expires_at > NOW() AND u.is_active = TRUE""",
            (_session_digest(token),),
        )
        row = cur.fetchone()
        cur.close()
        if not row:
            return None
        return {"id": row[0], "email": row[1], "display_name": row[2], "role": row[3]}
    finally:
        conn.close()


def _current_user(request: Request):
    user = _optional_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Vui lòng đăng nhập để tiếp tục.")
    return user


def _admin_user(request: Request):
    user = _current_user(request)
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Tài khoản không có quyền quản trị.")
    return user


@app.on_event("startup")
def initialize_account_tables():
    if DATA_BACKEND != "postgres":
        return
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """CREATE TABLE IF NOT EXISTS users (
                 id BIGSERIAL PRIMARY KEY,
                 email VARCHAR(254) NOT NULL UNIQUE,
                 username VARCHAR(40),
                 display_name VARCHAR(80) NOT NULL,
                 password_hash TEXT NOT NULL,
                 role VARCHAR(16) NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
                 is_active BOOLEAN NOT NULL DEFAULT TRUE,
                 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                 last_login_at TIMESTAMPTZ,
                 login_count INTEGER NOT NULL DEFAULT 0
               )"""
        )
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS username VARCHAR(40)")
        cur.execute(
            """UPDATE users SET username = LEFT(REGEXP_REPLACE(SPLIT_PART(email, '@', 1), '[^a-zA-Z0-9_.-]', '', 'g'), 28) || '_' || id
               WHERE username IS NULL OR BTRIM(username) = ''"""
        )
        cur.execute("ALTER TABLE users ALTER COLUMN username SET NOT NULL")
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_lower ON users(LOWER(username))")
        cur.execute(
            """CREATE TABLE IF NOT EXISTS user_sessions (
                 session_hash CHAR(64) PRIMARY KEY,
                 user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                 expires_at TIMESTAMPTZ NOT NULL
               )"""
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_user_sessions_expiry ON user_sessions(expires_at)")
        cur.execute(
            """CREATE TABLE IF NOT EXISTS pending_email_verifications (
                 email VARCHAR(254) PRIMARY KEY,
                 username VARCHAR(40) NOT NULL,
                 password_hash TEXT NOT NULL,
                 otp_hash CHAR(64) NOT NULL,
                 expires_at TIMESTAMPTZ NOT NULL,
                 requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                 attempts INTEGER NOT NULL DEFAULT 0
               )"""
        )
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_pending_username_lower ON pending_email_verifications(LOWER(username))")
        cur.execute(
            """CREATE TABLE IF NOT EXISTS password_reset_verifications (
                 email VARCHAR(254) PRIMARY KEY,
                 otp_hash CHAR(64) NOT NULL,
                 expires_at TIMESTAMPTZ NOT NULL,
                 requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                 attempts INTEGER NOT NULL DEFAULT 0
               )"""
        )
        cur.execute(
            """CREATE TABLE IF NOT EXISTS saved_travel_plans (
                 id BIGSERIAL PRIMARY KEY,
                 user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                 title VARCHAR(180) NOT NULL,
                 destination VARCHAR(160),
                 plan_json JSONB NOT NULL,
                 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                 updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
               )"""
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_saved_travel_plans_user_created ON saved_travel_plans(user_id, created_at DESC)")
        admin_email = os.getenv("ADMIN_EMAIL", "").strip().lower()
        admin_password = os.getenv("ADMIN_PASSWORD", "")
        if admin_email and admin_password:
            if len(admin_password) < 8:
                raise RuntimeError("ADMIN_PASSWORD phải có ít nhất 8 ký tự.")
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", admin_email):
                raise RuntimeError("ADMIN_EMAIL không đúng định dạng email.")
            cur.execute("SELECT id FROM users WHERE email = %s", (admin_email,))
            existing = cur.fetchone()
            if existing:
                cur.execute(
                    "UPDATE users SET role = 'admin', is_active = TRUE, password_hash = %s WHERE id = %s",
                    (_password_hash(admin_password), existing[0]),
                )
            else:
                cur.execute(
                    "INSERT INTO users (email, username, display_name, password_hash, role) VALUES (%s, 'admin', 'Quản trị viên', %s, 'admin')",
                    (admin_email, _password_hash(admin_password)),
                )
        conn.commit()
        cur.close()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _registration_otp_hash(email: str, otp: str) -> str:
    secret = os.getenv("AUTH_OTP_SECRET") or DB_PASSWORD
    return hmac.new(secret.encode("utf-8"), f"{email}:{otp}".encode("utf-8"), hashlib.sha256).hexdigest()


def _password_reset_otp_hash(email: str, otp: str) -> str:
    secret = os.getenv("AUTH_OTP_SECRET") or DB_PASSWORD
    return hmac.new(secret.encode("utf-8"), f"password-reset:{email}:{otp}".encode("utf-8"), hashlib.sha256).hexdigest()


def _send_auth_otp(email: str, subject: str, body: str) -> None:
    host = os.getenv("SMTP_HOST", "").strip()
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    sender = os.getenv("SMTP_FROM_EMAIL", "").strip() or username
    if not host or not username or not password or not sender:
        raise HTTPException(status_code=503, detail="Chưa cấu hình dịch vụ gửi email OTP trong file .env.")
    try:
        port = int(os.getenv("SMTP_PORT", "587"))
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = sender
        message["To"] = email
        message.set_content(body)
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=20) as smtp:
                smtp.login(username, password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=20) as smtp:
                if os.getenv("SMTP_USE_TLS", "true").strip().lower() in {"1", "true", "yes"}:
                    smtp.starttls()
                smtp.login(username, password)
                smtp.send_message(message)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=502, detail="Không gửi được email OTP. Kiểm tra cấu hình email trong .env rồi thử lại.")


def _send_registration_otp(email: str, otp: str) -> None:
    _send_auth_otp(
        email,
        "Mã xác nhận đăng ký Travel Path",
        f"Mã xác nhận tài khoản Travel Path của bạn là: {otp}\n\n"
        "Mã có hiệu lực trong 10 phút và chỉ sử dụng một lần. Nếu bạn không yêu cầu đăng ký, hãy bỏ qua email này.",
    )


def _send_password_reset_otp(email: str, otp: str) -> None:
    _send_auth_otp(
        email,
        "Mã đặt lại mật khẩu Travel Path",
        f"Mã OTP đặt lại mật khẩu Travel Path của bạn là: {otp}\n\n"
        "Mã có hiệu lực trong 10 phút và chỉ sử dụng một lần. Nếu bạn không yêu cầu đặt lại mật khẩu, hãy bỏ qua email này.",
    )


@app.post("/api/auth/register/request-otp")
def request_registration_otp(payload: RegisterRequest):
    if not all(os.getenv(key, "").strip() for key in ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD")):
        raise HTTPException(status_code=503, detail="Chưa cấu hình dịch vụ gửi email OTP trong file .env.")
    email = payload.email.strip().lower()
    username = payload.username.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(status_code=422, detail="Email chưa đúng định dạng.")
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{3,40}", username):
        raise HTTPException(status_code=422, detail="Tên đăng nhập gồm 3–40 ký tự: chữ, số, dấu chấm, gạch dưới hoặc gạch ngang.")
    if username == "admin":
        raise HTTPException(status_code=422, detail="Tên đăng nhập này được dành riêng cho tài khoản quản trị.")
    if payload.password != payload.password_confirm:
        raise HTTPException(status_code=422, detail="Hai lần nhập mật khẩu chưa trùng khớp.")
    otp = f"{secrets.randbelow(1_000_000):06d}"
    conn = _auth_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM users WHERE LOWER(email) = %s", (email,))
        if cur.fetchone():
            cur.close()
            raise HTTPException(status_code=409, detail="Email này đã có tài khoản. Vui lòng đăng nhập.")
        cur.execute("SELECT 1 FROM users WHERE LOWER(username) = %s", (username,))
        if cur.fetchone():
            cur.close()
            raise HTTPException(status_code=409, detail="Tên đăng nhập đã được sử dụng. Vui lòng chọn tên khác.")
        cur.execute(
            "SELECT requested_at > NOW() - INTERVAL '60 seconds' FROM pending_email_verifications WHERE email = %s",
            (email,),
        )
        recent = cur.fetchone()
        if recent and recent[0]:
            cur.close()
            raise HTTPException(status_code=429, detail="Bạn vừa yêu cầu mã. Hãy chờ 60 giây rồi thử gửi lại.")
        cur.execute(
            "SELECT 1 FROM pending_email_verifications WHERE LOWER(username) = %s AND email <> %s",
            (username, email),
        )
        if cur.fetchone():
            cur.close()
            raise HTTPException(status_code=409, detail="Tên đăng nhập đang được dùng cho một đăng ký khác.")
        cur.execute(
            """INSERT INTO pending_email_verifications (email, username, password_hash, otp_hash, expires_at, requested_at, attempts)
               VALUES (%s, %s, %s, %s, NOW() + INTERVAL '10 minutes', NOW(), 0)
               ON CONFLICT (email) DO UPDATE SET username = EXCLUDED.username,
                 password_hash = EXCLUDED.password_hash, otp_hash = EXCLUDED.otp_hash,
                 expires_at = EXCLUDED.expires_at, requested_at = NOW(), attempts = 0""",
            (email, username, _password_hash(payload.password), _registration_otp_hash(email, otp)),
        )
        conn.commit()
        cur.close()
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        raise HTTPException(status_code=409, detail="Tên đăng nhập đang được sử dụng.")
    finally:
        conn.close()
    try:
        _send_registration_otp(email, otp)
    except HTTPException:
        conn = _auth_connection()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM pending_email_verifications WHERE email = %s", (email,))
            conn.commit()
            cur.close()
        finally:
            conn.close()
        raise
    return {"ok": True, "message": "Mã OTP đã được gửi đến email của bạn.", "expires_in_seconds": 600}


@app.post("/api/auth/register/verify-otp", status_code=201)
def verify_registration_otp(payload: VerifyRegistrationRequest):
    email = payload.email.strip().lower()
    if not re.fullmatch(r"\d{6}", payload.otp):
        raise HTTPException(status_code=422, detail="Mã OTP cần gồm 6 chữ số.")
    conn = _auth_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT username, password_hash, otp_hash, expires_at, attempts FROM pending_email_verifications WHERE email = %s FOR UPDATE",
            (email,),
        )
        pending = cur.fetchone()
        if not pending:
            cur.close()
            raise HTTPException(status_code=400, detail="Không tìm thấy đăng ký đang chờ xác nhận. Hãy yêu cầu mã mới.")
        if pending[3] <= datetime.now(timezone.utc):
            cur.execute("DELETE FROM pending_email_verifications WHERE email = %s", (email,))
            conn.commit()
            cur.close()
            raise HTTPException(status_code=400, detail="Mã OTP đã hết hạn. Hãy đăng ký lại để nhận mã mới.")
        expected = _registration_otp_hash(email, payload.otp)
        if pending[4] >= 5 or not hmac.compare_digest(pending[2].strip(), expected):
            if pending[4] + 1 >= 5:
                cur.execute("DELETE FROM pending_email_verifications WHERE email = %s", (email,))
                conn.commit()
                cur.close()
                raise HTTPException(status_code=400, detail="Bạn đã nhập sai mã quá số lần cho phép. Hãy yêu cầu mã mới.")
            cur.execute("UPDATE pending_email_verifications SET attempts = attempts + 1 WHERE email = %s", (email,))
            conn.commit()
            cur.close()
            raise HTTPException(status_code=400, detail="Mã OTP chưa chính xác. Vui lòng kiểm tra lại email.")
        cur.execute(
            "INSERT INTO users (email, username, display_name, password_hash) VALUES (%s, %s, %s, %s) RETURNING id, email, username, display_name, role",
            (email, pending[0], pending[0], pending[1]),
        )
        user = cur.fetchone()
        cur.execute("DELETE FROM pending_email_verifications WHERE email = %s", (email,))
        conn.commit()
        cur.close()
        return {"user": {"id": user[0], "email": user[1], "username": user[2], "display_name": user[3], "role": user[4]}}
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        raise HTTPException(status_code=409, detail="Email hoặc tên đăng nhập đã được sử dụng.")
    finally:
        conn.close()


@app.post("/api/auth/password/forgot/request-otp")
def request_password_reset_otp(payload: PasswordResetOtpRequest):
    if not all(os.getenv(key, "").strip() for key in ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD")):
        raise HTTPException(status_code=503, detail="Chưa cấu hình dịch vụ gửi email OTP trong file .env.")
    email = payload.email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(status_code=422, detail="Email chưa đúng định dạng.")

    otp = f"{secrets.randbelow(1_000_000):06d}"
    conn = _auth_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT requested_at > NOW() - INTERVAL '60 seconds' FROM password_reset_verifications WHERE email = %s FOR UPDATE",
            (email,),
        )
        recent = cur.fetchone()
        if recent and recent[0]:
            cur.close()
            raise HTTPException(status_code=429, detail="Bạn vừa yêu cầu mã. Hãy chờ 60 giây rồi thử gửi lại.")
        cur.execute("SELECT 1 FROM users WHERE email = %s AND is_active = TRUE", (email,))
        account_exists = cur.fetchone() is not None
        if not account_exists:
            cur.close()
            raise HTTPException(status_code=404, detail="Email chưa tồn tại trong hệ thống. Vui lòng đăng ký tài khoản.")
        cur.execute(
            """INSERT INTO password_reset_verifications (email, otp_hash, expires_at, requested_at, attempts)
               VALUES (%s, %s, NOW() + INTERVAL '10 minutes', NOW(), 0)
               ON CONFLICT (email) DO UPDATE SET otp_hash = EXCLUDED.otp_hash,
                 expires_at = EXCLUDED.expires_at, requested_at = NOW(), attempts = 0""",
            (email, _password_reset_otp_hash(email, otp)),
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()

    try:
        _send_password_reset_otp(email, otp)
    except HTTPException:
        conn = _auth_connection()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM password_reset_verifications WHERE email = %s", (email,))
            conn.commit()
            cur.close()
        finally:
            conn.close()
        raise
    return {
        "ok": True,
        "message": "Nếu email đã đăng ký, mã OTP sẽ được gửi đến hộp thư của bạn. Hãy kiểm tra cả thư mục Spam.",
        "expires_in_seconds": 600,
    }


@app.post("/api/auth/password/forgot/verify-otp")
def reset_password_with_otp(payload: ResetPasswordRequest):
    email = payload.email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(status_code=422, detail="Email chưa đúng định dạng.")
    if not re.fullmatch(r"\d{6}", payload.otp):
        raise HTTPException(status_code=422, detail="Mã OTP cần gồm 6 chữ số.")
    if payload.new_password != payload.password_confirm:
        raise HTTPException(status_code=422, detail="Hai lần nhập mật khẩu mới chưa trùng khớp.")

    conn = _auth_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT otp_hash, expires_at, attempts FROM password_reset_verifications WHERE email = %s FOR UPDATE",
            (email,),
        )
        pending = cur.fetchone()
        if not pending:
            cur.close()
            raise HTTPException(status_code=400, detail="Không tìm thấy yêu cầu đặt lại mật khẩu. Hãy gửi mã OTP mới.")
        if pending[1] <= datetime.now(timezone.utc):
            cur.execute("DELETE FROM password_reset_verifications WHERE email = %s", (email,))
            conn.commit()
            cur.close()
            raise HTTPException(status_code=400, detail="Mã OTP đã hết hạn. Hãy yêu cầu mã mới.")
        expected = _password_reset_otp_hash(email, payload.otp)
        if pending[2] >= 5 or not hmac.compare_digest(pending[0].strip(), expected):
            if pending[2] + 1 >= 5:
                cur.execute("DELETE FROM password_reset_verifications WHERE email = %s", (email,))
                conn.commit()
                cur.close()
                raise HTTPException(status_code=400, detail="Bạn đã nhập sai mã quá số lần cho phép. Hãy yêu cầu mã mới.")
            cur.execute("UPDATE password_reset_verifications SET attempts = attempts + 1 WHERE email = %s", (email,))
            conn.commit()
            cur.close()
            raise HTTPException(status_code=400, detail="Mã OTP chưa chính xác. Vui lòng kiểm tra lại email.")

        cur.execute(
            "UPDATE users SET password_hash = %s WHERE email = %s AND is_active = TRUE RETURNING id",
            (_password_hash(payload.new_password), email),
        )
        user = cur.fetchone()
        if not user:
            cur.execute("DELETE FROM password_reset_verifications WHERE email = %s", (email,))
            conn.commit()
            cur.close()
            raise HTTPException(status_code=400, detail="Không tìm thấy tài khoản để đặt lại mật khẩu.")
        cur.execute("DELETE FROM password_reset_verifications WHERE email = %s", (email,))
        cur.execute("DELETE FROM user_sessions WHERE user_id = %s", (user[0],))
        conn.commit()
        cur.close()
        return {"ok": True, "message": "Đặt lại mật khẩu thành công. Hãy đăng nhập bằng mật khẩu mới."}
    finally:
        conn.close()


@app.post("/api/auth/login")
def login_account(payload: LoginRequest, request: Request, response: Response):
    email = payload.email.strip().lower()
    conn = _auth_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, email, display_name, password_hash, role FROM users WHERE email = %s AND is_active = TRUE",
            (email,),
        )
        row = cur.fetchone()
        if not row:
            cur.close()
            raise HTTPException(status_code=404, detail="Tài khoản không tồn tại. Vui lòng đăng ký trước.")
        if not _password_matches(payload.password, row[3]):
            cur.close()
            raise HTTPException(status_code=401, detail="Mật khẩu chưa chính xác. Vui lòng kiểm tra lại.")
        token = secrets.token_urlsafe(32)
        session_days = max(1, min(int(os.getenv("AUTH_SESSION_DAYS", "7")), 30))
        cur.execute(
            "INSERT INTO user_sessions (session_hash, user_id, expires_at) VALUES (%s, %s, NOW() + (%s * INTERVAL '1 day'))",
            (_session_digest(token), row[0], session_days),
        )
        cur.execute("UPDATE users SET last_login_at = NOW(), login_count = login_count + 1 WHERE id = %s", (row[0],))
        conn.commit()
        cur.close()
        response.set_cookie(
            AUTH_SESSION_COOKIE,
            token,
            max_age=session_days * 86400,
            httponly=True,
            secure=os.getenv("AUTH_COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes"},
            samesite="lax",
            path="/",
        )
        return {"user": {"id": row[0], "email": row[1], "display_name": row[2], "role": row[4]}}
    finally:
        conn.close()


@app.get("/api/auth/me")
def auth_me(request: Request):
    user = _optional_current_user(request)
    return {"user": user}


@app.post("/api/auth/logout")
def logout_account(request: Request, response: Response):
    token = request.cookies.get(AUTH_SESSION_COOKIE)
    if token:
        conn = _auth_connection()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM user_sessions WHERE session_hash = %s", (_session_digest(token),))
            conn.commit()
            cur.close()
        finally:
            conn.close()
    response.delete_cookie(AUTH_SESSION_COOKIE, path="/", httponly=True, samesite="lax")
    return {"ok": True}


@app.get("/api/admin/users")
def list_registered_users(request: Request, limit: int = Query(500, ge=1, le=1000)):
    _admin_user(request)
    conn = _auth_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT id, email, display_name, role, is_active, created_at, last_login_at, login_count
               FROM users ORDER BY created_at DESC LIMIT %s""",
            (limit,),
        )
        rows = cur.fetchall()
        cur.close()
        return [{
            "id": row[0], "email": row[1], "display_name": row[2], "role": row[3],
            "is_active": row[4], "created_at": row[5].isoformat() if row[5] else None,
            "last_login_at": row[6].isoformat() if row[6] else None, "login_count": row[7],
        } for row in rows]
    finally:
        conn.close()


@app.get("/api/my/plans")
def list_my_plans(request: Request, limit: int = Query(100, ge=1, le=500)):
    user = _current_user(request)
    conn = _auth_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT id, title, destination, created_at, updated_at
               FROM saved_travel_plans WHERE user_id = %s
               ORDER BY updated_at DESC, id DESC LIMIT %s""",
            (user["id"], limit),
        )
        rows = cur.fetchall()
        cur.close()
        return [{
            "id": row[0], "title": row[1], "destination": row[2],
            "created_at": row[3].isoformat() if row[3] else None,
            "updated_at": row[4].isoformat() if row[4] else None,
        } for row in rows]
    finally:
        conn.close()


@app.get("/api/my/plans/{plan_id}")
def get_my_plan(plan_id: int, request: Request):
    user = _current_user(request)
    conn = _auth_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, title, destination, plan_json, created_at, updated_at FROM saved_travel_plans WHERE id = %s AND user_id = %s",
            (plan_id, user["id"]),
        )
        row = cur.fetchone()
        cur.close()
        if not row:
            raise HTTPException(status_code=404, detail="Không tìm thấy plan trong tài khoản của bạn.")
        return {
            "id": row[0], "title": row[1], "destination": row[2], "plan": row[3],
            "created_at": row[4].isoformat() if row[4] else None,
            "updated_at": row[5].isoformat() if row[5] else None,
        }
    finally:
        conn.close()


@app.post("/api/my/plans")
def save_my_plan(payload: SavePlanRequest, request: Request):
    user = _current_user(request)
    plan = payload.plan
    itinerary = plan.get("itinerary")
    if not isinstance(itinerary, list) or not itinerary:
        raise HTTPException(status_code=422, detail="Plan chưa có lịch trình để lưu.")
    destination = str(plan.get("province_name") or plan.get("destination") or "Chuyến đi")[:160]
    title = (payload.title or f"Du lịch {destination} · {plan.get('days') or len(itinerary)} ngày").strip()[:180]
    encoded = json.dumps(plan, ensure_ascii=False, separators=(",", ":"))
    conn = _auth_connection()
    try:
        cur = conn.cursor()
        if payload.plan_id:
            cur.execute(
                """UPDATE saved_travel_plans SET title = %s, destination = %s, plan_json = %s::jsonb,
                   updated_at = NOW() WHERE id = %s AND user_id = %s RETURNING id""",
                (title or "Kế hoạch du lịch", destination, encoded, payload.plan_id, user["id"]),
            )
            row = cur.fetchone()
            if not row:
                conn.rollback()
                raise HTTPException(status_code=404, detail="Plan này không thuộc tài khoản của bạn.")
            plan_id = row[0]
        else:
            cur.execute(
                """INSERT INTO saved_travel_plans (user_id, title, destination, plan_json)
                   VALUES (%s, %s, %s, %s::jsonb) RETURNING id""",
                (user["id"], title or "Kế hoạch du lịch", destination, encoded),
            )
            plan_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        return {"id": plan_id, "title": title or "Kế hoạch du lịch", "destination": destination}
    finally:
        conn.close()


@app.delete("/api/my/plans/{plan_id}")
def delete_my_plan(plan_id: int, request: Request):
    user = _current_user(request)
    conn = _auth_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM saved_travel_plans WHERE id = %s AND user_id = %s", (plan_id, user["id"]))
        deleted = cur.rowcount
        conn.commit()
        cur.close()
        if not deleted:
            raise HTTPException(status_code=404, detail="Không tìm thấy plan trong tài khoản của bạn.")
        return {"ok": True}
    finally:
        conn.close()


def normalize(text: Any) -> str:
    return str(text or "").strip().lower()

def fold_text(text: Any) -> str:
    value = normalize(text)
    value = unicodedata.normalize("NFD", value)
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    return value.replace("đ", "d").replace("Đ", "d")


# Tọa độ tham chiếu bãi biển dùng khi DB không có POI mang tên "biển".
# Đây chỉ là điểm tham chiếu địa lý để tính khoảng cách Haversine; khách sạn
# vẫn luôn lấy trực tiếp từ PostgreSQL.
COASTAL_ANCHORS = {
    "Đà Nẵng": [(16.0678, 108.2468), (16.0005, 108.2620), (16.0946, 108.2770)],
    "Khánh Hòa": [(12.2388, 109.1967), (12.2450, 109.1940)],
    "Nha Trang": [(12.2388, 109.1967), (12.2450, 109.1940)],
    "Bà Rịa - Vũng Tàu": [(10.3378, 107.0843), (10.4114, 107.1362)],
    "Bình Định": [(13.7660, 109.2230), (13.7420, 109.1970)],
    "Quy Nhơn": [(13.7660, 109.2230), (13.7420, 109.1970)],
    "Bình Thuận": [(10.9333, 108.1000), (10.8100, 108.3360)],
    "Phan Thiết": [(10.9333, 108.1000), (10.8100, 108.3360)],
    "Quảng Ninh": [(20.9500, 107.0800), (20.9550, 107.0450)],
    "Hải Phòng": [(20.6900, 106.9300), (20.7000, 106.8500)],
    "Thanh Hóa": [(19.7200, 105.9000), (19.7300, 105.9200)],
    "Nghệ An": [(18.7700, 105.7700), (18.7800, 105.7400)],
    "Hà Tĩnh": [(18.3400, 105.9000), (18.2700, 106.0000)],
    "Quảng Bình": [(17.4800, 106.6200), (17.4600, 106.6100)],
    "Quảng Trị": [(17.1700, 107.1900), (16.9500, 107.2000)],
    "Huế": [(16.4800, 107.6700), (16.4700, 107.6800)],
    "Thừa Thiên Huế": [(16.4800, 107.6700), (16.4700, 107.6800)],
    "Quảng Nam": [(15.8800, 108.3300), (15.9000, 108.3400)],
    "Quảng Ngãi": [(15.1200, 108.8700), (15.1300, 108.9000)],
    "Phú Yên": [(13.0800, 109.3100), (13.0900, 109.3200)],
    "Ninh Thuận": [(11.5700, 109.0200), (11.5800, 109.0000)],
    "Kiên Giang": [(10.0500, 105.9200), (9.6100, 105.9600)],
    "Cà Mau": [(9.0300, 105.0900), (8.9800, 105.1200)],
}

def _province_anchor_name(name: str) -> Optional[str]:
    n = normalize(name)
    for key in COASTAL_ANCHORS:
        if normalize(key) in n or n in normalize(key):
            return key
    return None

def serialize_place(place: Place, score: float = 0.0) -> dict[str, Any]:
    demo = demo_price(place)
    verified_price = TripPlanner._has_current_published_price(place)
    actual_min = place.price_min if verified_price else None
    actual_max = place.price_max if verified_price else None
    return {
        "id": place.id,
        "name": place.name,
        "category": place.category,
        "subcategory": place.subcategory,
        "province_code": place.province_code,
        "province": place.province_name,
        "district": place.district_name,
        "ward": place.ward_name,
        "address": format_place_address(place),
        "latitude": place.latitude,
        "longitude": place.longitude,
        "rating": place.rating,
        "review_count": place.review_count,
        "directory_source_url": getattr(place, "directory_source_url", None),
        "directory_phone": getattr(place, "directory_phone", None),
        "directory_website": getattr(place, "directory_website", None),
        "price_min": actual_min,
        "price_max": actual_max,
        "price_source_url": place.price_source_url if verified_price else None,
        "price_source": getattr(place, "price_source", None) if verified_price else None,
        "price_checked_at": place.price_checked_at if verified_price else None,
        "price_kind": place.price_kind if verified_price else None,
        "display_price_min": actual_min if actual_min is not None else demo["min"],
        "display_price_max": actual_max if actual_max is not None else demo["max"],
        "display_price_unit": demo["unit"],
        "image_url": displayable_image_url(place),
        "image_source_url": getattr(place, "image_source_url", None),
        "image_source_name": getattr(place, "image_source_name", None),
        "image_license": getattr(place, "image_license", None),
        "opening_hours": getattr(place, "opening_hours", None) or demo_opening_hours(place),
        "distance_km": getattr(place, "distance_km", None),
        "score": round(float(score), 4),
    }


def remove_duplicate_photos(payload, seen: Optional[set[str]] = None):
    """Keep a source photo at most once in one API response tree."""
    if seen is None:
        seen = set()
    if isinstance(payload, list):
        for value in payload:
            remove_duplicate_photos(value, seen)
    elif isinstance(payload, dict):
        image_url = str(payload.get("image_url") or "").strip()
        if image_url and image_url in seen:
            payload["image_url"] = ""
            payload["image_source_url"] = None
            payload["image_source_name"] = None
            payload["image_license"] = None
        elif image_url:
            seen.add(image_url)
        for key, value in payload.items():
            if key != "image_url":
                remove_duplicate_photos(value, seen)
    return payload


def _photo_family(category: Any) -> str:
    category = fold_text(category)
    if any(word in category for word in ("luu tru", "khach san", "hotel", "resort", "homestay")):
        return "Lưu trú"
    if any(word in category for word in ("cafe", "ca phe", "coffee")):
        return "Cafe"
    if any(word in category for word in ("an uong", "nha hang", "restaurant", "food")):
        return "Ăn uống"
    if any(word in category for word in ("mua sam", "shopping", "sieu thi", "market")):
        return "Mua sắm"
    return "Tham quan"


def _local_photo_pool(conn) -> dict[str, list[dict[str, Any]]]:
    """Load distinct, saved photos grouped by a suitable venue category."""
    if hasattr(conn, "database"):
        rows = [
            (doc.get("category"), doc.get("image_url"), doc.get("image_source_url"),
             doc.get("image_source_name"), doc.get("image_license"))
            for doc in conn["places"].find(
                {"image_url": {"$regex": r"^/media/places/"}},
                {"category": 1, "image_url": 1, "image_source_url": 1,
                 "image_source_name": 1, "image_license": 1, "_id": 0},
            ).sort([("image_checked_at", -1), ("id", 1)])
        ]
    else:
        with conn.cursor() as cur:
            cur.execute("""SELECT category, image_url, image_source_url, image_source_name, image_license
                           FROM places
                           WHERE image_url LIKE '/media/places/%'
                           ORDER BY image_checked_at DESC NULLS LAST, id""")
            rows = cur.fetchall()
    pool: dict[str, list[dict[str, Any]]] = {}
    seen = set()
    for category, image_url, source_url, source_name, license_name in rows:
        if not image_url or image_url in seen:
            continue
        seen.add(image_url)
        candidate = {
            "image_url": image_url,
            "image_source_url": source_url,
            "image_source_name": source_name,
            "image_license": license_name,
        }
        pool.setdefault(_photo_family(category), []).append(candidate)
    # Supermarkets and shopping centers can use suitable attraction/market
    # photos when the smaller shopping-photo collection is exhausted.
    pool["Mua sắm"] = pool.get("Mua sắm", []) + pool.get("Tham quan", [])
    return pool


def attach_category_photos(payload, photo_pool: dict[str, list[dict[str, Any]]]):
    """Fill missing or duplicate photos with distinct photos of the same kind."""
    remove_duplicate_photos(payload)
    used = set()

    def collect_existing(value):
        if isinstance(value, list):
            for item in value:
                collect_existing(item)
        elif isinstance(value, dict):
            image_url = str(value.get("image_url") or "").strip()
            if image_url:
                used.add(image_url)
            for key, item in value.items():
                if key != "image_url":
                    collect_existing(item)

    collect_existing(payload)

    def fill(value):
        if isinstance(value, list):
            for item in value:
                fill(item)
        elif isinstance(value, dict):
            if "image_url" in value and not str(value.get("image_url") or "").strip():
                family = _photo_family(value.get("category") or value.get("type"))
                candidates = photo_pool.get(family, [])
                for candidate in candidates:
                    if candidate["image_url"] in used:
                        continue
                    value.update(candidate)
                    used.add(candidate["image_url"])
                    break
            for key, item in value.items():
                if key != "image_url":
                    fill(item)

    fill(payload)
    return payload


def resolve_province(conn, province_code: str):
    if hasattr(conn, "database"):
        row = conn["provinces"].find_one({"code": str(province_code).strip()}, {"_id": 0, "id": 1, "code": 1, "name": 1, "type": 1})
        return (row.get("id"), row.get("code"), row.get("name"), row.get("type")) if row else None
    cur = conn.cursor()
    cur.execute(
        "SELECT id, code, name, type FROM provinces WHERE code = %s LIMIT 1",
        [str(province_code).strip()],
    )
    row = cur.fetchone()
    cur.close()
    return row


def find_hotel(recommender, hotel_id: int, province_code: str) -> Optional[Place]:
    hotel = recommender.get_place_by_id(hotel_id)
    def is_usable_stay(candidate):
        if candidate is None or not has_usable_place_address(candidate):
            return False
        if normalize(candidate.province_code) != normalize(province_code):
            return False
        category = normalize(candidate.category)
        subcategory = normalize(candidate.subcategory)
        is_stay = any(k in category or k in subcategory for k in [
            "lưu trú", "khách sạn", "hotel", "hostel", "resort", "homestay", "motel"
        ])
        return is_stay and has_plausible_accommodation_rate(candidate)

    if is_usable_stay(hotel):
        return hotel

    # The background crawler may enrich a hotel after the in-memory planner
    # cache was built. Reload only this province so a newly offered hotel can
    # be selected and planned without waiting for the cache refresh interval.
    conn = get_connection()
    try:
        fresh_places = PlaceRepository(conn).get_places(
            province_code=province_code, limit=100000
        )
    finally:
        conn.close()
    fresh_hotel = next((place for place in fresh_places if place.id == hotel_id), None)
    return fresh_hotel if is_usable_stay(fresh_hotel) else None


def hotel_payload(hotel: Place) -> dict[str, Any]:
    demo = demo_price(hotel)
    verified_price = TripPlanner._has_current_published_price(hotel)
    actual_min = hotel.price_min if verified_price else None
    actual_max = hotel.price_max if verified_price else None
    verified_nightly_rate = verified_price and getattr(hotel, "price_kind", None) == "hotel_rate_per_night"
    estimated_nightly_rate = (
        (float(hotel.price_min) + float(hotel.price_max if hotel.price_max is not None else hotel.price_min)) / 2
        if verified_nightly_rate
        else (float(demo["min"]) + float(demo["max"])) / 2
    )
    return {
        "id": hotel.id,
        "name": hotel.name,
        "category": hotel.category,
        "subcategory": hotel.subcategory,
        "province_code": hotel.province_code,
        "province": hotel.province_name,
        "district": hotel.district_name,
        "ward": hotel.ward_name,
        "address": format_place_address(hotel),
        "latitude": hotel.latitude,
        "longitude": hotel.longitude,
        "rating": hotel.rating,
        "review_count": hotel.review_count,
        "directory_source_url": getattr(hotel, "directory_source_url", None),
        "directory_phone": getattr(hotel, "directory_phone", None),
        "directory_website": getattr(hotel, "directory_website", None),
        "price_min": actual_min,
        "price_max": actual_max,
        "price_source_url": hotel.price_source_url if verified_price else None,
        "price_source": getattr(hotel, "price_source", None) if verified_price else None,
        "price_checked_at": hotel.price_checked_at if verified_price else None,
        "price_kind": hotel.price_kind if verified_price else None,
        "display_price_min": actual_min if actual_min is not None else demo["min"],
        "display_price_max": actual_max if actual_max is not None else demo["max"],
        "display_price_unit": "per_night",
        "estimated_price_per_night": round(estimated_nightly_rate, 0),
        "nightly_rate_source_verified": bool(verified_nightly_rate),
        "image_url": displayable_image_url(hotel),
        "image_source_url": getattr(hotel, "image_source_url", None),
        "image_source_name": getattr(hotel, "image_source_name", None),
        "image_license": getattr(hotel, "image_license", None),
        "opening_hours": getattr(hotel, "opening_hours", None) or demo_opening_hours(hotel),
    }


def format_place_address(place: Place) -> Optional[str]:
    """Keep the street and current ward/province in one consistent order."""
    return TripPlanner._full_address(place)


def deduplicate_accommodations(places: list[Place]) -> list[Place]:
    """Collapse duplicate source records without merging distinct branches."""
    unique: list[Place] = []
    by_name: dict[str, list[Place]] = {}
    for place in places:
        name_key = re.sub(r"[^a-z0-9]+", " ", fold_text(place.name)).strip()
        address_key = re.sub(r"[^a-z0-9]+", " ", fold_text(format_place_address(place))).strip()
        duplicates = False
        for existing in by_name.get(name_key, []):
            existing_address = re.sub(
                r"[^a-z0-9]+", " ", fold_text(format_place_address(existing))
            ).strip()
            same_address = bool(address_key and existing_address and address_key == existing_address)
            distance = distance_km(
                place.latitude, place.longitude,
                existing.latitude, existing.longitude,
            )
            # Identical listings often have minor coordinate/address
            # differences between OpenStreetMap and tourism-directory data.
            # Keep branches with the same brand when they are farther apart.
            same_location = distance is not None and distance <= 0.15
            if same_address or same_location:
                duplicates = True
                break
        if duplicates:
            continue
        unique.append(place)
        by_name.setdefault(name_key, []).append(place)
    return unique


def has_usable_place_address(place: Place) -> bool:
    """Use the planner's street-address validation throughout the API."""
    return TripPlanner._has_usable_address(place)


def is_accommodation(place: Place) -> bool:
    text = normalize(f"{place.name} {place.category} {place.subcategory}")
    # Tránh nhầm sân golf / khu vui chơi có chữ "resort" thành khách sạn.
    if "golf" in text and not any(k in text for k in ["khách sạn", "hotel", "lưu trú", "hostel", "homestay", "motel", "nhà nghỉ"]):
        return False
    keywords = [
        "lưu trú", "khách sạn", "hotel", "hostel", "resort",
        "homestay", "motel", "nhà nghỉ", "chalet", "camping",
    ]
    return any(k in text for k in keywords)


def has_plausible_accommodation_rate(place: Place) -> bool:
    """Reject accommodation records with obviously corrupted VND room rates."""
    values = []
    for field in ("price_min", "price_max"):
        raw = getattr(place, field, None)
        if raw is None or raw == "":
            continue
        try:
            values.append(float(raw))
        except (TypeError, ValueError):
            continue
    # Keep unpriced stays available (their estimate is displayed separately),
    # but never present a supplied zero or implausibly low nightly rate.
    return not values or all(50_000 <= value <= 100_000_000 for value in values)


def distance_km(lat1, lon1, lat2, lon2):
    if None in (lat1, lon1, lat2, lon2):
        return None
    try:
        return TourismDistance.haversine(float(lat1), float(lon1), float(lat2), float(lon2))
    except (TypeError, ValueError):
        return None


class TourismDistance:
    @staticmethod
    def haversine(lat1, lon1, lat2, lon2):
        from math import radians, sin, cos, sqrt, atan2
        r = 6371.0
        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)
        a = (
            sin(dlat / 2) ** 2
            + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
        )
        return r * 2 * atan2(sqrt(a), sqrt(max(0.0, 1.0 - a)))


def _location_anchor(recommender, province_code: str, preference: str):
    """Tạo điểm tham chiếu hoàn toàn từ dữ liệu places hiện có.

    - near_beach: các bản ghi có từ khóa biển/bãi biển/beach/sea/coast.
    - near_attractions: các bản ghi thuộc nhóm tham quan/attraction/di tích.
    - near_center: trung tâm dữ liệu (median lat/lon) của các địa điểm có tọa độ.

    Không tự tạo khách sạn/địa điểm giả. Nếu DB không có dữ liệu biển thì trả
    về None để API nói rõ rằng chưa đủ dữ liệu để xếp theo tiêu chí đó.
    """
    pref = normalize(preference)
    aliases = {
        "gần biển": "near_beach",
        "near beach": "near_beach",
        "gần trung tâm": "near_center",
        "near center": "near_center",
        "gần khu tham quan": "near_attractions",
        "near attractions": "near_attractions",
    }
    pref = aliases.get(pref, pref)
    places = [
        p for p in recommender.places
        if normalize(p.province_code) == normalize(province_code)
        and p.latitude is not None
        and p.longitude is not None
    ]

    if pref == "near_beach":
        beach_pattern = re.compile(
            r"\b(?:bai bien|bien|beach|sea|coast|coastal|cua bien|bo bien|seaside)\b"
        )
        anchors = []
        for p in places:
            if is_accommodation(p):
                continue
            text = fold_text(f"{p.name} {p.category} {p.subcategory} {p.address}")
            if beach_pattern.search(text):
                anchors.append(p)
        if anchors:
            return anchors, f"{len(anchors)} điểm biển/bãi biển trong DB"

        # Fallback địa lý: nếu DB không có POI biển, dùng các điểm bờ biển
        # tham chiếu của chính tỉnh/thành. Khoảng cách vẫn được tính bằng
        # tọa độ thật của khách sạn, không dựa vào tên/địa chỉ.
        province_name = next((p.province_name for p in places if p.province_name), "")
        key = _province_anchor_name(province_name)
        if key:
            return COASTAL_ANCHORS[key], f"Điểm tham chiếu bờ biển của {key}"
        return None, "Chưa có dữ liệu địa lý biển cho tỉnh/thành này"

    if pref == "near_attractions":
        anchors = []
        for p in places:
            if is_accommodation(p):
                continue
            text = normalize(f"{p.category} {p.subcategory} {p.name}")
            if any(k in text for k in [
                "tham quan", "attraction", "sightseeing", "di tích", "landmark",
            ]):
                anchors.append(p)
        if not anchors:
            return None, "Chưa có bản ghi khu tham quan trong DB để tính khoảng cách"
        return anchors, f"{len(anchors)} điểm tham quan trong DB"

    if not places:
        return None, "Không có tọa độ trong DB để tính trung tâm"

    return (median([p.latitude for p in places]), median([p.longitude for p in places])), "Trung tâm dữ liệu theo median tọa độ DB"


def hotel_location_info(place: Place, anchor_data):
    anchors, reference = anchor_data
    if anchors is None:
        return None, 0.5, reference

    # A single median coordinate is a 2-tuple; coastal fallbacks are a list of
    # (lat, lon) tuples; DB-derived anchors are Place objects.
    if isinstance(anchors, (tuple, list)) and len(anchors) == 2 and all(
        isinstance(value, (int, float)) for value in anchors
    ):
        distance = distance_km(place.latitude, place.longitude, anchors[0], anchors[1])
    else:
        distances = []
        for anchor in anchors:
            if isinstance(anchor, (tuple, list)) and len(anchor) >= 2:
                lat, lon = anchor[0], anchor[1]
            else:
                lat, lon = getattr(anchor, "latitude", None), getattr(anchor, "longitude", None)
            if lat is None or lon is None:
                continue
            distances.append(distance_km(place.latitude, place.longitude, lat, lon))
        distances = [d for d in distances if d is not None]
        distance = min(distances) if distances else None

    if distance is None:
        return None, 0.5, reference

    # 0 km => 1.0, từ 15 km trở lên => 0.0.
    location_score = max(0.0, 1.0 - (distance / 15.0))
    return distance, location_score, reference


def pick_hotel_score(place: Place, preference: str, budget: Optional[float], days: int, anchor_data=None, people: int = 1):
    rating = (place.rating or 0) / 5 if place.rating is not None else 0.5
    reviews = place.review_count or 0
    review_score = 1.0 if reviews >= 1000 else 0.9 if reviews >= 500 else 0.8 if reviews >= 100 else 0.7 if reviews >= 50 else 0.6 if reviews >= 10 else 0.5
    price = ((place.price_min + place.price_max) / 2 if place.price_min is not None and place.price_max is not None
             else (place.price_min if place.price_min is not None else place.price_max)) \
        if getattr(place, "price_kind", None) == "hotel_rate_per_night" else None
    if budget is None or budget <= 0 or price is None:
        budget_score = 0.5
    else:
        total = price * max(days, 1)
        ratio = total / budget
        budget_score = 1.0 if ratio <= .25 else .9 if ratio <= .4 else .75 if ratio <= .5 else .45 if ratio <= .65 else .2 if ratio <= .8 else .05

    location_distance, location_score, location_reference = hotel_location_info(place, anchor_data) if anchor_data else (None, 0.5, "Không áp dụng")
    # Khi người dùng chọn khu vực, vị trí là tiêu chí chính. Điều này bảo đảm
    # chuyển từ "Gần trung tâm" sang "Gần biển" thực sự làm thay đổi thứ tự.
    final_score = round(
        .20 * rating
        + .10 * review_score
        + .15 * budget_score
        + .55 * location_score,
        4,
    )
    return final_score, location_distance, location_reference


def find_plan_place_names(plan: list[dict[str, Any]]) -> list[str]:
    names = []
    for day in plan or []:
        for item in day.get("schedule", []):
            place = item.get("place")
            if place and place.get("name"):
                names.append(str(place["name"]))
    return names


def find_name_in_message(message: str, plan: list[dict[str, Any]], last_place_name: Optional[str] = None) -> Optional[str]:
    text = fold_text(message)
    names = sorted(find_plan_place_names(plan), key=len, reverse=True)
    for name in names:
        n = fold_text(name)
        if n and n in text:
            return name
    if any(k in text for k in ["dia diem nay", "dia diem do", "cho nay", "cho do", "diem nay", "diem do", " no ", "cho cuoi", "diem cuoi", "last stop"]):
        if last_place_name:
            return last_place_name
        for name in reversed(names):
            if name:
                return name
    return None


def _removes_named_plan_place(message: str, state: dict[str, Any]) -> bool:
    text = fold_text(message)
    removal_words = ("bo ", "xoa ", "loai bo", "khong muon den", "dung dua", "remove ", "exclude ", "do not include")
    if not any(word in text for word in removal_words):
        return False
    plan = state.get("itinerary") or (state.get("plan") or {}).get("itinerary") or []
    return any(fold_text(name) in text for name in find_plan_place_names(plan) if fold_text(name))


def find_requested_poi(message: str, recommender, plan: list[dict[str, Any]]):
    """Resolve a named POI in natural Vietnamese without requiring its full DB name."""
    text = fold_text(message)
    candidates = []
    seen = set()
    for day in plan or []:
        for slot in day.get("schedule", []):
            place = slot.get("place") or {}
            if place.get("place_id") is not None:
                try:
                    seen.add(int(place["place_id"]))
                except (TypeError, ValueError):
                    pass
    candidates.extend(p for p in recommender.places if int(p.id) in seen)
    candidates.extend(p for p in recommender.places if int(p.id) not in seen)
    candidates = [
        p for p in candidates
        if (has_usable_place_address(p)
                and TripPlanner._is_tourism_candidate(p))
    ]

    # Common short names used in chat; matching still resolves to a DB place.
    aliases = {"ba na": ("ba na", "bana"), "hoi an": ("hoi an",)}
    for alias, needles in aliases.items():
        if alias in text and alias == "ba na":
            matches = [
                p for p in candidates
                if re.search(r"\bba\s+na\b", fold_text(p.name))
                or re.search(r"\bbana\b", fold_text(p.name))
            ]
            if matches:
                return max(matches, key=lambda p: (
                    int(p.id) in seen,
                    "sun world" in fold_text(p.name),
                    float(p.rating or 0),
                ))
        elif alias in text:
            matches = [p for p in candidates if any(n in fold_text(p.name) for n in needles)]
            if matches:
                return max(matches, key=lambda p: float(p.rating or 0))

    matches = [p for p in candidates if fold_text(p.name) and fold_text(p.name) in text]
    if matches:
        return max(matches, key=lambda p: len(fold_text(p.name)))

    # Match meaningful multiword fragments for requests like "đi Bà Nà".
    words = [w for w in re.findall(r"[a-z0-9]+", text) if len(w) >= 3 and w not in {"muon", "buoi", "sang", "chieu", "toi", "diem", "den", "ngay"}]
    ranked = []
    for place in candidates:
        name_words = set(re.findall(r"[a-z0-9]+", fold_text(place.name)))
        overlap = len(name_words.intersection(words))
        if overlap:
            ranked.append((overlap, float(place.rating or 0), place))
    return max(ranked, key=lambda row: (row[0], row[1]))[2] if ranked else None


def requested_time_of_day(message: str) -> Optional[str]:
    text = fold_text(message)
    if any(k in text for k in ["buoi sang", "sang som", "morning", "early morning"]):
        return "morning"
    if any(k in text for k in ["buoi chieu", "chieu", "afternoon"]):
        return "afternoon"
    if any(k in text for k in ["buoi toi", "ban dem", "buoi dem", "at night", "evening"]):
        return "evening"
    return None

def classify_requested_category(message: str) -> Optional[str]:
    categories = classify_requested_categories(message)
    return categories[0] if categories else None


def classify_requested_categories(message: str) -> list[str]:
    text = fold_text(message)
    categories = []
    if (re.search(r"\ban\b", text) or any(k in text for k in [
        "an uong", "quan an", "mon an", "mon dia phuong", "nha hang",
        "am thuc", "dac san", "quan ngon", "an ngon", "an vat", "an sang",
        "an trua", "an toi", "hai san", "food tour", "restaurant", "food",
        "local food", "cuisine",
    ])):
        categories.append("Ăn uống")
    mapping = [
        (["cafe", "ca phe", "coffee", "tra sua", "tiem banh", "banh ngot"], "Cafe"),
        (["tham quan", "diem tham quan", "dia diem du lich", "dia diem noi tieng", "diem noi tieng", "danh lam", "di tich", "di san", "bao tang", "lich su", "van hoa", "tam linh", "chua", "den tho", "lang nghe", "kien truc", "ngam canh", "chup anh", "check in", "check-in", "sightseeing", "attraction", "tourist spot", "landmark", "museum", "history", "culture"], "Tham quan"),
        (["vui choi", "di choi", "choi gi", "tro choi", "giai tri", "show dien", "pho di bo", "mao hiem", "duoi nuoc", "the thao", "gia dinh", "tre em", "vuon thu", "dong vat", "cong vien", "entertainment", "play", "fun", "fun activities", "nightlife", "family", "zoo"], "Vui chơi"),
        (["mua sam", "di cho", "cho dem", "cho noi", "mua qua", "shopping", "market"], "Mua sắm"),
        (["thien nhien", "bien", "bai bien", "ngam bien", "leo nui", "trekking", "cam trai", "picnic", "sinh thai", "vuon quoc gia", "thac", "suoi", "rung", "nature", "outdoors", "waterfall", "camping", "hiking"], "Thiên nhiên"),
    ]
    for keywords, category in mapping:
        if any(k in text for k in keywords) and category not in categories:
            categories.append(category)
    return categories


def requested_category_removals(message: str) -> list[str]:
    """Find category-level opt-outs without mistaking them for place replacement."""
    text = fold_text(message)
    negation = re.compile(
        r"\b(?:bo(?: bot| het)?|xoa|loai bo|khong muon|khong can|khong thich|khong di|"
        r"dung(?: them)?|han che|avoid|without|no more|stop adding|remove)\b"
    )
    split_message = re.split(r"[,;]|\b(?:nhung|but|however)\b", text)
    removals = []
    for segment in split_message:
        if negation.search(segment):
            for category in classify_requested_categories(segment):
                if category not in removals:
                    removals.append(category)

    # Common natural phrasing puts the opt-out after the category, e.g.
    # “ngày nào cũng mua sắm, bỏ bớt”.
    if not removals and any(term in text for term in [
        "bo bot", "bo het", "loai bo", "khong can nua", "khong muon nua",
        "han che lai", "less shopping", "stop adding",
    ]):
        removals = classify_requested_categories(message)
    return removals


def is_more_request(message: str) -> bool:
    t = normalize(message)
    return any(k in t for k in [
        "nhiều hơn", "nhieu hon", "more",
        "tăng", "tang", "ưu tiên hơn", "uu tien hon", "thêm", "them",
    ])


def is_vietnamese_message(message: str) -> bool:
    t = fold_text(message)
    return any(k in t for k in [
        "toi", "muon", "choi", "di ", "doi", "them", "nhieu", "khong",
        "lich", "dia diem", "khach san", "bien", "trung tam",
    ])


def recompute_day(plan_day: dict[str, Any], hotel: Place, recommender) -> None:
    route_info = TripPlanner(recommender)._route_and_schedule_day(
        plan_day.get("schedule", []), hotel.latitude, hotel.longitude
    )
    plan_day["route"] = route_info
    previous_lat = hotel.latitude
    previous_lon = hotel.longitude
    day_cost = 0.0
    real_cost = 0.0
    estimated_cost = 0.0
    unpriced_places = 0
    activity_count = 0
    published_price_places = 0

    for slot in plan_day.get("schedule", []):
        place = slot.get("place")
        if not place:
            slot["travel_from_previous"] = None
            continue
        activity_count += 1
        if (place.get("price_kind") == "published_offer"
                and place.get("price_source_url") and place.get("price_checked_at")
                and (place.get("price_min") is not None or place.get("price_max") is not None)):
            published_price_places += 1
        leg = slot.get("travel_from_previous") or {}
        distance = leg.get("distance_km")
        minutes = leg.get("estimated_minutes")
        if distance is None:
            distance = recommender._distance_km(previous_lat, previous_lon, place.get("latitude"), place.get("longitude"))
            minutes = TripPlannerTravel.minutes(distance)
            slot["travel_from_previous"] = {
                "distance_km": round(distance or 0, 2),
                "estimated_minutes": minutes,
                "distance_method": "straight_line_haversine",
                "duration_method": "rough_distance_estimate",
            }
        place["distance_km"] = round(float(distance or 0), 2)
        place["travel_time_minutes"] = minutes
        raw_total = place.get("total_cost")
        if raw_total is None:
            unpriced_places += 1
        else:
            total = float(raw_total)
            day_cost += total
            if place.get("cost_type") == "estimated":
                estimated_cost += total
            elif place.get("cost_type") == "real":
                real_cost += total
        previous_lat = place.get("latitude")
        previous_lon = place.get("longitude")

    plan_day["estimated_day_cost"] = None if unpriced_places else round(day_cost, 0)
    plan_day["unpriced_places"] = unpriced_places
    plan_day["activity_count"] = activity_count
    plan_day["published_price_places"] = published_price_places
    plan_day["activities_without_public_price"] = activity_count - published_price_places
    plan_day["place_count"] = sum(1 for x in plan_day.get("schedule", []) if x.get("place") is not None)
    plan_day["missing_slots"] = sum(1 for x in plan_day.get("schedule", []) if x.get("status") == "unavailable")
    plan_day["activity_distribution"] = {
        period: sum(
            1 for slot in plan_day.get("schedule", [])
            if slot.get("place") and slot.get("activity_period") == period
        )
        for period in TripPlanner.ACTIVITY_SLOT_TARGETS
    }
    return real_cost, estimated_cost, unpriced_places


class TripPlannerTravel:
    @staticmethod
    def minutes(distance):
        if distance is None:
            return None
        return max(3, int(__import__("math").ceil((float(distance) / 30.0) * 60)))


def rebuild_cost_summary(result: dict[str, Any], hotel: Place, recommender) -> None:
    real = 0.0
    estimated = 0.0
    unpriced_places = 0
    for day in result.get("itinerary", []):
        r, e, u = recompute_day(day, hotel, recommender)
        real += r
        estimated += e
        unpriced_places += u
    missing = sum(int(day.get("missing_slots") or 0) for day in result.get("itinerary", []))
    result["missing_slots"] = missing
    itinerary = result.get("itinerary", [])
    exploration_gaps = days_without_sightseeing(itinerary)
    result["complete"] = (
        missing == 0
        and len(itinerary) >= int(result.get("days") or 1)
        and not exploration_gaps
    )
    result["planning_warnings"] = plan_quality_warnings(itinerary, missing)
    result["planning_warnings"].extend(TripPlanner(recommender)._activity_distribution_warnings(itinerary))
    trip_days = int(result.get("days") or len(result.get("itinerary", [])) or 1)
    hotel_days = max(trip_days, 1)
    nightly_rate = float(hotel_payload(hotel)["estimated_price_per_night"])
    hotel_estimated_cost = nightly_rate * hotel_days
    total = real + estimated + hotel_estimated_cost
    activity_count = sum(int(day.get("activity_count") or 0) for day in result.get("itinerary", []))
    published_price_places = sum(int(day.get("published_price_places") or 0) for day in result.get("itinerary", []))
    result["cost_summary"] = {
        "real_cost": round(real, 0),
        "estimated_cost": round(estimated, 0),
        "hotel_estimated_cost": round(hotel_estimated_cost, 0),
        "hotel_days": hotel_days,
        "hotel_nights": hotel_days,
        "total_estimated_trip_cost": None if unpriced_places else round(total, 0),
        "known_cost": round(total, 0),
        "unpriced_places": unpriced_places,
        "activity_count": activity_count,
        "published_price_places": published_price_places,
        "activities_without_public_price": activity_count - published_price_places,
        "budget": result.get("budget"),
        "within_budget": total <= result.get("budget") if result.get("budget") is not None and not unpriced_places else None,
    }


@app.get("/")
def root():
    return {"message": "AI Smart Travel Assistant API", "status": "running", "version": app.version}


@app.get("/api/test-db")
def test_db():
    conn = get_connection()
    try:
        if hasattr(conn, "database"):
            total = conn["places"].estimated_document_count()
            conn["places"].find_one({}, {"_id": 1})
            return {"database": "connected", "engine": "MongoDB", "total_places": total}
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM places")
        total = cur.fetchone()[0]
        cur.close()
        return {"database": "connected", "total_places": total}
    finally:
        conn.close()


@app.get("/api/provinces")
def provinces():
    conn = get_connection()
    try:
        if hasattr(conn, "database"):
            return list(conn["provinces"].find(
                {}, {"_id": 0, "id": 1, "code": 1, "name": 1, "type": 1}
            ).sort("id", 1))
        cur = conn.cursor()
        cur.execute("SELECT id, code, name, type FROM provinces ORDER BY id")
        rows = cur.fetchall()
        cur.close()
        return [{"id": r[0], "code": r[1], "name": r[2], "type": r[3]} for r in rows]
    finally:
        conn.close()


@app.get("/api/places")
def places(
    province_code: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    has_public_price: bool = Query(False),
    limit: int = Query(50, ge=1, le=500),
):
    conn = get_connection()
    try:
        if hasattr(conn, "database"):
            mongo_filter: dict[str, Any] = {"category": {"$in": ["Ăn uống", "Lưu trú", "Cafe", "Vui chơi", "Giải trí", "Tham quan", "Mua sắm", "Di chuyển", "Thiên nhiên"]}}
            if province_code:
                mongo_filter["province_code"] = province_code
            if category:
                mongo_filter["$or"] = [
                    {"category": {"$regex": re.escape(category), "$options": "i"}},
                    {"subcategory": {"$regex": re.escape(category), "$options": "i"}},
                ]
            if has_public_price:
                from datetime import datetime, timezone
                mongo_filter.update({
                    "price_kind": "published_offer",
                    "price_source_url": {"$ne": None},
                    "price_source": {"$not": re.compile("estimated", re.I)},
                    "price_checked_at": {"$gte": datetime.now(timezone.utc) - timedelta(days=45)},
                })
            docs = list(conn["places"].find(mongo_filter, {"_id": 0}).sort(
                [("rating", -1), ("review_count", -1), ("id", 1)]
            ).limit(min(limit * 3, 1500)))
            result = [{
                "id": doc.get("id"), "name": doc.get("name"), "category": doc.get("category"),
                "subcategory": doc.get("subcategory"), "province_code": doc.get("province_code"),
                "province": doc.get("province_name"), "ward": doc.get("ward_name") or doc.get("ward"),
                "address": doc.get("address"), "district": doc.get("district"),
                "latitude": safe_float(doc.get("latitude")), "longitude": safe_float(doc.get("longitude")),
                "rating": safe_float(doc.get("rating")), "review_count": safe_int(doc.get("review_count")),
                "price_min": safe_float(doc.get("price_min")), "price_max": safe_float(doc.get("price_max")),
                "price_source_url": doc.get("price_source_url"), "price_checked_at": doc.get("price_checked_at"),
                "price_kind": doc.get("price_kind"), "directory_source_url": doc.get("directory_source_url"),
                "phone": doc.get("phone"), "directory_website": doc.get("website"),
                "image_url": doc.get("image_url"), "image_source_url": doc.get("image_source_url"),
                "image_source_name": doc.get("image_source_name"), "image_license": doc.get("image_license"),
                "price_source": doc.get("price_source"), "opening_hours": doc.get("opening_hours"),
            } for doc in docs]
        else:
            cur = conn.cursor()
            query = """
            SELECT pl.id, pl.name, pl.category, pl.subcategory,
                   p.code, p.name, pl.ward, pl.address,
                   pl.latitude, pl.longitude, pl.rating, pl.review_count,
                   pl.price_min, pl.price_max, pl.price_source_url,
                   pl.price_checked_at, pl.price_kind, pl.district,
                   directory.source_url, directory.phone, directory.website,
                   pl.image_url, pl.image_source_url, pl.image_source_name, pl.image_license,
                   pl.price_source, pl.opening_hours
            FROM places pl
            LEFT JOIN provinces p ON p.id = pl.province_id
            LEFT JOIN LATERAL (
                SELECT td.source_url, td.phone, td.website
                FROM tourism_directory_records td
                WHERE td.place_id = pl.id
                ORDER BY td.last_seen_at DESC, td.id DESC
                LIMIT 1
            ) directory ON TRUE
            WHERE pl.category IN ('Ăn uống','Lưu trú','Cafe','Vui chơi','Giải trí','Tham quan','Mua sắm','Di chuyển','Thiên nhiên')
        """
            params: list[Any] = []
            if province_code:
                query += " AND p.code = %s"
                params.append(province_code)
            if category:
                query += " AND (pl.category ILIKE %s OR COALESCE(pl.subcategory,'') ILIKE %s)"
                keyword = f"%{category}%"
                params.extend([keyword, keyword])
            if has_public_price:
                query += " AND pl.price_kind = 'published_offer' AND pl.price_source_url IS NOT NULL AND LOWER(COALESCE(pl.price_source,'')) <> 'estimated' AND pl.price_checked_at >= NOW() - INTERVAL '45 days'"
            query += " ORDER BY (pl.price_kind = 'published_offer' AND pl.price_source_url IS NOT NULL AND pl.price_checked_at >= NOW() - INTERVAL '45 days') DESC, pl.rating DESC NULLS LAST, pl.review_count DESC NULLS LAST, pl.id LIMIT %s"
            params.append(min(limit * 3, 1500))
            cur.execute(query, params)
            rows = cur.fetchall()
            cur.close()
            result = [
                {
                    "id": r[0], "name": r[1], "category": r[2], "subcategory": r[3],
                    "province_code": r[4], "province": r[5], "ward": r[6], "address": r[7], "district": r[17],
                    "latitude": safe_float(r[8]), "longitude": safe_float(r[9]),
                    "rating": safe_float(r[10]), "review_count": safe_int(r[11]),
                    "price_min": safe_float(r[12]), "price_max": safe_float(r[13]),
                    "price_source_url": r[14], "price_checked_at": r[15].isoformat() if r[15] else None,
                    "price_kind": r[16], "directory_source_url": r[18], "phone": r[19], "directory_website": r[20],
                    "image_url": r[21], "image_source_url": r[22], "image_source_name": r[23],
                    "image_license": r[24], "price_source": r[25], "opening_hours": r[26],
                }
                for r in rows
            ]
        result = [item for item in result if TripPlanner._has_usable_address(SimpleNamespace(
            name=item["name"], address=item["address"], ward_name=item["ward"],
            district_name=item["district"], province_name=item["province"],
            province_code=item["province_code"],
            latitude=item["latitude"], longitude=item["longitude"],
        ))]
        for item in result:
            sample = demo_price(type("PlaceForPrice", (), {"id": item["id"], "name": item["name"], "category": item["category"], "subcategory": item["subcategory"]})())
            verified = TripPlanner._has_current_published_price(SimpleNamespace(
                price_kind=item["price_kind"], price_source=item["price_source"],
                price_source_url=item["price_source_url"],
                price_min=item["price_min"], price_max=item["price_max"],
                price_checked_at=item["price_checked_at"],
            ))
            if not verified:
                item["price_min"] = item["price_max"] = None
                item["price_source_url"] = item["price_checked_at"] = None
                item["price_source"] = item["price_kind"] = None
                item["display_price_min"], item["display_price_max"] = sample["min"], sample["max"]
            else:
                item["display_price_min"] = item["price_min"] if item["price_min"] is not None else sample["min"]
                item["display_price_max"] = item["price_max"] if item["price_max"] is not None else sample["max"]
            item["display_price_unit"] = sample["unit"]
            item["opening_hours"] = item["opening_hours"] or demo_opening_hours(SimpleNamespace(
                id=item["id"], name=item["name"], category=item["category"], subcategory=item["subcategory"]
            ))
            item["image_url"] = displayable_image_url(SimpleNamespace(
                id=item["id"], name=item["name"], category=item["category"], subcategory=item["subcategory"],
                image_url=item["image_url"], image_source_url=item["image_source_url"],
            ))
        used_images: set[str] = set()
        for item in result:
            image_url = str(item.get("image_url") or "")
            if image_url and image_url in used_images:
                item["image_url"] = ""
                item["image_source_url"] = None
                item["image_source_name"] = None
                item["image_license"] = None
            elif image_url:
                used_images.add(image_url)
        return attach_category_photos(result[:limit], _local_photo_pool(conn))
    finally:
        conn.close()


@app.get("/api/crawl-preview")
def crawl_preview(
    province_code: str = Query(..., min_length=1, max_length=10),
    limit: int = Query(200, ge=1, le=500),
):
    """Show raw crawled records for review without publishing them to planner data."""
    if DATA_BACKEND == "mongodb":
        raise HTTPException(status_code=400, detail="Crawl preview is available from the PostgreSQL research database.")
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT s.id, s.name, s.category, p.name, s.address, s.price_text,
                   s.opening_hours, s.image_url, s.image_source_url,
                   s.source_urls, s.missing_fields, s.status, h.description
            FROM place_crawl_staging s
            JOIN provinces p ON p.code = s.province_code
            LEFT JOIN LATERAL (
                SELECT cp.description
                FROM html_crawled_places cp
                WHERE cp.id = ANY(s.source_record_ids)
                ORDER BY cp.crawled_at DESC, cp.id DESC
                LIMIT 1
            ) h ON TRUE
            WHERE s.province_code = %s
            ORDER BY s.category, s.name
            LIMIT %s
        """, (province_code, limit))
        rows = cur.fetchall()
        cur.close()
        return [{
            "id": row[0], "name": row[1], "category": row[2], "province": row[3],
            "address": row[4], "price_text": row[5], "opening_hours": row[6],
            "image_url": row[7], "image_source_url": row[8], "source_urls": row[9] or [],
            "missing_fields": row[10] or [], "status": row[11], "description": row[12],
        } for row in rows]
    finally:
        conn.close()


@app.post("/api/recommend")
def recommend(req: RecommendRequest):
    try:
        recommender, _ = get_system()
        result = recommender.recommend(
            province_code=req.province_code,
            categories=req.categories,
            interests=req.interests,
            budget_min=req.budget_min,
            budget_max=req.budget_max,
            limit=req.limit,
        )
        result = [p for p in result if has_usable_place_address(p)
                  and TripPlanner._is_tourism_candidate(p)]
        data = [serialize_place(p) for p in result]
        photo_conn = get_connection()
        try:
            attach_category_photos(data, _local_photo_pool(photo_conn))
        finally:
            photo_conn.close()
        return {"success": True, "count": len(result), "data": data}
    except Exception as exc:
        return {"success": False, "message": str(exc), "data": []}


@app.post("/api/recommend/hotels")
def recommend_hotels(req: HotelRecommendRequest):
    """Return real accommodation records from the loaded PostgreSQL dataset.

    Do not let the optional trip budget remove all hotel candidates. Budget is
    used for ranking, while location preference remains the primary criterion.
    """
    try:
        recommender, _ = get_system()
        # Photo and price crawls write to PostgreSQL while the recommendation
        # system cache is alive. Read stays fresh and do not hide a real hotel
        # just because its public image has not been collected yet. The UI
        # marks missing photos honestly and never substitutes a generic image.
        conn = get_connection()
        try:
            fresh_places = PlaceRepository(conn).get_places(
                province_code=req.province_code, limit=100000
            )
        finally:
            conn.close()
        accommodations = [
            p for p in fresh_places
            if normalize(p.province_code) == normalize(req.province_code)
            and is_accommodation(p)
            and has_plausible_accommodation_rate(p)
            and has_usable_place_address(p)
            and p.latitude is not None
            and p.longitude is not None
        ]
        accommodations = deduplicate_accommodations(accommodations)

        if not accommodations:
            return {
                "success": True, "count": 0, "data": [], "recommendations": [],
                "message": "Chưa có nơi lưu trú có địa chỉ đường và tọa độ dùng được trong khu vực này."
            }

        anchor_data = _location_anchor(recommender, req.province_code, req.accommodation_preference)
        ranked_items = []
        for p in accommodations:
            score, location_distance, location_reference = pick_hotel_score(
                p, req.accommodation_preference, req.budget, req.days, anchor_data, req.people
            )
            ranked_items.append((score, p, location_distance, location_reference))

        ranked_items.sort(
            key=lambda x: (
                x[2] if x[2] is not None else 10**9,
                -x[0],
                -(float(x[1].rating) if x[1].rating is not None else 0),
            )
        )

        data = []
        for score, p, location_distance, location_reference in ranked_items[:req.limit]:
            item = serialize_place(p, score)
            item["opening_hours"] = getattr(p, "opening_hours", None) or demo_opening_hours(p)
            verified = TripPlanner._has_current_published_price(p)
            price = (p.price_min if p.price_min is not None else p.price_max) if verified else None
            is_nightly_rate = verified and getattr(p, "price_kind", None) == "hotel_rate_per_night"
            nightly_estimate = (
                (float(p.price_min) + float(p.price_max if p.price_max is not None else p.price_min)) / 2
                if is_nightly_rate else None
            )
            item["estimated_price_per_night"] = nightly_estimate
            item["published_price"] = price
            item["price_available"] = verified and price is not None
            item["price_unit_verified"] = is_nightly_rate
            sample = demo_price(p)
            item["display_price_min"] = p.price_min if verified and p.price_min is not None else sample["min"]
            item["display_price_max"] = p.price_max if verified and p.price_max is not None else sample["max"]
            item["display_price_unit"] = "per_night"
            item["estimated_price_per_night"] = nightly_estimate if is_nightly_rate else sample["min"] + (sample["max"] - sample["min"]) // 2
            item["nights"] = max(req.days, 1)
            item["accommodation_preference"] = req.accommodation_preference
            item["location_distance_km"] = round(location_distance, 2) if location_distance is not None else None
            item["location_reference"] = location_reference
            data.append(item)

        photo_conn = get_connection()
        try:
            attach_category_photos(data, _local_photo_pool(photo_conn))
        finally:
            photo_conn.close()
        return {
            "success": True,
            "count": len(data),
            "data": data,
            "recommendations": data,
            "total_candidates": len(accommodations),
            "location_preference": req.accommodation_preference,
            "location_reference": anchor_data[1],
            "location_data_available": anchor_data[0] is not None,
        }
    except Exception as exc:
        return {"success": False, "message": str(exc), "data": [], "recommendations": []}


@app.post("/api/plan")
def create_plan(req: PlanRequest):
    try:
        recommender, planner = get_system()
        hotel = find_hotel(recommender, req.hotel_id, req.province_code)
        if hotel is None:
            return {"success": False, "message": "Không tìm thấy hotel_id trong tỉnh/thành đã chọn hoặc địa điểm không phải nơi lưu trú."}

        result = planner.create_plan(
            province_code=req.province_code,
            days=req.days,
            people=req.people,
            budget=req.budget,
            preferences=req.preferences,
            start_date=req.start_date,
            hotel_id=req.hotel_id,
            radius_km=req.radius_km,
            limit_per_day=req.limit_per_day,
        )
        result["province_code"] = req.province_code
        result["hotel"] = hotel_payload(hotel)
        result["request"] = req.model_dump()
        rebuild_cost_summary(result, hotel, recommender)
        photo_conn = get_connection()
        try:
            attach_category_photos(result, _local_photo_pool(photo_conn))
        finally:
            photo_conn.close()
        return result
    except Exception as exc:
        return {"success": False, "message": str(exc)}


def _staging_place_payload(row):
    image_url = row[10]
    image_source_url = row[11]
    if _staging_photo_is_clearly_wrong(row[3], row[4], image_url, image_source_url):
        # Keep the place usable, but never display a crawler UI asset or a
        # lodging-room photo as the picture of a restaurant/attraction.
        image_url = None
        image_source_url = None
    return {
        "id": row[0], "name": row[3], "category": row[4],
        "province_code": row[1], "province": row[2],
        "price_text": row[5], "price_min": safe_float(row[6]),
        "price_max": safe_float(row[7]), "address": row[8],
        "opening_hours": row[9], "image_url": image_url,
        "image_source_url": image_source_url, "source_urls": row[12] or [],
        "currency": row[13],
        "source": "place_crawl_staging",
    }


def _staging_photo_is_clearly_wrong(name, category, image_url, image_source_url):
    """Reject obvious UI assets and source images from a different venue type."""
    image = unquote(str(image_url or "")).lower()
    source = unquote(str(image_source_url or "")).lower()
    combined = f"{image} {source}"
    if re.search(
        r"(?:/social/|/icon/|/icons?/|/logo(?:[._/-]|$)|favicon|"
        r"search[-_]?grey[-_]?loupe|qb-search|icon[_-](?:address|email|flag|tin|zion)|"
        r"news[_-](?:blog|promotion)|bct\d*\.(?:png|svg|webp)$|"
        r"youtube[-_]channel|dat[_-]khach[_-]san[_-]tren[_-]zalopay|"
        r"(?:^|[/_.-])goi[-_]dien\.(?:png|jpe?g|webp)$|"
        r"customer[-_]service[-_]avatar|phone[-_]icon|new[-_]zalopay[-_]cta|"
        r"(?:^|[/_.-])(?:undefined|avatar|placeholder|gravatar)(?:\.[a-z0-9]+)?$|"
        r"(?:^|[/_.-])(?:zalo\d*|messenger|whatsapp|telegram|wechat|viber|tiktok|facebook)\.(?:png|svg|webp)$)",
        combined,
        re.I,
    ):
        return True
    is_stay = normalize(category) == normalize("Lưu trú")
    if not is_stay and re.search(
        r"(?:thanh[-_]an[-_]homestay|homestay[-_][^/]*\.(?:jpe?g|png|webp)|"
        r"(?:khach[-_]san|hotel|hostel|resort|bedroom|bed[-_]room)[^/]*\.(?:jpe?g|png|webp))",
        combined,
        re.I,
    ):
        return True
    return False


# Tên tỉnh/thành trước đợt sắp xếp năm 2025, ánh xạ về tỉnh/thành hiện hành.
# Dùng để loại bản ghi bị gắn nhầm tỉnh khi lập kế hoạch; không sửa dữ liệu gốc.
_CURRENT_PROVINCE_NAMES = (
    "Hà Nội", "Cao Bằng", "Tuyên Quang", "Điện Biên", "Lai Châu", "Sơn La",
    "Lào Cai", "Thái Nguyên", "Lạng Sơn", "Quảng Ninh", "Bắc Ninh", "Phú Thọ",
    "Hải Phòng", "Hưng Yên", "Ninh Bình", "Thanh Hóa", "Nghệ An", "Hà Tĩnh",
    "Quảng Trị", "Huế", "Đà Nẵng", "Quảng Ngãi", "Gia Lai", "Khánh Hòa",
    "Đắk Lắk", "Lâm Đồng", "Đồng Nai", "TP Hồ Chí Minh", "Tây Ninh",
    "Đồng Tháp", "Vĩnh Long", "An Giang", "Cần Thơ", "Cà Mau",
)
_LEGACY_PROVINCE_OWNER = {
    "Hà Giang": "Tuyên Quang", "Bắc Kạn": "Thái Nguyên",
    "Vĩnh Phúc": "Phú Thọ", "Hòa Bình": "Phú Thọ",
    "Bắc Giang": "Bắc Ninh", "Hải Dương": "Hải Phòng",
    "Thái Bình": "Hưng Yên", "Hà Nam": "Ninh Bình", "Nam Định": "Ninh Bình",
    "Yên Bái": "Lào Cai", "Quảng Bình": "Quảng Trị", "Quảng Nam": "Đà Nẵng",
    "Kon Tum": "Quảng Ngãi", "Bình Định": "Gia Lai", "Phú Yên": "Đắk Lắk",
    "Ninh Thuận": "Khánh Hòa", "Bình Thuận": "Lâm Đồng", "Đắk Nông": "Lâm Đồng",
    "Bình Phước": "Đồng Nai", "Long An": "Tây Ninh",
    "Bình Dương": "TP Hồ Chí Minh", "Bà Rịa - Vũng Tàu": "TP Hồ Chí Minh",
    "Tiền Giang": "Đồng Tháp", "Kiên Giang": "An Giang",
    "Trà Vinh": "Vĩnh Long", "Bến Tre": "Vĩnh Long",
    "Sóc Trăng": "Cần Thơ", "Hậu Giang": "Cần Thơ", "Bạc Liêu": "Cà Mau",
}


def _staging_record_matches_province(place: dict[str, Any], province_name: str) -> bool:
    """Reject records whose own name/address clearly points to another province."""
    expected = fold_text(province_name)
    aliases = {expected}
    province_owners = {fold_text(name): name for name in _CURRENT_PROVINCE_NAMES}
    province_owners.update({fold_text(old): current for old, current in _LEGACY_PROVINCE_OWNER.items()})
    aliases.update(
        fold_text(old_name)
        for old_name, current_name in _LEGACY_PROVINCE_OWNER.items()
        if fold_text(current_name) == expected
    )
    name = fold_text(place.get("name"))
    address = fold_text(place.get("address"))

    # A province written into the venue's proper name is a strong location cue.
    for other, owner_name in province_owners.items():
        owner = fold_text(owner_name)
        if fold_text(owner) == expected or other in aliases:
            continue
        if re.search(rf"(?<![a-z]){re.escape(other)}(?![a-z])", name):
            return False

    # In addresses, only treat an explicit province/city designation or a
    # trailing administrative component as a location, avoiding street names
    # such as Điện Biên Phủ and Đường Hà Nội.
    for other, owner_name in province_owners.items():
        owner = fold_text(owner_name)
        if fold_text(owner) == expected or other in aliases:
            continue
        if re.search(rf"\b(?:tinh|thanh pho|tp)\s+{re.escape(other)}\b", address):
            return False
        if re.search(rf"(?:,|;|/|-)\s*{re.escape(other)}\.?\s*$", address):
            return False
    return True


def _staging_price_bounds_vnd(place):
    """Return source-backed VND bounds, or (None, None) when currency is unclear."""
    text = str(place.get("price_text") or "").strip().lower()
    # Some crawled menu prices use a colon where Vietnamese notation expects
    # a thousands separator (for example, "15:00 – 25:00 VNĐ"). Normalize
    # only an otherwise complete price range, so opening hours stay untouched.
    colon_range = re.fullmatch(
        r"\s*\d{1,3}:\d{2,3}\s*[–—-]\s*\d{1,3}:\d{2,3}\s*(?:vnd|vnđ)\s*",
        text,
        re.I,
    )
    if colon_range:
        text = re.sub(r"(?<=\d):(?=\d{2,3}\b)", ".", text)
    # A single malformed range bound may also use a colon, e.g. 80:000.
    text = re.sub(r"\b(\d{1,3}):(\d{3})\b", r"\1.\2", text)
    category = fold_text(place.get("category"))
    explicit_nightly_rate = False
    if category == fold_text("Lưu trú") and "đêm" in text:
        night_end = text.index("đêm") + len("đêm")
        # Hotel pages often list hourly, nightly, and daily prices together.
        # Keep the clause immediately associated with the nightly rate.
        delimiters = list(re.finditer(r",\s+", text[:night_end]))
        if delimiters:
            text = text[delimiters[-1].end():night_end]
        else:
            text = text[:night_end]
        explicit_nightly_rate = True
    if re.search(r"miễn\s*phí|mien\s*phi|\bfree\b|không\s*thu\s*phí|khong\s*thu\s*phi", text):
        return 0, 0
    currency = fold_text(place.get("currency"))
    has_vnd = (
        bool(re.search(r"(?:vnd|vnđ|đồng|₫|\bđ\b|(?<=\d)đ)", text))
        or currency in {"vnd", "vnđ", "dong"}
        or (bool(re.search(r"\d\s*k\b|triệu|trieu|million", text))
            and not re.search(r"\b(?:usd|eur|cny|jpy|thb)\b|[$€¥฿]", text))
    )
    if not has_vnd:
        return None, None
    stored_min = None if explicit_nightly_rate else safe_float(place.get("price_min"))
    stored_max = None if explicit_nightly_rate else safe_float(place.get("price_max"))
    parsed = []
    malformed_amount = False
    number_pattern = r"(?<!\d)(?:\d{1,3}(?:[\s\u00a0.,]\d{3})+|\d+)(?:\s*k)?(?!\d)"
    for match in re.finditer(number_pattern, text, re.I):
        raw = match.group(0).strip()
        has_k_suffix = raw.lower().endswith("k")
        token = raw[:-1].strip() if has_k_suffix else raw
        token = token.rstrip(".,").strip()
        if not token:
            continue
        compact = re.sub(r"[\s\u00a0]", "", token)
        groups = re.split(r"[.,]", compact)
        if len(groups) > 1 and all(group.isdigit() for group in groups) and len(groups[0]) <= 3 and all(len(group) == 3 for group in groups[1:]):
            amount = float("".join(groups))
        elif len(groups) == 2 and all(group.isdigit() for group in groups):
            # A single separator with three trailing digits is a thousands
            # separator in Vietnamese price text; other cases are decimals.
            amount = float("".join(groups)) if len(groups[1]) == 3 and len(groups[0]) <= 3 else float(f"{groups[0]}.{groups[1]}")
        elif len(groups) == 1 and groups[0].isdigit():
            amount = float(groups[0])
        else:
            malformed_amount = True
            continue
        if has_k_suffix:
            amount *= 1_000
        elif re.search(r"triệu|trieu|\bmillion\b", text):
            amount *= 1_000_000
        elif re.search(r"nghìn|nghin|ngàn|ngan|\bthousand\b", text):
            amount *= 1_000
        parsed.append(amount)
    if malformed_amount and stored_min is None and stored_max is None:
        # Keep the plan usable, but don't budget-rank from an ambiguous source price.
        return None, None
    low = stored_min if stored_min is not None and stored_min >= 0 else (min(parsed) if parsed else None)
    high = stored_max if stored_max is not None and stored_max >= 0 else (max(parsed) if parsed else low)
    if low is None:
        return None, None
    if high is None:
        high = low
    return min(low, high), max(low, high)


def _staging_price_min_vnd(place):
    """Compatibility helper for callers that need the lower source price."""
    return _staging_price_bounds_vnd(place)[0]


def _staging_price_midpoint_vnd(place):
    low, high = _staging_price_bounds_vnd(place)
    return None if low is None else (low + high) / 2


def _per_person_plan_cost(place, activity, people=1):
    """Estimate realistic per-person spend from DB bounds and the recorded price unit."""
    low, high = _staging_price_bounds_vnd(place)
    if low is None:
        return None
    midpoint = (low + high) / 2
    unit = fold_text(place.get("price_text"))
    category = fold_text(place.get("category"))
    if midpoint == 0:
        return 0
    if category == fold_text("Ăn uống"):
        # Budget two portions/items for each traveler's meal stop.
        return midpoint * 2
    if activity == "Ăn nhẹ":
        # A snack stop budgets two small portions from the source price.
        if any(x in unit for x in ("phan", "hu", "ly", "mon")):
            return high * 2
        return midpoint
    if activity in {"Ăn sáng", "Ăn trưa", "Ăn tối"}:
        if "/mon" in unit or "moi mon" in unit:
            # A meal is budgeted as two dishes; divide the group total per traveler.
            return midpoint * 2 / max(int(people or 1), 1)
        if any(x in unit for x in ("/phan", "/suat", "/to", "/nguoi", "nguoi/", "per person")):
            # The source already gives a person-sized serving or per-person price.
            return midpoint
        # Unqualified restaurant prices are treated as one person's serving.
        # Only explicit per-dish prices use the shared 4–5 dish estimate above.
        return midpoint
    if category == fold_text("Lưu trú"):
        return midpoint
    return midpoint


def _breakfast_suitable(place):
    """Only schedule a morning meal when DB text identifies a filling breakfast."""
    evidence = fold_text(f"{place.get('name') or ''} {place.get('price_text') or ''}")
    unsuitable = ("sua chua", "kem", "che ", "tra sua", "dessert", "banh ngot", "yogurt", "ice cream", "mit lanh")
    if any(term in evidence for term in unsuitable):
        return False
    return bool(re.search(
        r"\b(?:pho|bun|mi quang|banh mi|banh cuon|xoi|chao|hu tieu|banh canh|com tam|banh uot|breakfast|an sang)\b",
        evidence,
    ))


def _main_meal_suitable(place):
    """Keep snacks and desserts out of breakfast/lunch/dinner slots."""
    evidence = fold_text(f"{place.get('name') or ''} {place.get('price_text') or ''}")
    snack_only = (
        "sua chua", "kem", "che ", "dessert", "yogurt", "ice cream", "mit lanh",
        "an vat", "banh trang kep", "tau hu", "tra sua", "snack", "oc hut",
    )
    if any(term in evidence for term in snack_only):
        return False
    return bool(re.search(
        r"\b(?:pho|bun|mi quang|banh mi|banh cuon|xoi|chao|hu tieu|banh canh|com|hai san|lau|banh xeo|be thui|ga|vit|bo|heo|thit|restaurant|nha hang)\b",
        evidence,
    ))


def _restaurant_suitable(place):
    """Identify DB entries that are explicitly restaurants or full-service dining."""
    name = fold_text(place.get("name"))
    return any(term in name for term in (
        "nha hang", "restaurant", "buffet", "hai san", "seafood",
        "bbq", "bistro", "lau nuong", "grill",
    ))


def _is_market_place(place):
    name = fold_text(place.get("name"))
    category = fold_text(place.get("category"))
    return category == fold_text("Mua sắm") or bool(re.match(r"^(?:cho|cho dem)\b", name))


def _is_cafe_place(place):
    name = fold_text(place.get("name"))
    category = fold_text(place.get("category"))
    return category in {fold_text("Cafe"), fold_text("Cà phê"), "coffee"} or bool(
        re.search(r"\b(?:cafe|coffee|quan ca phe|quan cafe)\b", name)
    )


def _open_during(place, start_time, end_time, require_known=False):
    """Use published opening-hour ranges when present; unknown hours are allowed only for daytime slots."""
    text = fold_text(place.get("opening_hours"))
    if any(term in text for term in ("ca ngay", "24/7", "24 gio")):
        return True
    ranges = []
    for match in re.finditer(r"(?<!\d)(\d{1,2})\s*[:h]\s*(\d{2})\s*[-–to den]+\s*(\d{1,2})\s*[:h]\s*(\d{2})(?!\d)", text):
        start = int(match.group(1)) * 60 + int(match.group(2))
        end = int(match.group(3)) * 60 + int(match.group(4))
        if end <= start:
            end += 24 * 60
        ranges.append((start, end))
    if not ranges:
        return not require_known
    wanted_start = int(start_time[:2]) * 60 + int(start_time[3:5])
    wanted_end = int(end_time[:2]) * 60 + int(end_time[3:5])
    if wanted_end <= wanted_start:
        wanted_end += 24 * 60
    return any(start <= wanted_start and end >= wanted_end for start, end in ranges)


@app.get("/api/chat/places")
def chat_places(limit: int = Query(150, ge=1, le=500), gallery: bool = Query(False)):
    """Return ready-to-use crawled places for the chat UI, without coordinate requirements."""
    if DATA_BACKEND == "mongodb":
        return []
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if gallery:
                cur.execute("""
                    SELECT id, province_code, province_name, name, category, price_text,
                           price_min, price_max, address, opening_hours, image_url,
                           image_source_url, source_urls, currency
                    FROM place_crawl_staging
                    WHERE status = 'ready_for_use'
                      AND category IN ('Tham quan', 'Thiên nhiên', 'Vui chơi', 'Giải trí')
                      AND NULLIF(BTRIM(address), '') IS NOT NULL
                      AND NULLIF(BTRIM(image_url), '') IS NOT NULL
                      -- Decorative rails should show attractions, not lodging
                      -- records accidentally tagged as sightseeing, or UI assets.
                      AND name !~* '(khách sạn|hotel|homestay|hostel|motel|nhà nghỉ|căn hộ|apartment|condotel|resort|villa)'
                      AND COALESCE(image_source_url, '') !~* '(messenger-chat|facebook-chat|/logo([._/-]|$)|favicon|minus_icon|google-play|play\.google|app-store|apps\.apple|icon[_-](chat|logo|messenger))'
                      AND image_url !~* '(google[-_]?maps|/maps/|map[_-]?(image|anh|tile)|ban[-_]?do|openstreetmap|mapbox)'
                      AND image_url !~* '(youtube|youtu\.be|ytimg|homestay|hotel|resort|bedroom|bed[_-]?room|nha-nghi|khach-san)'
                      -- Crawlers sometimes assign social buttons or avatars as
                      -- a place photo (e.g. iVivu's Zalo icon for Cù Lao Xanh).
                      AND image_url !~* '(zalo|messenger|whatsapp|telegram|wechat|viber|tiktok|youtube|logo|avatar|favicon|placeholder|gravatar|icon[_-]?(chat|social|share))'
                      AND COALESCE(image_source_url, '') !~* '(/social/|/icon/|/logo|favicon|zalo|messenger|whatsapp|telegram|wechat|viber|tiktok|youtube|gravatar|avatar|placeholder)'
                    ORDER BY RANDOM()
                    LIMIT %s
                """, (limit,))
            else:
                cur.execute("""
                    SELECT id, province_code, province_name, name, category, price_text,
                           price_min, price_max, address, opening_hours, image_url,
                           image_source_url, source_urls, currency
                    FROM place_crawl_staging
                    WHERE status = 'ready_for_use'
                      AND NULLIF(BTRIM(address), '') IS NOT NULL
                    ORDER BY province_name, category, id
                    LIMIT %s
                """, (limit,))
            rows = cur.fetchall()
        if gallery:
            rows = [row for row in rows
                    if not _gallery_image_source_mismatches_place(row)
                    and not _staging_photo_is_clearly_wrong(row[3], row[4], row[10], row[11])]
        return [_staging_place_payload(row) for row in rows]
    finally:
        conn.close()


def _gallery_image_source_mismatches_place(row) -> bool:
    """Skip gallery photos whose source clearly points to a different province."""
    source = " " + re.sub(r"[^a-z0-9]+", " ", fold_text(unquote(str(row[11] or "")))) + " "
    own_province = str(row[2] or "").strip()
    own_owner = _LEGACY_PROVINCE_OWNER.get(own_province, own_province)
    candidates = (*_CURRENT_PROVINCE_NAMES, *_LEGACY_PROVINCE_OWNER.keys())
    for candidate in candidates:
        owner = _LEGACY_PROVINCE_OWNER.get(candidate, candidate)
        if owner == own_owner:
            continue
        candidate_tokens = re.sub(r"[^a-z0-9]+", " ", fold_text(candidate)).strip()
        if not candidate_tokens or len(candidate_tokens) < 4:
            continue
        if f" {candidate_tokens} " in source:
            return True
    return False


@app.post("/api/chat-plan")
def create_chat_plan(req: ChatPlanRequest):
    """Build a chat itinerary from ready staged records using addresses only."""
    if DATA_BACKEND == "mongodb":
        return {"success": False, "message": "Dữ liệu crawl sẵn sàng hiện được lưu trong DB PostgreSQL."}
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT name FROM provinces WHERE code = %s LIMIT 1", (req.province_code,))
            province_row = cur.fetchone()
            requested_province_name = province_row[0] if province_row else None
            cur.execute("""
                SELECT id, province_code, province_name, name, category, price_text,
                       price_min, price_max, address, opening_hours, image_url,
                       image_source_url, source_urls, currency
                FROM place_crawl_staging
                WHERE province_code = %s AND status = 'ready_for_use'
                  AND NULLIF(BTRIM(address), '') IS NOT NULL
                ORDER BY category, price_min NULLS LAST, name
            """, (req.province_code,))
            rows = cur.fetchall()
        places = [_staging_place_payload(row) for row in rows]
    finally:
        conn.close()

    if not requested_province_name:
        return {"success": False, "message": "Không tìm thấy tỉnh/thành bạn đã chọn trong dữ liệu."}

    # Some scraped rows were imported under the selected province code even
    # though their venue name/address clearly belongs elsewhere (e.g. a Quảng
    # Ngãi guesthouse labeled as Đà Nẵng). Exclude those rows from plans without
    # changing the underlying records.
    places = [
        place for place in places
        if _staging_record_matches_province(place, requested_province_name)
    ]

    stays = [place for place in places if normalize(place["category"]) == normalize("Lưu trú")]
    activities = [place for place in places if normalize(place["category"]) != normalize("Lưu trú")]
    if not stays:
        return {"success": False, "message": "DB chưa có nơi lưu trú sẵn sàng với địa chỉ cụ thể ở tỉnh này."}
    if not activities:
        return {"success": False, "message": "DB chưa có địa điểm hoạt động sẵn sàng ở tỉnh này."}

    if req.hotel_id is not None:
        stays = [place for place in stays if int(place["id"]) == int(req.hotel_id)]
        if not stays:
            return {"success": False, "message": "Khách sạn hiện tại không còn trong dữ liệu sẵn sàng của DB."}
    avoided_places = {int(value) for value in req.avoid_place_ids}

    preference_aliases = {
        "thien nhien": {"thien nhien", "tham quan"},
        "vui choi": {"vui choi", "giai tri", "tham quan"},
        "tham quan": {"tham quan", "thien nhien"},
        "an uong": {"an uong"}, "cafe": {"cafe"},
        "mua sam": {"mua sam"},
    }
    day_budget_per_person = (req.budget / (req.days * req.people)) if req.budget else None

    def vary_ranked(items, label):
        """Rotate a small, similarly ranked shortlist so new plans are not clones."""
        ordered = list(items)
        if not req.strict_budget and req.variation_seed and len(ordered) > 1:
            window = min(5, len(ordered))
            offset = (abs(req.variation_seed) + sum(map(ord, str(label)))) % window
            head = ordered[:window]
            ordered = head[offset:] + head[:offset] + ordered[window:]
        return ordered

    def budget_rank(place):
        category = fold_text(place["category"])
        bounds = _staging_price_bounds_vnd(place)
        amount = (bounds[0] if req.strict_budget and category == fold_text("Lưu trú") else
                  ((bounds[0] + bounds[1]) / 2 if bounds[0] is not None else None))
        if req.strict_budget:
            if category == fold_text("Lưu trú") and amount is not None:
                amount *= max(req.days, 1)
            elif category == fold_text("Ăn uống") and amount is not None:
                amount *= 2
            return (amount is None, amount if amount is not None else float("inf"), place["name"])
        if category == fold_text("Ăn uống") and amount is not None:
            amount *= 2
        if day_budget_per_person is None:
            target = None
        elif category == fold_text("Lưu trú"):
            target = req.budget * 0.35 / max(req.days, 1)
        elif category in (fold_text("Ăn uống"), fold_text("Cafe")):
            target = day_budget_per_person / 2
        else:
            target = day_budget_per_person
        over_budget = amount is not None and target is not None and amount > target
        return (amount is None, over_budget, amount if amount is not None else float("inf"), place["name"])

    required_place_ids = {int(value) for value in req.required_place_ids}

    def place_rank(place):
        place_id = int(place["id"])
        # Keep requested must-see places first, then prefer places not used in
        # the previous plan. Reuse remains possible when this province has few
        # ready records for the requested interests.
        return (place_id not in required_place_ids,
                place_id in avoided_places and not req.strict_budget,
                budget_rank(place),
                fold_text(place.get("name")))

    avoided_hotels = {int(value) for value in req.avoid_hotel_ids}
    stays.sort(key=lambda place: (place["id"] in avoided_hotels and not req.strict_budget, budget_rank(place)))
    preferred_stays = vary_ranked([place for place in stays if place["id"] not in avoided_hotels], "hotel")
    fallback_stays = vary_ranked([place for place in stays if place["id"] in avoided_hotels], "hotel-fallback")
    stays = preferred_stays + fallback_stays
    preferences = [fold_text(item) for item in req.preferences if str(item).strip()]
    food_categories = {fold_text("Ăn uống"), fold_text("Cafe"), fold_text("Cà phê")}
    def looks_like_food_or_drink(place):
        name = f" {fold_text(place.get('name'))} "
        return any(token in name for token in (
            " pho ", " bun ", " mi quang ", " cao lau ", " banh ", " com ", " xoi ",
            " hai san ", " nha hang ", " quan an ", " am thuc ", " lau ", " restaurant ",
            " coffee ", " cafe ", " ca phe ", " tra sua ", " sua chua ", " an vat ",
        ))

    activity_only = [place for place in activities
                     if fold_text(place["category"]) not in food_categories
                     and not _is_cafe_place(place) and not looks_like_food_or_drink(place)]
    required_places = [place for place in activity_only if int(place["id"]) in required_place_ids]
    preference_groups = [
        sorted(
            [place for place in activity_only
             if fold_text(place["category"]) in preference_aliases.get(pref, {pref})],
            key=place_rank,
        )
        for pref in preferences
    ]
    preference_groups = [vary_ranked(group, f"preference-{index}")
                         for index, group in enumerate(preference_groups) if group]
    food_preferences_available = all(
        pref in {fold_text("Ăn uống"), fold_text("Cafe")} and any(
            fold_text(place["category"]) == pref for place in activities
        )
        for pref in preferences
    )
    if preferences and not preference_groups and not food_preferences_available:
        return {
            "success": False,
            "message": "DB hiện chưa có địa điểm sẵn sàng đúng nhóm sở thích bạn nhắn ở tỉnh này. Bạn thử nhắn sở thích khác nhé.",
        }
    if preference_groups:
        candidates, seen_ids = [], set()
        for index in range(max(map(len, preference_groups))):
            for group in preference_groups:
                if index < len(group) and group[index]["id"] not in seen_ids:
                    candidates.append(group[index])
                    seen_ids.add(group[index]["id"])
    else:
        candidates = sorted(activity_only, key=place_rank)
    candidates = sorted(candidates, key=lambda place: int(place["id"]) not in required_place_ids)
    candidates = vary_ranked(candidates, "general-activities")
    food_places = vary_ranked(sorted([p for p in activities if fold_text(p["category"]) == fold_text("Ăn uống")], key=place_rank), "food")
    meals = [place for place in food_places if _main_meal_suitable(place)]
    # Keep breakfast and lunch on the existing meal list. For dinner, prefer
    # full-service restaurants whose DB prices can be estimated, ranked by the
    # same budget-aware score as other places. Fall back to existing meal data
    # only when this province has no priced restaurant records.
    priced_dinner_restaurants = sorted(
        [place for place in meals
         if _restaurant_suitable(place) and _staging_price_midpoint_vnd(place) is not None],
        key=place_rank,
    )
    dinner_places = priced_dinner_restaurants or meals
    snacks = [place for place in food_places if place not in meals]
    breakfasts = [place for place in meals if _breakfast_suitable(place)]
    cafes = vary_ranked(sorted([p for p in activities if _is_cafe_place(p)], key=place_rank), "cafe")
    activity_pools = {}
    for place in activity_only:
        activity_pools.setdefault(fold_text(place["category"]), []).append(place)
    for pool in activity_pools.values():
        pool.sort(key=place_rank)
    activity_pools = {category: vary_ranked(pool, f"activity-{category}")
                      for category, pool in activity_pools.items()}
    itinerary = []
    used_meals, used_cafes, used_activities = set(), set(), set()
    food_interest = any(pref in {"an uong", "am thuc", "mon an", "food"} for pref in preferences)
    cafe_interest = any(pref in {"cafe", "ca phe", "coffee"} for pref in preferences)
    shopping_interest = any(pref in {"mua sam", "shopping", "cho"} for pref in preferences)
    evening_preferences = []
    if cafe_interest:
        evening_preferences.append("Cafe")
    if any(pref in {"mua sam", "shopping"} for pref in preferences):
        evening_preferences.append("Mua sắm")
    if any(pref in {"vui choi", "giai tri", "tham quan", "thien nhien"} for pref in preferences):
        evening_preferences.append("Vui chơi")
    night_categories_base = list(evening_preferences)
    if any(pref in {"tham quan", "thien nhien"} for pref in preferences):
        night_categories_base.append("Tham quan")
    full_day_destination = next((place for place in required_places
                                 if any(term in fold_text(f"{place.get('name', '')} {place.get('address', '')}")
                                        for term in ("ba na", "sun world"))), None)
    full_day_number = 2 if full_day_destination and req.days > 1 else 1

    def take_distinct(pool, used, start_time=None, end_time=None, require_hours=False):
        for place in pool:
            if (place["id"] not in used and (not start_time or _open_during(
                    place, start_time, end_time, require_known=require_hours))):
                used.add(place["id"])
                return place
        return None

    def take_dinner(start_time, end_time):
        place = take_distinct(dinner_places, used_meals, start_time, end_time)
        if place is None and dinner_places is not meals:
            place = take_distinct(meals, used_meals, start_time, end_time)
        return place

    def take_activity(preferred_categories=None, start_time=None, end_time=None, require_hours=False):
        preferred_categories = preferred_categories or []
        for category in preferred_categories:
            folded_category = fold_text(category)
            pool = cafes if folded_category == fold_text("Cafe") else activity_pools.get(folded_category, [])
            used = used_cafes if folded_category == fold_text("Cafe") else used_activities
            for place in pool:
                if _is_market_place(place):
                    continue
                if (place["id"] not in used and (not start_time or _open_during(
                        place, start_time, end_time, require_known=require_hours))):
                    used.add(place["id"])
                    return place
        if require_hours:
            return None
        for pool in (candidates, activity_only):
            for place in pool:
                if _is_market_place(place):
                    continue
                if place["id"] in used_activities:
                    continue
                if start_time and not _open_during(place, start_time, end_time, require_known=require_hours):
                    continue
                used_activities.add(place["id"])
                return place
        return None

    def activity_theme(place):
        """Collapse near-identical attractions so one day feels like a varied trip."""
        name = fold_text(place.get("name"))
        if _is_market_place(place): return "market"
        if any(word in name for word in ("bai bien", "bai tam", "bai but", "vung but", "my khe", "non nuoc", "bac my an", "pham van dong")): return "beach"
        if any(word in name for word in ("bao tang", "museum")): return "museum"
        if any(word in name for word in ("chua ", "linh ung", "tam thai")): return "temple"
        if any(word in name for word in ("cau rong", "cau song han", "cau thuan phuoc", "cau tran thi ly")): return "bridge"
        if any(word in name for word in ("tuong da ca chep", "cau tau tinh yeu", "ben du thuyen")): return "riverside"
        if any(word in name for word in ("son tra", "hai van", "ngu hanh son", "deo ", "ho hoa trung", "ran nam o", "gieng troi")): return "nature"
        if any(word in name for word in ("cong vien", "park")): return "park"
        if any(word in name for word in ("lang co", "lang bich hoa", "lang da", "lang chieu")): return "local culture"
        return f"place:{place['id']}"

    def place_area(place):
        address = fold_text(place.get("address"))
        for area in ("son tra", "ngu hanh son", "hai chau", "thanh khe", "lien chieu", "cam le", "hoa vang"):
            if area in address:
                return area
        return ""

    def night_eligible(place, start_time, end_time):
        if _open_during(place, start_time, end_time, require_known=True):
            return True
        name = fold_text(place.get("name"))
        hours = fold_text(place.get("opening_hours"))
        # The source explicitly says this landmark is freely accessible; bridge and
        # riverside visits are evening-appropriate without claiming a closing time.
        return (
            place.get("category") == "Tham quan"
            and any(word in name for word in ("cau rong", "cau song han", "cau thuan phuoc", "cau tran thi ly", "tuong da ca chep", "cau tau tinh yeu"))
            and ("mien phi tham quan" in fold_text(place.get("price_text")) or "mo cua tham quan tu do" in hours)
        )

    for day_number in range(1, req.days + 1):
        schedule = []
        day_themes = set()
        day_area = ""

        def add_slot(activity, start_time, end_time, place):
            if place is None:
                return
            schedule.append({"order": len(schedule) + 1, "activity": activity,
                             "start_time": start_time, "end_time": end_time,
                             "type": place["category"] if place else activity,
                             "place": place})

        def take_day_activity(preferred_categories=None, start_time=None, end_time=None, require_hours=False):
            nonlocal day_area
            preferred_categories = preferred_categories or []
            for place in required_places:
                theme = activity_theme(place)
                if place["id"] in used_activities or theme in day_themes:
                    continue
                if start_time and not _open_during(place, start_time, end_time, require_known=require_hours):
                    continue
                used_activities.add(place["id"]); day_themes.add(theme)
                if not day_area: day_area = place_area(place)
                return place
            for category in preferred_categories:
                folded_category = fold_text(category)
                pool = cafes if folded_category == fold_text("Cafe") else activity_pools.get(folded_category, [])
                used = used_cafes if folded_category == fold_text("Cafe") else used_activities
                for place in sorted(pool, key=lambda item: (bool(day_area) and place_area(item) != day_area, place_rank(item))):
                    theme = activity_theme(place)
                    if _is_market_place(place) or theme in day_themes or place["id"] in used:
                        continue
                    if folded_category == fold_text("Tham quan") and night_eligible(place, "20:00", "21:00"):
                        continue
                    if start_time and not _open_during(place, start_time, end_time, require_known=require_hours):
                        continue
                    used.add(place["id"]); day_themes.add(theme)
                    if not day_area: day_area = place_area(place)
                    return place
            if require_hours:
                return None
            for pool in (candidates, activity_only):
                for place in sorted(pool, key=lambda item: (bool(day_area) and place_area(item) != day_area, place_rank(item))):
                    theme = activity_theme(place)
                    if _is_market_place(place) or theme in day_themes or place["id"] in used_activities:
                        continue
                    if night_categories_base and night_eligible(place, "20:00", "21:00"):
                        continue
                    if start_time and not _open_during(place, start_time, end_time):
                        continue
                    used_activities.add(place["id"]); day_themes.add(theme)
                    if not day_area: day_area = place_area(place)
                    return place
            return None

        if day_number == 1:
            add_slot("Đến nơi lưu trú, gửi hành lý", "07:00", "07:30", stays[0])

        if full_day_destination and day_number == full_day_number:
            destination_theme = activity_theme(full_day_destination)
            used_activities.add(full_day_destination["id"])
            day_themes.add(destination_theme)
            add_slot("Ăn sáng", "07:00", "08:00", take_distinct(breakfasts, used_meals, "07:00", "08:00"))
            add_slot(f"Tham quan trọn buổi sáng · {full_day_destination['name']}",
                     "08:30", "12:00", full_day_destination)
            schedule.append({
                "order": len(schedule) + 1,
                "activity": "Ăn trưa tại khu tham quan (DB chưa có quán và giá xác thực; chưa tính vào chi phí)",
                "start_time": "12:00", "end_time": "13:00", "type": "Ăn uống", "place": None,
            })
            schedule.append({
                "order": len(schedule) + 1,
                "activity": f"Tiếp tục khám phá {full_day_destination['name']} · vé đã tính ở mục trước",
                "start_time": "13:00", "end_time": "16:30", "type": "Tham quan", "place": None,
                "price_exempt": True,
            })
            add_slot("Trở về nơi lưu trú, nghỉ ngơi sau chuyến đi", "16:30", "19:00", stays[0])
            add_slot("Ăn tối", "19:00", "20:00", take_dinner("19:00", "20:00"))
            add_slot("Về khách sạn, kết thúc ngày", "20:00", "20:15", stays[0])
            itinerary.append({"day": day_number, "title": f"Gợi ý ngày {day_number} · tham quan trọn ngày", "schedule": schedule})
            continue

        market_candidates = [place for place in activities if _is_market_place(place)]
        market_candidates.sort(key=place_rank)
        market_candidates = vary_ranked(market_candidates, "market")
        market = take_distinct(market_candidates, used_activities, "15:00", "16:00") if shopping_interest else None

        add_slot("Ăn sáng", "07:30", "08:30", take_distinct(breakfasts, used_meals, "07:30", "08:30"))
        add_slot("Tham quan theo sở thích", "08:30", "10:00", take_day_activity(start_time="08:30", end_time="10:00"))
        if food_interest and not market:
            morning_extra = take_distinct(snacks, used_meals, "10:00", "10:30")
            add_slot("Ăn nhẹ", "10:00", "10:30", morning_extra)
        else:
            morning_extra = take_distinct(cafes, used_cafes, "10:00", "11:15") if cafe_interest else None
            if not morning_extra:
                morning_extra = take_day_activity(start_time="10:00", end_time="11:15")
            morning_label = "Uống cafe" if morning_extra and fold_text(morning_extra["category"]) == fold_text("Cafe") else "Đi chơi tiếp"
            add_slot(morning_label, "10:00", "10:30", morning_extra)
        if day_number > 1:
            add_slot("Tham quan tiếp", "10:30", "11:30", take_day_activity(start_time="10:30", end_time="11:30"))

        add_slot("Ăn trưa", "11:30", "12:30", take_distinct(meals, used_meals, "11:30", "12:30"))
        add_slot("Về khách sạn nghỉ ngơi", "12:30", "13:30", stays[0])
        add_slot("Tham quan buổi chiều", "13:30", "15:00", take_day_activity(start_time="13:30", end_time="15:00"))
        market_label = "Dạo chợ / mua sắm" if market else "Ăn nhẹ"
        if not market:
            market = take_distinct(snacks, used_meals, "15:00", "16:00")
        add_slot(market_label, "15:00", "16:00", market)
        add_slot("Tiếp tục vui chơi, tham quan", "16:00", "17:00", take_day_activity(start_time="16:00", end_time="17:00"))
        add_slot("Về khách sạn nghỉ ngơi, tắm rửa", "17:00", "19:00", stays[0])
        add_slot("Ăn tối", "19:00", "20:00", take_dinner("19:00", "20:00"))

        # Two distinct, preference-matched outings after dinner. A day visit to a
        # market rules out another market, and related beaches/bridges aren't paired.
        night_categories = list(night_categories_base)
        if market:
            night_categories = [category for category in night_categories if fold_text(category) != fold_text("Mua sắm")]

        def take_night(categories, start_time, end_time):
            for category in categories:
                folded = fold_text(category)
                if folded == fold_text("Cafe"):
                    pool = cafes
                elif folded == fold_text("Mua sắm"):
                    pool = market_candidates
                else:
                    pool = activity_pools.get(folded, [])
                used = used_cafes if folded == fold_text("Cafe") else used_activities
                for place in pool:
                    theme = activity_theme(place)
                    if theme in day_themes or place["id"] in used or (_is_market_place(place) and market):
                        continue
                    if not night_eligible(place, start_time, end_time):
                        continue
                    used.add(place["id"]); day_themes.add(theme)
                    return place
            return None

        first_night = take_night(night_categories, "20:00", "21:00")
        if first_night:
            label = "Uống cafe buổi tối" if _is_cafe_place(first_night) else "Dạo chơi buổi tối"
            add_slot(label, "20:00", "21:00", first_night)
        # Sightseeing can supply two different evening experiences (for example a
        # riverside landmark and a bridge). Avoid a back-to-back double-cafe stop.
        second_categories = [category for category in night_categories
                             if not (first_night and _is_cafe_place(first_night)
                                     and fold_text(category) == fold_text("Cafe"))]
        second_night = take_night(second_categories, "21:00", "22:15")
        if second_night:
            label = "Uống cafe / thư giãn" if _is_cafe_place(second_night) else "Trải nghiệm buổi tối"
            add_slot(label, "21:00", "22:15", second_night)
        add_slot("Về khách sạn, kết thúc ngày", "22:30", "22:45", stays[0])
        itinerary.append({"day": day_number, "title": f"Gợi ý ngày {day_number}", "schedule": schedule})

    hotel = stays[0]
    hotel_days = max(req.days, 1)
    hotel_amount = _staging_price_midpoint_vnd(hotel)
    total_known = 0.0
    per_person_known = 0.0
    missing_price_count = 0
    if hotel_amount is None:
        missing_price_count += 1
    else:
        lodging_total = hotel_amount * hotel_days
        total_known += lodging_total
        per_person_known += lodging_total / max(req.people, 1)
    for day in itinerary:
        for slot in day["schedule"]:
            place = slot.get("place")
            if slot.get("price_exempt"):
                continue
            if not place:
                missing_price_count += 1
                continue
            if fold_text(place.get("category")) == fold_text("Lưu trú"):
                continue
            amount = _per_person_plan_cost(place, slot["activity"], req.people)
            if amount is None:
                missing_price_count += 1
                continue
            slot["estimated_per_person"] = round(amount)
            per_person_known += amount
            total_known += amount * req.people
    known_budget_overrun = req.budget is not None and total_known > req.budget
    budget_remaining = (
        req.budget - total_known
        if req.budget is not None and (missing_price_count == 0 or known_budget_overrun)
        else None
    )
    cost_summary = {
        "currency": "VND",
        "known_cost": round(total_known),
        "total_estimated_trip_cost": round(total_known),
        "per_person_estimate": round(per_person_known),
        "lodging_cost": round(hotel_amount * hotel_days) if hotel_amount is not None else None,
        "budget": req.budget,
        "remaining_budget": round(budget_remaining) if budget_remaining is not None else None,
        "within_budget": (
            False if known_budget_overrun
            else (True if req.budget is not None and missing_price_count == 0 else None)
        ),
        "unpriced_places": missing_price_count,
        "partial": missing_price_count > 0,
        "hotel_days": hotel_days,
        "hotel_nights": hotel_days,
        "estimate_basis": "Dùng khoảng giữa giá trong DB; chi phí mỗi mục ăn uống tính giá đó nhân 2 cho mỗi người. Lưu trú tính giá trung bình mỗi đêm nhân số ngày/đêm, không nhân số người.",
    }
    result = {
        "plan_version": 11,
        "province_code": req.province_code,
        "days": req.days, "people": req.people, "budget": req.budget,
        "preferences": req.preferences, "hotel": hotel,
        "itinerary": itinerary,
        "cost_summary": cost_summary,
        "selected_activity_count": sum(1 for day in itinerary for slot in day["schedule"] if slot.get("place")),
        "unmatched_preferences": [str(pref) for pref in req.preferences
                                  if not any(fold_text(place["category"]) in preference_aliases.get(fold_text(pref), {fold_text(pref)})
                                             for place in activities)],
        "planning_basis": "Địa chỉ và bản ghi ready_for_use trong DB; không sử dụng tọa độ.",
        "price_note": "Buổi sáng 5 mục tính cả bữa trưa; buổi chiều 4 mục; sau bữa tối có tối đa hai hoạt động riêng biệt rồi về nơi lưu trú. Mỗi ngày tránh lặp nhóm điểm gần giống nhau; không lặp địa điểm hoặc xếp hai chợ trong ngày; nếu đã đi chợ thì bỏ ăn nhẹ riêng. Buổi tối ưu tiên sở thích và giờ hoạt động trong DB. Giá gốc lấy từ DB; lưu trú dùng giá trung bình mỗi đêm × số đêm × số người.",
    }
    return {"success": True, "count": result["selected_activity_count"], "data": result}


@app.post("/api/chat-plan/pdf")
def download_chat_plan_pdf(req: ChatPlanPDFRequest):
    """Render the selected chat plan as a downloadable, Vietnamese-ready PDF."""
    plan = req.plan
    destination = html.escape(str(plan.get("province_name") or plan.get("province") or "Chuyến đi"))
    days = plan.get("itinerary") if isinstance(plan.get("itinerary"), list) else []
    hotel = plan.get("hotel") if isinstance(plan.get("hotel"), dict) else {}
    costs = plan.get("cost_summary") if isinstance(plan.get("cost_summary"), dict) else {}

    def safe(value):
        return html.escape(str(value if value is not None else ""))

    pdf_image_cache = {}
    pdf_image_count = [0]
    pdf_image_limit = 120

    def pdf_image(value, alt, class_name="photo"):
        image_url = str(value or "").strip()
        if not re.fullmatch(r"/media/(?:crawled/places|places)/[A-Za-z0-9_./-]+\.(?:jpe?g|png|webp|gif)", image_url, re.I):
            return ""
        if ".." in image_url.split("/"):
            return ""
        thumbnail = pdf_image_cache.get(image_url)
        if thumbnail is None:
            if pdf_image_count[0] >= pdf_image_limit:
                return ""
            try:
                import base64
                from io import BytesIO
                from urllib.request import urlopen
                from PIL import Image

                # Keep every available place photo, but embed a compressed
                # thumbnail rather than making Chromium decode original files.
                with urlopen(f"http://frontend{image_url}", timeout=3) as response:
                    raw_image = response.read(4 * 1024 * 1024 + 1)
                if len(raw_image) > 4 * 1024 * 1024:
                    return ""
                with Image.open(BytesIO(raw_image)) as source_image:
                    source_image.thumbnail((400, 260), Image.Resampling.LANCZOS)
                    if source_image.mode not in ("RGB", "L"):
                        source_image = source_image.convert("RGB")
                    buffer = BytesIO()
                    source_image.save(buffer, format="JPEG", quality=66, optimize=True)
                thumbnail = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
                pdf_image_cache[image_url] = thumbnail
                pdf_image_count[0] += 1
            except Exception:
                return ""
        try:
            return f'<img class="{class_name}" src="{thumbnail}" alt="{safe(alt)}">'
        except Exception:
            return ""

    def price(value):
        try:
            return f"{float(value):,.0f} ₫".replace(",", ".")
        except (TypeError, ValueError):
            return "Chưa có dữ liệu giá"

    day_sections = []
    for day in days:
        schedule = day.get("schedule") if isinstance(day, dict) else []
        entries = []
        for slot in schedule or []:
            place = slot.get("place") if isinstance(slot.get("place"), dict) else {}
            time_label = " – ".join(filter(None, [str(slot.get("start_time") or ""), str(slot.get("end_time") or "")]))
            estimated = slot.get("estimated_per_person")
            cost_label = f"Chi phí dự kiến: {price(estimated)}/người" if estimated is not None else ""
            place_meta = "<br>".join(filter(None, [safe(place.get("address")), safe(place.get("price_text")), safe(cost_label)]))
            photo = pdf_image(place.get("image_url"), place.get("name") or "Ảnh địa điểm") if place else ""
            entries.append(
                '<div class="stop"><div class="time">' + safe(time_label) + '</div><div class="stop-main">'
                + (('<div class="stop-content">' + photo + '<div><strong>' + safe(slot.get("activity") or slot.get("type") or "Hoạt động") + '</strong>'
                    + '<div class="place">' + safe(place.get("name")) + '</div><div class="meta">' + place_meta + '</div></div></div>') if place else '<strong>' + safe(slot.get("activity") or slot.get("type") or "Hoạt động") + '</strong>')
                + '</div></div>'
            )
        day_sections.append(
            '<section class="day"><div class="day-head"><span>NGÀY ' + safe(day.get("day")) + '</span>'
            '<h2>' + safe(day.get("title") or "Lịch trình khám phá") + '</h2></div>'
            + "".join(entries) + '</section>'
        )

    hotel_block = ""
    if hotel:
        hotel_block = (
            '<section class="stay">' + pdf_image(hotel.get("image_url"), hotel.get("name") or "Ảnh nơi lưu trú", "hotel-photo")
            + '<div><small>NƠI LƯU TRÚ</small><h2>' + safe(hotel.get("name")) + '</h2>'
            '<p>' + safe(hotel.get("address")) + '</p><p>' + safe(hotel.get("price_text")) + '</p></div></section>'
        )
    summary_items = [
        ("Lưu trú", costs.get("lodging_cost")),
        ("Tổng chuyến", costs.get("total_estimated_trip_cost")),
        ("Ngân sách", costs.get("budget")),
        ("Còn lại", costs.get("remaining_budget")),
    ]
    summary = "".join(
        '<div><small>' + safe(label) + '</small><b>' + (price(amount) if amount is not None else "Chưa đủ dữ liệu") + '</b></div>'
        for label, amount in summary_items
    )
    document = f"""<!doctype html><html lang="vi"><meta charset="utf-8"><style>
      @page {{ size:A4; margin:16mm 15mm 18mm; }}
      * {{ box-sizing:border-box; }} body {{ margin:0; color:#193f3e; font:12px Arial,sans-serif; }}
      .cover {{ padding:18px 20px; border-radius:16px; color:#fff; background:linear-gradient(120deg,#116b63,#2b9b83); }}
      .eyebrow {{ font-size:9px; letter-spacing:2px; opacity:.8; }} h1 {{ margin:9px 0 6px; font-size:27px; }}
      .cover p {{ margin:0; color:#e5f5ef; }} .summary {{ display:grid; grid-template-columns:repeat(4,1fr); gap:8px; margin:12px 0; }}
      .summary>div,.stay {{ padding:10px; border:1px solid #dcebe5; border-radius:11px; background:#f7fbf8; }}
      .summary small,.stay small {{ display:block; color:#66877e; font-size:9px; }} .summary b {{ display:block; margin-top:5px; font-size:12px; }}
      .stay {{ display:flex; align-items:center; gap:12px; margin:10px 0 15px; }} .stay h2 {{ margin:5px 0; font-size:15px; }} .stay p {{ margin:4px 0; color:#5d7773; }}
      .day {{ margin-top:13px; break-inside:avoid; }} .day-head {{ padding:9px 12px; border-radius:10px; background:#eaf5ef; }}
      .day-head span {{ color:#328270; font-size:9px; font-weight:bold; letter-spacing:1px; }} .day-head h2 {{ margin:3px 0 0; font-size:15px; }}
      .stop {{ display:grid; grid-template-columns:75px 1fr; gap:10px; padding:9px 10px; border-bottom:1px solid #e9efeb; break-inside:avoid; }}
      .time {{ color:#438577; font-weight:bold; }} .place {{ margin-top:4px; font-weight:bold; }} .meta {{ margin-top:4px; color:#718581; font-size:10px; line-height:1.45; }}
      .stop-main {{ min-width:0; }} .stop-content {{ display:flex; align-items:flex-start; gap:10px; }}
      .photo,.hotel-photo {{ width:70px; height:54px; flex:0 0 70px; border-radius:7px; object-fit:cover; background:#e6efeb; }}
      .hotel-photo {{ width:100px; height:72px; flex-basis:100px; }}
      .footer {{ margin-top:14px; color:#80928d; font-size:9px; }}
    </style><base href="http://frontend/"><body><header class="cover"><div class="eyebrow">TRAVEL PATH · LỊCH TRÌNH CỦA BẠN</div>
      <h1>{destination} · {safe(plan.get('days') or len(days))} ngày</h1>
      <p>{safe(plan.get('people') or 1)} người · Sở thích: {safe(', '.join(plan.get('preferences') or [])) or 'Khám phá đa dạng'}</p></header>
      <div class="summary">{summary}</div>{hotel_block}{''.join(day_sections)}
      <div class="footer">Lịch trình và giá được tổng hợp từ dữ liệu địa điểm hiện có trong Travel Path.</div>
    </body></html>"""
    browser = None
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(args=["--no-sandbox"])
            page = browser.new_page()
            # Remote or missing gallery images can keep Chromium in the
            # network-idle state indefinitely. The document is self-contained
            # apart from photos, so render it as soon as HTML is ready and wait
            # briefly for photos without letting a slow image break the PDF.
            page.set_content(document, wait_until="domcontentloaded", timeout=15000)
            try:
                page.wait_for_function(
                    "Array.from(document.images).every(image => image.complete)",
                    timeout=8000,
                )
            except Exception:
                pass
            pdf_bytes = page.pdf(
                format="A4", print_background=True,
                margin={"top": "16mm", "right": "15mm", "bottom": "18mm", "left": "15mm"},
            )
            browser.close()
            browser = None
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": 'attachment; filename="travel-path-plan.pdf"'},
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Không tạo được PDF: {exc}") from exc
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass


@app.post("/api/chat-plan/change-hotel")
def change_chat_plan_hotel(req: ChangeChatHotelRequest):
    """Swap the stay on a staging-data chat plan without rebuilding its itinerary."""
    if DATA_BACKEND == "mongodb":
        return {"success": False, "message": "Dữ liệu crawl sẵn sàng hiện được lưu trong DB PostgreSQL."}
    plan = deepcopy(req.current_plan)
    old_hotel = plan.get("hotel") or {}
    if not plan.get("itinerary") or str(plan.get("province_code") or req.province_code) != str(req.province_code):
        return {"success": False, "message": "Mình chưa thấy plan hiện tại để giữ nguyên. Bạn tạo plan trước rồi nhắn đổi khách sạn nhé."}

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT name FROM provinces WHERE code = %s LIMIT 1", (req.province_code,))
            province_row = cur.fetchone()
            requested_province_name = province_row[0] if province_row else None
            cur.execute("""
                SELECT id, province_code, province_name, name, category, price_text,
                       price_min, price_max, address, opening_hours, image_url,
                       image_source_url, source_urls, currency
                FROM place_crawl_staging
                WHERE province_code = %s AND status = 'ready_for_use'
                  AND NULLIF(BTRIM(address), '') IS NOT NULL
            """, (req.province_code,))
            rows = cur.fetchall()
        stays = [_staging_place_payload(row) for row in rows]
    finally:
        conn.close()

    stays = [
        place for place in stays
        if fold_text(place.get("category")) == fold_text("Lưu trú")
        and requested_province_name
        and _staging_record_matches_province(place, requested_province_name)
    ]
    old_id = old_hotel.get("id")
    old_name = fold_text(old_hotel.get("name"))
    avoided_ids = {int(value) for value in req.avoid_hotel_ids}
    if old_id is not None:
        avoided_ids.add(int(old_id))
    alternatives = [place for place in stays
                    if place.get("id") not in avoided_ids
                    and (not old_name or fold_text(place.get("name")) != old_name)]
    if not alternatives:
        return {"success": False, "message": "DB hiện không có khách sạn khác đủ dữ liệu địa chỉ trong tỉnh này."}

    def hotel_rank(place):
        amount = _staging_price_midpoint_vnd(place)
        return (amount is None, amount if amount is not None else float("inf"), fold_text(place.get("name")))

    alternatives.sort(key=hotel_rank)
    hotel = alternatives[0]
    for day in plan.get("itinerary", []):
        for slot in day.get("schedule", []):
            place = slot.get("place") or {}
            if (fold_text(place.get("category")) == fold_text("Lưu trú")
                    or (old_id is not None and str(place.get("id")) == str(old_id))):
                slot["place"] = hotel
                slot["type"] = hotel.get("category") or "Lưu trú"

    plan["hotel"] = hotel
    plan["province_code"] = req.province_code
    plan["days"] = req.days
    plan["people"] = req.people
    plan["budget"] = req.budget
    summary = plan.get("cost_summary") or {}
    people = max(int(req.people or 1), 1)
    stay_days = max(int(req.days or 1), 1)
    old_lodging = summary.get("lodging_cost")
    old_lodging = float(old_lodging) if old_lodging is not None else None
    new_rate = _staging_price_midpoint_vnd(hotel)
    new_lodging = new_rate * stay_days if new_rate is not None else None
    old_known = float(summary.get("known_cost") or summary.get("total_estimated_trip_cost") or 0)
    base_known = old_known - (old_lodging or 0)
    new_known = base_known + (new_lodging or 0)
    old_per_person = float(summary.get("per_person_estimate") or 0)
    new_per_person = old_per_person - ((old_lodging or 0) / people) + ((new_lodging or 0) / people)
    unpriced = int(summary.get("unpriced_places") or 0)
    if old_lodging is None:
        unpriced = max(0, unpriced - 1)
    if new_lodging is None:
        unpriced += 1
    budget = req.budget
    summary.update({
        "currency": "VND", "known_cost": round(new_known),
        "total_estimated_trip_cost": round(new_known),
        "per_person_estimate": round(new_per_person),
        "lodging_cost": round(new_lodging) if new_lodging is not None else None,
        "hotel_days": stay_days, "hotel_nights": stay_days, "budget": budget,
        "remaining_budget": round(budget - new_known) if budget is not None and unpriced == 0 else None,
        "within_budget": new_known <= budget if budget is not None and unpriced == 0 else None,
        "unpriced_places": unpriced, "partial": unpriced > 0,
    })
    plan["cost_summary"] = summary
    return {"success": True, "message": f"Đã đổi nơi lưu trú sang {hotel.get('name')} và giữ nguyên toàn bộ lịch trình.", "data": plan}


def _handle_staging_chat(req: ChatRequest, state: dict[str, Any]):
    """Keep conversational edits on the active crawl DB-backed plan."""
    message = req.message.strip()
    current_plan = state.get("plan") or {}
    folded_message = fold_text(message)
    current_people = int(state.get("people") or current_plan.get("people") or 1)
    people_mentioned = bool(re.search(r"\b(?:nguoi|people|person|travelers?)\b", folded_message))
    people_number = _extract_number(message, ["người", "nguoi", "person", "people", "travelers"])
    if people_number is None:
        written_count = re.search(r"\b(mot|hai|ba|bon|nam|sau|bay|tam|chin)\s*(?:nguoi|people|person)\b", folded_message)
        people_number = {"mot": 1, "hai": 2, "ba": 3, "bon": 4, "nam": 5, "sau": 6, "bay": 7, "tam": 8, "chin": 9}.get(written_count.group(1)) if written_count else None
    if people_number is not None and re.search(r"\b(?:them|tang them|add)\s+(?:\d+|mot|hai|ba|bon|nam)\s*(?:nguoi|people|person)", folded_message):
        people_number = current_people + people_number
    budget_mentioned = any(term in folded_message for term in (
        "ngan sach", "kinh phi", "chi phi", "budget", "trieu", "dong", "vnd", "nghin", "ngan"
    ))
    budget_number = _extract_budget(message) if budget_mentioned else None
    current_budget = state.get("budget", current_plan.get("budget"))
    budget_optimize = any(term in folded_message for term in (
        "phu hop ngan sach", "khong vuot ngan sach", "khong vuot", "toi uu ngan sach",
        "toi uu chi phi", "nam trong ngan sach", "trong ngan sach", "tiet kiem chi phi",
        "stay within budget", "under budget", "optimize budget", "budget friendly",
    ))
    requested_categories = classify_requested_categories(message)
    preference_addition = (
        bool(requested_categories)
        and not requested_category_removals(message)
        and any(term in folded_message for term in (
            "them", "uu tien", "thich", "muon", "prefer", "focus", "add",
        ))
    )
    if budget_number is not None and re.search(r"\b(?:tang them|them)\b", folded_message):
        budget_number = float(current_budget or 0) + budget_number
    elif budget_number is not None and re.search(r"\b(?:giam|bot)\b", folded_message):
        budget_number = max(0.0, float(current_budget or 0) - budget_number)
    place_action = any(term in folded_message for term in (
        "muon di", "muon them", "di den", "ghe", "tham quan", "visit", "go to", "add "
    ))
    province_code = str(state.get("province_code") or current_plan.get("province_code") or "")
    requested_places = _find_requested_staging_places(province_code, message) if place_action and province_code else []
    constraints_requested = (
        people_number is not None or budget_number is not None or bool(requested_places)
        or budget_optimize or preference_addition
        or (people_mentioned and any(term in folded_message for term in ("them", "tang", "increase", "add")))
        or (budget_mentioned and budget_number is None)
    )
    commands = [message]
    intent = _chat_intent(message)
    if intent["regenerate"] and not intent["change_hotel"]:
        # Start a fresh planning flow while keeping earlier messages and plans
        # in the chat transcript. Keep only the history used to avoid repeating
        # hotels and places in the next plan.
        previous_place_ids = [
            int(slot["place"]["id"])
            for day in (current_plan.get("itinerary") or [])
            for slot in day.get("schedule", [])
            if slot.get("place") and slot["place"].get("id") is not None
            and fold_text(slot["place"].get("category")) != fold_text("Lưu trú")
        ]
        previous_hotel_id = (current_plan.get("hotel") or {}).get("id")
        hotel_history_ids = list(state.get("hotel_history_ids") or [])
        if previous_hotel_id is not None:
            hotel_history_ids.append(int(previous_hotel_id))
        state.update({
            "province_code": None,
            "province_name": None,
            "province_id": None,
            "days": None,
            "people": None,
            "budget": None,
            "preferences": [],
            "start_date": None,
            "radius_km": 50,
            "hotel_id": None,
            "hotel_name": "",
            "plan": None,
            "itinerary": [],
            "plan_source": None,
            "plan_generation": int(state.get("plan_generation") or 0),
            "hotel_history_ids": list(dict.fromkeys(hotel_history_ids)),
            "chat_avoided_ids": list(dict.fromkeys([
                *(state.get("chat_avoided_ids") or []), *previous_place_ids,
            ])),
        })
        return {
            "success": True,
            "action": "new_plan",
            "message": "Được, mình giữ nguyên plan cũ trong cuộc trò chuyện và bắt đầu tạo plan mới từ đầu. Bạn gửi lại điểm đến, số ngày, số người, ngân sách và sở thích nhé.",
            "data": {"state": state},
        }
    preference_phrase = any(term in folded_message for term in ("so thich", "interests", "interest"))
    preference_action = any(term in folded_message for term in (
        "them", "bo sung", "uu tien", "thich", "muon", "prefer", "add",
    ))
    if preference_phrase and preference_action and not requested_categories and not requested_category_removals(message):
        return {
            "success": True,
            "action": "clarify",
            "message": "Mình có thể xếp lại plan theo sở thích mới. Bạn muốn thêm ăn uống, cafe, tham quan lịch sử/văn hóa, thiên nhiên/biển, vui chơi hay mua sắm?",
            "data": {"plan": current_plan, "itinerary": current_plan.get("itinerary") or [], "state": state},
        }
    if (not intent["change_hotel"] and not intent["regenerate"] and not intent["extend_days"]
            and not constraints_requested):
        interpreted = interpret_trip_message(message, state, req.history) if req.allow_ai_interpretation else None
        if interpreted and interpreted.get("mode") == "command":
            commands = [interpreted.get("command", message)]
        elif interpreted and interpreted.get("mode") == "commands":
            commands = interpreted.get("commands") or [message]
        elif interpreted and interpreted.get("mode") in {"answer", "clarify"}:
            return {"success": True, "action": interpreted["mode"], "message": interpreted["reply"], "data": {"state": state}}
        intent = {
            key: any(_chat_intent(command)[key] for command in commands)
            for key in ("change_hotel", "regenerate", "extend_days")
        }

    if constraints_requested:
        if people_mentioned and people_number is None:
            return {"success": True, "action": "clarify", "message": "Bạn muốn tổng cộng bao nhiêu người đi? Mình sẽ tính lại chi phí theo số người đó.",
                    "data": {"plan": current_plan, "itinerary": current_plan.get("itinerary") or [], "state": state}}
        if budget_mentioned and budget_number is None and not budget_optimize:
            return {"success": True, "action": "clarify", "message": "Bạn muốn đặt ngân sách chuyến đi là bao nhiêu? Ví dụ: 6 triệu đồng.",
                    "data": {"plan": current_plan, "itinerary": current_plan.get("itinerary") or [], "state": state}}
        if budget_optimize and budget_number is None and current_budget is None:
            return {"success": True, "action": "clarify", "message": "Bạn muốn giới hạn tổng chi phí chuyến đi ở mức bao nhiêu? Ví dụ: tối đa 5 triệu đồng.",
                    "data": {"plan": current_plan, "itinerary": current_plan.get("itinerary") or [], "state": state}}
        if place_action and not requested_places:
            return {"success": False, "action": "clarify", "message": "Mình chưa tìm thấy địa điểm bạn vừa nhắc trong DB của tỉnh này, nên chưa tự thêm vào lịch.",
                    "data": {"plan": current_plan, "itinerary": current_plan.get("itinerary") or [], "state": state}}
        if people_number is not None:
            if not 1 <= people_number <= 100:
                return {"success": False, "action": "clarify", "message": "Số người cần nằm trong khoảng 1–100.",
                        "data": {"plan": current_plan, "itinerary": current_plan.get("itinerary") or [], "state": state}}
            state["people"] = people_number
        if budget_number is not None:
            state["budget"] = budget_number
        if preference_addition:
            existing_preferences = list(state.get("preferences") or current_plan.get("preferences") or [])
            existing_keys = {fold_text(value) for value in existing_preferences}
            existing_preferences.extend(
                category for category in requested_categories if fold_text(category) not in existing_keys
            )
            state["preferences"] = existing_preferences
        hotel = current_plan.get("hotel") or {}
        required_ids = [int(place["id"]) for place in requested_places if place.get("id") is not None]
        old_ids = {
            int(slot["place"]["id"])
            for day in (current_plan.get("itinerary") or [])
            for slot in day.get("schedule", [])
            if slot.get("place") and slot["place"].get("id") is not None
            and int(slot["place"]["id"]) != int(hotel.get("id") or -1)
            and int(slot["place"]["id"]) not in required_ids
        }
        variation_seed = int(state.get("plan_generation") or 0) + 1
        rebuilt = create_chat_plan(ChatPlanRequest(
            province_code=province_code,
            days=int(state.get("days") or current_plan.get("days") or 1),
            people=int(state.get("people") or current_people),
            budget=state.get("budget", current_budget),
            preferences=state.get("preferences") or current_plan.get("preferences") or [],
            hotel_id=(int(hotel["id"]) if hotel.get("id") is not None else None) if not budget_optimize else None,
            avoid_hotel_ids=[] if budget_optimize else list(state.get("hotel_history_ids") or []),
            avoid_place_ids=[] if budget_optimize else sorted(old_ids),
            required_place_ids=required_ids,
            variation_seed=0 if budget_optimize else variation_seed,
            strict_budget=budget_optimize,
        ))
        if not rebuilt.get("success"):
            return {"success": False, "action": "clarify", "message": rebuilt.get("message"),
                    "data": {"plan": current_plan, "itinerary": current_plan.get("itinerary") or [], "state": state}}
        plan = rebuilt.get("data") or rebuilt
        if budget_optimize:
            costs = plan.get("cost_summary") or {}
            total = costs.get("total_estimated_trip_cost")
            unpriced = int(costs.get("unpriced_places") or 0)
            budget_cap = float(state.get("budget") or current_budget or 0)
            if total is None or float(total) > budget_cap or unpriced:
                state["budget"] = current_budget
                detail = (
                    f"Rẻ nhất trong DB vẫn ước tính {float(total):,.0f} ₫, cao hơn ngân sách {budget_cap:,.0f} ₫."
                    if total is not None and float(total) > budget_cap
                    else f"Còn {unpriced} mục chưa có giá xác thực trong DB nên mình chưa thể bảo đảm chuyến đi nằm trong ngân sách."
                )
                return {"success": False, "action": "budget_optimization_failed",
                        "message": f"Mình chưa thay plan hiện tại. {detail} Bạn có thể tăng ngân sách hoặc giảm số ngày/số người để mình xếp lại.",
                        "data": {"plan": current_plan, "itinerary": current_plan.get("itinerary") or [], "state": state}}
        state["plan"] = plan
        state["itinerary"] = plan.get("itinerary") or []
        selected_hotel = plan.get("hotel") or hotel
        state["hotel_name"] = selected_hotel.get("name") or state.get("hotel_name", "")
        state["hotel_id"] = None
        state["plan_generation"] = variation_seed
        if budget_optimize:
            selected_hotel_id = selected_hotel.get("id")
            if selected_hotel_id is not None:
                state["hotel_history_ids"] = list(dict.fromkeys([
                    *(state.get("hotel_history_ids") or []), int(selected_hotel_id),
                ]))
            state["chat_avoided_ids"] = []
        changed_bits = []
        if people_number is not None:
            changed_bits.append(f"{people_number} người")
        if budget_number is not None:
            changed_bits.append(f"ngân sách {budget_number:,.0f}đ")
        if requested_places:
            changed_bits.append("thêm " + ", ".join(place["name"] for place in requested_places))
        if preference_addition:
            changed_bits.append("sở thích " + ", ".join(category.lower() for category in requested_categories))
        message = "Mình đã xếp lại toàn bộ lịch theo " + " và ".join(changed_bits)
        if budget_optimize:
            remaining = (plan.get("cost_summary") or {}).get("remaining_budget")
            message = (f"Mình đã tạo lại plan với các lựa chọn tiết kiệm hơn. Tổng dự kiến {float(total):,.0f} ₫, "
                       f"còn {float(remaining or 0):,.0f} ₫ trong ngân sách {budget_cap:,.0f} ₫.")
            if selected_hotel.get("name"):
                message += f" Nơi lưu trú: {selected_hotel['name']}."
        else:
            if hotel.get("name"):
                message += f", giữ khách sạn {hotel['name']}"
            message += ". Chi phí đã tính lại theo dữ liệu DB; mục nào DB chưa có giá sẽ được đánh dấu chưa đủ dữ liệu."
        return {"success": True, "action": "replan", "message": message,
                "data": {"plan": plan, "itinerary": plan.get("itinerary") or [], "state": state}}

    if intent.get("extend_days"):
        current_plan = state.get("plan") or {}
        current_days = list(current_plan.get("itinerary") or state.get("itinerary") or [])
        current_day_count = len(current_days)
        extra_days = _requested_extra_days(message)
        if not current_day_count or current_day_count + extra_days > 30:
            return {"success": False, "action": "clarify", "message": "Chuyến đi có thể dài tối đa 30 ngày.",
                    "data": {"plan": current_plan, "itinerary": current_days, "state": state}}
        hotel = current_plan.get("hotel") or {}
        old_place_ids = {
            int(slot["place"]["id"])
            for day in current_days
            for slot in day.get("schedule", [])
            if slot.get("place") and slot["place"].get("id") is not None
            and int(slot["place"]["id"]) != int(hotel.get("id") or -1)
        }
        new_day_count = current_day_count + extra_days
        extension = create_chat_plan(ChatPlanRequest(
            province_code=str(state.get("province_code") or current_plan.get("province_code") or ""),
            days=new_day_count,
            people=int(state.get("people") or current_plan.get("people") or 1),
            budget=state.get("budget", current_plan.get("budget")),
            preferences=state.get("preferences") or current_plan.get("preferences") or [],
            hotel_id=int(hotel.get("id")) if hotel.get("id") is not None else None,
            avoid_place_ids=sorted(old_place_ids),
            variation_seed=int(state.get("plan_generation") or 0) + 500 + new_day_count,
        ))
        if not extension.get("success"):
            return {"success": False, "action": "clarify", "message": extension.get("message"),
                    "data": {"plan": current_plan, "itinerary": current_days, "state": state}}
        generated_days = list((extension.get("data") or {}).get("itinerary") or [])
        appended_days = generated_days[-extra_days:]
        for index, day in enumerate(appended_days, start=current_day_count + 1):
            day["day"] = index
            day["title"] = f"Gợi ý ngày {index}"

        plan = deepcopy(current_plan)
        plan["days"] = new_day_count
        plan["itinerary"] = current_days + appended_days
        plan["selected_activity_count"] = sum(
            1 for day in plan["itinerary"] for slot in day.get("schedule", []) if slot.get("place")
        )
        summary = deepcopy(plan.get("cost_summary") or {})
        people = int(state.get("people") or current_plan.get("people") or 1)
        old_known = float(summary.get("known_cost") or 0)
        old_per_person = float(summary.get("per_person_estimate") or 0)
        old_missing = int(summary.get("unpriced_places") or 0)
        added_known = added_per_person = 0.0
        added_missing = 0
        hotel_rate = _staging_price_midpoint_vnd(hotel) if hotel else None
        if hotel_rate is None:
            added_missing += extra_days
            lodging_cost = None
        else:
            added_known += hotel_rate * extra_days
            added_per_person += hotel_rate * extra_days / max(people, 1)
            old_lodging = summary.get("lodging_cost")
            lodging_cost = (float(old_lodging) if old_lodging is not None else hotel_rate * current_day_count) + hotel_rate * extra_days
        for day in appended_days:
            for slot in day.get("schedule", []):
                place = slot.get("place")
                if not place or fold_text(place.get("category")) == fold_text("Lưu trú"):
                    continue
                amount = _per_person_plan_cost(place, slot.get("activity", ""), people)
                if amount is None:
                    added_missing += 1
                    continue
                slot["estimated_per_person"] = round(amount)
                added_per_person += amount
                added_known += amount * people
        total_known = old_known + added_known
        total_per_person = old_per_person + added_per_person
        unpriced = old_missing + added_missing
        budget = state.get("budget", current_plan.get("budget"))
        summary.update({
            "currency": "VND",
            "known_cost": round(total_known),
            "total_estimated_trip_cost": round(total_known),
            "per_person_estimate": round(total_per_person),
            "lodging_cost": round(lodging_cost) if lodging_cost is not None else None,
            "hotel_days": max(new_day_count, 1),
            "hotel_nights": max(new_day_count, 1),
            "budget": budget,
            "remaining_budget": round(budget - total_known) if budget is not None and unpriced == 0 else None,
            "within_budget": total_known <= budget if budget is not None and unpriced == 0 else None,
            "unpriced_places": unpriced,
            "partial": unpriced > 0,
        })
        plan["cost_summary"] = summary
        state["days"] = new_day_count
        state["plan"] = plan
        state["itinerary"] = plan["itinerary"]
        return {"success": True, "action": "extend_days",
                "message": f"Mình đã thêm {extra_days} ngày mới, giữ nguyên lịch cũ và khách sạn {hotel.get('name', 'hiện tại')}; chi phí lưu trú đã tính thêm {extra_days} đêm.",
                "data": {"plan": plan, "itinerary": plan["itinerary"], "state": state}}

    if _chat_intent(message)["regenerate"] and not intent["change_hotel"]:
        current_plan = state.get("plan") or {}
        variation_seed = int(state.get("plan_generation") or 0) + 1
        hotel_history_ids = list(state.get("hotel_history_ids") or [])
        current_hotel_id = (current_plan.get("hotel") or {}).get("id")
        if current_hotel_id is not None:
            hotel_history_ids.append(int(current_hotel_id))
        rebuilt = create_chat_plan(ChatPlanRequest(
            province_code=str(state.get("province_code") or current_plan.get("province_code") or ""),
            days=int(state.get("days") or current_plan.get("days") or 1),
            people=int(state.get("people") or current_plan.get("people") or 1),
            budget=state.get("budget", current_plan.get("budget")),
            preferences=state.get("preferences") or current_plan.get("preferences") or [],
            avoid_hotel_ids=list(dict.fromkeys(hotel_history_ids)),
            avoid_place_ids=state.get("chat_avoided_ids") or [
                int(slot["place"]["id"])
                for day in (current_plan.get("itinerary") or [])
                for slot in day.get("schedule", [])
                if slot.get("place") and slot["place"].get("id") is not None
                and fold_text(slot["place"].get("category")) != fold_text("Lưu trú")
            ],
            variation_seed=variation_seed,
        ))
        if not rebuilt.get("success"):
            return {"success": False, "action": "clarify", "message": rebuilt.get("message"), "data": {"state": state}}
        plan = rebuilt.get("data") or rebuilt
        state["plan"] = plan
        state["itinerary"] = plan.get("itinerary") or []
        state["hotel_name"] = (plan.get("hotel") or {}).get("name") or ""
        state["hotel_id"] = None
        state["plan_generation"] = variation_seed
        state["hotel_history_ids"] = list(dict.fromkeys(
            [*(state.get("hotel_history_ids") or []), (plan.get("hotel") or {}).get("id")]
        ))
        state["chat_avoided_ids"] = list(dict.fromkeys([
            *(state.get("chat_avoided_ids") or []),
            *[
                int(slot["place"]["id"])
                for day in (current_plan.get("itinerary") or [])
                for slot in day.get("schedule", [])
                if slot.get("place") and slot["place"].get("id") is not None
                and fold_text(slot["place"].get("category")) != fold_text("Lưu trú")
            ],
        ]))
        return {"success": True, "action": "regenerate", "message": "Mình đã tạo lịch mới với nơi lưu trú khác nếu DB còn lựa chọn, và vẫn giữ một khách sạn xuyên suốt chuyến đi.",
                "data": {"plan": plan, "itinerary": plan.get("itinerary") or [], "state": state}}

    if intent["change_hotel"]:
        current_plan = state.get("plan") or {}
        changed = change_chat_plan_hotel(ChangeChatHotelRequest(
            province_code=str(state.get("province_code") or current_plan.get("province_code") or ""),
            days=int(state.get("days") or current_plan.get("days") or len(current_plan.get("itinerary") or []) or 1),
            people=int(state.get("people") or current_plan.get("people") or 1),
            budget=state.get("budget", current_plan.get("budget")),
            avoid_hotel_ids=state.get("hotel_history_ids") or [],
            current_plan=current_plan,
        ))
        if not changed.get("success"):
            return {"success": False, "action": "clarify", "message": changed.get("message"), "data": {"state": state}}
        plan = changed["data"]
        state["plan"] = plan
        state["itinerary"] = plan.get("itinerary") or []
        state["hotel_name"] = (plan.get("hotel") or {}).get("name") or ""
        state["hotel_id"] = None
        state["hotel_history_ids"] = list(dict.fromkeys(
            [*(state.get("hotel_history_ids") or []), (plan.get("hotel") or {}).get("id")]
        ))
        message = changed.get("message") or "Đã đổi nơi lưu trú và giữ nguyên lịch trình."
        return {"success": True, "action": "change_hotel", "message": message,
                "data": {"plan": plan, "itinerary": plan.get("itinerary") or [], "state": state}}

    return {
        "success": False, "action": "clarify",
        "message": "Mình vẫn giữ nguyên plan hiện tại. Bạn có thể nhắn rõ thay đổi muốn làm, ví dụ: đổi khách sạn.",
        "data": {"plan": state.get("plan"), "itinerary": state.get("itinerary") or [], "state": state},
    }


def _requested_extra_days(text: str) -> int:
    folded = fold_text(text)
    words = {"mot": 1, "hai": 2, "ba": 3, "bon": 4, "nam": 5}
    match = re.search(
        r"\b(?:o them|o lai them|them|tang them|keo dai(?: them)?|gia han|stay another|add)\s*(\d+|mot|hai|ba|bon|nam)?\s*(?:ngay|day)\b",
        folded,
    )
    if not match or not match.group(1):
        return 1
    token = match.group(1)
    try:
        return min(30, max(1, int(token)))
    except ValueError:
        return words.get(token, 1)


def _find_requested_staging_places(province_code: str, message: str) -> list[dict[str, Any]]:
    if DATA_BACKEND == "mongodb":
        return []
    words = set(fold_text(message).split())
    ignored = {"khu", "du", "lich", "dia", "diem", "tham", "quan", "noi", "den",
               "toi", "muon", "di", "ghe", "them", "nua", "va", "tai", "cho",
               "visit", "go", "to", "the", "please", "want", "add"}
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, province_code, province_name, name, category, price_text,
                       price_min, price_max, address, opening_hours, image_url,
                       image_source_url, source_urls, currency
                FROM place_crawl_staging
                WHERE province_code = %s AND status = 'ready_for_use'
                  AND NULLIF(BTRIM(address), '') IS NOT NULL
                ORDER BY id
            """, (province_code,))
            rows = cur.fetchall()
        requested = []
        province_name = None
        with conn.cursor() as cur:
            cur.execute("SELECT name FROM provinces WHERE code = %s LIMIT 1", (province_code,))
            province_row = cur.fetchone()
            province_name = province_row[0] if province_row else None
        for row in rows:
            place = _staging_place_payload(row)
            if province_name and not _staging_record_matches_province(place, province_name):
                continue
            name_words = []
            for token in fold_text(place.get("name", "")).split():
                token = token[:-1] if len(token) > 4 and token.endswith("s") else token
                if token not in ignored:
                    name_words.append(token)
            distinct = set(name_words)
            if not distinct:
                continue
            matches = distinct.intersection(words)
            threshold = min(2, len(distinct))
            if len(matches) >= threshold:
                requested.append((len(matches), len(distinct), place))
        requested.sort(key=lambda item: (item[0] / item[1], item[0], len(item[2].get("name", ""))), reverse=True)
        return [item[2] for item in requested[:3]]
    finally:
        conn.close()


def _chat_intent(text: str) -> dict[str, Any]:
    """Nhận diện yêu cầu hội thoại tự nhiên, không ép mẫu câu."""
    t = fold_text(text)
    has = lambda terms: any(fold_text(k) in t for k in terms)
    return {
        "extend_days": bool(re.search(
            r"\b(?:o them|o lai them|them|tang them|keo dai(?: them)?|gia han|stay another|add)\s*(?:\d+|mot|hai|ba|bon|nam)?\s*(?:ngay|day)\b",
            t,
        )),
        "regenerate": has([
            "tạo lịch", "lập lịch", "lên lịch", "làm lại lịch", "làm lại chuyến",
            "tạo lại", "làm mới lịch", "lịch khác", "plan lại", "plan khác",
            "tạo mới lịch", "đổi toàn bộ lịch", "làm một lịch mới",
            "lịch trình mới", "tạo plan mới", "plan mới", "tạo một plan mới",
            "tạo một lịch trình hoàn toàn khác", "lịch trình hoàn toàn khác",
            "completely different itinerary", "new itinerary", "new plan", "another plan",
        ]),
        "replace": has([
            "loại bỏ", "đổi điểm", "đổi địa điểm",
            "thay điểm", "thay địa điểm", "thay thế", "đừng cho", "đừng đưa",
            "không muốn đến", "không thích điểm", "remove", "replace", "change place",
        ]) or bool(re.search(
            r"\b(?:bo|xoa)\s+(?:dia diem|diem|cho|quan|nha hang|khach san|hotel|place)\b", t
        )),
        "change_hotel": has([
            "đổi khách sạn", "thay khách sạn", "chọn khách sạn", "tìm khách sạn",
            "đổi nơi ở", "đổi chỗ ở", "đổi lưu trú", "change hotel",
            "change my hotel", "switch hotel", "find a hotel",
        ]),
        "beach": has([
            "gần biển", "sát biển", "ven biển", "bãi biển", "gần bãi",
            "đi biển", "biển mỹ khê", "mỹ khê", "beach", "seaside",
        ]),
        "center": has(["gần trung tâm", "trung tâm thành phố", "ở trung tâm", "city center"]),
        "attractions": has(["gần điểm tham quan", "gần khu tham quan", "gần địa điểm du lịch", "near attractions"]),
        "time_change": requested_time_of_day(text) is not None,
    }


def _trip_question_reply(message: str, state: dict[str, Any]) -> Optional[str]:
    """Answer common questions from the current trip data without guessing."""
    text = fold_text(message)
    question_like = "?" in message or any(k in text for k in (
        "bao nhieu", "co gi", "ten gi", "o dau", "nao vay", "la gi",
        "hien tai", "how much", "what is", "where is", "which places",
    ))
    if not question_like:
        return None
    plan = state.get("plan") or {}
    hotel = plan.get("hotel") or state.get("hotel") or {}
    days = state.get("itinerary") or plan.get("itinerary") or []

    if any(k in text for k in ("chi phi", "tong tien", "het bao nhieu", "ngan sach", "cost", "total")):
        summary = plan.get("cost_summary") or {}
        total = summary.get("total_estimated_trip_cost")
        known = summary.get("known_cost")
        missing = int(summary.get("unpriced_places") or 0)
        if total is not None:
            return f"Tổng chi phí hiện tính được là {float(total):,.0f} ₫."
        if known is not None:
            return f"Các khoản đã có dữ liệu cộng lại là {float(known):,.0f} ₫; hiện còn {missing} địa điểm chưa có giá xác thực nên chưa thể chốt tổng."
        budget = state.get("budget")
        return f"Ngân sách bạn đặt là {float(budget):,.0f} ₫. Lịch hiện chưa có đủ dữ liệu giá để tính tổng." if budget else "Chuyến đi chưa có ngân sách hoặc đủ dữ liệu giá để tính tổng."

    traveler_question = any(k in text for k in (
        "may nguoi", "bao nhieu nguoi", "so nguoi", "travelers", "travellers",
        "how many people", "party size",
    ))
    if traveler_question:
        people = state.get("people")
        facts = [f"Chuyến đi hiện có {int(people)} người" if people is not None else "Chưa có số người trong thông tin chuyến đi"]
        if any(k in text for k in ("khach san", "noi luu tru", "cho o", "hotel", "stay")):
            name = hotel.get("name") or state.get("hotel_name")
            address = hotel.get("address")
            if name:
                facts.append(f"nơi lưu trú hiện tại là {name}" + (f", tại {address}" if address else ""))
        return "; ".join(facts) + "."

    # Support compound questions such as "How many days is this trip, and
    # what is on Day 2?" The old day-first branch returned immediately and
    # silently dropped the trip-duration part of the question.
    asks_trip_days = any(k in text for k in (
        "bao nhieu ngay", "chuyen di may ngay", "lich co may ngay",
        "so ngay", "how many days", "trip duration",
    ))
    trip_day_count = max(
        len(days),
        int(state.get("days", 0) or 0),
        max((int(item.get("day", 0)) for item in days if str(item.get("day", "")).isdigit()), default=0),
    )
    days_prefix = f"Chuyến đi hiện có {trip_day_count} ngày. " if asks_trip_days and trip_day_count else ""

    day_number = _extract_day_number(text)
    category = classify_requested_category(message)
    if day_number is not None:
        day = next((item for item in days if str(item.get("day")) == str(day_number)), None)
        if day:
            slots = [slot for slot in day.get("schedule", []) if slot.get("place") and slot["place"].get("name")]
            if category:
                slots = [slot for slot in slots if normalize(slot["place"].get("category")) == normalize(category)]
            names = [slot["place"].get("name") for slot in slots]
            if names:
                if category:
                    return days_prefix + f"Ngày {day_number} hiện có các địa điểm {category.lower()}: " + "; ".join(names) + "."
                return days_prefix + f"Ngày {day_number} hiện có: " + "; ".join(names) + "."
            if category:
                return days_prefix + f"Ngày {day_number} hiện chưa có địa điểm thuộc nhóm {category.lower()}."
            return days_prefix + f"Ngày {day_number} chưa có địa điểm nào được xếp."

    if category:
        names = [
            slot["place"].get("name")
            for day in days
            for slot in day.get("schedule", [])
            if slot.get("place") and slot["place"].get("name")
            and normalize(slot["place"].get("category")) == normalize(category)
        ]
        if names:
            return f"Trong lịch hiện có các địa điểm {category.lower()}: " + "; ".join(names) + "."
        return f"Lịch hiện chưa có địa điểm thuộc nhóm {category.lower()}."

    if any(k in text for k in ("khach san", "noi luu tru", "cho o", "hotel", "stay")):
        name = hotel.get("name") or state.get("hotel_name")
        if any(k in text for k in ("gia", "bao nhieu", "cost", "price", "how much")):
            price = hotel.get("price_min") if hotel.get("price_min") is not None else hotel.get("price_max")
            if price is None:
                return f"{name or 'Nơi lưu trú này'} chưa có giá công khai được xác thực trong dữ liệu hiện tại."
            return f"Giá niêm yết được lưu cho {name or 'nơi lưu trú này'} là {float(price):,.0f} ₫. Đây là giá công khai từ website, chưa xác nhận theo ngày đặt phòng."
        if name:
            address = hotel.get("address")
            return f"Nơi lưu trú hiện tại là {name}" + (f", tại {address}." if address else ".")

    if any(k in text for k in ("lich trinh", "ke hoach", "di dau", "dia diem nao", "itinerary", "schedule")):
        if not days:
            return "Chuyến đi hiện chưa có lịch trình. Bạn có thể yêu cầu mình tạo lại lịch."
        return f"Lịch hiện có {len(days)} ngày ở {state.get('province_name') or 'điểm đến đã chọn'}; bạn có thể hỏi riêng từng ngày hoặc bảo mình đổi địa điểm."
    return None


def _extract_number(text: str, keywords: list[str]) -> Optional[int]:
    t = fold_text(text)
    for key in keywords:
        folded_key = fold_text(key)
        m = re.search(rf"(?:{re.escape(folded_key)})\s*(\d{{1,3}})", t)
        if not m:
            m = re.search(rf"(\d{{1,3}})\s*(?:{re.escape(folded_key)})", t)
        if m:
            return int(m.group(1))
    return None


def _trip_duration_update(text: str, state: dict[str, Any]) -> tuple[Optional[int], int]:
    """Return (new total days, delta); keep day-specific edits out of trip duration."""
    current_days = int(state.get("days", 1) or 1)
    itinerary = state.get("itinerary") or (state.get("plan") or {}).get("itinerary") or []
    day_numbers = [
        int(day.get("day")) for day in itinerary
        if str(day.get("day", "")).isdigit()
    ]
    # Older browser state can have a stale duration while retaining the full plan.
    current_days = max(current_days, len(itinerary), max(day_numbers, default=0))
    current_days = max(1, min(current_days, 30))
    folded = fold_text(text)
    number = r"(\d{1,2}|mot|một|hai|ba|bon|tu|nam|sau|bay|tam|chin)"
    values = {
        "mot": 1, "một": 1, "hai": 2, "ba": 3, "bon": 4, "tu": 4,
        "nam": 5, "sau": 6, "bay": 7, "tam": 8, "chin": 9,
    }

    add = re.search(
        rf"\b(?:them|tang|keo dai|gia han|add|extend|increase)\s+{number}\s*(?:ngay|days?)\b",
        folded,
    )
    if add:
        amount = int(add.group(1)) if add.group(1).isdigit() else values[add.group(1)]
        return min(30, current_days + amount), amount

    subtract = re.search(
        rf"\b(?:giam|bot|rut ngan|shorten|reduce)\s+{number}\s*(?:ngay|days?)\b",
        folded,
    )
    if subtract:
        amount = int(subtract.group(1)) if subtract.group(1).isdigit() else values[subtract.group(1)]
        target = max(1, current_days - amount)
        return target, target - current_days

    if _extract_day_number(text) is not None:
        return None, 0

    total = _extract_number(text, ["ngày", "day", "days"])
    if total is not None and 1 <= total <= 30:
        return total, total - current_days
    return None, 0



def _extract_radius(text: str, current_radius: Optional[float] = None) -> Optional[float]:
    t = fold_text(text)
    m = re.search(r"(?:pham vi|ban kinh|radius)\s*(?:quanh khach san|around the hotel)?\s*(\d+(?:[.,]\d+)?)\s*(?:km|kilometers?|kilomet)?", t)
    if not m:
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*km\b", t)
    if not m:
        if any(k in t for k in [
            "gan nhau", "di chuyen it", "di lai it", "di lai gan", "han che di xa",
            "khong di xa", "duong di ngan", "do met", "nhe nhang", "lon tuoi", "elderly",
            "seniors", "older adults", "less tiring", "easy pace", "less travel",
            "closer together", "avoid long drives",
        ]):
            return min(float(current_radius or 50), 5.0)
        return None
    try:
        return max(1.0, min(float(m.group(1).replace(',', '.')), 100.0))
    except ValueError:
        return None


def _extract_limit_per_day(text: str, current_limit: Optional[int] = None) -> Optional[int]:
    """Read explicit daily stop limits such as '4 điểm mỗi ngày' or '4 stops/day'."""
    t = fold_text(text)
    m = re.search(
        r"(?:toi da|max(?:imum)?|gioi han)?\s*(\d{1,2})\s*"
        r"(?:diem|dia diem|hoat dong|stops?)\s*(?:moi ngay|/ngay|per day|each day)",
        t,
    )
    if not m:
        m = re.search(
            r"(?:moi ngay|/ngay|per day|each day)\s*(?:toi da|max(?:imum)?|gioi han)?\s*"
            r"(\d{1,2})\s*(?:diem|dia diem|hoat dong|stops?)",
            t,
        )
    if m:
        return max(1, min(int(m.group(1)), 7))
    if any(k in t for k in [
        "it diem hon", "it hoat dong hon", "fewer stops", "less activities", "less stops",
        "do met", "nhe nhang", "thu gian", "thoang hon", "dung don", "khong don",
        "less tiring", "more relaxed", "less packed", "not too many stops",
        "lon tuoi", "elderly", "seniors", "older adults", "senior friendly",
    ]):
        return max(2, min(int(current_limit or 6) - 2, 7))
    return None

def _extract_budget(text: str) -> Optional[float]:
    # Hỗ trợ: 3 triệu, 3tr, 3m, 500 nghìn, 500 ngàn, 500k,
    # 500.000 VND và 3000000 khi câu có nhắc đến ngân sách.
    t = fold_text(text)
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(trieu|tr|m)\b", t)
    if m:
        return float(m.group(1).replace(',', '.')) * 1_000_000
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(nghin|ngan|k)\b", t)
    if m:
        return float(m.group(1).replace(',', '.')) * 1_000
    nums = re.findall(r"\d[\d.,]{2,}", t)
    if nums:
        raw = nums[-1].replace('.', '').replace(',', '')
        try:
            return float(raw)
        except ValueError:
            pass
    return None


def _choose_hotel(recommender, state: dict[str, Any], preference: str):
    """Chọn một khách sạn KHÁC khách sạn hiện tại theo ưu tiên vị trí."""
    province = state.get("province_code")
    if not province:
        return None
    budget = state.get("budget")
    days = int(state.get("days", 1) or 1)
    province_name = state.get("province_name") or next(
        (p.province_name for p in recommender.places if normalize(p.province_code) == normalize(province) and p.province_name),
        None,
    )
    current_id = state.get("hotel_id")
    try:
        current_id = int(current_id) if current_id is not None else None
    except (TypeError, ValueError):
        current_id = None
    anchor_data = _location_anchor(recommender, province, preference)
    hotels = [
        p for p in recommender.places
        if normalize(p.province_code) == normalize(province)
        and is_accommodation(p)
        and has_plausible_accommodation_rate(p)
        and has_usable_place_address(p)
        and province_name
        and _staging_record_matches_province({"name": p.name, "address": p.address}, province_name)
        and p.latitude is not None and p.longitude is not None
        and (current_id is None or int(p.id) != current_id)
    ]
    ranked = []
    for p in hotels:
        score, dist, ref = pick_hotel_score(
            p, preference, budget, days, anchor_data, int(state.get("people") or 1)
        )
        ranked.append((dist if dist is not None else 10**9, -score, p, ref))
    ranked.sort(key=lambda x: (x[0], x[1]))
    return ranked[0][2] if ranked else None

def _rebuild_from_state(state: dict[str, Any], recommender, planner):
    if not state.get("province_code") or not state.get("hotel_id"):
        return None
    # Repair persisted state from older planner versions before rebuilding.
    # In particular, a stale 0 used to make ordinary chat edits raise a 500.
    # The form no longer asks people to choose a stop count. Normalize older
    # saved trips to the automatic, active itinerary pace during chat edits.
    safe_limit_per_day = None
    state.pop("limit_per_day", None)
    result = planner.create_plan(
        province_code=state["province_code"],
        days=int(state.get("days", 1)),
        people=int(state.get("people", 1)),
        budget=state.get("budget"),
        preferences=state.get("preferences", []),
        start_date=state.get("start_date"),
        day_preferences=state.get("chat_day_preferences", {}),
        hotel_id=int(state["hotel_id"]),
        radius_km=float(state.get("radius_km", 50)),
        limit_per_day=safe_limit_per_day,
        avoid_place_ids=state.get("chat_avoided_ids", []),
        excluded_categories=state.get("excluded_categories", []),
        day_excluded_categories=state.get("day_excluded_categories", {}),
    )
    hotel = find_hotel(recommender, int(state["hotel_id"]), state["province_code"])
    if hotel:
        result["hotel"] = hotel_payload(hotel)
        result["province_code"] = state["province_code"]
        result["request"] = {
            "province_code": state["province_code"], "days": int(state.get("days", 1)),
            "people": int(state.get("people", 1)), "budget": state.get("budget"),
            "preferences": state.get("preferences", []), "start_date": state.get("start_date"),
            "hotel_id": int(state["hotel_id"]),
            "radius_km": float(state.get("radius_km", 50)),
            "excluded_categories": state.get("excluded_categories", []),
        }
    state["plan"] = result
    state["itinerary"] = result.get("itinerary", [])
    return result


def _plan_place_rows(plan: list[dict[str, Any]]):
    rows = []
    for day in plan or []:
        for slot in day.get("schedule", []):
            place = slot.get("place")
            if place and place.get("name"):
                rows.append((day, slot, place))
    return rows

def _extract_requested_count(text: str) -> int:
    m = re.search(r"(?:đổi|thay|bỏ|xóa|remove|replace|change)\s*(\d{1,2})\s*(?:địa điểm|điểm|chỗ|places?|spots?)?", normalize(text))
    if m:
        return max(1, min(int(m.group(1)), 10))
    return 1

def _replacement_candidates(recommender, province_code, lat, lon, category=None, keywords=None, used_ids=None, limit=100, start_time=None, end_time=None, weekday=None, used_places=None):
    used_ids = {int(x) for x in (used_ids or [])}
    used_places = [place for place in (used_places or []) if place]

    def duplicates_existing(candidate):
        candidate_name = re.sub(r"[^a-z0-9]+", " ", fold_text(candidate.name)).strip()
        candidate_tokens = {token for token in candidate_name.split() if len(token) > 1}
        for existing in used_places:
            existing_name = re.sub(r"[^a-z0-9]+", " ", fold_text(existing.get("name"))).strip()
            if not existing_name:
                continue
            if candidate_name == existing_name:
                return True
            existing_lat, existing_lon = existing.get("latitude"), existing.get("longitude")
            if None in (candidate.latitude, candidate.longitude, existing_lat, existing_lon):
                continue
            distance = recommender._distance_km(
                candidate.latitude, candidate.longitude, existing_lat, existing_lon,
            )
            existing_tokens = {token for token in existing_name.split() if len(token) > 1}
            # Many attractions have multiple database records or alternate
            # labels. Treat near-identical names at the same site as one stop.
            if distance is not None and distance <= 1.25 and len(candidate_tokens & existing_tokens) >= 2:
                return True
        return False

    keywords = [normalize(x) for x in (keywords or []) if x]
    candidates = []
    for p in recommender.places:
        try:
            pid = int(p.id)
        except Exception:
            continue
        if pid in used_ids or normalize(p.province_code) != normalize(province_code):
            continue
        if duplicates_existing(p):
            continue
        if p.latitude is None or p.longitude is None or is_accommodation(p):
            continue
        if (not has_usable_place_address(p)
                or not TripPlanner._is_tourism_candidate(p)):
            continue
        if not TripPlanner._open_during_slot(p, start_time, end_time, weekday):
            continue
        cat = normalize(f"{p.category} {p.subcategory}")
        if category and category != "any" and normalize(category) not in cat:
            aliases = {"Ăn uống":"ăn uống", "Cafe":"cafe", "Tham quan":"tham quan", "Vui chơi":"vui chơi", "Thiên nhiên":"thiên nhiên", "Mua sắm":"mua sắm"}
            if normalize(aliases.get(category, category)) not in cat:
                continue
        text = normalize(f"{p.name} {p.category} {p.subcategory} {p.address}")
        keyword_hits = sum(1 for k in keywords if k and k in text)
        dist = recommender._distance_km(lat, lon, p.latitude, p.longitude) if lat is not None and lon is not None else 999
        rating = float(p.rating or 0)
        score = keyword_hits * 1000 + rating * 10 - float(dist or 999)
        candidates.append((score, dist or 999, p))
    candidates.sort(key=lambda x: (-x[0], x[1]))
    return [p for _, _, p in candidates[:limit]]


def _append_afternoon_stop(day, state, recommender, planner, category, used_ids, used_places=None):
    """Fill one unoccupied afternoon outing slot without rebuilding the day."""
    schedule = day.get("schedule") or []
    afternoon_places = []
    outing_categories = {"Tham quan", "Vui chơi", "Giải trí", "Thiên nhiên"}
    for slot in schedule:
        place = slot.get("place") or {}
        try:
            start = TripPlanner._minutes(slot.get("start_time", "00:00"))
        except (TypeError, ValueError):
            continue
        if 12 * 60 <= start < 18 * 60 and TripPlanner._normalize_category(place.get("category")) in outing_categories:
            afternoon_places.append(slot)
    if len(afternoon_places) >= 2:
        return None

    hotel = find_hotel(recommender, int(state.get("hotel_id") or 0), state.get("province_code"))
    if hotel is None:
        return None
    if not afternoon_places:
        start_time, end_time = "14:15", "15:30"
    else:
        try:
            previous_end = max(TripPlanner._minutes(slot.get("end_time", "16:00")) for slot in afternoon_places)
        except (TypeError, ValueError):
            previous_end = 16 * 60
        start_minute = max(16 * 60 + 15, previous_end + 10)
        if start_minute > 16 * 60 + 45:
            return None
        start_time = TripPlanner._format_minutes(start_minute)
        end_time = TripPlanner._format_minutes(start_minute + 45)

    previous_slot = next((slot for slot in reversed(schedule) if slot.get("place") and slot.get("start_time", "") < start_time), None)
    previous_place = (previous_slot or {}).get("place") or {}
    previous_lat = previous_place.get("latitude", hotel.latitude)
    previous_lon = previous_place.get("longitude", hotel.longitude)
    day_number = int(day.get("day") or 1)
    weekday = TripPlanner.weekday_for_day(day_number, state.get("start_date"))
    if used_places is None:
        used_places = [place for _, _, place in _plan_place_rows(state.get("itinerary") or [])]
    preferences = state.get("preferences") or []
    preference_order = [
        value for value in preferences
        if TripPlanner._normalize_category(value) in outing_categories
    ]
    categories = []
    if category in outing_categories:
        categories.append(category)
    categories.extend(preference_order)
    categories.extend(["Tham quan", "Vui chơi", "Thiên nhiên"])
    categories = list(dict.fromkeys(categories))

    replacement = None
    visit_duration = 45 if afternoon_places else 75
    for candidate_category in categories:
        candidates = _replacement_candidates(
            recommender, state.get("province_code"), previous_lat, previous_lon,
            candidate_category, [], used_ids, 80, start_time, end_time, weekday,
            used_places=used_places,
        )
        for candidate in candidates:
            distance = recommender._distance_km(previous_lat, previous_lon, candidate.latitude, candidate.longitude)
            from_hotel = recommender._distance_km(hotel.latitude, hotel.longitude, candidate.latitude, candidate.longitude)
            if distance is None or distance > 8 or from_hotel is None or from_hotel > 10:
                continue
            candidate_duration = TripPlanner._recommended_visit_duration(candidate, visit_duration)
            if candidate_duration > 90:
                continue
            candidate_end = TripPlanner._format_minutes(TripPlanner._minutes(start_time) + candidate_duration)
            if not TripPlanner._open_during_slot(candidate, start_time, candidate_end, weekday):
                continue
            replacement = candidate
            visit_duration = candidate_duration
            break
        if replacement is not None:
            break
    if replacement is None:
        return None

    distance = recommender._distance_km(previous_lat, previous_lon, replacement.latitude, replacement.longitude)
    actual_category = TripPlanner._effective_category(replacement)
    label = f"{actual_category} buổi chiều"
    activity, _, _ = planner._build_activity(
        place=replacement,
        people=int(state.get("people", 1)),
        distance_km=distance,
        travel_minutes=TripPlannerTravel.minutes(distance),
        start_time=start_time,
        end_time=TripPlanner._format_minutes(TripPlanner._minutes(start_time) + visit_duration),
        slot_type=label,
    )
    new_slot = {
        "start_time": start_time,
        "end_time": TripPlanner._format_minutes(TripPlanner._minutes(start_time) + visit_duration),
        "type": label,
        "duration_minutes": visit_duration,
        "latest_start_time": "16:45",
        "status": "scheduled",
        "requested_category": category or actual_category,
        "category_substitution": category is not None and actual_category != category,
        "travel_from_previous": None,
        "activity_period": "afternoon",
        "place": activity,
    }
    schedule.append(new_slot)
    schedule.sort(key=lambda slot: TripPlanner._minutes(slot.get("start_time", "00:00")))
    used_ids.add(int(replacement.id))
    used_places.append({"name": replacement.name})
    return replacement.name


def _append_named_afternoon_stop(day, state, recommender, planner, place):
    """Add a specifically requested stop to a free afternoon window in place."""
    schedule = day.get("schedule") or []
    outing_categories = {"Tham quan", "Vui chơi", "Giải trí", "Thiên nhiên"}
    afternoon_places = []
    for slot in schedule:
        current = slot.get("place") or {}
        try:
            start = TripPlanner._minutes(slot.get("start_time", "00:00"))
        except (TypeError, ValueError):
            continue
        if 12 * 60 <= start < 18 * 60 and TripPlanner._normalize_category(current.get("category")) in outing_categories:
            afternoon_places.append(slot)
    if len(afternoon_places) >= 3:
        return None

    if not afternoon_places:
        start_time = "14:15"
        default_duration = 75
    else:
        last_end = max(TripPlanner._minutes(slot.get("end_time", "16:00")) for slot in afternoon_places)
        start_minute = max(16 * 60 + 15, last_end + 10)
        if start_minute > 16 * 60 + 15:
            return None
        start_time = TripPlanner._format_minutes(start_minute)
        default_duration = 45
    duration = TripPlanner._recommended_visit_duration(place, default_duration)
    if duration > 90:
        return None
    end_time = TripPlanner._format_minutes(TripPlanner._minutes(start_time) + duration)
    weekday = TripPlanner.weekday_for_day(int(day.get("day") or 1), state.get("start_date"))
    if not TripPlanner._open_during_slot(place, start_time, end_time, weekday):
        return None

    hotel = find_hotel(recommender, int(state.get("hotel_id") or 0), state.get("province_code"))
    if hotel is None:
        return None
    previous_slot = next((slot for slot in reversed(schedule)
                          if slot.get("place") and slot.get("start_time", "") < start_time), None)
    previous_place = (previous_slot or {}).get("place") or {}
    previous_lat = previous_place.get("latitude", hotel.latitude)
    previous_lon = previous_place.get("longitude", hotel.longitude)
    distance = recommender._distance_km(previous_lat, previous_lon, place.latitude, place.longitude)
    from_hotel = recommender._distance_km(hotel.latitude, hotel.longitude, place.latitude, place.longitude)
    if distance is None or distance > 15 or from_hotel is None or from_hotel > 20:
        return None

    label = f"{TripPlanner._effective_category(place)} buổi chiều"
    activity, _, _ = planner._build_activity(
        place=place,
        people=int(state.get("people", 1)),
        distance_km=distance,
        travel_minutes=TripPlannerTravel.minutes(distance),
        start_time=start_time,
        end_time=end_time,
        slot_type=label,
    )
    schedule.append({
        "start_time": start_time,
        "end_time": end_time,
        "type": label,
        "duration_minutes": duration,
        "latest_start_time": "16:15",
        "status": "scheduled",
        "requested_category": TripPlanner._effective_category(place),
        "category_substitution": False,
        "travel_from_previous": None,
        "activity_period": "afternoon",
        "place": activity,
    })
    schedule.sort(key=lambda slot: TripPlanner._minutes(slot.get("start_time", "00:00")))
    return start_time


def _add_day_cafe(state, recommender, planner, day_number, afternoon_only=False):
    """Add one distinct photographed cafe to an actual free window of one day."""
    current_plan = deepcopy(state.get("itinerary") or (state.get("plan") or {}).get("itinerary") or [])
    day = next((row for row in current_plan if int(row.get("day") or 0) == int(day_number)), None)
    hotel = find_hotel(recommender, int(state.get("hotel_id") or 0), state.get("province_code"))
    if day is None or hotel is None:
        return None, None, None

    schedule = day.get("schedule") or []
    rows = _plan_place_rows(current_plan)
    used_ids = {int(place["place_id"]) for _, _, place in rows if place.get("place_id") is not None}
    used_ids.add(int(hotel.id))
    used_names = {
        re.sub(r"[^a-z0-9]+", " ", fold_text(place.get("name"))).strip()
        for _, _, place in rows if place.get("name")
    }
    candidates = [
        place for place in recommender.places
        if normalize(place.province_code) == normalize(state.get("province_code"))
        and TripPlanner._effective_category(place) == "Cafe"
        and int(place.id) not in used_ids
        and re.sub(r"[^a-z0-9]+", " ", fold_text(place.name)).strip() not in used_names
        and TripPlanner._has_usable_address(place)
        and TripPlanner._is_tourism_candidate(place)
    ]
    if not candidates:
        return None, None, None

    ordered = sorted(schedule, key=lambda slot: TripPlanner._minutes(slot.get("start_time", "00:00")))
    gaps = []
    for previous, following in zip(ordered, ordered[1:]):
        try:
            gap_start = TripPlanner._minutes(previous.get("end_time", "00:00"))
            gap_end = TripPlanner._minutes(following.get("start_time", "00:00"))
        except (TypeError, ValueError):
            continue
        if (8 * 60 + 30 <= gap_start and gap_end <= 18 * 60 + 30
                and gap_end - gap_start >= 45
                and (not afternoon_only or gap_start >= 12 * 60)):
            # Leave time after a meal and do not squeeze another cafe directly
            # against lunch when a longer afternoon break is available.
            start = gap_start + max(15, min(60, (gap_end - gap_start - 30) // 2))
            end = start + 30
            if end <= gap_end - 10:
                gaps.append((start, end, previous, following))
    if not gaps:
        return None, None, None

    ranked = []
    for start, end, previous, following in gaps:
        prev_place = previous.get("place") or {}
        next_place = following.get("place") or {}
        prev_lat = prev_place.get("latitude", hotel.latitude)
        prev_lon = prev_place.get("longitude", hotel.longitude)
        for place in candidates:
            to_place = recommender._distance_km(prev_lat, prev_lon, place.latitude, place.longitude)
            to_next = recommender._distance_km(place.latitude, place.longitude,
                                               next_place.get("latitude", hotel.latitude),
                                               next_place.get("longitude", hotel.longitude))
            if to_place is None or to_next is None or to_place > 10 or to_next > 10:
                continue
            weekday = TripPlanner.weekday_for_day(day_number, state.get("start_date"))
            start_text = TripPlanner._format_minutes(start)
            end_text = TripPlanner._format_minutes(end)
            if not TripPlanner._open_during_slot(place, start_text, end_text, weekday):
                continue
            score = to_place + to_next + (1.0 / max(float(place.rating or 0), 1.0))
            ranked.append((score, start, end, place, to_place))
    if not ranked:
        return None, None, None

    _, start, end, cafe, distance = min(ranked, key=lambda item: item[0])
    start_text, end_text = TripPlanner._format_minutes(start), TripPlanner._format_minutes(end)
    activity, _, _ = planner._build_activity(
        place=cafe,
        people=int(state.get("people", 1)),
        distance_km=distance,
        travel_minutes=TripPlannerTravel.minutes(distance),
        start_time=start_text,
        end_time=end_text,
        slot_type="Cafe / nghỉ chân",
    )
    schedule.append({
        "start_time": start_text,
        "end_time": end_text,
        "type": "Cafe / nghỉ chân",
        "duration_minutes": 30,
        "status": "scheduled",
        "requested_category": "Cafe",
        "category_substitution": False,
        "travel_from_previous": None,
        "place": activity,
    })
    schedule.sort(key=lambda slot: TripPlanner._minutes(slot.get("start_time", "00:00")))
    recompute_day(day, hotel, recommender)
    result = deepcopy(state.get("plan") or {})
    result.update({
        "itinerary": current_plan,
        "hotel": hotel_payload(hotel),
        "province_code": state["province_code"],
        "days": int(state.get("days", len(current_plan)) or len(current_plan)),
    })
    rebuild_cost_summary(result, hotel, recommender)
    state["itinerary"] = current_plan
    state["plan"] = result
    state["last_place_name"] = cafe.name
    return result, cafe.name, start_text


def _update_afternoon_activity(state, recommender, planner, category, requested_day=None, add_only=False):
    """Change afternoon outing stops in place instead of rebuilding every day."""
    current_plan = deepcopy(
        state.get("itinerary") or (state.get("plan") or {}).get("itinerary") or []
    )
    hotel = find_hotel(recommender, int(state.get("hotel_id") or 0), state.get("province_code"))
    if hotel is None or not current_plan:
        return None, []

    used_ids = {
        int(place.get("place_id"))
        for _, _, place in _plan_place_rows(current_plan)
        if place.get("place_id") is not None
    }
    used_ids.add(int(hotel.id))
    used_places = [place for _, _, place in _plan_place_rows(current_plan)]
    changed = []
    valid_categories = {"Tham quan", "Vui chơi", "Giải trí", "Thiên nhiên", "Mua sắm"}
    label = "Vui chơi buổi chiều" if category in {"Vui chơi", "Giải trí"} else f"{category} buổi chiều"

    for day in current_plan:
        day_number = int(day.get("day") or 0)
        if requested_day is not None and day_number != requested_day:
            continue
        schedule = day.get("schedule") or []
        added_name = _append_afternoon_stop(day, state, recommender, planner, category, used_ids, used_places)
        if added_name:
            changed.append((day_number, "", added_name))
            continue
        if add_only:
            # “Add” must never silently replace a scheduled activity when
            # there is no free afternoon window or the day is already full.
            continue
        targets = []
        for index, slot in enumerate(schedule):
            place = slot.get("place")
            start = str(slot.get("start_time") or "")
            try:
                hour, minute = (int(value) for value in start.split(":", 1))
            except (TypeError, ValueError):
                continue
            minute_of_day = hour * 60 + minute
            if not (12 * 60 <= minute_of_day < 18 * 60):
                continue
            if not place or TripPlanner._normalize_category(place.get("category")) not in valid_categories:
                continue
            if TripPlanner._normalize_category(place.get("category")) == category:
                continue
            targets.append((0 if TripPlanner._normalize_category(place.get("category")) == "Mua sắm" else 1, index, slot, place))
        if not targets:
            continue

        # Prefer converting an unwanted shopping stop; otherwise change one
        # existing sightseeing stop while keeping meal and coffee breaks.
        _, index, slot, old_place = min(targets, key=lambda row: (row[0], row[1]))
        previous_place = next(
            (entry.get("place") for entry in reversed(schedule[:index]) if entry.get("place")),
            None,
        )
        previous_lat = (previous_place or {}).get("latitude", hotel.latitude)
        previous_lon = (previous_place or {}).get("longitude", hotel.longitude)
        weekday = TripPlanner.weekday_for_day(day_number, state.get("start_date"))
        candidates = _replacement_candidates(
            recommender, state["province_code"], previous_lat, previous_lon,
            category, [], used_ids, 80, slot.get("start_time"), slot.get("end_time"), weekday,
            used_places=used_places,
        )
        replacement = candidates[0] if candidates else None
        if replacement is None:
            continue
        distance = recommender._distance_km(
            previous_lat, previous_lon, replacement.latitude, replacement.longitude,
        )
        activity, _, _ = planner._build_activity(
            place=replacement,
            people=int(state.get("people", 1)),
            distance_km=distance,
            travel_minutes=TripPlannerTravel.minutes(distance),
            start_time=slot.get("start_time", ""),
            end_time=slot.get("end_time", ""),
            slot_type=label,
        )
        slot["type"] = label
        slot["requested_category"] = category
        slot["category_substitution"] = False
        slot["place"] = activity
        used_ids.add(int(replacement.id))
        changed.append((day_number, old_place.get("name"), replacement.name))

    if not changed:
        return None, []

    result = deepcopy(state.get("plan") or {})
    result.update({
        "itinerary": current_plan,
        "hotel": hotel_payload(hotel),
        "province_code": state["province_code"],
        "days": int(state.get("days", len(current_plan)) or len(current_plan)),
        "people": int(state.get("people", 1)),
        "radius_km": float(state.get("radius_km", 50) or 50),
        "preferences": state.get("preferences", []),
    })
    for day_number, *_ in changed:
        changed_day = next((day for day in current_plan if int(day.get("day") or 0) == day_number), None)
        if changed_day is not None:
            recompute_day(changed_day, hotel, recommender)
    rebuild_cost_summary(result, hotel, recommender)
    state["plan"] = result
    state["itinerary"] = current_plan
    state["last_place_name"] = changed[-1][2]
    return result, changed


def _update_day_meal_place(state, recommender, planner, day_number, message):
    """Change only one meal venue on a requested day; keep that day's outings intact."""
    current_plan = deepcopy(
        state.get("itinerary") or (state.get("plan") or {}).get("itinerary") or []
    )
    day = next((row for row in current_plan if int(row.get("day") or 0) == day_number), None)
    hotel = find_hotel(recommender, int(state.get("hotel_id") or 0), state.get("province_code"))
    if day is None or hotel is None:
        return None, None, None, None, []

    # Keep the first occurrence in trip order and remove repeat stops only
    # from the day the traveler is editing. This repairs a duplicate introduced
    # by an earlier rebuild without disturbing the other days.
    seen_names = set()
    removed_duplicates = []
    target_day_schedule = day.get("schedule", [])
    for existing_day in sorted(current_plan, key=lambda row: int(row.get("day") or 0)):
        for slot in sorted(existing_day.get("schedule", []), key=lambda row: TripPlanner._minutes(row.get("start_time", "00:00"))):
            place = slot.get("place") or {}
            if not place.get("name"):
                continue
            name_key = re.sub(r"[^a-z0-9]+", " ", fold_text(place.get("name"))).strip()
            if not name_key:
                continue
            is_duplicate = name_key in seen_names
            if is_duplicate and existing_day is day:
                removed_duplicates.append(place.get("name"))
                target_day_schedule.remove(slot)
                continue
            seen_names.add(name_key)

    text = fold_text(message)
    meal_aliases = [
        (("an sang", "breakfast"), "Ăn sáng"),
        (("an trua", "lunch"), "Ăn trưa"),
        (("an toi", "dinner"), "Ăn tối"),
    ]
    requested_meal = next(
        (label for aliases, label in meal_aliases if any(alias in text for alias in aliases)),
        "Ăn tối",
    )
    meals = [
        slot for slot in target_day_schedule
        if any(alias in fold_text(slot.get("type", "")) for aliases, _ in meal_aliases for alias in aliases)
    ]
    target = next((slot for slot in meals if fold_text(slot.get("type", "")) == fold_text(requested_meal)), None)
    if target is None:
        target = next((slot for slot in meals if requested_meal in fold_text(slot.get("type", ""))), None)
    if target is None:
        return None, None, requested_meal, None, removed_duplicates

    all_rows = _plan_place_rows(current_plan)
    used_ids = {
        int(place["place_id"]) for _, _, place in all_rows
        if place.get("place_id") is not None
    }
    used_ids.add(int(hotel.id))
    used_places = [place for _, _, place in all_rows]
    previous_place = next(
        (slot.get("place") for slot in reversed(day.get("schedule", [])[:day["schedule"].index(target)]) if slot.get("place")),
        hotel_payload(hotel),
    )
    weekday = TripPlanner.weekday_for_day(day_number, state.get("start_date"))
    candidates = _replacement_candidates(
        recommender, state.get("province_code"), previous_place.get("latitude"),
        previous_place.get("longitude"), "Ăn uống", [], used_ids, 100,
        target.get("start_time"), target.get("end_time"), weekday,
        used_places=used_places,
    )
    restaurant = next((place for place in candidates if
                       (recommender._distance_km(previous_place.get("latitude"), previous_place.get("longitude"), place.latitude, place.longitude) or 999) <= 15), None)
    if restaurant is None:
        return None, None, requested_meal, None, removed_duplicates

    distance = recommender._distance_km(
        previous_place.get("latitude"), previous_place.get("longitude"),
        restaurant.latitude, restaurant.longitude,
    )
    activity, _, _ = planner._build_activity(
        place=restaurant,
        people=int(state.get("people", 1)),
        distance_km=distance,
        travel_minutes=TripPlannerTravel.minutes(distance),
        start_time=target.get("start_time", ""),
        end_time=target.get("end_time", ""),
        slot_type=requested_meal,
    )
    old_name = (target.get("place") or {}).get("name")
    target["place"] = activity
    target["type"] = requested_meal
    target["requested_category"] = "Ăn uống"
    target["category_substitution"] = False
    target["status"] = "scheduled"
    target.pop("reason", None)

    recompute_day(day, hotel, recommender)
    result = deepcopy(state.get("plan") or {})
    result.update({
        "itinerary": current_plan,
        "hotel": hotel_payload(hotel),
        "province_code": state["province_code"],
    })
    rebuild_cost_summary(result, hotel, recommender)
    state["itinerary"] = current_plan
    state["plan"] = result
    state["last_place_name"] = restaurant.name
    return result, restaurant.name, requested_meal, old_name, removed_duplicates

def _keywords_for_message(text: str):
    t = normalize(text)
    if any(k in t for k in ["mỹ khê", "my khe"]):
        return ["mỹ khê", "my khe"]
    if any(k in t for k in ["biển", "bãi biển", "đi biển", "beach", "seaside"]):
        return ["biển", "bãi biển", "beach", "sea"]
    if any(k in t for k in ["cafe", "cà phê", "coffee"]):
        return ["cafe", "coffee"]
    if any(k in t for k in ["ăn", "nhà hàng", "food", "restaurant"]):
        return ["ăn uống", "nhà hàng", "restaurant"]
    return []


def _extract_day_number(text: str) -> Optional[int]:
    """Nhận 'ngày 3', 'day 3' nhưng không nhầm '3 ngày'."""
    t = fold_text(text)
    m = re.search(r"(?:ngay|day)\s*(\d{1,2})\b", t)
    if not m:
        return None
    return max(1, min(int(m.group(1)), 30))


def _is_compound_trip_request(message: str) -> bool:
    """Recognize when one message contains multiple independent trip edits."""
    text = fold_text(message)
    clauses = [part.strip() for part in re.split(r"[,;]|\b(?:va|dong thoi|ngoai ra|sau do|roi)\b", text) if part.strip()]
    if len(clauses) < 2:
        return False
    action_terms = (
        "them", "bo ", "xoa", "doi ", "chuyen", "dua ", "tang", "giam", "uu tien",
        "khach san gan", "ngan sach", "so nguoi", "bao nhieu nguoi", "ban kinh",
        "add ", "remove", "change", "replace", "move", "increase", "decrease", "prefer",
        "budget", "hotel near", "travelers",
    )
    active = sum(1 for clause in clauses if any(term in clause for term in action_terms))
    return active >= 2


def _rebuild_single_day(state: dict[str, Any], recommender, planner, day_number: int, limit_override: Optional[int] = None):
    """Rebuild one day with fresh places; if the day is missing, restore the full itinerary first."""
    requested_days = int(state.get("days", 1) or 1)
    current_plan = deepcopy(state.get("itinerary") or [])
    hotel = find_hotel(recommender, int(state.get("hotel_id")), state["province_code"])
    if hotel is None:
        return None, 0
    if day_number < 1 or day_number > requested_days:
        return None, 0

    all_current_ids = {
        int(p.get("place_id"))
        for _, _, p in _plan_place_rows(current_plan)
        if p.get("place_id") is not None
    }
    all_current_ids.discard(int(state["hotel_id"]))

    # Rebuilt days use the same time-and-route-based pacing as new plans.
    limit = None
    radius = float(state.get("radius_km", 50) or 50)

    def generate(days_to_make, avoid_ids):
        day_preferences = state.get("chat_day_preferences", {}) or {}
        day_exclusions = state.get("day_excluded_categories", {}) or {}
        if days_to_make == 1:
            day_preferences = {1: day_preferences.get(str(day_number), day_preferences.get(day_number, []))}
            day_exclusions = {1: day_exclusions.get(str(day_number), day_exclusions.get(day_number, []))}
        return planner.create_plan(
            province_code=state["province_code"],
            days=days_to_make,
            people=int(state.get("people", 1)),
            budget=state.get("budget"),
            preferences=state.get("preferences", []),
            day_preferences=day_preferences,
            start_date=(date.fromisoformat(state["start_date"]) + timedelta(days=day_number - 1)).isoformat()
                if state.get("start_date") else None,
            hotel_id=int(state["hotel_id"]),
            radius_km=radius,
            limit_per_day=limit,
            avoid_place_ids=list(avoid_ids),
            excluded_categories=state.get("excluded_categories", []),
            day_excluded_categories=day_exclusions,
        )

    fresh = None
    try:
        # Nếu ngày hiện tại tồn tại: chỉ tạo 1 ngày mới.
        if len(current_plan) >= day_number:
            candidate = generate(1, all_current_ids)
            if candidate and candidate.get("itinerary"):
                fresh = candidate
        else:
            # Ngày bị mất: tạo lại đủ N ngày, sau đó dùng toàn bộ kết quả.
            candidate = generate(requested_days, all_current_ids)
            if candidate and candidate.get("itinerary"):
                fresh = candidate
    except Exception:
        fresh = None

    if fresh is None:
        return None, 0

    fresh_days = deepcopy(fresh.get("itinerary") or [])
    if not fresh_days:
        return None, 0

    if len(current_plan) < day_number:
        # Khôi phục đủ ngày; giữ các ngày cũ nếu có, lấy ngày mới cho phần thiếu.
        merged = []
        fresh_by_day = {int(d.get("day", i + 1)): d for i, d in enumerate(fresh_days)}
        old_by_day = {int(d.get("day", i + 1)): d for i, d in enumerate(current_plan)}
        for n in range(1, requested_days + 1):
            merged.append(fresh_by_day.get(n, old_by_day.get(n, {"day": n, "title": planner._day_title(n), "schedule": [], "place_count": 0, "estimated_day_cost": 0})))
        current_plan = merged
    else:
        new_day = deepcopy(fresh_days[0])
        new_day["day"] = current_plan[day_number - 1].get("day", day_number)
        new_day["title"] = current_plan[day_number - 1].get("title") or planner._day_title(day_number)
        current_plan[day_number - 1] = new_day

    result = deepcopy(state.get("plan") or {})
    result["itinerary"] = current_plan
    result["hotel"] = hotel_payload(hotel)
    result["province_code"] = state["province_code"]
    result["radius_km"] = radius
    result["days"] = requested_days
    result["people"] = int(state.get("people", 1))
    result["budget"] = state.get("budget")
    result["preferences"] = state.get("preferences", [])
    rebuild_cost_summary(result, hotel, recommender)

    state["itinerary"] = current_plan
    state["plan"] = result
    state.pop("limit_per_day", None)
    return result, sum(1 for x in current_plan[day_number - 1].get("schedule", []) if x.get("place") is not None)


@app.post("/api/chat")
def chat(req: ChatRequest):
    message = req.message.strip()
    state = deepcopy(req.state or {})
    if (state.get("plan_source") == "staging_chat"
            and (state.get("plan") or {}).get("itinerary")):
        return _handle_staging_chat(req, state)
    if not state.get("province_code") or not state.get("hotel_id"):
        return {
            "success": False,
            "action": "need_context",
            "message": "Bạn hãy tạo chuyến đi và chọn nơi lưu trú trước, sau đó cứ nói tự nhiên điều muốn thay đổi.",
            "data": {"state": state},
        }

    # Phân biệt câu hỏi chỉ đọc với câu ghép vừa yêu cầu sửa chuyến đi vừa hỏi
    # thông tin. Không trả lời từ lịch cũ trước khi áp dụng ý sửa rõ ràng.
    direct_intent = _chat_intent(message)
    if _removes_named_plan_place(message, state):
        direct_intent["replace"] = True
    requested_categories = classify_requested_categories(message)
    category_removals = requested_category_removals(message)
    requested_categories = [category for category in requested_categories if category not in category_removals]
    message_folded = fold_text(message)
    question_like = "?" in message or any(k in fold_text(message) for k in [
        "bao nhieu", "co gi", "ten gi", "o dau", "nao vay", "la gi",
        "how much", "what is", "where is", "which places",
        "co nhung", "gom nhung", "liet ke", "cho xem", "ke ten", "quy trinh nao",
        "which day", "what places", "show me", "list the",
    ])
    command_marker = (
        direct_intent["regenerate"] or direct_intent["replace"] or direct_intent["change_hotel"]
        or direct_intent["time_change"]
        or any(k in message_folded for k in [
            "them ", "tang ", "giam ", "lam lai", "cap nhat", "cho toi di",
            "toi muon di", "toi muon them", "please add", "add ", "make it",
            "set ", "increase ", "decrease ", "i want to go with ",
        ])
    )
    explicit_change = any(direct_intent.values()) or any((
        _extract_day_number(message) is not None,
        _extract_number(message, ["người", "nguoi", "person", "people", "travelers"]) is not None,
        _trip_duration_update(message, state)[0] is not None,
        _extract_budget(message) is not None,
        _extract_radius(message, float(state.get("radius_km", 50) or 50)) is not None,
        _extract_limit_per_day(message, int(state.get("limit_per_day", 6) or 6)) is not None,
        bool(requested_categories) and not question_like and (
            is_more_request(message)
            or any(k in fold_text(message) for k in ["uu tien", "tap trung", "thich", "muon", "prefer", "focus"])
        ),
        bool(category_removals),
    ))
    if question_like and not command_marker:
        explicit_change = False

    # Trả lời trực tiếp những câu hỏi thuần tra cứu. Câu có dấu hiệu ra lệnh
    # đi tiếp xuống luồng cập nhật rồi mới tạo câu trả lời theo lịch mới.
    known_answer = None if explicit_change else _trip_question_reply(message, state)
    if known_answer:
        return {"success": True, "action": "answer", "message": known_answer, "data": {"state": state}}

    # Ollama xử lý câu tự nhiên chưa phân loại được; lệnh rõ ràng tiếp tục dùng
    # parser giới hạn để không làm rơi các con số hoặc ngày người dùng nêu.
    # Clear trip edits are executed by the deterministic planner even when
    # the sentence contains several clauses. Sending those requests through
    # the local model made routine edits take tens of seconds and could split
    # one coherent plan update into several stale intermediate plans. Keep
    # the model for genuinely open-ended language and questions.
    should_interpret = req.allow_ai_interpretation and not explicit_change
    interpretation = interpret_trip_message(message, state, req.history) if should_interpret else None
    if interpretation and interpretation["mode"] == "commands":
        current_state = state
        summaries = []
        last_plan = current_state.get("plan")
        completed = 0
        for command in interpretation["commands"]:
            subresult = chat(ChatRequest(
                message=command,
                state=current_state,
                history=[],
                allow_ai_interpretation=False,
            ))
            subdata = subresult.get("data") or {}
            current_state = subdata.get("state") or current_state
            last_plan = subdata.get("plan") or current_state.get("plan") or last_plan
            if subresult.get("success") is False or subresult.get("action") == "clarify":
                reason = str(subresult.get("message") or "Chưa thực hiện được yêu cầu này.")
                if completed:
                    return {
                        "success": True,
                        "action": "partial_update",
                        "message": "Mình đã thực hiện một phần: " + "; ".join(summaries)
                                   + f". Phần còn lại chưa áp dụng: {reason}",
                        "data": {"plan": last_plan, "itinerary": (last_plan or {}).get("itinerary", current_state.get("itinerary", [])), "state": current_state},
                    }
                return {"success": False, "action": "clarify", "message": reason, "data": {"state": current_state}}
            completed += 1
            if subresult.get("message"):
                summaries.append(str(subresult["message"]).strip())
        return {
            "success": True,
            "action": "compound_update",
            "message": "Mình đã cập nhật lần lượt các yêu cầu: " + "; ".join(summaries),
            "data": {"plan": last_plan, "itinerary": (last_plan or {}).get("itinerary", current_state.get("itinerary", [])), "state": current_state},
        }
    if interpretation and interpretation["mode"] in {"answer", "clarify"}:
        return {
            "success": True,
            "action": interpretation["mode"],
            "message": interpretation["reply"],
            "data": {"state": state},
        }
    if interpretation and interpretation["mode"] == "command":
        message = interpretation["command"]

    # The model may translate colloquial wording into a concise planner
    # command. Re-run lightweight extraction on that command so the executor
    # acts on the model's interpretation, not stale matches from the original.
    requested_categories = classify_requested_categories(message)
    category_removals = requested_category_removals(message)
    requested_categories = [category for category in requested_categories if category not in category_removals]

    # Nếu mô hình ngôn ngữ chưa chạy, vẫn trả lời được các câu hỏi phổ biến
    # bằng dữ liệu chuyến đi hiện có thay vì nhầm câu hỏi thành lệnh đổi lịch.
    if not interpretation and not explicit_change:
        answer = _trip_question_reply(message, state)
        if answer:
            return {"success": True, "action": "answer", "message": answer, "data": {"state": state}}

    text = normalize(message)
    requested_day = _extract_day_number(text)
    intent = _chat_intent(text)
    if _removes_named_plan_place(message, state):
        intent["replace"] = True
    positive_category_request = bool(requested_categories) and any(term in fold_text(message) for term in [
        "them", "uu tien", "tap trung", "thich", "muon", "more", "prefer", "focus", "increase",
    ])
    if positive_category_request:
        if requested_day is not None:
            day_preferences = dict(state.get("chat_day_preferences") or {})
            current_preferences = list(day_preferences.get(str(requested_day), day_preferences.get(requested_day, [])) or [])
            current_preferences.extend(category for category in requested_categories if category not in current_preferences)
            day_preferences[str(requested_day)] = current_preferences
            state["chat_day_preferences"] = day_preferences
        else:
            preferences = list(state.get("preferences") or [])
            preferences.extend(category for category in requested_categories if category not in preferences)
            state["preferences"] = preferences
        global_exclusions = list(state.get("excluded_categories") or [])
        reenabled = [category for category in requested_categories if category in global_exclusions]
        if requested_day is not None:
            if reenabled:
                state["excluded_categories"] = [category for category in global_exclusions if category not in reenabled]
            day_exclusions = dict(state.get("day_excluded_categories") or {})
            if reenabled:
                trip_days = max(int(state.get("days", 1) or 1), len(state.get("itinerary") or []))
                for day_number in range(1, trip_days + 1):
                    if day_number == requested_day:
                        continue
                    current_other = list(day_exclusions.get(str(day_number), day_exclusions.get(day_number, [])) or [])
                    current_other.extend(category for category in reenabled if category not in current_other)
                    day_exclusions[str(day_number)] = current_other
            current = list(day_exclusions.get(str(requested_day), day_exclusions.get(requested_day, [])) or [])
            current = [category for category in current if category not in requested_categories]
            if current:
                day_exclusions[str(requested_day)] = current
            else:
                day_exclusions.pop(str(requested_day), None)
                day_exclusions.pop(requested_day, None)
            state["day_excluded_categories"] = day_exclusions
        else:
            state["excluded_categories"] = [
                category for category in (state.get("excluded_categories") or [])
                if category not in requested_categories
            ]
            day_exclusions = dict(state.get("day_excluded_categories") or {})
            for day_number, values in list(day_exclusions.items()):
                values = [category for category in (values or []) if category not in requested_categories]
                if values:
                    day_exclusions[day_number] = values
                else:
                    day_exclusions.pop(day_number, None)
            state["day_excluded_categories"] = day_exclusions
    if category_removals:
        # Category exclusions are a preference operation, never a request to
        # replace an arbitrary last stop in the itinerary.
        intent["replace"] = False
    recommender, planner = get_system()

    plan = state.get("itinerary") or []
    if not state.get("last_place_name"):
        names = find_plan_place_names(plan)
        if names:
            state["last_place_name"] = names[-1]

    def append_followup_answer(reply: str, result: dict[str, Any]) -> str:
        if not question_like:
            return reply
        answer_state = dict(state)
        answer_state["plan"] = result
        answer_state["itinerary"] = result.get("itinerary") or []
        answer = _trip_question_reply(message, answer_state)
        return reply + (" " + answer if answer else "")

    # Move a specific landmark to the requested part of the day, for example
    # “đưa Bà Nà vào buổi sáng”. This is a direct edit, not a generic LLM reply.
    requested_day = _extract_day_number(text)
    time_of_day = requested_time_of_day(message)
    if time_of_day:
        requested_poi = find_requested_poi(message, recommender, plan)
        if requested_poi is not None:
            requested_id = int(requested_poi.id)
            target_day_number = requested_day
            if target_day_number is None:
                target_day_number = next((int(day.get("day", 0)) for day in plan if any(
                    (slot.get("place") or {}).get("place_id") is not None
                    and int((slot.get("place") or {}).get("place_id")) == requested_id
                    for slot in day.get("schedule", [])
                )), 1)
            target_day = next((day for day in plan if int(day.get("day", 0)) == target_day_number), None)
            if target_day is None and plan:
                target_day = plan[0]
                target_day_number = int(target_day.get("day", 1))
            wanted = {
                "morning": lambda slot: "sáng" in str(slot.get("type", "")).casefold() and any(k in str(slot.get("type", "")).casefold() for k in ("tham quan", "thiên nhiên", "vui chơi")),
                "afternoon": lambda slot: "chiều" in str(slot.get("type", "")).casefold(),
                "evening": lambda slot: "tối" in str(slot.get("type", "")).casefold() or "đêm" in str(slot.get("type", "")).casefold(),
            }[time_of_day]
            target_slot = next((slot for slot in (target_day or {}).get("schedule", []) if wanted(slot)), None)
            add_named_intent = any(term in fold_text(message) for term in (
                "them ", "bo sung", "ghe ", "tham quan ", "add ", "include ",
            ))
            already_scheduled = any(
                int((slot.get("place") or {}).get("place_id") or -1) == requested_id
                for day in plan for slot in day.get("schedule", [])
            )
            # A new named place plus “add” should occupy an empty window; it
            # must not silently replace the afternoon stop already on the plan.
            if add_named_intent and not already_scheduled:
                target_slot = None
            if time_of_day == "evening":
                existing = next((slot for day in plan for slot in day.get("schedule", [])
                                 if (slot.get("place") or {}).get("place_id") == requested_id), None)
                previous_slot = None
                if target_slot:
                    slots = target_day.get("schedule", [])
                    target_index = slots.index(target_slot)
                    previous_slot = next((s for s in reversed(slots[:target_index]) if s.get("place")), None)
                anchor = (previous_slot or {}).get("place") or (target_day or {}).get("hotel") or {}
                near_km = recommender._distance_km(anchor.get("latitude"), anchor.get("longitude"), requested_poi.latitude, requested_poi.longitude) if anchor else None
                night_valid = (
                    requested_poi.category in {"Vui chơi", "Ăn uống", "Cafe", "Mua sắm"}
                    and bool(requested_poi.opening_hours)
                    and TripPlanner._open_during_slot(requested_poi, "19:15", "20:45", TripPlanner.weekday_for_day(target_day or {}, state.get("start_date")))
                    and near_km is not None and near_km <= 10
                )
                if not night_valid:
                    return {"success": False, "action": "time_change_rejected", "message": f"{requested_poi.name} không phù hợp để xếp buổi tối (cần đúng nhóm hoạt động, có giờ mở cửa buổi tối và ở gần điểm trước đó). Mình có thể xếp địa điểm này vào ban ngày.", "data": {"state": state}}

            if target_slot is not None and target_day is not None:
                schedule = target_day.get("schedule", [])
                source = next(((day, slot) for day in plan for slot in day.get("schedule", [])
                               if (slot.get("place") or {}).get("place_id") == requested_id), None)
                if source and source[1] is target_slot:
                    return {"success": True, "action": "already_in_time_slot", "message": f"{requested_poi.name} đã nằm trong khung {target_slot.get('type')}.", "data": {"plan": state.get("plan"), "itinerary": plan, "state": state}}
                old_target_name = (target_slot.get("place") or {}).get("name")
                if source:
                    source_day, source_slot = source
                    source_slot["place"] = None
                    source_slot["status"] = "unavailable"
                    source_slot["reason"] = f"Đã chuyển {requested_poi.name} sang khung giờ khác theo yêu cầu."
                    source_slot["travel_from_previous"] = None
                target_index = schedule.index(target_slot)
                previous_place = next((s.get("place") for s in reversed(schedule[:target_index]) if s.get("place")), None) or {"latitude": (state.get("plan") or {}).get("hotel", {}).get("latitude"), "longitude": (state.get("plan") or {}).get("hotel", {}).get("longitude")}
                distance = recommender._distance_km(previous_place.get("latitude"), previous_place.get("longitude"), requested_poi.latitude, requested_poi.longitude)
                activity, _, _ = planner._build_activity(
                    place=requested_poi, people=int(state.get("people", 1)), distance_km=distance,
                    travel_minutes=TripPlannerTravel.minutes(distance), start_time=target_slot.get("start_time", ""),
                    end_time=target_slot.get("end_time", ""), slot_type=target_slot.get("type", requested_poi.category),
                )
                target_slot["place"] = activity
                target_slot["status"] = "scheduled"
                target_slot.pop("reason", None)
                hotel = find_hotel(recommender, int(state["hotel_id"]), state["province_code"])
                result = deepcopy(state.get("plan") or {})
                result["itinerary"] = plan
                if hotel:
                    rebuild_cost_summary(result, hotel, recommender)
                state["itinerary"] = result.get("itinerary", plan)
                state["plan"] = result
                moved_from = f" (thay {old_target_name})" if old_target_name else ""
                return {"success": True, "action": "move_place_time", "message": f"Đã xếp {requested_poi.name} vào {target_slot.get('type')}{moved_from}. Mình đã tính lại thứ tự tuyến và thời gian di chuyển trong ngày {target_day_number}.", "data": {"plan": result, "itinerary": result.get("itinerary", plan), "state": state}}

    # A generic cafe request is not a named-POI search. Add a distinct cafe
    # into a real gap and keep the day's meals and sightseeing in place.
    cafe_add_request = (
        requested_day is not None
        and 1 <= requested_day <= max(int(state.get("days", 1) or 1), len(plan))
        and "Cafe" in requested_categories
        and is_more_request(message)
        and any(term in fold_text(message) for term in ("them", "bo sung", "add", "more"))
    )
    if cafe_add_request:
        result, cafe_name, cafe_time = _add_day_cafe(
            state, recommender, planner, requested_day,
            afternoon_only=requested_time_of_day(message) == "afternoon",
        )
        if result is not None:
            return {
                "success": True,
                "action": "add_cafe",
                "message": f"Đã thêm {cafe_name} lúc {cafe_time} vào ngày {requested_day}; các bữa ăn và điểm tham quan khác được giữ nguyên.",
                "data": {"plan": result, "itinerary": result.get("itinerary", []), "state": state},
            }
        return {
            "success": False,
            "action": "clarify",
            "message": f"Ngày {requested_day} chưa có khoảng trống đủ để thêm quán cà phê có ảnh và địa chỉ phù hợp mà không chồng lịch. Lịch hiện tại được giữ nguyên.",
            "data": {"state": state},
        }

    # Add a named place from natural requests such as “ghé Chợ Cồn ngày 2”.
    # Prefer a genuinely vacant daytime slot; when a day is full, replace the
    # least prominent compatible stop and tell the traveler what changed.
    add_place_request = any(term in fold_text(message) for term in (
        "them ", "bo sung", "cho vao lich", "dua vao lich", "include ", "add ",
        "visit ", "muon ghe", "muon tham", "ghe ", "tham quan ", "toi muon di ",
    ))
    if add_place_request:
        requested_poi = find_requested_poi(message, recommender, plan)
        if requested_poi is not None and normalize(requested_poi.province_code) == normalize(state.get("province_code")):
            requested_id = int(requested_poi.id)
            existing_slot = next((slot for day in plan for slot in day.get("schedule", [])
                                  if int((slot.get("place") or {}).get("place_id") or -1) == requested_id), None)
            if existing_slot:
                return {"success": True, "action": "already_in_plan", "message": f"{requested_poi.name} đã có trong lịch trình.", "data": {"plan": state.get("plan"), "itinerary": plan, "state": state}}

            desired_day = requested_day
            candidate_days = [day for day in plan if desired_day is None or int(day.get("day", 0)) == desired_day]
            desired_time = requested_time_of_day(message)
            add_named_intent = any(term in fold_text(message) for term in (
                "them ", "bo sung", "ghe ", "tham quan ", "add ", "include ",
            ))
            if desired_time == "afternoon" and add_named_intent:
                hotel = find_hotel(recommender, int(state["hotel_id"]), state["province_code"])
                for target_day in candidate_days:
                    added_time = _append_named_afternoon_stop(
                        target_day, state, recommender, planner, requested_poi,
                    )
                    if added_time:
                        if hotel:
                            recompute_day(target_day, hotel, recommender)
                        result = deepcopy(state.get("plan") or {})
                        result.update({
                            "itinerary": plan,
                            "hotel": hotel_payload(hotel) if hotel else result.get("hotel"),
                            "province_code": state["province_code"],
                        })
                        if hotel:
                            rebuild_cost_summary(result, hotel, recommender)
                        state["itinerary"] = plan
                        state["plan"] = result
                        state["last_place_name"] = requested_poi.name
                        return {
                            "success": True,
                            "action": "add_named_place",
                            "message": f"Đã thêm {requested_poi.name} vào buổi chiều ngày {target_day.get('day')} lúc {added_time}; các điểm đã có được giữ nguyên.",
                            "data": {"plan": result, "itinerary": plan, "state": state},
                        }
            attraction_categories = {"Tham quan", "Vui chơi", "Giải trí", "Thiên nhiên", "Mua sắm"}
            food_categories = {"Ăn uống", "Cafe"}
            slot_candidates = []
            for day in candidate_days:
                weekday = TripPlanner.weekday_for_day(day, state.get("start_date"))
                for slot in day.get("schedule", []):
                    start = slot.get("start_time", "")
                    end = slot.get("end_time", "")
                    try:
                        duration = TripPlanner._duration_minutes(start, end)
                        start_minute = TripPlanner._minutes(start)
                    except (ValueError, TypeError):
                        continue
                    if duration <= 0 or duration >= 240 or start_minute < 8 * 60 or start_minute >= 18 * 60:
                        continue
                    if desired_time == "morning" and start_minute >= 12 * 60:
                        continue
                    if desired_time == "afternoon" and not (12 * 60 <= start_minute < 18 * 60):
                        continue
                    if desired_time == "evening":
                        continue
                    if add_named_intent and slot.get("place") is not None:
                        # An addition can use a genuine vacancy only. Existing
                        # stops require explicit “replace” wording to displace.
                        continue
                    slot_category = (slot.get("place") or {}).get("category")
                    if requested_poi.category in attraction_categories and slot_category not in attraction_categories and slot.get("place") is not None:
                        continue
                    if requested_poi.category in food_categories and slot_category not in food_categories and slot.get("place") is not None:
                        continue
                    if not TripPlanner._open_during_slot(requested_poi, start, end, weekday):
                        continue
                    old_place = slot.get("place") or {}
                    prominence = recommender._prominence_score(requested_poi) if hasattr(recommender, "_prominence_score") else 0
                    old_prominence = recommender._prominence_score(old_place) if old_place and hasattr(recommender, "_prominence_score") else 0
                    # Vacant slots rank first, then keep high-profile places in
                    # the itinerary whenever another compatible slot exists.
                    slot_candidates.append((0 if slot.get("place") is None else 1, old_prominence, start_minute, day, slot))

            slot_candidates.sort(key=lambda item: item[:3])
            if slot_candidates:
                _, _, _, target_day, target_slot = slot_candidates[0]
                hotel = find_hotel(recommender, int(state["hotel_id"]), state["province_code"])
                previous_place = next((s.get("place") for s in reversed(target_day.get("schedule", [])[:target_day["schedule"].index(target_slot)]) if s.get("place")), None)
                if previous_place is None:
                    previous_place = hotel_payload(hotel) if hotel else {}
                distance = recommender._distance_km(previous_place.get("latitude"), previous_place.get("longitude"), requested_poi.latitude, requested_poi.longitude)
                activity, _, _ = planner._build_activity(
                    place=requested_poi, people=int(state.get("people", 1)), distance_km=distance,
                    travel_minutes=TripPlannerTravel.minutes(distance), start_time=target_slot["start_time"],
                    end_time=target_slot["end_time"], slot_type=target_slot.get("type", requested_poi.category),
                )
                old_name = (target_slot.get("place") or {}).get("name")
                target_slot["place"] = activity
                target_slot["status"] = "scheduled"
                target_slot.pop("reason", None)
                if hotel:
                    recompute_day(target_day, hotel, recommender)
                result = deepcopy(state.get("plan") or {})
                result["itinerary"] = plan
                if hotel:
                    rebuild_cost_summary(result, hotel, recommender)
                state["itinerary"] = plan
                state["plan"] = result
                placement = f"ngày {target_day.get('day')} lúc {target_slot.get('start_time')}"
                msg = f"Đã xếp {requested_poi.name} vào {placement}." + (f" Để giữ lịch không quá dày, điểm {old_name} được thay thế." if old_name else "")
                return {"success": True, "action": "add_named_place", "message": msg, "data": {"plan": result, "itinerary": plan, "state": state}}
            return {"success": False, "action": "clarify", "message": f"Mình chưa thể xếp {requested_poi.name} hợp lý vào ngày này vì các khung ban ngày đã kín hoặc địa điểm không mở cửa trong khung còn phù hợp. Bạn muốn đổi ngày, hay thay một điểm nào trong lịch?", "data": {"state": state}}

    # Gộp các yêu cầu đổi nơi lưu trú với ngân sách, số ngày, số người,
    # phạm vi hoặc sở thích thành một lần lập lịch để không làm rơi ý sau.
    requested_day = _extract_day_number(text)
    new_days, days_delta = _trip_duration_update(message, state)
    new_people = _extract_number(text, ["người", "nguoi", "person", "people", "travelers"])
    budget_mentioned = any(k in text for k in [
        "ngân sách", "kinh phí", "chi phí", "budget", "triệu", "tr", "đồng", "vnd"
    ]) or bool(re.search(r"\d+(?:[.,]\d+)?\s*(?:k|nghin|ngan)\b", fold_text(text)))
    new_budget = _extract_budget(text) if budget_mentioned else None
    new_radius = _extract_radius(text, float(state.get("radius_km", 50) or 50))
    new_limit_per_day = _extract_limit_per_day(text, int(state.get("limit_per_day", 6) or 6))
    if category_removals:
        if requested_day is not None:
            day_exclusions = dict(state.get("day_excluded_categories") or {})
            current = list(day_exclusions.get(str(requested_day), day_exclusions.get(requested_day, [])) or [])
            current.extend(category for category in category_removals if category not in current)
            day_exclusions[str(requested_day)] = current
            state["day_excluded_categories"] = day_exclusions
        else:
            exclusions = list(state.get("excluded_categories") or [])
            exclusions.extend(category for category in category_removals if category not in exclusions)
            state["excluded_categories"] = exclusions
            state["preferences"] = [
                category for category in (state.get("preferences") or [])
                if category not in category_removals
            ]
    changed_trip = False
    if new_days and 1 <= new_days <= 30:
        state["days"] = new_days
        changed_trip = True
    if new_people and 1 <= new_people <= 100:
        state["people"] = new_people
        changed_trip = True
    if new_budget is not None:
        state["budget"] = new_budget
        changed_trip = True
    if new_radius is not None:
        state["radius_km"] = new_radius
        changed_trip = True
    if new_limit_per_day is not None:
        state["limit_per_day"] = new_limit_per_day
        changed_trip = True

    requested_days = int(state.get("days", 1) or 1)
    existing_days = {int(d.get("day")) for d in plan if str(d.get("day", "")).isdigit()}
    # ------------------------------------------------------------
    # 1. HOTEL INTENT: luôn xử lý trước replace-place.
    #    'đổi khách sạn' tuyệt đối không được đổi một POI bất kỳ.
    # ------------------------------------------------------------
    if intent["change_hotel"]:
        preference = None
        if intent["beach"]: preference = "near_beach"
        elif intent["center"]: preference = "near_center"
        elif intent["attractions"]: preference = "near_attractions"
        else: preference = state.get("accommodation_preference") or "near_center"

        hotel = _choose_hotel(recommender, state, preference)
        if hotel is None:
            return {"success": False, "message": "Không tìm thấy nơi lưu trú khác phù hợp trong tỉnh/thành này.", "data": {"state": state}}

        state["hotel_id"] = int(hotel.id)
        state["hotel_name"] = hotel.name
        state["accommodation_preference"] = preference
        add_category_to_day = None
        if requested_categories and is_more_request(message):
            if requested_day is not None:
                add_category_to_day = requested_day
                day_prefs = dict(state.get("chat_day_preferences") or {})
                current = list(day_prefs.get(str(requested_day), day_prefs.get(requested_day, [])) or [])
                current.extend(category for category in requested_categories if category not in current)
                day_prefs[str(requested_day)] = current
                state["chat_day_preferences"] = day_prefs
            else:
                prefs = list(state.get("preferences") or [])
                prefs.extend(category for category in requested_categories if category not in prefs)
                state["preferences"] = prefs
        state["chat_avoided_ids"] = []
        result = _rebuild_from_state(state, recommender, planner)
        if not result:
            return {"success": False, "message": "Không thể tạo lại lịch trình quanh khách sạn mới.", "data": {"state": state}}
        if add_category_to_day and add_category_to_day <= len(result.get("itinerary") or []):
            day = result["itinerary"][add_category_to_day - 1]
            current_count = sum(1 for slot in day.get("schedule", []) if slot.get("place") is not None)
            expanded, _ = _rebuild_single_day(
                state, recommender, planner, add_category_to_day,
                min(current_count + len(requested_categories), 7)
            )
            if expanded is not None:
                result = expanded
        vi = is_vietnamese_message(message)
        msg = (f"Đã đổi khách sạn sang {hotel.name} và cập nhật lại lịch trình theo các điều kiện bạn vừa nêu."
               if vi else f"Done — I changed the stay to {hotel.name} and rebuilt the itinerary with your updated trip requirements.")
        msg = append_followup_answer(msg, result)
        return {"success": True, "action": "change_hotel", "message": msg, "data": {"plan": result, "itinerary": result.get("itinerary", []), "state": state}}

    # Category opt-outs are applied to the planner itself, either for the
    # requested day or for the whole trip. They must not replace the last POI.
    if category_removals:
        labels = ", ".join(category.lower() for category in category_removals)
        if requested_day is not None:
            if requested_day > requested_days:
                return {"success": False, "action": "clarify", "message": f"Lịch hiện có {requested_days} ngày. Bạn muốn thêm ngày {requested_day} hay áp dụng yêu cầu này cho ngày nào đang có?", "data": {"state": state}}
            current_count = 5
            if requested_day <= len(plan):
                current_count = max(2, sum(1 for slot in plan[requested_day - 1].get("schedule", []) if slot.get("place") is not None))
            result, count = _rebuild_single_day(state, recommender, planner, requested_day, current_count)
            if result is None:
                return {"success": False, "action": "clarify", "message": f"Mình chưa tìm được phương án thay thế phù hợp sau khi bỏ {labels} khỏi ngày {requested_day}. Bạn muốn nới phạm vi tìm kiếm không?", "data": {"state": state}}
            msg = f"Đã bỏ nhóm {labels} khỏi ngày {requested_day} và xếp lại ngày đó với {count} điểm phù hợp."
            return {"success": True, "action": "exclude_category", "message": msg, "data": {"plan": state.get("plan"), "itinerary": state.get("itinerary", []), "state": state}}

        result = _rebuild_from_state(state, recommender, planner)
        if not result:
            return {"success": False, "action": "clarify", "message": f"Mình chưa thể xếp lại chuyến đi sau khi bỏ nhóm {labels}; dữ liệu địa điểm hiện có chưa đủ phương án thay thế.", "data": {"state": state}}
        msg = f"Đã bỏ nhóm {labels} khỏi chuyến đi và xếp lại lịch theo các sở thích còn lại."
        return {"success": True, "action": "exclude_category", "message": msg, "data": {"plan": result, "itinerary": result.get("itinerary", []), "state": state}}

    # ------------------------------------------------------------
    # 2. Nếu itinerary bị mất ngày: tự phục hồi đủ N ngày.
    #    Ví dụ user nói 'sao còn 1 ngày vậy' hoặc 'ngày 3 không có lịch'.
    # ------------------------------------------------------------
    if len(existing_days) < requested_days and any(k in fold_text(message) for k in ["sao con", "con ", "thieu", "khong co lich", "mat ngay", "missing day", "only one day"]):
        result = _rebuild_from_state(state, recommender, planner)
        if result and len(result.get("itinerary", [])) >= requested_days:
            state["last_place_name"] = None
            vi = is_vietnamese_message(message)
            msg = f"Mình đã khôi phục đủ {requested_days} ngày cho chuyến đi." if vi else f"I restored the full {requested_days}-day itinerary."
            return {"success": True, "action": "restore_days", "message": msg, "data": {"plan": result, "itinerary": result.get("itinerary", []), "state": state}}

    # ------------------------------------------------------------
    # 3. Day-level requests: rebuild the ENTIRE requested day.
    # ------------------------------------------------------------
    generic_meal_add = (
        requested_day is not None
        and 1 <= requested_day <= requested_days
        and "Ăn uống" in requested_categories
        and is_more_request(message)
        and any(term in fold_text(message) for term in (
            "quan an", "nha hang", "an ngon", "mon ngon", "food", "restaurant",
        ))
    )
    if generic_meal_add:
        result, restaurant_name, meal_name, old_restaurant, removed_duplicates = _update_day_meal_place(
            state, recommender, planner, requested_day, message,
        )
        if result is not None:
            removed_note = (
                f" Đã bỏ điểm bị lặp {', '.join(dict.fromkeys(removed_duplicates))} khỏi ngày {requested_day}."
                if removed_duplicates else ""
            )
            if old_restaurant:
                message_text = (
                    f"Đã đổi quán {meal_name.lower()} ngày {requested_day} từ {old_restaurant} "
                    f"sang {restaurant_name}. Các điểm tham quan và khung giờ khác được giữ nguyên.{removed_note}"
                )
            else:
                message_text = (
                    f"Đã xếp {restaurant_name} cho {meal_name.lower()} ngày {requested_day}. "
                    f"Các điểm tham quan và khung giờ khác được giữ nguyên.{removed_note}"
                )
            return {
                "success": True,
                "action": "update_meal_place",
                "message": message_text,
                "data": {"plan": result, "itinerary": result.get("itinerary", []), "state": state},
            }
        return {
            "success": False,
            "action": "clarify",
            "message": f"Mình chưa tìm được quán ăn khác, đang mở cửa và phù hợp tuyến đi để xếp cho ngày {requested_day}. Lịch hiện tại được giữ nguyên.",
            "data": {"state": state},
        }

    afternoon_request = any(term in fold_text(message) for term in [
        "buoi chieu", "chieu hon", "chieu", "afternoon",
    ])
    afternoon_category = next(
        (category for category in requested_categories
         if category in {"Vui chơi", "Giải trí", "Tham quan", "Thiên nhiên"}),
        None,
    )
    generic_afternoon_add = (
        afternoon_request
        and is_more_request(message)
        and not any(term in fold_text(message) for term in ("cafe", "ca phe", "coffee", "an uong", "nha hang", "hai san"))
    )
    if afternoon_request and is_more_request(message) and (afternoon_category or generic_afternoon_add):
        afternoon_category = afternoon_category or "Tham quan"
        add_only = any(term in fold_text(message) for term in ("them", "bo sung", "add"))
        result, changed = _update_afternoon_activity(
            state, recommender, planner, afternoon_category, requested_day, add_only,
        )
        if result is not None:
            day_labels = ", ".join(f"ngày {day}" for day in sorted({row[0] for row in changed}))
            place_labels = ", ".join((f"thêm {new}" if not old else f"{old} → {new}") for _, old, new in changed)
            msg = (
                f"Đã đổi {len(changed)} điểm buổi chiều sang {afternoon_category.lower()} ({day_labels}): {place_labels}. "
                "Các bữa ăn, quán cà phê và lịch các khung giờ khác được giữ nguyên."
            )
            msg = append_followup_answer(msg, result)
            return {"success": True, "action": "update_afternoon", "message": msg,
                    "data": {"plan": result, "itinerary": result.get("itinerary", []), "state": state}}
        return {
            "success": False,
            "action": "clarify",
            "message": f"Mình chưa tìm được khung giờ và địa điểm phù hợp để bổ sung buổi chiều{f' ngày {requested_day}' if requested_day else ''}. Lịch hiện tại được giữ nguyên; bạn có thể nêu ngày khác hoặc cho phép đi xa hơn.",
            "data": {"state": state},
        }

    if requested_day is not None and requested_day <= requested_days:
        more = is_more_request(message)
        less = any(k in text for k in ["ít hơn", "it hon", "bớt", "bot ", "ít điểm", "it diem", "fewer", "less"])
        day_change_request = any(k in text for k in [
            "đổi lịch", "doi lich", "làm lại", "lam lai", "đổi ngày", "doi ngay",
            "thay đổi ngày", "thay doi ngay", "rebuild day", "change day", "redo day",
            "lịch khác ngày", "lich khac ngay", "không có lịch", "khong co lich",
            "đổi hết", "doi het", "toàn bộ ngày", "toan bo ngay", "làm lại toàn bộ ngày", "lam lai toan bo ngay",
        ]) or more or less or new_limit_per_day is not None or new_radius is not None
        if day_change_request:
            if requested_categories and is_more_request(message):
                day_prefs = dict(state.get("chat_day_preferences") or {})
                current = list(day_prefs.get(str(requested_day), day_prefs.get(requested_day, [])) or [])
                current.extend(category for category in requested_categories if category not in current)
                day_prefs[str(requested_day)] = current
                state["chat_day_preferences"] = day_prefs
            current_count = 5
            if requested_day <= len(plan):
                current_count = sum(1 for x in plan[requested_day - 1].get("schedule", []) if x.get("place") is not None)
                current_count = max(current_count, 2)
            if new_limit_per_day is not None:
                target_count = int(state.get("limit_per_day", current_count))
            elif more:
                target_count = min(current_count + (len(requested_categories) if requested_categories else 2), 7)
            elif less:
                target_count = max(current_count - 2, 2)
            else:
                target_count = int(state.get("limit_per_day", 7) or 7)
            result, count = _rebuild_single_day(state, recommender, planner, requested_day, target_count)
            if result is not None:
                state["last_place_name"] = None
                vi = is_vietnamese_message(message)
                missing = int((result.get("itinerary") or [])[requested_day - 1].get("missing_slots") or 0) if len(result.get("itinerary") or []) >= requested_day else 0
                if vi:
                    msg = f"Đã làm lại ngày {requested_day} với {count} địa điểm mới. Các ngày khác được giữ nguyên."
                    if missing: msg += f" Còn {missing} khung giờ chưa có địa điểm phù hợp nên mình không lặp địa điểm cũ; bạn có thể tăng bán kính."
                else:
                    msg = f"Done — I rebuilt Day {requested_day} with {count} places. Other days stay unchanged."
                    if missing: msg += f" {missing} slots remain unfilled rather than repeating a place; increase the radius to find more options."
                msg = append_followup_answer(msg, result)
                return {"success": True, "action": "replace_day", "message": msg, "data": {"plan": result, "itinerary": result.get("itinerary", []), "state": state, "day": requested_day}}
            return {"success": False, "message": f"Không đủ dữ liệu để tạo lại ngày {requested_day}. Hãy tăng phạm vi quanh khách sạn.", "data": {"state": state}}

    # ------------------------------------------------------------
    # 4. Full-trip changes / natural 'make another plan'.
    # ------------------------------------------------------------
    if intent["regenerate"]:
        current_ids = [int(p["place_id"]) for _, _, p in _plan_place_rows(plan) if p.get("place_id") is not None]
        history = {int(x) for x in state.get("chat_avoided_ids", []) if str(x).isdigit()}
        history.update(current_ids)
        state["chat_avoided_ids"] = list(history)
        result = _rebuild_from_state(state, recommender, planner)
        if not result or not result.get("itinerary"):
            state["chat_avoided_ids"] = []
            result = _rebuild_from_state(state, recommender, planner)
        if not result:
            return {"success": False, "message": "Không thể tạo lịch trình mới với dữ liệu hiện có.", "data": {"state": state}}
        vi = is_vietnamese_message(message)
        msg = (f"Đã tạo lại toàn bộ lịch trình {state.get('days', requested_days)} ngày với các địa điểm mới khi DB có phương án thay thế."
               if vi else f"Done — I rebuilt the full {state.get('days', requested_days)}-day itinerary with new places where alternatives exist.")
        msg = append_followup_answer(msg, result)
        return {"success": True, "action": "create_new_plan", "message": msg, "data": {"plan": result, "itinerary": result.get("itinerary", []), "state": state}}

    # ------------------------------------------------------------
    # 5. Replace one or many specific places.
    # ------------------------------------------------------------
    if intent["replace"]:
        rows = _plan_place_rows(plan)
        requested_old_names = [name for name in sorted(find_plan_place_names(plan), key=len, reverse=True) if fold_text(name) in fold_text(message)]
        targets = []
        for day, slot, place in rows:
            if requested_old_names and any(normalize(place.get("name")) == normalize(n) for n in requested_old_names):
                targets.append((day, slot, place))
        if not targets:
            named_target = find_requested_poi(message, recommender, plan)
            if named_target is not None:
                targets = [
                    (day, slot, place) for day, slot, place in rows
                    if int(place.get("place_id") or -1) == int(named_target.id)
                ]
        if not targets:
            old_name = find_name_in_message(message, plan, state.get("last_place_name"))
            if old_name:
                for day, slot, place in rows:
                    if normalize(place.get("name")) == normalize(old_name):
                        targets = [(day, slot, place)]; break
        if not targets and rows:
            count_match = re.search(
                r"\b(?:doi|thay|bo|xoa|remove|replace|change)\s*(\d{1,2})\s*(?:dia diem|diem|cho|places?|spots?)?",
                fold_text(text),
            )
            if count_match:
                targets = rows[-max(1, min(int(count_match.group(1)), len(rows))):]
            else:
                return {"success": False, "action": "clarify", "message": "Bạn muốn bỏ hoặc thay địa điểm nào? Hãy nêu tên điểm, hoặc nói ‘điểm cuối ngày 2’ để mình xác định đúng chỗ.", "data": {"state": state}}

        count = min(_extract_requested_count(text), len(targets))
        if len(targets) > count and not requested_old_names:
            targets = targets[-count:]

        hotel = find_hotel(recommender, int(state["hotel_id"]), state["province_code"])
        used_ids = {int(p.get("place_id")) for _, _, p in rows if p.get("place_id") is not None}
        used_ids.add(int(state["hotel_id"]))
        keywords = _keywords_for_message(text)
        category = classify_requested_category(message)
        replaced = []
        for target_day, slot, old_place in targets:
            trip_weekday = TripPlanner.weekday_for_day(target_day, state.get("start_date"))
            prev_lat, prev_lon = hotel.latitude, hotel.longitude
            for s2 in target_day.get("schedule", []):
                if s2 is slot: break
                p2 = s2.get("place")
                if p2 and p2.get("latitude") is not None:
                    prev_lat, prev_lon = p2.get("latitude"), p2.get("longitude")
            candidates = _replacement_candidates(recommender, state["province_code"], prev_lat, prev_lon, category, keywords, used_ids, 80, slot.get("start_time"), slot.get("end_time"), trip_weekday)
            if not candidates:
                candidates = _replacement_candidates(recommender, state["province_code"], prev_lat, prev_lon, None, keywords, used_ids, 80, slot.get("start_time"), slot.get("end_time"), trip_weekday)
            replacement = candidates[0] if candidates else None
            if not replacement: continue
            used_ids.add(int(replacement.id))
            distance = recommender._distance_km(prev_lat, prev_lon, replacement.latitude, replacement.longitude)
            activity, _, _ = planner._build_activity(place=replacement, people=int(state.get("people", 1)), distance_km=distance, travel_minutes=TripPlannerTravel.minutes(distance), start_time=slot.get("start_time", ""), end_time=slot.get("end_time", ""), slot_type=slot.get("type", replacement.category))
            slot["place"] = activity
            replaced.append((old_place.get("name"), replacement.name))

        if replaced:
            temp = {"itinerary": plan, "budget": state.get("budget")}
            rebuild_cost_summary(temp, hotel, recommender)
            state["itinerary"] = plan
            state["last_place_name"] = replaced[-1][1]
            result = state.get("plan") or {}
            result.update({"itinerary": plan, "hotel": hotel_payload(hotel), "province_code": state["province_code"], "cost_summary": temp["cost_summary"]})
            state["plan"] = result
            names = ", ".join(f"{old} → {new}" for old, new in replaced)
            vi = is_vietnamese_message(message)
            msg = f"Đã thay {len(replaced)} địa điểm: {names}." if vi else f"Done — I changed {len(replaced)} place(s): {names}."
            msg = append_followup_answer(msg, result)
            return {"success": True, "action": "replace_place", "message": msg, "data": {"plan": result, "itinerary": plan, "state": state, "replaced": replaced}}

    # ------------------------------------------------------------
    # 6. Category / preference changes.
    # ------------------------------------------------------------
    category = classify_requested_category(message)
    if requested_categories and is_more_request(message):
        prefs = list(state.get("preferences") or [])
        prefs.extend(item for item in requested_categories if item not in prefs)
        state["preferences"] = prefs
        try:
            current_limit = int(state.get("limit_per_day", 6) or 6)
        except (TypeError, ValueError):
            current_limit = 6
        # "More sightseeing" should add visit opportunities as well as alter
        # category ranking; meals are scheduled independently of this limit.
        state["limit_per_day"] = min(max(current_limit, 1) + 1, 7)
        current_ids = {int(p["place_id"]) for _, _, p in _plan_place_rows(plan) if p.get("place_id") is not None}
        avoided = {int(x) for x in state.get("chat_avoided_ids", []) if str(x).isdigit()}
        state["chat_avoided_ids"] = list(avoided | current_ids)
        result = _rebuild_from_state(state, recommender, planner)
        if result:
            vi = is_vietnamese_message(message)
            labels = ", ".join(item.lower() for item in requested_categories)
            msg = (f"Mình đã thêm sở thích {labels} bên cạnh các sở thích trước và xếp lại toàn bộ lịch theo đó; khách sạn được giữ nguyên."
                   if vi else f"Done — I added {labels} to your existing preferences and rebuilt the full itinerary around them. Your hotel stays the same.")
            return {"success": True, "action": "increase_category", "message": msg, "data": {"plan": result, "itinerary": result.get("itinerary", []), "state": state}}

    if requested_categories:
        prefs = list(state.get("preferences") or [])
        prefs.extend(item for item in requested_categories if item not in prefs)
        state["preferences"] = prefs
        if any(k in fold_text(message) for k in ["uu tien", "thich", "muon", "cho toi", "tap trung", "more", "focus", "prefer"]):
            result = _rebuild_from_state(state, recommender, planner)
            if result:
                labels = ", ".join(item.lower() for item in requested_categories)
                return {"success": True, "action": "preference_updated", "message": f"Mình đã thêm sở thích {labels} bên cạnh các sở thích trước và xếp lại toàn bộ lịch theo đó; khách sạn được giữ nguyên.", "data": {"plan": result, "itinerary": result.get("itinerary", []), "state": state}}

    if changed_trip:
        result = _rebuild_from_state(state, recommender, planner)
        if result:
            vi = is_vietnamese_message(message)
            if days_delta > 0:
                msg = f"Đã kéo dài chuyến đi thêm {days_delta} ngày, lịch mới có {new_days} ngày." if vi else f"Done — I added {days_delta} day(s); the trip now has {new_days} days."
            elif days_delta < 0:
                msg = f"Đã rút ngắn chuyến đi, lịch mới có {new_days} ngày." if vi else f"Done — I shortened the trip to {new_days} days."
            elif new_days is not None:
                msg = f"Đã cập nhật chuyến đi thành {new_days} ngày." if vi else f"Done — I set the trip to {new_days} days."
            else:
                msg = "Đã cập nhật thông tin chuyến đi và tạo lại lịch trình." if vi else "Done — I updated the trip details and rebuilt the itinerary."
            msg = append_followup_answer(msg, result)
            return {"success": True, "action": "trip_updated", "message": msg, "data": {"plan": result, "itinerary": result.get("itinerary", []), "state": state}}

    clarification = (
        "Mình chưa cập nhật lịch vì chưa xác định chắc yêu cầu này cần thay đổi phần nào. Bạn cho mình biết tên địa điểm hoặc ngày muốn chỉnh, và muốn thêm, bỏ, đổi hay chuyển sang buổi nào nhé."
        if is_vietnamese_message(message)
        else "I have not changed the itinerary because I could not identify the requested edit. Please name the place or day and say whether you want to add, remove, replace, or move it to another time."
    )
    return {"success": True, "action": "clarify", "message": clarification, "data": {"state": state}}


@app.get("/api/chat/status")
def get_chat_status():
    return chat_model_status()


@app.post("/api/admin/reload-system")
def reload_system(request: Request):
    _admin_user(request)
    invalidate_system_cache()
    get_system(force_reload=True)
    return {"success": True, "message": f"Đã tải lại dữ liệu places từ {DATA_BACKEND.upper()}."}
