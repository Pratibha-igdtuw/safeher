"""
SafeHer v2 - AI Guardian for Women's Safety
Hackathon Prototype (Flask backend + WebSocket + Admin Analytics)

FEATURES ADDED:
1. REAL AI-BASED DISTRESS DETECTION (audio classification via TensorFlow.js)
2. PREDICTIVE RISK ALERT (proactive warnings for low-safety areas)
3. MULTI-USER LOGIN/ACCOUNTS (email+password signup/login, session-based)
4. REAL-TIME CONTACT NOTIFICATION (WebSocket push instead of polling)
5. ANALYTICS DASHBOARD (admin heatmap view of all audits + stats)

Run:
    pip install -r requirements.txt
    python app.py

Then open http://127.0.0.1:5000 in your browser.
"""

from flask import Flask, render_template, request, jsonify, session, redirect, url_for, g
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import sqlite3
import os
import math
import io
import base64
import json
import hashlib
import logging
from logging.handlers import RotatingFileHandler

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Load environment variables from a local .env file (if present) BEFORE
# config.py is imported below — config.py's Config classes read
# os.environ at import/class-definition time, so .env has to be loaded
# first or those values never make it in. No-op in environments (CI, prod
# containers) where real environment variables are already set some other
# way.
load_dotenv(os.path.join(BASE_DIR, ".env"))

# --- Tier 2: 2FA -------------------------------------------------------
import pyotp
import qrcode
import logging
from collections import defaultdict

from utils.alerts import send_sos_alert
from utils.route_safety import get_route_safety_score
from utils.distress_detector import check_distress
from utils.safety_services import get_nearby_services
from utils.audio_classifier import classify_audio_payload
from utils.risk_predictor import get_predictor
<<<<<<< HEAD
=======
from utils.push import (
    send_push_to_user,
    send_push_to_users,
    get_vapid_public_key,
    push_configured,
)
>>>>>>> 12f0b65 (Updated SafeHer features and UI)
from validators import (
    validate_json,
    LoginSchema,
    SignupSchema,
    ContactSchema,
    ContactInviteSchema,
    SOSSchema,
    DistressCheckSchema,
    RouteSafetySchema,
    CheckinStartSchema,
    AuditSchema,
    CheckLocationRiskSchema,
    GuardianShareSchema,
    FeedPostSchema,
<<<<<<< HEAD
=======
    PushSubscribeSchema,
    PushUnsubscribeSchema,
    ClientErrorSchema,
>>>>>>> 12f0b65 (Updated SafeHer features and UI)
)

from config import get_config

# ---------------------------------------------------------------------------
# TIER 3 PART 1: environment-driven config
# ---------------------------------------------------------------------------
# FLASK_ENV=production enables secure cookies + HTTPS-only headers.
# Defaults to "development" so local `python app.py` still works over http.
FLASK_ENV = os.environ.get("FLASK_ENV", "development").lower()
IS_PRODUCTION = FLASK_ENV == "production"

# Comma-separated allow-list, e.g. "https://safeher.app,https://www.safeher.app"
CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("CORS_ALLOWED_ORIGINS", "http://127.0.0.1:5000,http://localhost:5000").split(",")
    if origin.strip()
]

# ---------------------------------------------------------------------------
# TIER 3 PART 1: environment-driven config
# ---------------------------------------------------------------------------
# FLASK_ENV=production enables secure cookies + HTTPS-only headers.
# Defaults to "development" so local `python app.py` still works over http.
FLASK_ENV = os.environ.get("FLASK_ENV", "development").lower()
IS_PRODUCTION = FLASK_ENV == "production"

# Comma-separated allow-list, e.g. "https://safeher.app,https://www.safeher.app"
CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("CORS_ALLOWED_ORIGINS", "http://127.0.0.1:5000,http://localhost:5000").split(",")
    if origin.strip()
]

app = Flask(__name__)
<<<<<<< HEAD
app.config.from_object(get_config())

DB_PATH = app.config["DATABASE_PATH"]  # kept for any code/tests referencing DB_PATH directly

socketio = SocketIO(app, cors_allowed_origins=app.config["CORS_ORIGINS"])
=======
>>>>>>> 12f0b65 (Updated SafeHer features and UI)
app.secret_key = os.environ.get("SECRET_KEY", "safeher-secret-key-change-in-production")

# --- Secure session / cookie config ---
app.config.update(
    SESSION_COOKIE_SECURE=IS_PRODUCTION,   # only sent over HTTPS in production
    SESSION_COOKIE_HTTPONLY=True,          # never accessible to JS
    SESSION_COOKIE_SAMESITE="Lax",
)

# --- Rate limiting storage (in-memory by default; point RATELIMIT_STORAGE_URI
#     at redis:// in production so limits are shared across workers) ---
app.config["RATELIMIT_STORAGE_URI"] = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")
app.config["RATELIMIT_HEADERS_ENABLED"] = True  # adds Retry-After / X-RateLimit-* headers

socketio = SocketIO(app, cors_allowed_origins=CORS_ALLOWED_ORIGINS)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    storage_uri=app.config["RATELIMIT_STORAGE_URI"],
    default_limits=[],  # no global default; limits are applied per-route below
)

# --- Security headers (flask-talisman) ---
try:
    from flask_talisman import Talisman

    Talisman(
        app,
        force_https=IS_PRODUCTION,
        strict_transport_security=IS_PRODUCTION,
        session_cookie_secure=IS_PRODUCTION,
        content_security_policy={
            "default-src": "'self'",
            "script-src": "'self' 'unsafe-inline' https://cdn.socket.io https://unpkg.com",
            "style-src": "'self' 'unsafe-inline' https://unpkg.com",
            "img-src": "'self' data: https://*.tile.openstreetmap.org",
            "connect-src": "'self' ws: wss:",
        },
        content_security_policy_nonce_in=[],
    )
except ImportError:  # pragma: no cover - only hit if flask-talisman isn't installed
    @app.after_request
    def _fallback_security_headers(resp):
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: https://*.tile.openstreetmap.org; "
            "script-src 'self' 'unsafe-inline' https://cdn.socket.io https://unpkg.com; "
            "style-src 'self' 'unsafe-inline' https://unpkg.com; connect-src 'self' ws: wss:",
        )
        return resp


# ---------------------------------------------------------------------------
# TIER 3 PART 1: failed-login logging (foundation for future lockout/alerting)
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
security_logger = logging.getLogger("safeher.security")

_failed_login_counts = defaultdict(int)


def log_failed_login(email, ip):
    key = (email, ip)
    _failed_login_counts[key] += 1
    security_logger.warning(
        "Failed login attempt #%d for email=%s from ip=%s",
        _failed_login_counts[key], email, ip,
    )


def log_successful_login(email, ip):
    _failed_login_counts.pop((email, ip), None)


@app.errorhandler(429)
def handle_rate_limit(e):
    # flask-limiter (RATELIMIT_HEADERS_ENABLED=True, above) already attaches
    # a Retry-After header to the response for us; surface that same value
    # in the JSON body too so clients that don't read headers still get it.
    resp = jsonify({
        "error": "rate_limited",
        "message": "Too many attempts. Please wait before trying again.",
    })
    resp.status_code = 429
    return resp

<<<<<<< HEAD

# ---------------------------------------------------------------------------
# Structured logging (console + rotating file handler)
# ---------------------------------------------------------------------------
def configure_logging(flask_app):
    """INFO for normal request/connect events, WARNING for failed logins
    and invalid 2FA codes, ERROR for unhandled exceptions. Every SOS
    trigger, failed login attempt, and 2FA failure is logged with a
    timestamp + hashed user identifier — the audit trail a real safety
    app needs."""
    log_dir = flask_app.config["LOG_DIR"]
    os.makedirs(log_dir, exist_ok=True)

    log_level = getattr(logging, str(flask_app.config.get("LOG_LEVEL", "INFO")).upper(), logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"
    )

    file_handler = RotatingFileHandler(
        os.path.join(log_dir, "app.log"),
        maxBytes=flask_app.config.get("LOG_MAX_BYTES", 1_000_000),
        backupCount=flask_app.config.get("LOG_BACKUP_COUNT", 5),
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(log_level)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(log_level)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    # Avoid duplicate handlers if configure_logging() runs more than once
    # (e.g. re-imported by tests).
    root_logger.handlers = [file_handler, console_handler]

    werkzeug_logger = logging.getLogger("werkzeug")
    werkzeug_logger.handlers = [file_handler, console_handler]
    werkzeug_logger.setLevel(log_level)

    return logging.getLogger("safeher")


logger = configure_logging(app)


def hash_identifier(value):
    """One-way hash of an identifying value (email, user id) for audit
    logs, so log files don't contain raw PII while still letting the same
    user's events be correlated across log lines."""
    if value is None:
        return "unknown"
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]


@app.errorhandler(Exception)
def handle_unhandled_exception(exc):
    from werkzeug.exceptions import HTTPException

    if isinstance(exc, HTTPException):
        # Let Flask's normal handling deal with expected HTTP errors
        # (404s, etc.) instead of logging them as unhandled crashes.
        return exc

    logger.error("Unhandled exception on %s %s: %s", request.method, request.path, exc, exc_info=True)
    return jsonify({"error": "internal server error"}), 500

=======
>>>>>>> 12f0b65 (Updated SafeHer features and UI)

# ---------------------------------------------------------------------------
# User model for Flask-Login
# ---------------------------------------------------------------------------
class User(UserMixin):
    def __init__(self, user_id, email, is_admin=False):
        self.id = user_id
        self.email = email
        self.is_admin = is_admin


@login_manager.user_loader
def load_user(user_id):
    conn = get_db()
    row = conn.execute("SELECT id, email, is_admin FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    if not row:
        return None
    return User(row["id"], row["email"], row["is_admin"])


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def get_db():
    """Open a SQLite connection configured for concurrent web-app usage.

    Two things make "database is locked" errors much rarer under
    concurrent writes (e.g. simultaneous SOS + location-history inserts):
    - WAL mode lets readers and a writer proceed concurrently instead of
      sqlite3's default behavior of blocking all readers during a write.
    - A busy timeout makes SQLite retry for a bit instead of immediately
      raising `sqlite3.OperationalError: database is locked` when it does
      hit contention.

    Reads DATABASE_PATH from app.config so DATABASE_URL (see config.py /
    .env.example) controls where the file lives.
    """
    db_path = app.config.get("DATABASE_PATH", DB_PATH)
    timeout_s = app.config.get("DB_CONNECT_TIMEOUT_S", 10)
    busy_timeout_ms = app.config.get("DB_BUSY_TIMEOUT_MS", 5000)

    conn = sqlite3.connect(db_path, timeout=timeout_s)
    conn.row_factory = sqlite3.Row
    if db_path != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
    return conn


def init_db():
    conn = get_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            relation TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            trigger_type TEXT NOT NULL,
            latitude REAL,
            longitude REAL,
            message TEXT,
            alert_type TEXT DEFAULT 'sos',
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS checkins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            deadline TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS audits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            area_name TEXT,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            lighting INTEGER,
            openness INTEGER,
            walkpath INTEGER,
            security INTEGER,
            transport INTEGER,
            crowd INTEGER,
            overall_score INTEGER,
            comment TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS guardian_shares (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            latitude REAL,
            longitude REAL,
            active INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS feed_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            message TEXT NOT NULL,
            post_type TEXT NOT NULL DEFAULT 'alert',
            latitude REAL,
            longitude REAL,
            area_name TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS linked_contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            contact_user_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            UNIQUE(user_id, contact_user_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(contact_user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS risk_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            latitude REAL,
            longitude REAL,
            risk_score REAL,
            nearby_low_score_area TEXT,
            dismissed INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        -- ================= TIER 2 =================

        CREATE TABLE IF NOT EXISTS live_tracking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            started_at TEXT NOT NULL,
            ended_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS location_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            timestamp TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS offline_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            action_type TEXT NOT NULL,
            payload TEXT,
            synced INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            synced_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        -- ================= TIER 3 PART 3 =================

        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            endpoint TEXT UNIQUE NOT NULL,
            p256dh TEXT NOT NULL,
            auth TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        """
    )
    conn.commit()

    # --- Migration: add ML prediction columns to risk_alerts if missing ---
    existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(risk_alerts)")}
    if "prediction_score" not in existing_cols:
        conn.execute("ALTER TABLE risk_alerts ADD COLUMN prediction_score REAL")
    if "prediction_confidence" not in existing_cols:
        conn.execute("ALTER TABLE risk_alerts ADD COLUMN prediction_confidence REAL")

    # --- Migration: add TOTP 2FA columns to users if missing ---
    user_cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
    if "totp_secret" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN totp_secret TEXT")
    if "totp_enabled" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN totp_enabled INTEGER NOT NULL DEFAULT 0")

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Auth Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    return render_template("landing.html")


def _login_rate_limit_key():
    """5 attempts per 15 min per IP + email combination."""
    email = ""
    try:
        body = request.get_json(silent=True) or {}
        email = (body.get("email") or "").strip().lower()
    except Exception:
        pass
    return f"{get_remote_address()}:{email}"


def _login_rate_limit_key():
    """5 attempts per 15 min per IP + email combination."""
    email = ""
    try:
        body = request.get_json(silent=True) or {}
        email = (body.get("email") or "").strip().lower()
    except Exception:
        pass
    return f"{get_remote_address()}:{email}"


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per 15 minutes", methods=["POST"], key_func=_login_rate_limit_key)
@validate_json(LoginSchema)
def login():
    if request.method == "POST":
        data = g.validated_data
        email = data["email"].strip().lower()
        password = data["password"]
        ip = get_remote_address()

        conn = get_db()
        user_row = conn.execute(
            "SELECT id, email, password_hash, is_admin, totp_enabled FROM users WHERE email = ?", (email,)
        ).fetchone()
        conn.close()

        if user_row and check_password_hash(user_row["password_hash"], password):
            if user_row["totp_enabled"]:
                # Password is correct, but TOTP code still required.
                # Stash the user id in session; nothing is logged in yet.
                session["pending_2fa_user_id"] = user_row["id"]
                logger.info("Password verified, awaiting 2FA code: user=%s", hash_identifier(email))
                return jsonify({"status": "2fa_required"})

            log_successful_login(email, ip)
            user = User(user_row["id"], user_row["email"], user_row["is_admin"])
            login_user(user)
            logger.info("Successful login: user=%s", hash_identifier(email))
            return jsonify({"status": "logged_in", "is_admin": user.is_admin})
        else:
<<<<<<< HEAD
            logger.warning("Failed login attempt: user=%s", hash_identifier(email))
=======
>>>>>>> 12f0b65 (Updated SafeHer features and UI)
            log_failed_login(email, ip)
            return jsonify({"error": "invalid email or password"}), 401

    return render_template("login.html")


@app.route("/signup", methods=["GET", "POST"])
@limiter.limit("10 per hour", methods=["POST"])
@validate_json(SignupSchema)
def signup():
    if request.method == "POST":
        data = g.validated_data
        email = data["email"].strip().lower()
        password = data["password"]

        conn = get_db()
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            conn.close()
            return jsonify({"error": "email already exists"}), 409

        password_hash = generate_password_hash(password)
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, is_admin, created_at) VALUES (?, ?, ?, ?)",
            (email, password_hash, 0, datetime.utcnow().isoformat()),
        )
        conn.commit()
        user_id = cur.lastrowid
        conn.close()

        user = User(user_id, email, False)
        login_user(user)
        return jsonify({"status": "signed_up", "is_admin": False})

    return render_template("signup.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# TIER 2 FEATURE: Two-Factor Authentication (TOTP)
# ---------------------------------------------------------------------------
@app.route("/api/2fa/status", methods=["GET"])
@login_required
def two_fa_status():
    conn = get_db()
    row = conn.execute("SELECT totp_enabled FROM users WHERE id = ?", (current_user.id,)).fetchone()
    conn.close()
    return jsonify({"enabled": bool(row["totp_enabled"]) if row else False})


@app.route("/api/2fa/setup", methods=["POST"])
@login_required
def enable_2fa_setup():
    """Generate a new TOTP secret + QR code for the current user.

    The secret is stored right away but totp_enabled stays 0 until the user
    proves they scanned it correctly via /api/2fa/confirm. This avoids a
    user getting locked out because they saved a QR code that was never
    actually confirmed.
    """
    secret = pyotp.random_base32()

    conn = get_db()
    conn.execute("UPDATE users SET totp_secret = ? WHERE id = ?", (secret, current_user.id))
    conn.commit()
    conn.close()

    uri = pyotp.totp.TOTP(secret).provisioning_uri(name=current_user.email, issuer_name="SafeHer")

    qr_img = qrcode.make(uri)
    buf = io.BytesIO()
    qr_img.save(buf, format="PNG")
    qr_base64 = base64.b64encode(buf.getvalue()).decode("ascii")

    return jsonify({
        "secret": secret,
        "qr_code": f"data:image/png;base64,{qr_base64}",
    })


@app.route("/api/2fa/confirm", methods=["POST"])
@login_required
def enable_2fa_confirm():
    """Confirm setup by verifying a code generated from the just-scanned QR."""
    data = request.get_json(force=True)
    code = (data.get("code") or "").strip()

    conn = get_db()
    row = conn.execute("SELECT totp_secret FROM users WHERE id = ?", (current_user.id,)).fetchone()

    if not row or not row["totp_secret"]:
        conn.close()
        return jsonify({"error": "call /api/2fa/setup first"}), 400

    totp = pyotp.TOTP(row["totp_secret"])
    if not totp.verify(code, valid_window=1):
        conn.close()
        logger.warning("Invalid 2FA setup code: user=%s", hash_identifier(current_user.email))
        return jsonify({"error": "invalid code"}), 401

    conn.execute("UPDATE users SET totp_enabled = 1 WHERE id = ?", (current_user.id,))
    conn.commit()
    conn.close()
    logger.info("2FA enabled: user=%s", hash_identifier(current_user.email))
    return jsonify({"status": "2fa_enabled"})


@app.route("/api/2fa/disable", methods=["POST"])
@login_required
def disable_2fa():
    data = request.get_json(force=True)
    password = data.get("password", "")

    conn = get_db()
    row = conn.execute("SELECT password_hash, is_admin FROM users WHERE id = ?", (current_user.id,)).fetchone()

    if not row or not check_password_hash(row["password_hash"], password):
        conn.close()
        logger.warning("Failed 2FA-disable attempt (wrong password): user=%s", hash_identifier(current_user.email))
        return jsonify({"error": "incorrect password"}), 401

    if row["is_admin"]:
        conn.close()
        return jsonify({"error": "2FA is mandatory for admin accounts and cannot be disabled"}), 403

    conn.execute("UPDATE users SET totp_enabled = 0, totp_secret = NULL WHERE id = ?", (current_user.id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "2fa_disabled"})


@app.route("/api/2fa/verify-login", methods=["POST"])
def verify_2fa_login():
    """Second step of login for accounts with 2FA enabled."""
    pending_user_id = session.get("pending_2fa_user_id")
    if not pending_user_id:
        return jsonify({"error": "no pending 2FA login"}), 400

    data = request.get_json(force=True)
    code = (data.get("code") or "").strip()

    conn = get_db()
    user_row = conn.execute(
        "SELECT id, email, is_admin, totp_secret FROM users WHERE id = ?", (pending_user_id,)
    ).fetchone()
    conn.close()

    if not user_row or not user_row["totp_secret"]:
        session.pop("pending_2fa_user_id", None)
        return jsonify({"error": "2FA not configured for this account"}), 400

    totp = pyotp.TOTP(user_row["totp_secret"])
    if not totp.verify(code, valid_window=1):
        logger.warning("Invalid 2FA login code: user=%s", hash_identifier(user_row["email"]))
        return jsonify({"error": "invalid or expired code"}), 401

    session.pop("pending_2fa_user_id", None)
    logger.info("Successful 2FA login: user=%s", hash_identifier(user_row["email"]))
    user = User(user_row["id"], user_row["email"], user_row["is_admin"])
    login_user(user)
    return jsonify({"status": "logged_in", "is_admin": user.is_admin})


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@app.route("/dashboard")
@login_required
def dashboard():
    return render_template("index.html", is_admin=current_user.is_admin)


@app.route("/offline")
def offline_fallback():
    """Served by the browser (via the service worker's navigation fallback)
    when a page request fails while offline."""
    return render_template("offline.html")


# ---------------------------------------------------------------------------
# Admin Dashboard
# ---------------------------------------------------------------------------
@app.route("/admin")
@login_required
def admin_dashboard():
    if not current_user.is_admin:
        return jsonify({"error": "unauthorized"}), 403
    return render_template("admin.html")


@app.route("/api/admin/analytics")
@login_required
def admin_analytics():
    """
    Return analytics data for admin dashboard:
    - all audits with coords (for heatmap)
    - total audits, avg safety score
    - alerts timeline
    - most unsafe zones
    """
    if not current_user.is_admin:
        return jsonify({"error": "unauthorized"}), 403

    conn = get_db()

    # All audits for heatmap
    audits = conn.execute("SELECT * FROM audits ORDER BY created_at DESC").fetchall()
    audits_list = [dict(a) for a in audits]

    # Stats
    total_audits = len(audits_list)
    avg_score = round(sum([a["overall_score"] for a in audits_list]) / total_audits) if total_audits > 0 else 0

    # High-risk zones (score < 45)
    high_risk = [a for a in audits_list if a["overall_score"] < 45]
    high_risk_summary = {
        "count": len(high_risk),
        "zones": sorted(
            [(a["area_name"], a["overall_score"]) for a in high_risk],
            key=lambda x: x[1]
        )[:10]  # top 10 worst
    }

    # Alerts over time (last 7 days)
    seven_days_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()
    alerts = conn.execute(
        "SELECT DATE(created_at) as date, COUNT(*) as count FROM alerts WHERE created_at > ? GROUP BY date ORDER BY date",
        (seven_days_ago,)
    ).fetchall()
    alerts_timeline = [(dict(a)["date"], dict(a)["count"]) for a in alerts]

    # Risk alerts triggered (Feature 2)
    risk_alerts = conn.execute("SELECT COUNT(*) as count FROM risk_alerts").fetchone()
    risk_alerts_count = dict(risk_alerts)["count"] if risk_alerts else 0

    conn.close()

    return jsonify({
        "total_audits": total_audits,
        "avg_safety_score": avg_score,
        "high_risk_zones": high_risk_summary,
        "audits_for_map": audits_list,
        "alerts_timeline": alerts_timeline,
        "risk_alerts_triggered": risk_alerts_count,
    })


# ---------------------------------------------------------------------------
# Trusted contacts (user-specific)
# ---------------------------------------------------------------------------
@app.route("/api/contacts", methods=["GET"])
@login_required
def list_contacts():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM contacts WHERE user_id = ? ORDER BY id DESC",
        (current_user.id,)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/contacts", methods=["POST"])
@login_required
@validate_json(ContactSchema)
def add_contact():
    data = g.validated_data
    name = data["name"].strip()
    phone = data["phone"].strip()
    relation = data.get("relation", "").strip()

    conn = get_db()
    conn.execute(
        "INSERT INTO contacts (user_id, name, phone, relation, created_at) VALUES (?, ?, ?, ?, ?)",
        (current_user.id, name, phone, relation, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()
    return jsonify({"status": "added"}), 201


@app.route("/api/contacts/<int:contact_id>", methods=["DELETE"])
@login_required
def delete_contact(contact_id):
    conn = get_db()
    # Verify ownership
    contact = conn.execute(
        "SELECT user_id FROM contacts WHERE id = ?",
        (contact_id,)
    ).fetchone()
    if not contact or contact["user_id"] != current_user.id:
        conn.close()
        return jsonify({"error": "unauthorized"}), 403

    conn.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "deleted"})


# ---------------------------------------------------------------------------
# TIER 3 PART 1: Linked contacts — authorizes who may join a user's live
# tracking room (`join_tracking`) and fetch their `/api/tracking/history`.
#
# Semantics of a linked_contacts row (user_id, contact_user_id, status):
#   user_id         = the person being tracked (the "owner")
#   contact_user_id = the person granted permission to view them (the "viewer")
# An invite is created by the owner naming a viewer's email; the viewer must
# accept before `join_tracking` / `/api/tracking/history` will allow them in.
# ---------------------------------------------------------------------------
def _find_user_by_email(conn, email):
    return conn.execute("SELECT id, email FROM users WHERE email = ?", (email,)).fetchone()


def is_accepted_linked_contact(conn, owner_user_id, viewer_user_id):
    """True if viewer_user_id is an accepted linked contact of owner_user_id."""
    if owner_user_id == viewer_user_id:
        return True
    row = conn.execute(
        "SELECT 1 FROM linked_contacts WHERE user_id = ? AND contact_user_id = ? AND status = 'accepted'",
        (owner_user_id, viewer_user_id),
    ).fetchone()
    return row is not None


<<<<<<< HEAD
=======
def get_push_recipients(conn, owner_user_id):
    """TIER 3 PART 3: who should get a native push for owner_user_id's SOS /
    risk / check-in events. That's the account holder themselves (so they
    get confirmation even if they background the app right after tapping
    SOS) plus every Bubble member who has accepted an invite to track them
    — the phone-number `contacts` table is SMS-only and has no SafeHer
    account to hold a push subscription."""
    viewers = conn.execute(
        "SELECT contact_user_id FROM linked_contacts WHERE user_id = ? AND status = 'accepted'",
        (owner_user_id,),
    ).fetchall()
    return [owner_user_id] + [row["contact_user_id"] for row in viewers]


>>>>>>> 12f0b65 (Updated SafeHer features and UI)
@app.route("/api/contacts/invite", methods=["POST"])
@login_required
@validate_json(ContactInviteSchema)
def invite_linked_contact():
    email = g.validated_data["email"].strip().lower()

    conn = get_db()
    target = _find_user_by_email(conn, email)
    if not target:
        conn.close()
        return jsonify({"error": "no SafeHer account with that email"}), 404

    if target["id"] == current_user.id:
        conn.close()
        return jsonify({"error": "you cannot invite yourself"}), 400

    existing = conn.execute(
        "SELECT id, status FROM linked_contacts WHERE user_id = ? AND contact_user_id = ?",
        (current_user.id, target["id"]),
    ).fetchone()
    if existing:
        conn.close()
        return jsonify({"error": f"already {existing['status']}", "invite_id": existing["id"]}), 409

    cur = conn.execute(
        "INSERT INTO linked_contacts (user_id, contact_user_id, status, created_at) VALUES (?, ?, 'pending', ?)",
        (current_user.id, target["id"], datetime.utcnow().isoformat()),
    )
    conn.commit()
    invite_id = cur.lastrowid
    conn.close()

    # Real-time nudge to the invitee if they're online
    socketio.emit("tracking_invite_received", {
        "invite_id": invite_id,
        "from_email": current_user.email,
    }, room=f"user_{target['id']}")

    return jsonify({"status": "invited", "invite_id": invite_id}), 201


@app.route("/api/contacts/invite/<int:invite_id>/accept", methods=["POST"])
@login_required
def accept_linked_contact_invite(invite_id):
    conn = get_db()
    invite = conn.execute("SELECT * FROM linked_contacts WHERE id = ?", (invite_id,)).fetchone()
    if not invite or invite["contact_user_id"] != current_user.id:
        conn.close()
        return jsonify({"error": "unauthorized"}), 403
    if invite["status"] != "pending":
        conn.close()
        return jsonify({"error": f"invite is already {invite['status']}"}), 409

    conn.execute("UPDATE linked_contacts SET status = 'accepted' WHERE id = ?", (invite_id,))
    conn.commit()
    owner_id = invite["user_id"]
    conn.close()

    socketio.emit("tracking_invite_accepted", {
        "invite_id": invite_id,
        "by_email": current_user.email,
    }, room=f"user_{owner_id}")

    return jsonify({"status": "accepted"})


@app.route("/api/contacts/invite/<int:invite_id>/decline", methods=["POST"])
@login_required
def decline_linked_contact_invite(invite_id):
    conn = get_db()
    invite = conn.execute("SELECT * FROM linked_contacts WHERE id = ?", (invite_id,)).fetchone()
    if not invite or invite["contact_user_id"] != current_user.id:
        conn.close()
        return jsonify({"error": "unauthorized"}), 403

    conn.execute("DELETE FROM linked_contacts WHERE id = ?", (invite_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "declined"})


@app.route("/api/contacts/linked", methods=["GET"])
@login_required
def list_linked_contacts():
    """Everything relevant to the current user: invites/contacts they've
    granted access to (people who can track *them*), and people who've
    granted *them* access to track others."""
    conn = get_db()
    granted_to_others = conn.execute(
        """SELECT lc.id, lc.status, lc.created_at, u.email AS contact_email, u.id AS contact_user_id
           FROM linked_contacts lc JOIN users u ON u.id = lc.contact_user_id
           WHERE lc.user_id = ? ORDER BY lc.id DESC""",
        (current_user.id,),
    ).fetchall()
    can_track = conn.execute(
        """SELECT lc.id, lc.status, lc.created_at, u.email AS owner_email, u.id AS owner_user_id
           FROM linked_contacts lc JOIN users u ON u.id = lc.user_id
           WHERE lc.contact_user_id = ? ORDER BY lc.id DESC""",
        (current_user.id,),
    ).fetchall()
    conn.close()
    return jsonify({
        "people_who_can_track_me": [dict(r) for r in granted_to_others],
        "people_i_can_track": [dict(r) for r in can_track],
    })


@app.route("/api/tracking/history", methods=["GET"])
@login_required
def tracking_history():
    """TIER 3 PART 1: only the owner themselves, or an accepted linked
    contact of the owner, may fetch a user's location-sharing history."""
    target_user_id = request.args.get("user_id", type=int)
    if target_user_id is None:
        target_user_id = current_user.id  # default: your own history

    conn = get_db()
    if not is_accepted_linked_contact(conn, target_user_id, current_user.id):
        conn.close()
        return jsonify({"error": "unauthorized"}), 403

    rows = conn.execute(
        "SELECT latitude, longitude, active, updated_at FROM guardian_shares "
        "WHERE user_id = ? ORDER BY id DESC LIMIT 200",
        (target_user_id,),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------------------
# SOS (with WebSocket real-time notification)
# ---------------------------------------------------------------------------
@app.route("/api/sos", methods=["POST"])
@login_required
@validate_json(SOSSchema)
def trigger_sos():
    data = g.validated_data
    lat = data.get("latitude")
    lng = data.get("longitude")
    trigger_type = data.get("trigger_type", "manual")

    conn = get_db()
    contacts = conn.execute("SELECT * FROM contacts WHERE user_id = ?", (current_user.id,)).fetchall()

    message = (
        f"SOS ALERT ({trigger_type}) - {current_user.email} needs help. "
        f"Location: https://maps.google.com/?q={lat},{lng}"
    )

    delivery_results = send_sos_alert(contacts, message)

    conn.execute(
        "INSERT INTO alerts (user_id, trigger_type, latitude, longitude, message, alert_type, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (current_user.id, trigger_type, lat, lng, message, "sos", datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()

    logger.info(
        "SOS triggered: user=%s trigger_type=%s contacts_notified=%d",
        hash_identifier(current_user.email), trigger_type, len(contacts),
    )

    # ===== FEATURE 4: WebSocket real-time notification =====
    socketio.emit("sos_triggered", {
        "user_email": current_user.email,
        "location": {"latitude": lat, "longitude": lng},
        "message": message,
        "timestamp": datetime.utcnow().isoformat(),
    }, room="sos_room")

    # ===== TIER 3 PART 3: native push, works even with every tab closed =====
    conn = get_db()
    send_push_to_users(
        conn,
        get_push_recipients(conn, current_user.id),
        title="🚨 SOS Alert",
        body=message,
        url="/",
        tag="safeher-sos",
        critical=True,
    )
    conn.close()

    return jsonify(
        {
            "status": "alert_sent",
            "contacts_notified": len(contacts),
            "delivery": delivery_results,
            "message": message,
        }
    )


@app.route("/api/alerts", methods=["GET"])
@login_required
def list_alerts():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM alerts WHERE user_id = ? ORDER BY id DESC LIMIT 20",
        (current_user.id,)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------------------
# FEATURE 1: Distress detection (transcript + ML fallback)
# ---------------------------------------------------------------------------
@app.route("/api/distress-check", methods=["POST"])
@login_required
@validate_json(DistressCheckSchema)
def distress_check():
    """
    FEATURE 1: REAL AI-BASED DISTRESS DETECTION
    
    Browser uses TensorFlow.js + YAMNet to detect audio distress.
    This endpoint acts as keyword-match fallback if ML inference fails.
    """
    data = g.validated_data
    transcript = data.get("transcript", "")
    result = check_distress(transcript)
    
    if result.get("auto_trigger_sos"):
        loc = data.get("location", {})
        if loc.get("latitude") and loc.get("longitude"):
            trigger_sos_internal(current_user.id, loc["latitude"], loc["longitude"], "audio_ml")
    
    return jsonify(result)


@app.route("/api/audio-classify", methods=["POST"])
@login_required
def audio_classify():
    """
    TIER 1 FEATURE 1: Real audio ML deployment.

    Accepts a browser-computed log-mel spectrogram (preferred) or raw
    waveform, runs YAMNet inference (or the documented heuristic fallback
    when TensorFlow/the YAMNet weights aren't available — see
    utils/audio_classifier.py for details), and auto-triggers SOS above
    the confidence threshold.
    """
    # NOTE: the spectrogram/waveform payload here is large, numeric, and
    # shape-dependent on the client-side ML pipeline — not a good fit for a
    # fixed marshmallow schema. We still avoid request.get_json(force=True)
    # crashing on malformed JSON, returning a clean 400 instead of a 500.
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "invalid_json", "message": "Request body must be a JSON object"}), 400
    result = classify_audio_payload(data)

    if result.get("auto_trigger_sos"):
        loc = data.get("location", {})
        if loc.get("latitude") and loc.get("longitude"):
            trigger_sos_internal(current_user.id, loc["latitude"], loc["longitude"], "audio_ml_deployed")
    elif result.get("distress_detected"):
        # Log even when below the auto-trigger threshold, for the admin
        # analytics dashboard, without firing a full SOS.
        conn = get_db()
        conn.execute(
            "INSERT INTO alerts (user_id, trigger_type, latitude, longitude, message, alert_type, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                current_user.id,
                "audio_ml_deployed",
                data.get("location", {}).get("latitude"),
                data.get("location", {}).get("longitude"),
                f"Possible distress audio detected ({result.get('distress_type')}, "
                f"confidence {result.get('confidence')}) — below auto-SOS threshold.",
                "audio_flag",
                datetime.utcnow().isoformat(),
            ),
        )
        conn.commit()
        conn.close()

    return jsonify(result)


def trigger_sos_internal(user_id, lat, lng, trigger_type):
    """Internal SOS trigger without full request context"""
    conn = get_db()
    contacts = conn.execute("SELECT * FROM contacts WHERE user_id = ?", (user_id,)).fetchall()
    
    user_email = conn.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()["email"]
    message = (
        f"SOS ALERT ({trigger_type}) - {user_email} needs help. "
        f"Location: https://maps.google.com/?q={lat},{lng}"
    )
    
    send_sos_alert(contacts, message)
    
    conn.execute(
        "INSERT INTO alerts (user_id, trigger_type, latitude, longitude, message, alert_type, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, trigger_type, lat, lng, message, "sos", datetime.utcnow().isoformat()),
    )
    conn.commit()

    # ===== TIER 3 PART 3: native push (covers audio-ML auto-SOS and the
    # check-in-timer-expired path, both of which call this helper) =====
    send_push_to_users(
        conn,
        get_push_recipients(conn, user_id),
        title="🚨 SOS Alert",
        body=message,
        url="/",
        tag="safeher-sos",
        critical=True,
    )
    conn.close()

    logger.info(
        "SOS triggered (internal): user=%s trigger_type=%s contacts_notified=%d",
        hash_identifier(user_email), trigger_type, len(contacts),
    )


# ---------------------------------------------------------------------------
# Route safety scoring
# ---------------------------------------------------------------------------
@app.route("/api/route-safety", methods=["POST"])
@login_required
@validate_json(RouteSafetySchema)
def route_safety():
    data = g.validated_data
    origin = data.get("origin", "")
    destination = data.get("destination", "")
    result = get_route_safety_score(origin, destination)
    return jsonify(result)


# ---------------------------------------------------------------------------
# Safe check-in timer (user-specific)
# ---------------------------------------------------------------------------
@app.route("/api/checkin/start", methods=["POST"])
@login_required
@validate_json(CheckinStartSchema)
def start_checkin():
    data = g.validated_data
    minutes = data["minutes"]
    deadline = datetime.utcnow() + timedelta(minutes=minutes)

    conn = get_db()
    cur = conn.execute(
        "INSERT INTO checkins (user_id, deadline, status, created_at) VALUES (?, ?, 'pending', ?)",
        (current_user.id, deadline.isoformat(), datetime.utcnow().isoformat()),
    )
    conn.commit()
    checkin_id = cur.lastrowid
    conn.close()

    return jsonify({"checkin_id": checkin_id, "deadline": deadline.isoformat()})


@app.route("/api/checkin/<int:checkin_id>/confirm", methods=["POST"])
@login_required
def confirm_checkin(checkin_id):
    conn = get_db()
    checkin = conn.execute("SELECT user_id FROM checkins WHERE id = ?", (checkin_id,)).fetchone()
    if not checkin or checkin["user_id"] != current_user.id:
        conn.close()
        return jsonify({"error": "unauthorized"}), 403

    conn.execute("UPDATE checkins SET status = 'safe' WHERE id = ?", (checkin_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "safe"})


@app.route("/api/checkin/<int:checkin_id>/status", methods=["GET"])
@login_required
def checkin_status(checkin_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM checkins WHERE id = ?", (checkin_id,)).fetchone()
    if not row or row["user_id"] != current_user.id:
        conn.close()
        return jsonify({"error": "not found"}), 404

    result = dict(row)
    deadline = datetime.fromisoformat(result["deadline"])
    result["expired"] = datetime.utcnow() > deadline and result["status"] == "pending"
    
    if result["expired"]:
        conn.close()
        trigger_sos_internal(current_user.id, None, None, "checkin_timeout")
        return jsonify(result)
    
    conn.close()
    return jsonify(result)


# ---------------------------------------------------------------------------
# Safety Audit + Predictive Risk Alert
# ---------------------------------------------------------------------------
@app.route("/api/audits", methods=["GET"])
@login_required
def list_audits():
    conn = get_db()
    rows = conn.execute("SELECT * FROM audits ORDER BY id DESC LIMIT 200").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/audits", methods=["POST"])
@login_required
@validate_json(AuditSchema)
def add_audit():
    data = g.validated_data
    lat = data["latitude"]
    lng = data["longitude"]

    params = ["lighting", "openness", "walkpath", "security", "transport", "crowd"]
    values = {p: data[p] for p in params}
    overall_score = round(sum(values.values()) / (len(params) * 4) * 100)

    conn = get_db()
    conn.execute(
        """INSERT INTO audits
           (user_id, area_name, latitude, longitude, lighting, openness, walkpath, security,
            transport, crowd, overall_score, comment, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            current_user.id,
            data.get("area_name", "").strip() or "Unnamed area",
            lat,
            lng,
            values["lighting"],
            values["openness"],
            values["walkpath"],
            values["security"],
            values["transport"],
            values["crowd"],
            overall_score,
            data.get("comment", "").strip(),
            datetime.utcnow().isoformat(),
        ),
    )
    conn.commit()
    conn.close()
    return jsonify({"status": "added", "overall_score": overall_score}), 201


# ---------------------------------------------------------------------------
# FEATURE 2: Predictive Risk Alert (proactive low-safety area warning)
# ---------------------------------------------------------------------------
@app.route("/api/check-location-risk", methods=["POST"])
@login_required
@validate_json(CheckLocationRiskSchema)
def check_location_risk():
    """
    TIER 1 FEATURE 2 ENHANCEMENT: ML-powered risk prediction.

    Replaces the pure "within 500m of a low-score audit" geofence rule
    with a RandomForest-based continuous risk_score (0-100) built from
    location, time-of-day, nearby audit density/severity, nearby active
    user density, and recency of nearby incidents. The original geofence
    signal is still computed and folded in as one of the model's input
    features (nearby_audits_count / avg_nearby_score), so this is a
    strict enhancement rather than a replacement of that logic.
    """
    data = g.validated_data
    lat = data["latitude"]
    lng = data["longitude"]

    conn = get_db()
    prediction, nearest_threat, threat_distance = _predict_location_risk(conn, lat, lng)

    risk_score = prediction["risk_score"]
    risk_detected = risk_score >= 45  # keep the existing "Caution" cutoff for the boolean flag

    if risk_detected:
        conn.execute(
            """INSERT INTO risk_alerts
               (user_id, latitude, longitude, risk_score, nearby_low_score_area,
                prediction_score, prediction_confidence, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                current_user.id,
                lat,
                lng,
                nearest_threat["overall_score"] if nearest_threat else None,
                nearest_threat["area_name"] if nearest_threat else None,
                prediction["risk_score"],
                prediction["confidence"],
                datetime.utcnow().isoformat(),
            ),
        )
        conn.commit()

        socketio.emit("risk_alert", {
            "message": f"⚠️ Elevated risk near your location (ML risk score: {risk_score:.0f}/100)",
            "area": nearest_threat["area_name"] if nearest_threat else None,
            "score": nearest_threat["overall_score"] if nearest_threat else None,
            "distance_km": round(threat_distance, 2) if nearest_threat else None,
            "risk_score": risk_score,
            "recommendation": prediction["factors"]["recommendation"],
        }, room=f"user_{current_user.id}")

        # ===== TIER 3 PART 3: native push for high-risk-area detection =====
        send_push_to_users(
            conn,
            get_push_recipients(conn, current_user.id),
            title="⚠️ High-Risk Area Nearby",
            body=f"Risk score {risk_score:.0f}/100"
            + (f" near {nearest_threat['area_name']}" if nearest_threat else "")
            + ". Consider a different route.",
            url="/",
            tag="safeher-risk",
        )

    if risk_score > 60:
        socketio.emit("ml_risk_alert", {
            "risk_score": risk_score,
            "confidence": prediction["confidence"],
            "engine": prediction["engine"],
            "factors": prediction["factors"],
            "location": {"latitude": lat, "longitude": lng},
            "timestamp": datetime.utcnow().isoformat(),
        }, room=f"user_{current_user.id}")

    conn.close()

    return jsonify({
        "risk_detected": risk_detected,
        "risk_score": risk_score,
        "confidence": prediction["confidence"],
        "engine": prediction["engine"],
        "factors": prediction["factors"],
        "area": nearest_threat["area_name"] if nearest_threat else None,
        "score": nearest_threat["overall_score"] if nearest_threat else None,
        "distance_km": round(threat_distance, 2) if nearest_threat else None,
    })


def _predict_location_risk(conn, lat, lng, user_id_for_density=None):
    """Shared helper: builds ML features from the DB and runs the risk
    predictor. Returns (prediction_dict, nearest_low_score_audit_or_None, distance_km)."""
    # Broad candidate radius (~1km via lat/lng box) fetched once, then
    # precisely filtered by haversine distance inside build_features /
    # for the legacy nearest-threat display.
    candidate_audits = conn.execute(
        "SELECT * FROM audits WHERE latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?",
        (lat - 0.01, lat + 0.01, lng - 0.01, lng + 0.01),
    ).fetchall()
    candidate_audits = [dict(a) for a in candidate_audits]

    low_score_nearby = [a for a in candidate_audits if a["overall_score"] < 45]
    nearest_threat = None
    threat_distance = float("inf")
    for audit in low_score_nearby:
        dist = haversine_distance(lat, lng, audit["latitude"], audit["longitude"])
        if dist <= 0.5 and dist < threat_distance:
            threat_distance = dist
            nearest_threat = audit

    one_hour_ago = (datetime.utcnow() - timedelta(hours=1)).isoformat()
    active_shares = conn.execute(
        "SELECT latitude, longitude FROM guardian_shares WHERE active = 1 AND updated_at > ?",
        (one_hour_ago,),
    ).fetchall()
    user_density = sum(
        1 for s in active_shares
        if s["latitude"] is not None and haversine_distance(lat, lng, s["latitude"], s["longitude"]) <= 0.5
    )

    recent_alert = conn.execute(
        "SELECT created_at FROM alerts WHERE latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ? "
        "ORDER BY created_at DESC LIMIT 1",
        (lat - 0.01, lat + 0.01, lng - 0.01, lng + 0.01),
    ).fetchone()
    if recent_alert:
        delta = datetime.utcnow() - datetime.fromisoformat(recent_alert["created_at"])
        hours_since_incident = delta.total_seconds() / 3600.0
    else:
        hours_since_incident = 999

    predictor = get_predictor()
    features = predictor.build_features(
        lat, lng, datetime.utcnow(), candidate_audits,
        nearby_user_count=user_density, hours_since_incident=hours_since_incident,
    )
    prediction = predictor.predict(features)
    return prediction, nearest_threat, threat_distance


def haversine_distance(lat1, lon1, lat2, lon2):
    """Calculate distance in km between coordinates"""
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))
    return R * c


# ---------------------------------------------------------------------------
# Nearby safety services directory
# ---------------------------------------------------------------------------
@app.route("/api/nearby-services", methods=["GET"])
@login_required
def nearby_services():
    lat = request.args.get("lat", type=float)
    lng = request.args.get("lng", type=float)
    service_type = request.args.get("type") or None
    results = get_nearby_services(lat, lng, service_type=service_type)
    return jsonify(results)


# ---------------------------------------------------------------------------
# Guardian live location sharing (user-specific)
# ---------------------------------------------------------------------------
@app.route("/api/guardian/share", methods=["POST"])
@login_required
@validate_json(GuardianShareSchema)
def guardian_share():
    data = g.validated_data
    lat = data.get("latitude")
    lng = data.get("longitude")

    conn = get_db()
    conn.execute("UPDATE guardian_shares SET active = 0 WHERE user_id = ? AND active = 1", (current_user.id,))
    conn.execute(
        "INSERT INTO guardian_shares (user_id, latitude, longitude, active, updated_at) VALUES (?, ?, ?, 1, ?)",
        (current_user.id, lat, lng, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()

    # ===== FEATURE 2 INTEGRATION: Check for risk when sharing =====
    check_location_risk_internal(lat, lng, current_user.id)

    # ===== TIER 3 PART 1: push the live position only to the tracking
    # room for THIS user — only sockets that passed the join_tracking
    # authorization check below are in that room. =====
    socketio.emit("location_update", {
        "user_id": current_user.id,
        "latitude": lat,
        "longitude": lng,
        "timestamp": datetime.utcnow().isoformat(),
    }, room=f"tracking_{current_user.id}")

    return jsonify({"status": "sharing"})


def check_location_risk_internal(lat, lng, user_id):
    """Internal version for risk check (used by guardian_share), now backed
    by the same ML risk predictor as /api/check-location-risk."""
    if lat is None or lng is None:
        return

    conn = get_db()
    prediction, nearest_threat, _ = _predict_location_risk(conn, lat, lng)

    if prediction["risk_score"] >= 45:
        conn.execute(
            """INSERT INTO risk_alerts
               (user_id, latitude, longitude, risk_score, nearby_low_score_area,
                prediction_score, prediction_confidence, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                lat,
                lng,
                nearest_threat["overall_score"] if nearest_threat else None,
                nearest_threat["area_name"] if nearest_threat else None,
                prediction["risk_score"],
                prediction["confidence"],
                datetime.utcnow().isoformat(),
            ),
        )
        conn.commit()

        # ===== TIER 3 PART 3: native push for high-risk-area detection =====
        send_push_to_users(
            conn,
            get_push_recipients(conn, user_id),
            title="⚠️ High-Risk Area Nearby",
            body=f"Risk score {prediction['risk_score']:.0f}/100"
            + (f" near {nearest_threat['area_name']}" if nearest_threat else "")
            + ". Consider a different route.",
            url="/",
            tag="safeher-risk",
        )

        if prediction["risk_score"] > 60:
            socketio.emit("ml_risk_alert", {
                "risk_score": prediction["risk_score"],
                "confidence": prediction["confidence"],
                "engine": prediction["engine"],
                "factors": prediction["factors"],
                "location": {"latitude": lat, "longitude": lng},
                "timestamp": datetime.utcnow().isoformat(),
            }, room=f"user_{user_id}")

    conn.close()


@app.route("/api/guardian/stop", methods=["POST"])
@login_required
def guardian_stop():
    conn = get_db()
    conn.execute("UPDATE guardian_shares SET active = 0 WHERE user_id = ? AND active = 1", (current_user.id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "stopped"})


@app.route("/api/guardian/status", methods=["GET"])
@login_required
def guardian_status():
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM guardian_shares WHERE user_id = ? AND active = 1 ORDER BY id DESC LIMIT 1",
        (current_user.id,)
    ).fetchone()
    conn.close()
    if not row:
        return jsonify({"active": False})
    return jsonify({**dict(row), "active": True})


# ---------------------------------------------------------------------------
# TIER 2 FEATURE: Live location tracking (continuous, not one-time)
# ---------------------------------------------------------------------------
@app.route("/api/tracking/start", methods=["POST"])
@login_required
def tracking_start():
    conn = get_db()
    # close out any stale active session for this user first
    conn.execute(
        "UPDATE live_tracking SET status = 'inactive', ended_at = ? WHERE user_id = ? AND status = 'active'",
        (datetime.utcnow().isoformat(), current_user.id),
    )
    conn.execute(
        "INSERT INTO live_tracking (user_id, status, started_at) VALUES (?, 'active', ?)",
        (current_user.id, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()

    socketio.emit("tracking_started", {
        "user_id": current_user.id,
        "timestamp": datetime.utcnow().isoformat(),
    }, room=f"tracking_{current_user.id}")

    return jsonify({"status": "tracking_started"})


@app.route("/api/tracking/stop", methods=["POST"])
@login_required
def tracking_stop():
    conn = get_db()
    conn.execute(
        "UPDATE live_tracking SET status = 'inactive', ended_at = ? WHERE user_id = ? AND status = 'active'",
        (datetime.utcnow().isoformat(), current_user.id),
    )
    conn.commit()
    conn.close()

    socketio.emit("tracking_stopped", {
        "user_id": current_user.id,
        "timestamp": datetime.utcnow().isoformat(),
    }, room=f"tracking_{current_user.id}")

    return jsonify({"status": "tracking_stopped"})


<<<<<<< HEAD

=======
@app.route("/api/tracking/my-history", methods=["GET"])
@login_required
def tracking_my_history():
    """Recent breadcrumb trail for the current user (used to redraw the
    trail on page reload). Distinct from GET /api/tracking/history, which
    is the authorization-checked endpoint for viewing a Bubble member's
    guardian-share history."""
>>>>>>> 12f0b65 (Updated SafeHer features and UI)
    conn = get_db()
    rows = conn.execute(
        "SELECT latitude, longitude, timestamp FROM location_history "
        "WHERE user_id = ? ORDER BY id DESC LIMIT 50",
        (current_user.id,),
    ).fetchall()
    conn.close()
    return jsonify(list(reversed([dict(r) for r in rows])))


# ---------------------------------------------------------------------------
# Community Safety Feed (user-specific)
# ---------------------------------------------------------------------------
@app.route("/api/feed", methods=["GET"])
@login_required
def list_feed():
    conn = get_db()
    rows = conn.execute("SELECT * FROM feed_posts ORDER BY id DESC LIMIT 100").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/feed", methods=["POST"])
@login_required
@validate_json(FeedPostSchema)
def add_feed_post():
    data = g.validated_data
    message = data["message"].strip()
    post_type = data.get("post_type", "alert")
    conn = get_db()
    conn.execute(
        """INSERT INTO feed_posts (user_id, message, post_type, latitude, longitude, area_name, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            current_user.id,
            message,
            post_type,
            data.get("latitude"),
            data.get("longitude"),
            data.get("area_name", "").strip(),
            datetime.utcnow().isoformat(),
        ),
    )
    conn.commit()
    conn.close()
    return jsonify({"status": "posted"}), 201


# ---------------------------------------------------------------------------
# TIER 2 FEATURE: Offline-first PWA — sync queue
# ---------------------------------------------------------------------------
@app.route("/api/offline-actions", methods=["POST"])
@login_required
def sync_offline_actions():
    """Called by the frontend once it comes back online. Body:
    { "actions": [ { "type": "sos" | "risk_report", "payload": {...}, "queued_at": iso_str }, ... ] }
    Each action is logged in offline_queue and, where we have a handler for
    it, actually applied (e.g. an SOS raised while offline still triggers
    a real alert once connectivity returns).
    """
    data = request.get_json(force=True)
    actions = data.get("actions", [])
    results = []

    conn = get_db()
    for action in actions:
        action_type = action.get("type", "unknown")
        payload = action.get("payload", {})

        cur = conn.execute(
            "INSERT INTO offline_queue (user_id, action_type, payload, synced, created_at) "
            "VALUES (?, ?, ?, 0, ?)",
            (current_user.id, action_type, json.dumps(payload), action.get("queued_at") or datetime.utcnow().isoformat()),
        )
        queue_id = cur.lastrowid
        conn.commit()

        applied = False
        if action_type == "sos":
            lat = payload.get("latitude")
            lng = payload.get("longitude")
            conn.execute(
                "INSERT INTO alerts (user_id, trigger_type, latitude, longitude, message, alert_type, created_at) "
                "VALUES (?, 'offline_sync', ?, ?, ?, 'sos', ?)",
                (current_user.id, lat, lng, payload.get("message", "SOS raised while offline"), datetime.utcnow().isoformat()),
            )
            conn.commit()
            socketio.emit("sos_triggered", {
                "message": f"Offline SOS from {current_user.email} has just synced",
                "latitude": lat,
                "longitude": lng,
            }, room="sos_room")
            send_push_to_users(
                conn,
                get_push_recipients(conn, current_user.id),
                title="🚨 SOS Alert (offline sync)",
                body=payload.get("message", "SOS raised while offline"),
                url="/",
                tag="safeher-sos",
                critical=True,
            )
            applied = True
            logger.info(
                "SOS triggered (offline sync): user=%s queue_id=%s",
                hash_identifier(current_user.email), queue_id,
            )

        conn.execute(
            "UPDATE offline_queue SET synced = 1, synced_at = ? WHERE id = ?",
            (datetime.utcnow().isoformat(), queue_id),
        )
        conn.commit()
        results.append({"type": action_type, "applied": applied, "queue_id": queue_id})

    conn.close()
    return jsonify({"status": "synced", "results": results})


# ---------------------------------------------------------------------------
# FEATURE 4: WebSocket Real-Time Notifications
# ---------------------------------------------------------------------------
@socketio.on("connect")
def handle_connect():
    logger.info("Client connected: sid=%s", request.sid)
    if current_user.is_authenticated:
        join_room(f"user_{current_user.id}")
        join_room("sos_room")


@socketio.on("disconnect")
def handle_disconnect():
    logger.info("Client disconnected: sid=%s", request.sid)


@socketio.on("subscribe_to_alerts")
def handle_subscribe_alerts():
    """Subscribe to real-time alert updates"""
    if current_user.is_authenticated:
        join_room(f"user_alerts_{current_user.id}")
        emit("subscribed", {"status": "listening for alerts"})


# ---------------------------------------------------------------------------
# TIER 3 FEATURE: Live tracking authorization + WebSocket location updates
# ---------------------------------------------------------------------------

@socketio.on("join_tracking")
def handle_join_tracking(data):
    if not current_user.is_authenticated:
        emit("tracking_denied", {"reason": "not_authenticated"})
        return

    target_user_id = (data or {}).get("user_id")

    try:
        target_user_id = int(target_user_id)
    except (TypeError, ValueError):
        emit("tracking_denied", {"reason": "invalid_user_id"})
        return

    conn = get_db()
    try:
        if not is_accepted_linked_contact(conn, target_user_id, current_user.id):
            emit(
                "tracking_denied",
                {"reason": "not_authorized", "user_id": target_user_id}
            )
            return
    finally:
        conn.close()

    join_room(f"tracking_{target_user_id}")
    emit("tracking_joined", {"user_id": target_user_id})


@socketio.on("leave_tracking")
def handle_leave_tracking(data):
    target_user_id = (data or {}).get("user_id")

    try:
        target_user_id = int(target_user_id)
    except (TypeError, ValueError):
        return

    leave_room(f"tracking_{target_user_id}")


@socketio.on("location_update")
def handle_location_update(data):
    if not current_user.is_authenticated:
        return

    lat = data.get("latitude")
    lng = data.get("longitude")

    if lat is None or lng is None:
        return

    timestamp = datetime.utcnow().isoformat()

    conn = get_db()
    conn.execute(
        "INSERT INTO location_history (user_id, latitude, longitude, timestamp) VALUES (?, ?, ?, ?)",
        (current_user.id, lat, lng, timestamp),
    )
    conn.commit()
    conn.close()

    socketio.emit(
        "contact_location_update",
        {
            "user_id": current_user.id,
            "latitude": lat,
            "longitude": lng,
            "timestamp": timestamp,
        },
        room=f"tracking_{current_user.id}",
    )

    check_location_risk_internal(lat, lng, current_user.id)



@app.route("/api/test-notification", methods=["POST"])
@login_required
def test_notification():
    socketio.emit("test_alert", {
        "message": "This is a test real-time notification!",
        "timestamp": datetime.utcnow().isoformat(),
    }, room=f"user_{current_user.id}")
    return jsonify({"status": "notification_sent"})


# ---------------------------------------------------------------------------
# TIER 3 PART 3: Real Web Push (Web Push API via pywebpush + VAPID)
# ---------------------------------------------------------------------------
@app.route("/api/push/vapid-public-key", methods=["GET"])
@login_required
def push_vapid_public_key():
    key = get_vapid_public_key()
    if not key:
        return jsonify({"error": "push_not_configured", "message": "Server has no VAPID keys configured."}), 503
    return jsonify({"public_key": key})


@app.route("/api/push/subscribe", methods=["POST"])
@login_required
@validate_json(PushSubscribeSchema)
def push_subscribe():
    data = g.validated_data
    endpoint = data["endpoint"]
    keys = data["keys"]

    conn = get_db()
    # One endpoint can only ever belong to one browser subscription; if a
    # different account previously subscribed the same endpoint (e.g. a
    # shared/borrowed device), re-point it to the current user instead of
    # erroring, since the old owner's subscription is no longer valid there.
    conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ? AND user_id != ?", (endpoint, current_user.id))
    conn.execute(
        """INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, created_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(endpoint) DO UPDATE SET p256dh = excluded.p256dh, auth = excluded.auth""",
        (current_user.id, endpoint, keys["p256dh"], keys["auth"], datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()

    return jsonify({"status": "subscribed", "configured": push_configured()}), 201


@app.route("/api/push/unsubscribe", methods=["POST"])
@login_required
@validate_json(PushUnsubscribeSchema)
def push_unsubscribe():
    endpoint = g.validated_data["endpoint"]

    conn = get_db()
    conn.execute(
        "DELETE FROM push_subscriptions WHERE endpoint = ? AND user_id = ?",
        (endpoint, current_user.id),
    )
    conn.commit()
    conn.close()

    return jsonify({"status": "unsubscribed"})


# ---------------------------------------------------------------------------
# TIER 3 PART 3: Frontend error resilience — client-side error reporting.
# Deliberately NOT @login_required: errors on the login/signup page itself
# (before a session exists) are exactly the kind of thing we want visibility
# into. Rate-limited to stop a broken client from looping into a log-flood.
# ---------------------------------------------------------------------------
@app.route("/api/client-error", methods=["POST"])
@limiter.limit("30 per minute")
@validate_json(ClientErrorSchema)
def client_error():
    data = g.validated_data
    user_label = current_user.email if current_user.is_authenticated else "anonymous"
    security_logger.warning(
        "Client-side error [%s] user=%s url=%s message=%s source=%s:%s stack=%s ua=%s",
        data.get("kind") or "error",
        user_label,
        data.get("url"),
        data.get("message"),
        data.get("source"),
        data.get("line"),
        (data.get("stack") or "")[:500],
        data.get("ua"),
    )
    # 204 No Content: fire-and-forget, nothing for the beacon/fetch call to parse.
    return "", 204


if __name__ == "__main__":
    os.makedirs(os.path.dirname(app.config["DATABASE_PATH"]) or ".", exist_ok=True)
    init_db()
    logger.info(
        "Starting SafeHer (env=%s, debug=%s, host=%s, port=%s)",
        app.config.get("ENV_NAME", "unknown"), app.config["DEBUG"], app.config["HOST"], app.config["PORT"],
    )
    socketio.run(app, debug=app.config["DEBUG"], host=app.config["HOST"], port=app.config["PORT"])