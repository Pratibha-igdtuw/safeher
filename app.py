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
from utils.route_safety import (
    get_route_safety_score,
    fetch_osrm_routes,
    sample_route_points,
    score_to_rating,
    fetch_nearby_amenities_osm,
    derive_open_status,
)
from utils.distress_detector import check_distress
from utils.safety_services import get_nearby_services, MOCK_SERVICES
from utils.assistant import generate_reply as generate_assistant_reply
from utils.audio_classifier import classify_audio_payload
from utils.risk_predictor import get_predictor
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
    FeedCommentSchema,
    AssistantChatSchema,
    JourneyStartSchema,
    JourneyLocationSchema,
    JourneyExtendSchema,
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

app = Flask(__name__)
app.config.from_object(get_config())

DB_PATH = app.config["DATABASE_PATH"]  # kept for any code/tests referencing DB_PATH directly

# BUGFIX: this used to be `app.secret_key = os.environ.get("SECRET_KEY", "safeher-secret-key-change-in-production")`
# here — a second, differently-worded fallback that silently overwrote the
# SECRET_KEY already set by app.config.from_object(get_config()) above.
# Harmless when SECRET_KEY *is* set in the environment (both read the same
# value), but confusing and a footgun if someone "fixes" the duplication by
# deleting the wrong one. config.py is now the single source of truth.
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

# BUGFIX: this used to be instantiated twice with two different,
# differently-defaulted CORS sources (app.config["CORS_ORIGINS"], a dead
# setting that defaulted to wildcard "*") — the first call's result was
# silently discarded when the second overwrote `socketio`. One instance,
# one explicit allowlist now.
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
            email TEXT,
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

        -- ================= JOURNEY MODE =================
        -- A journey is a destination + ETA "escort" session: if the user
        -- doesn't arrive (or extend/cancel) before the deadline, it behaves
        -- like a missed check-in and auto-escalates to a full SOS.
        CREATE TABLE IF NOT EXISTS journeys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            destination_name TEXT NOT NULL,
            destination_lat REAL,
            destination_lng REAL,
            origin_lat REAL,
            origin_lng REAL,
            guardian_contact_id INTEGER,
            eta_minutes INTEGER NOT NULL,
            deadline TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            last_lat REAL,
            last_lng REAL,
            last_update_at TEXT,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(guardian_contact_id) REFERENCES contacts(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS journey_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            journey_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            latitude REAL,
            longitude REAL,
            message TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(journey_id) REFERENCES journeys(id) ON DELETE CASCADE
        );

        -- Voice Distress Detection: Transcript History
        CREATE TABLE IF NOT EXISTS voice_analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            transcript TEXT NOT NULL,
            distress_detected INTEGER NOT NULL,
            confidence REAL,
            matched_keywords TEXT,
            emotion_label TEXT,
            emotion_intensity REAL,
            auto_triggered_sos INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        -- Community Feed: Likes, Helpful Votes, Comments
        CREATE TABLE IF NOT EXISTS feed_likes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(post_id, user_id),
            FOREIGN KEY(post_id) REFERENCES feed_posts(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS feed_helpful_votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(post_id, user_id),
            FOREIGN KEY(post_id) REFERENCES feed_posts(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS feed_comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            user_id INTEGER,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(post_id) REFERENCES feed_posts(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
        );

        -- AI Assistant conversation history
        CREATE TABLE IF NOT EXISTS assistant_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            message TEXT NOT NULL,
            intent TEXT,
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

    # --- Migration: add email column to contacts if missing ---
    contact_cols = {row["name"] for row in conn.execute("PRAGMA table_info(contacts)")}
    if "email" not in contact_cols:
        conn.execute("ALTER TABLE contacts ADD COLUMN email TEXT")

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Auth Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


def _login_rate_limit_key():
    """5 attempts per 15 min per IP + email combination."""
    email = ""
    try:
        body = request.get_json(silent=True) or {}
        email = (body.get("email") or "").strip().lower()
    except Exception:
        pass
    return f"{get_remote_address()}:{email}"


def _user_rate_limit_key():
    """Per-authenticated-user rate-limit key (falls back to IP for the rare
    case a limiter check somehow runs before login_required rejects the
    request). Used for authenticated, potentially-abusable endpoints
    (SOS, journey start, feed posts) where per-IP limiting alone is too
    coarse — e.g. NAT'd office wifi shouldn't throttle one person's abuse
    of the endpoint against everyone else on that IP, and one person
    shouldn't be able to spam contacts by rotating IPs."""
    try:
        if current_user.is_authenticated:
            return f"user:{current_user.id}"
    except Exception:
        pass
    return get_remote_address()


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
            logger.warning("Failed login attempt: user=%s", hash_identifier(email))
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
    email = data.get("email", "").strip()

    conn = get_db()
    conn.execute(
        "INSERT INTO contacts (user_id, name, phone, relation, email, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (current_user.id, name, phone, relation, email, datetime.utcnow().isoformat()),
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
def _format_sos_message(user_email, trigger_type, lat, lng, accuracy_m=None, location_source=None):
    """BUGFIX: previously built the message as an f-string with raw
    `{lat},{lng}` even when both were None (checkin timeouts, some
    internal escalations), producing a broken, non-functional
    `https://maps.google.com/?q=None,None` link sent straight to a
    trusted contact in an emergency. Now degrades gracefully, and (for
    manual triggers, where the browser can tell us) notes GPS accuracy or
    that a cached/last-known location is being used instead of live GPS."""
    if lat is None or lng is None:
        location_part = "Location unavailable — GPS could not be acquired."
    else:
        location_part = f"Location: https://maps.google.com/?q={lat},{lng}"
        if location_source == "cached":
            location_part += " (last known location — GPS unavailable at time of alert)"
        elif accuracy_m is not None:
            location_part += f" (±{round(accuracy_m)}m accuracy)"

    return f"SOS ALERT ({trigger_type}) - {user_email} needs help. {location_part}"


@app.route("/api/sos", methods=["POST"])
@login_required
@limiter.limit("15 per hour", methods=["POST"], key_func=_user_rate_limit_key)
@validate_json(SOSSchema)
def trigger_sos():
    data = g.validated_data
    lat = data.get("latitude")
    lng = data.get("longitude")
    trigger_type = data.get("trigger_type", "manual")

    conn = get_db()
    contacts = conn.execute("SELECT * FROM contacts WHERE user_id = ?", (current_user.id,)).fetchall()

    message = _format_sos_message(
        current_user.email, trigger_type, lat, lng,
        accuracy_m=data.get("accuracy_m"), location_source=data.get("location_source"),
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
    # SECURITY FIX: previously broadcast to a global "sos_room" every
    # logged-in user was a member of. Now scoped to the triggering user's
    # own devices plus anyone currently, authorizedly watching their live
    # tracking (join_tracking already enforces is_accepted_linked_contact).
    sos_payload = {
        "user_email": current_user.email,
        "location": {"latitude": lat, "longitude": lng},
        "message": message,
        "timestamp": datetime.utcnow().isoformat(),
    }
    socketio.emit("sos_triggered", sos_payload, room=f"user_{current_user.id}")
    socketio.emit("sos_triggered", sos_payload, room=f"tracking_{current_user.id}")

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
# In-memory per-user cooldown so a burst of continuous transcript analyses
# (e.g. someone leaves "Start Listening" running) can't repeatedly
# auto-trigger SOS every few seconds off the same ongoing distress —
# one real trigger is enough; the DB alert history still shows every
# analysis, just not every one becomes its own SOS.
LAST_VOICE_AUTO_TRIGGER = {}  # user_id -> datetime
VOICE_AUTO_TRIGGER_COOLDOWN_SECONDS = 90


@app.route("/api/distress-check", methods=["POST"])
@login_required
@validate_json(DistressCheckSchema)
def distress_check():
    """
    Transcript-based distress detection (utils/distress_detector.py) — a
    keyword + lightweight-heuristic signal, independent from the raw-audio
    YAMNet path in /api/audio-classify below (not a fallback for it).
    """
    data = g.validated_data
    transcript = data.get("transcript", "")
    result = check_distress(transcript)

    now = datetime.utcnow()
    if result.get("auto_trigger_sos"):
        last_trigger = LAST_VOICE_AUTO_TRIGGER.get(current_user.id)
        if last_trigger and (now - last_trigger).total_seconds() < VOICE_AUTO_TRIGGER_COOLDOWN_SECONDS:
            result["auto_trigger_sos"] = False
            result["cooldown_active"] = True
        else:
            loc = data.get("location", {})
            if loc.get("latitude") and loc.get("longitude"):
                trigger_sos_internal(current_user.id, loc["latitude"], loc["longitude"], "audio_ml")
                LAST_VOICE_AUTO_TRIGGER[current_user.id] = now

    conn = get_db()
    conn.execute(
        "INSERT INTO voice_analyses "
        "(user_id, transcript, distress_detected, confidence, matched_keywords, emotion_label, emotion_intensity, auto_triggered_sos, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            current_user.id, transcript, int(result.get("distress_detected", False)),
            result.get("confidence"), ",".join(result.get("matched", [])),
            result.get("emotion", {}).get("label"), result.get("emotion", {}).get("intensity"),
            int(bool(result.get("auto_trigger_sos"))), now.isoformat(),
        ),
    )
    conn.commit()
    conn.close()

    return jsonify(result)


@app.route("/api/distress-check/history", methods=["GET"])
@login_required
def distress_check_history():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM voice_analyses WHERE user_id = ? ORDER BY id DESC LIMIT 30",
        (current_user.id,),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


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
    message = _format_sos_message(user_email, trigger_type, lat, lng)
    
    send_sos_alert(contacts, message)
    
    conn.execute(
        "INSERT INTO alerts (user_id, trigger_type, latitude, longitude, message, alert_type, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, trigger_type, lat, lng, message, "sos", datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()

    logger.info(
        "SOS triggered (internal): user=%s trigger_type=%s contacts_notified=%d",
        hash_identifier(user_email), trigger_type, len(contacts),
    )


# ---------------------------------------------------------------------------
# Route safety scoring
# ---------------------------------------------------------------------------
def _score_route_geometry(conn, geometry, label):
    """Samples a route's geometry (or a synthetic 2-point straight line) at
    a handful of points, scores each with the real ML risk predictor, and
    aggregates into one route-level safety score. `risk_zones_crossed`
    counts how many sampled points fell within 500m of a known low-safety
    audit — a concrete, explainable number to show alongside the score."""
    samples = sample_route_points(geometry, num_samples=6)
    if not samples:
        return None

    risk_scores = []
    zones_crossed = 0
    for lat, lng in samples:
        prediction, nearest_threat, _ = _predict_location_risk(conn, lat, lng)
        risk_scores.append(prediction["risk_score"])
        if nearest_threat is not None:
            zones_crossed += 1

    avg_risk = sum(risk_scores) / len(risk_scores)
    score = max(0, min(100, round(100 - avg_risk)))
    rating, color = score_to_rating(score)

    return {
        "label": label,
        "score": score,
        "rating": rating,
        "color": color,
        "risk_zones_crossed": zones_crossed,
        "geometry": geometry,
    }


@app.route("/api/route-safety", methods=["POST"])
@login_required
@validate_json(RouteSafetySchema)
def route_safety():
    data = g.validated_data
    origin = data.get("origin", "")
    destination = data.get("destination", "")
    o_lat, o_lng = data.get("origin_lat"), data.get("origin_lng")
    d_lat, d_lng = data.get("destination_lat"), data.get("destination_lng")

    if o_lat is None or o_lng is None or d_lat is None or d_lng is None:
        # No coordinates given at all — original text-only heuristic,
        # unchanged, for backward compatibility.
        result = get_route_safety_score(origin, destination)
        result["routes"] = None
        return jsonify(result)

    conn = get_db()

    osrm_routes = fetch_osrm_routes(
        o_lat, o_lng, d_lat, d_lng, alternatives=data.get("compare_alternatives", False)
    )

    if osrm_routes:
        labels = ["Fastest Route", "Alternative Route"]
        scored_routes = []
        for i, route in enumerate(osrm_routes[:2]):
            scored = _score_route_geometry(conn, route["geometry"], labels[i] if i < len(labels) else f"Route {i+1}")
            if scored:
                scored.update({"distance_km": route["distance_km"], "duration_min": route["duration_min"]})
                scored_routes.append(scored)
        mode = "osrm"
    else:
        # OSRM unreachable — fall back to a straight-line estimate so the
        # feature still works offline, clearly labeled as approximate.
        straight_line_geometry = [[o_lat, o_lng], [d_lat, d_lng]]
        scored = _score_route_geometry(conn, straight_line_geometry, "Direct (estimated)")
        if scored:
            scored.update({
                "distance_km": round(haversine_distance(o_lat, o_lng, d_lat, d_lng), 2),
                "duration_min": None,
            })
        scored_routes = [scored] if scored else []
        mode = "straight_line_estimate"

    conn.close()

    if not scored_routes:
        result = get_route_safety_score(origin, destination)
        result["routes"] = None
        return jsonify(result)

    best = max(scored_routes, key=lambda r: r["score"])
    return jsonify({
        "origin": origin, "destination": destination,
        "mode": mode,
        "score": best["score"], "rating": best["rating"], "color": best["color"],
        "distance": best["distance_km"], "estimated_score": best["score"],
        "routes": scored_routes,
        "note": (
            "Real road routing + risk-zone scoring." if mode == "osrm"
            else "Routing service unavailable — showing a straight-line estimate."
        ),
    })


@app.route("/api/risk-zones", methods=["GET"])
@login_required
def risk_zones():
    """Safety Map overlay data: known low-safety audit clusters + active
    proactive risk_alerts near a point, plus a real (not mocked) crowd
    density count, for drawing on the Leaflet map as radius circles."""
    lat = request.args.get("lat", type=float)
    lng = request.args.get("lng", type=float)
    radius_km = min(max(request.args.get("radius_km", default=3.0, type=float), 0.5), 15)

    if lat is None or lng is None:
        return jsonify({"error": "lat/lng are required"}), 400

    conn = get_db()

    box = radius_km / 111.0  # rough degrees-per-km at most latitudes
    candidate_audits = conn.execute(
        "SELECT * FROM audits WHERE latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ? AND overall_score < 60",
        (lat - box, lat + box, lng - box, lng + box),
    ).fetchall()

    zones = []
    for a in candidate_audits:
        dist = haversine_distance(lat, lng, a["latitude"], a["longitude"])
        if dist <= radius_km:
            severity = "high" if a["overall_score"] < 35 else "medium"
            zones.append({
                "source": "audit", "severity": severity,
                "latitude": a["latitude"], "longitude": a["longitude"],
                "radius_m": 250, "label": a["area_name"] or "Unnamed area",
                "score": a["overall_score"], "lighting": a["lighting"],
            })

    candidate_risk_alerts = conn.execute(
        "SELECT * FROM risk_alerts WHERE latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ? AND dismissed = 0",
        (lat - box, lat + box, lng - box, lng + box),
    ).fetchall()
    for r in candidate_risk_alerts:
        if r["latitude"] is None:
            continue
        dist = haversine_distance(lat, lng, r["latitude"], r["longitude"])
        if dist <= radius_km:
            zones.append({
                "source": "risk_alert", "severity": "high" if (r["risk_score"] or 0) > 65 else "medium",
                "latitude": r["latitude"], "longitude": r["longitude"],
                "radius_m": 200, "label": r["nearby_low_score_area"] or "Reported risk area",
                "score": round(100 - (r["risk_score"] or 0)),
            })

    crowd_count = _count_active_users_nearby(conn, lat, lng, radius_km=radius_km)
    conn.close()

    return jsonify({"zones": zones, "crowd_density": crowd_count, "center": {"lat": lat, "lng": lng}, "radius_km": radius_km})


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
# JOURNEY MODE — destination + ETA escort sessions.
#
# Lifecycle: active -> arrived | cancelled | missed
# "missed" means the deadline passed without an extend/arrive/cancel call,
# and behaves exactly like a missed check-in: it auto-escalates to a full
# SOS (same trigger_sos_internal() used by /api/checkin/<id>/status and the
# audio-ML auto-trigger paths), so guardians get the same alert either way.
# ---------------------------------------------------------------------------
def _log_journey_event(conn, journey_id, event_type, lat=None, lng=None, message=None):
    conn.execute(
        "INSERT INTO journey_events (journey_id, event_type, latitude, longitude, message, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (journey_id, event_type, lat, lng, message, datetime.utcnow().isoformat()),
    )


def _journey_progress(journey):
    """Rough progress indicators derived from elapsed time and (if we have
    both origin and last-known point) distance closed toward the
    destination. Either signal can be missing (e.g. no destination pin was
    dropped), so the frontend falls back gracefully."""
    started = datetime.fromisoformat(journey["started_at"])
    deadline = datetime.fromisoformat(journey["deadline"])
    now = datetime.utcnow()
    total_seconds = max((deadline - started).total_seconds(), 1)
    elapsed_seconds = (now - started).total_seconds()
    time_progress_pct = max(0, min(100, round(elapsed_seconds / total_seconds * 100)))
    remaining_seconds = max(0, int((deadline - now).total_seconds()))

    distance_remaining_km = None
    if (
        journey["destination_lat"] is not None
        and journey["destination_lng"] is not None
        and journey["last_lat"] is not None
        and journey["last_lng"] is not None
    ):
        distance_remaining_km = round(
            haversine_distance(
                journey["last_lat"], journey["last_lng"],
                journey["destination_lat"], journey["destination_lng"],
            ),
            3,
        )

    return {
        "time_progress_pct": time_progress_pct,
        "remaining_seconds": remaining_seconds,
        "distance_remaining_km": distance_remaining_km,
    }


def _journey_to_dict(journey):
    result = dict(journey)
    result.update(_journey_progress(journey))
    return result


def _check_journey_expiry(conn, journey_row):
    """Shared expiry check used by every read path (active/status/timeline)
    so a stale-but-still-'active' row gets flipped to 'missed' and
    escalated to SOS the moment anyone asks about it — not just on a
    dedicated poll endpoint."""
    if journey_row["status"] != "active":
        return journey_row

    deadline = datetime.fromisoformat(journey_row["deadline"])
    if datetime.utcnow() <= deadline:
        return journey_row

    conn.execute(
        "UPDATE journeys SET status = 'missed', ended_at = ? WHERE id = ?",
        (datetime.utcnow().isoformat(), journey_row["id"]),
    )
    _log_journey_event(
        conn, journey_row["id"], "missed_checkin",
        lat=journey_row["last_lat"], lng=journey_row["last_lng"],
        message=f"No check-in before ETA for '{journey_row['destination_name']}' — auto-alerting.",
    )
    conn.commit()

    trigger_sos_internal(
        journey_row["user_id"],
        journey_row["last_lat"] or journey_row["origin_lat"],
        journey_row["last_lng"] or journey_row["origin_lng"],
        "journey_missed_checkin",
    )
    _log_journey_event(conn, journey_row["id"], "auto_alert", message="Guardians and trusted contacts notified.")
    conn.commit()

    journey_missed_payload = {
        "journey_id": journey_row["id"],
        "destination_name": journey_row["destination_name"],
        "timestamp": datetime.utcnow().isoformat(),
    }
    socketio.emit("journey_missed", journey_missed_payload, room=f"user_{journey_row['user_id']}")
    socketio.emit("journey_missed", journey_missed_payload, room=f"tracking_{journey_row['user_id']}")

    return conn.execute("SELECT * FROM journeys WHERE id = ?", (journey_row["id"],)).fetchone()


def _get_owned_journey(conn, journey_id, user_id):
    row = conn.execute("SELECT * FROM journeys WHERE id = ?", (journey_id,)).fetchone()
    if not row or row["user_id"] != user_id:
        return None
    return row


@app.route("/api/journey/start", methods=["POST"])
@login_required
@limiter.limit("20 per hour", methods=["POST"], key_func=_user_rate_limit_key)
@validate_json(JourneyStartSchema)
def journey_start():
    data = g.validated_data
    conn = get_db()

    guardian_contact_id = data.get("guardian_contact_id")
    guardian_contact = None
    if guardian_contact_id is not None:
        guardian_contact = conn.execute(
            "SELECT * FROM contacts WHERE id = ? AND user_id = ?",
            (guardian_contact_id, current_user.id),
        ).fetchone()
        if not guardian_contact:
            conn.close()
            return jsonify({"error": "guardian contact not found"}), 404

    # Only one active journey at a time — close out any stale one instead
    # of silently orphaning it.
    stale = conn.execute(
        "SELECT id FROM journeys WHERE user_id = ? AND status = 'active'", (current_user.id,)
    ).fetchall()
    for row in stale:
        conn.execute(
            "UPDATE journeys SET status = 'cancelled', ended_at = ? WHERE id = ?",
            (datetime.utcnow().isoformat(), row["id"]),
        )
        _log_journey_event(conn, row["id"], "cancelled", message="Superseded by a new journey.")

    now = datetime.utcnow()
    deadline = now + timedelta(minutes=data["eta_minutes"])
    cur = conn.execute(
        """INSERT INTO journeys
           (user_id, destination_name, destination_lat, destination_lng, origin_lat, origin_lng,
            guardian_contact_id, eta_minutes, deadline, status, last_lat, last_lng, last_update_at, started_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)""",
        (
            current_user.id, data["destination_name"].strip(),
            data.get("destination_lat"), data.get("destination_lng"),
            data.get("origin_lat"), data.get("origin_lng"),
            guardian_contact_id, data["eta_minutes"], deadline.isoformat(),
            data.get("origin_lat"), data.get("origin_lng"), now.isoformat(), now.isoformat(),
        ),
    )
    journey_id = cur.lastrowid
    _log_journey_event(
        conn, journey_id, "started",
        lat=data.get("origin_lat"), lng=data.get("origin_lng"),
        message=f"Journey started to '{data['destination_name']}', ETA {data['eta_minutes']} min.",
    )
    conn.commit()

    if guardian_contact is not None:
        send_sos_alert(
            [guardian_contact],
            f"{current_user.email} started a journey to {data['destination_name']} "
            f"(ETA {data['eta_minutes']} min) and asked you to keep an eye on it.",
        )

    journey = conn.execute("SELECT * FROM journeys WHERE id = ?", (journey_id,)).fetchone()
    conn.close()

    socketio.emit("journey_started", {
        "journey_id": journey_id,
        "destination_name": data["destination_name"],
        "timestamp": now.isoformat(),
    }, room=f"user_{current_user.id}")

    logger.info(
        "Journey started: user=%s journey_id=%s eta_minutes=%s guardian=%s",
        hash_identifier(current_user.email), journey_id, data["eta_minutes"], guardian_contact_id is not None,
    )

    return jsonify(_journey_to_dict(journey)), 201


@app.route("/api/journey/active", methods=["GET"])
@login_required
def journey_active():
    conn = get_db()
    journey = conn.execute(
        "SELECT * FROM journeys WHERE user_id = ? AND status = 'active' ORDER BY id DESC LIMIT 1",
        (current_user.id,),
    ).fetchone()
    if not journey:
        conn.close()
        return jsonify(None)

    journey = _check_journey_expiry(conn, journey)
    conn.commit()
    conn.close()
    return jsonify(_journey_to_dict(journey))


@app.route("/api/journey/<int:journey_id>/location", methods=["POST"])
@login_required
@validate_json(JourneyLocationSchema)
def journey_update_location(journey_id):
    data = g.validated_data
    conn = get_db()
    journey = _get_owned_journey(conn, journey_id, current_user.id)
    if not journey:
        conn.close()
        return jsonify({"error": "not found"}), 404

    journey = _check_journey_expiry(conn, journey)
    if journey["status"] != "active":
        conn.commit()
        conn.close()
        return jsonify(_journey_to_dict(journey))

    lat, lng = data["latitude"], data["longitude"]
    conn.execute(
        "UPDATE journeys SET last_lat = ?, last_lng = ?, last_update_at = ? WHERE id = ?",
        (lat, lng, datetime.utcnow().isoformat(), journey_id),
    )
    _log_journey_event(conn, journey_id, "location_update", lat=lat, lng=lng)

    # Auto-detect arrival if we know the destination coordinates and the
    # user is now within ~75m of them.
    arrived = False
    if journey["destination_lat"] is not None and journey["destination_lng"] is not None:
        distance_km = haversine_distance(lat, lng, journey["destination_lat"], journey["destination_lng"])
        if distance_km <= 0.075:
            arrived = True
            conn.execute(
                "UPDATE journeys SET status = 'arrived', ended_at = ? WHERE id = ?",
                (datetime.utcnow().isoformat(), journey_id),
            )
            _log_journey_event(conn, journey_id, "arrived", lat=lat, lng=lng, message="Reached destination.")

    conn.commit()
    journey = conn.execute("SELECT * FROM journeys WHERE id = ?", (journey_id,)).fetchone()
    conn.close()

    if arrived:
        socketio.emit("journey_arrived", {
            "journey_id": journey_id,
            "timestamp": datetime.utcnow().isoformat(),
        }, room=f"user_{current_user.id}")

    return jsonify(_journey_to_dict(journey))


@app.route("/api/journey/<int:journey_id>/extend", methods=["POST"])
@login_required
@validate_json(JourneyExtendSchema)
def journey_extend(journey_id):
    data = g.validated_data
    conn = get_db()
    journey = _get_owned_journey(conn, journey_id, current_user.id)
    if not journey:
        conn.close()
        return jsonify({"error": "not found"}), 404

    journey = _check_journey_expiry(conn, journey)
    if journey["status"] != "active":
        conn.commit()
        conn.close()
        return jsonify({"error": f"journey is already {journey['status']}"}), 409

    current_deadline = datetime.fromisoformat(journey["deadline"])
    base = max(current_deadline, datetime.utcnow())
    new_deadline = base + timedelta(minutes=data["extra_minutes"])
    conn.execute("UPDATE journeys SET deadline = ? WHERE id = ?", (new_deadline.isoformat(), journey_id))
    _log_journey_event(
        conn, journey_id, "extended",
        message=f"Check-in extended by {data['extra_minutes']} minutes.",
    )
    conn.commit()
    journey = conn.execute("SELECT * FROM journeys WHERE id = ?", (journey_id,)).fetchone()
    conn.close()
    return jsonify(_journey_to_dict(journey))


@app.route("/api/journey/<int:journey_id>/arrived", methods=["POST"])
@login_required
def journey_arrived(journey_id):
    conn = get_db()
    journey = _get_owned_journey(conn, journey_id, current_user.id)
    if not journey:
        conn.close()
        return jsonify({"error": "not found"}), 404

    journey = _check_journey_expiry(conn, journey)
    if journey["status"] != "active":
        conn.commit()
        conn.close()
        return jsonify({"error": f"journey is already {journey['status']}"}), 409

    conn.execute(
        "UPDATE journeys SET status = 'arrived', ended_at = ? WHERE id = ?",
        (datetime.utcnow().isoformat(), journey_id),
    )
    _log_journey_event(conn, journey_id, "arrived", message="Marked as arrived by user.")
    conn.commit()
    journey = conn.execute("SELECT * FROM journeys WHERE id = ?", (journey_id,)).fetchone()
    conn.close()
    return jsonify(_journey_to_dict(journey))


@app.route("/api/journey/<int:journey_id>/cancel", methods=["POST"])
@login_required
def journey_cancel(journey_id):
    conn = get_db()
    journey = _get_owned_journey(conn, journey_id, current_user.id)
    if not journey:
        conn.close()
        return jsonify({"error": "not found"}), 404

    if journey["status"] != "active":
        conn.close()
        return jsonify({"error": f"journey is already {journey['status']}"}), 409

    conn.execute(
        "UPDATE journeys SET status = 'cancelled', ended_at = ? WHERE id = ?",
        (datetime.utcnow().isoformat(), journey_id),
    )
    _log_journey_event(conn, journey_id, "cancelled", message="Cancelled by user.")
    conn.commit()
    journey = conn.execute("SELECT * FROM journeys WHERE id = ?", (journey_id,)).fetchone()
    conn.close()
    return jsonify(_journey_to_dict(journey))


@app.route("/api/journey/<int:journey_id>/timeline", methods=["GET"])
@login_required
def journey_timeline(journey_id):
    conn = get_db()
    journey = _get_owned_journey(conn, journey_id, current_user.id)
    if not journey:
        conn.close()
        return jsonify({"error": "not found"}), 404

    rows = conn.execute(
        "SELECT * FROM journey_events WHERE journey_id = ? ORDER BY id ASC",
        (journey_id,),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/journey/history", methods=["GET"])
@login_required
def journey_history():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM journeys WHERE user_id = ? ORDER BY id DESC LIMIT 30",
        (current_user.id,),
    ).fetchall()
    conn.close()
    return jsonify([_journey_to_dict(r) for r in rows])


# ---------------------------------------------------------------------------
# Safety score — dashboard summary widget, backed by the same ML risk
# predictor used for /api/check-location-risk (read-only, nothing persisted).
# ---------------------------------------------------------------------------
@app.route("/api/safety-score", methods=["GET"])
@login_required
def safety_score():
    lat = request.args.get("lat", type=float)
    lng = request.args.get("lng", type=float)

    if lat is None or lng is None:
        return jsonify({"score": None, "label": "Unknown", "engine": "none", "reason": "location unavailable"})

    conn = get_db()
    prediction, nearest_threat, _ = _predict_location_risk(conn, lat, lng)
    conn.close()

    score = max(0, min(100, round(100 - prediction["risk_score"])))
    if score >= 80:
        label = "Safe"
    elif score >= 55:
        label = "Caution"
    else:
        label = "High risk"

    return jsonify({
        "score": score,
        "label": label,
        "confidence": prediction["confidence"],
        "engine": prediction["engine"],
        "factors": prediction["factors"],
        "nearby_area": nearest_threat["area_name"] if nearest_threat else None,
    })


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


def _count_active_users_nearby(conn, lat, lng, radius_km=0.5):
    """Real (not mocked) crowd-density signal: how many other users have an
    active guardian-share within the last hour, physically close to this
    point. Same signal the ML risk predictor already uses internally
    (as `nearby_user_count`) — extracted here so the Safety Map's crowd
    density feature and the risk model stay consistent with each other."""
    one_hour_ago = (datetime.utcnow() - timedelta(hours=1)).isoformat()
    active_shares = conn.execute(
        "SELECT latitude, longitude FROM guardian_shares WHERE active = 1 AND updated_at > ?",
        (one_hour_ago,),
    ).fetchall()
    return sum(
        1 for s in active_shares
        if s["latitude"] is not None and haversine_distance(lat, lng, s["latitude"], s["longitude"]) <= radius_km
    )


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

    user_density = _count_active_users_nearby(conn, lat, lng, radius_km=0.5)

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
OSM_AMENITY_MAP = {"police": "police", "hospital": "hospital", "pharmacy": "pharmacy"}


def _lookup_nearby_services(lat, lng, service_type=None):
    """Shared by /api/nearby-services and the AI Assistant's nearby_help
    intent, so both give consistent, real answers instead of the
    assistant having its own separate (and potentially inconsistent)
    lookup path."""
    osm_results = []
    if lat is not None and lng is not None and (service_type is None or service_type in OSM_AMENITY_MAP):
        amenity_types = [OSM_AMENITY_MAP[service_type]] if service_type in OSM_AMENITY_MAP else list(OSM_AMENITY_MAP.values())
        osm_results = fetch_nearby_amenities_osm(lat, lng, amenity_types)

    if osm_results:
        source = "osm"
        for r in osm_results:
            r["distance_km"] = round(haversine_distance(lat, lng, r["lat"], r["lng"]), 2)
        results = osm_results
    else:
        # OSM unreachable, rate-limited, or returned nothing — fall back to
        # the full offline mock directory (police/hospital/pharmacy/
        # helpline all included, already filtered by service_type), not
        # just the pieces OSM doesn't cover.
        source = "mock"
        results = get_nearby_services(lat, lng, service_type=service_type)

    # Helplines aren't a queryable OSM amenity tag, so if OSM *did* succeed
    # they still need to be folded in separately from the local directory.
    if source == "osm" and service_type is None:
        results = results + [
            {
                **s,
                "distance_km": round(haversine_distance(lat, lng, s["lat"], s["lng"]), 2),
                "source": "directory",
                "open_status": derive_open_status(s["type"]),
            }
            for s in MOCK_SERVICES
            if s["type"] == "helpline"
        ]

    results.sort(key=lambda x: x["distance_km"] if x.get("distance_km") is not None else 999999)
    return results[:20], source


@app.route("/api/nearby-services", methods=["GET"])
@login_required
def nearby_services():
    lat = request.args.get("lat", type=float)
    lng = request.args.get("lng", type=float)
    service_type = request.args.get("type") or None
    results, source = _lookup_nearby_services(lat, lng, service_type)
    return jsonify({"results": results, "source": source})


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


@app.route("/api/tracking/live-history", methods=["GET"])
@login_required
def tracking_live_history():
    """BUGFIX: this was previously dead code — an orphaned, un-routed
    fragment left sitting after `tracking_stop()`'s `return` (never
    executed, no @app.route decorator). Restored as a real endpoint:
    the breadcrumb trail from `location_history`, authorized the same
    way as `/api/tracking/history` (self, or an accepted linked contact)."""
    target_user_id = request.args.get("user_id", type=int) or current_user.id

    conn = get_db()
    if not is_accepted_linked_contact(conn, target_user_id, current_user.id):
        conn.close()
        return jsonify({"error": "unauthorized"}), 403

    rows = conn.execute(
        "SELECT latitude, longitude, timestamp FROM location_history "
        "WHERE user_id = ? ORDER BY id DESC LIMIT 50",
        (target_user_id,),
    ).fetchall()
    conn.close()
    return jsonify(list(reversed([dict(r) for r in rows])))


@app.route("/api/guardian/watch/<int:user_id>/status", methods=["GET"])
@login_required
def guardian_watch_status(user_id):
    """Guardian Dashboard snapshot for a Bubble member you're authorized to
    watch: last known location + when, a live battery reading if their
    device has reported one recently, a derived connection status, and
    their active Journey (if any) so a guardian can see progress without
    switching accounts."""
    conn = get_db()
    if not is_accepted_linked_contact(conn, user_id, current_user.id):
        conn.close()
        return jsonify({"error": "unauthorized"}), 403

    last_location = conn.execute(
        "SELECT latitude, longitude, timestamp FROM location_history WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()

    active_journey = conn.execute(
        "SELECT * FROM journeys WHERE user_id = ? AND status = 'active' ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    conn.close()

    battery_state = DEVICE_BATTERY_STATE.get(user_id)

    last_update_iso = last_location["timestamp"] if last_location else None
    connection_status = "never_connected"
    if last_update_iso:
        seconds_since = (datetime.utcnow() - datetime.fromisoformat(last_update_iso)).total_seconds()
        if seconds_since < 30:
            connection_status = "live"
        elif seconds_since < 180:
            connection_status = "recent"
        else:
            connection_status = "stale"

    journey_summary = None
    if active_journey:
        journey_summary = _journey_to_dict(active_journey)

    return jsonify({
        "user_id": user_id,
        "last_location": dict(last_location) if last_location else None,
        "connection_status": connection_status,
        "battery_level": battery_state["battery_level"] if battery_state else None,
        "battery_updated_at": battery_state["updated_at"] if battery_state else None,
        "active_journey": journey_summary,
    })


# ---------------------------------------------------------------------------
# Community Safety Feed (user-specific)
# ---------------------------------------------------------------------------
@app.route("/api/feed", methods=["GET"])
@login_required
def list_feed():
    """Community Feed listing with search, category filtering, and a
    'trending' sort (engagement-weighted, recency-decayed) alongside the
    default recency sort. Each post includes like/helpful/comment counts
    and whether the current user has already liked/voted, so the
    frontend doesn't need a separate round-trip per post."""
    search = (request.args.get("q") or "").strip()
    post_type = request.args.get("post_type") or None
    sort = request.args.get("sort", "recent")

    query = """
        SELECT fp.*,
               (SELECT COUNT(*) FROM feed_likes fl WHERE fl.post_id = fp.id) AS like_count,
               (SELECT COUNT(*) FROM feed_helpful_votes fh WHERE fh.post_id = fp.id) AS helpful_count,
               (SELECT COUNT(*) FROM feed_comments fc WHERE fc.post_id = fp.id) AS comment_count,
               (SELECT COUNT(*) FROM feed_likes fl2 WHERE fl2.post_id = fp.id AND fl2.user_id = ?) AS user_has_liked,
               (SELECT COUNT(*) FROM feed_helpful_votes fh2 WHERE fh2.post_id = fp.id AND fh2.user_id = ?) AS user_has_voted_helpful
        FROM feed_posts fp
        WHERE 1=1
    """
    params = [current_user.id, current_user.id]

    if post_type:
        query += " AND fp.post_type = ?"
        params.append(post_type)
    if search:
        query += " AND (fp.message LIKE ? OR fp.area_name LIKE ?)"
        like_term = f"%{search}%"
        params.extend([like_term, like_term])

    query += " ORDER BY fp.id DESC LIMIT 150"

    conn = get_db()
    rows = [dict(r) for r in conn.execute(query, params).fetchall()]
    conn.close()

    for r in rows:
        r["user_has_liked"] = bool(r["user_has_liked"])
        r["user_has_voted_helpful"] = bool(r["user_has_voted_helpful"])

    if sort == "trending":
        # Engagement-weighted, with a recency decay so a hot post from
        # last week doesn't permanently outrank today's alerts.
        now = datetime.utcnow()
        def trending_score(r):
            age_hours = max(1, (now - datetime.fromisoformat(r["created_at"])).total_seconds() / 3600)
            engagement = r["like_count"] + 2 * r["helpful_count"] + r["comment_count"]
            return engagement / (age_hours ** 0.6)
        rows.sort(key=trending_score, reverse=True)

    return jsonify(rows[:100])


@app.route("/api/feed/trending", methods=["GET"])
@login_required
def feed_trending():
    """Compact 'Trending Alerts' strip: top engagement in the last 48h."""
    cutoff = (datetime.utcnow() - timedelta(hours=48)).isoformat()
    conn = get_db()
    rows = conn.execute(
        """
        SELECT fp.*,
               (SELECT COUNT(*) FROM feed_likes fl WHERE fl.post_id = fp.id) AS like_count,
               (SELECT COUNT(*) FROM feed_helpful_votes fh WHERE fh.post_id = fp.id) AS helpful_count,
               (SELECT COUNT(*) FROM feed_comments fc WHERE fc.post_id = fp.id) AS comment_count
        FROM feed_posts fp
        WHERE fp.created_at > ?
        """,
        (cutoff,),
    ).fetchall()
    conn.close()

    rows = [dict(r) for r in rows]
    rows.sort(key=lambda r: r["like_count"] + 2 * r["helpful_count"] + r["comment_count"], reverse=True)
    return jsonify(rows[:5])


@app.route("/api/feed", methods=["POST"])
@login_required
@limiter.limit("20 per hour", methods=["POST"], key_func=_user_rate_limit_key)
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


def _toggle_reaction(table, post_id):
    """Shared toggle logic for likes/helpful-votes: insert if absent,
    remove if present (a second tap un-likes/un-votes, matching every
    mainstream social feed's UX)."""
    conn = get_db()
    post_exists = conn.execute("SELECT 1 FROM feed_posts WHERE id = ?", (post_id,)).fetchone()
    if not post_exists:
        conn.close()
        return None

    existing = conn.execute(
        f"SELECT id FROM {table} WHERE post_id = ? AND user_id = ?", (post_id, current_user.id)
    ).fetchone()
    if existing:
        conn.execute(f"DELETE FROM {table} WHERE id = ?", (existing["id"],))
        active = False
    else:
        conn.execute(
            f"INSERT INTO {table} (post_id, user_id, created_at) VALUES (?, ?, ?)",
            (post_id, current_user.id, datetime.utcnow().isoformat()),
        )
        active = True
    conn.commit()
    count = conn.execute(f"SELECT COUNT(*) as c FROM {table} WHERE post_id = ?", (post_id,)).fetchone()["c"]
    conn.close()
    return active, count


@app.route("/api/feed/<int:post_id>/like", methods=["POST"])
@login_required
def feed_like(post_id):
    result = _toggle_reaction("feed_likes", post_id)
    if result is None:
        return jsonify({"error": "post not found"}), 404
    active, count = result
    return jsonify({"liked": active, "like_count": count})


@app.route("/api/feed/<int:post_id>/helpful", methods=["POST"])
@login_required
def feed_helpful(post_id):
    result = _toggle_reaction("feed_helpful_votes", post_id)
    if result is None:
        return jsonify({"error": "post not found"}), 404
    active, count = result
    return jsonify({"helpful": active, "helpful_count": count})


@app.route("/api/feed/<int:post_id>/comments", methods=["GET"])
@login_required
def feed_list_comments(post_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT fc.*, u.email as user_email FROM feed_comments fc "
        "LEFT JOIN users u ON u.id = fc.user_id WHERE fc.post_id = ? ORDER BY fc.id ASC",
        (post_id,),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/feed/<int:post_id>/comments", methods=["POST"])
@login_required
@limiter.limit("30 per hour", methods=["POST"], key_func=_user_rate_limit_key)
@validate_json(FeedCommentSchema)
def feed_add_comment(post_id):
    conn = get_db()
    post_exists = conn.execute("SELECT 1 FROM feed_posts WHERE id = ?", (post_id,)).fetchone()
    if not post_exists:
        conn.close()
        return jsonify({"error": "post not found"}), 404

    message = g.validated_data["message"].strip()
    conn.execute(
        "INSERT INTO feed_comments (post_id, user_id, message, created_at) VALUES (?, ?, ?, ?)",
        (post_id, current_user.id, message, datetime.utcnow().isoformat()),
    )
    conn.commit()
    count = conn.execute("SELECT COUNT(*) as c FROM feed_comments WHERE post_id = ?", (post_id,)).fetchone()["c"]
    conn.close()
    return jsonify({"status": "posted", "comment_count": count}), 201


# ---------------------------------------------------------------------------
# AI Assistant (see utils/assistant.py for the honesty note on what this
# actually is — a rule-based intent router grounded in real app data, not
# an LLM. Kept swappable behind generate_assistant_reply()).
# ---------------------------------------------------------------------------
@app.route("/api/assistant/chat", methods=["POST"])
@login_required
@limiter.limit("60 per hour", methods=["POST"], key_func=_user_rate_limit_key)
@validate_json(AssistantChatSchema)
def assistant_chat():
    data = g.validated_data
    message = data["message"].strip()
    lat, lng = data.get("latitude"), data.get("longitude")

    # Pre-classify so we only do the (potentially network-calling) real
    # data lookups the reply will actually use, not on every message.
    intent = generate_assistant_reply(message)["intent"]
    context = {}
    if intent == "nearby_help" and lat is not None and lng is not None:
        services, _source = _lookup_nearby_services(lat, lng)
        context["nearby_services"] = services

    result = generate_assistant_reply(message, context=context)

    conn = get_db()
    now = datetime.utcnow().isoformat()
    conn.execute(
        "INSERT INTO assistant_messages (user_id, role, message, intent, created_at) VALUES (?, 'user', ?, ?, ?)",
        (current_user.id, message, result["intent"], now),
    )
    conn.execute(
        "INSERT INTO assistant_messages (user_id, role, message, intent, created_at) VALUES (?, 'assistant', ?, ?, ?)",
        (current_user.id, result["reply"], result["intent"], now),
    )
    conn.commit()
    conn.close()

    return jsonify(result)


@app.route("/api/assistant/history", methods=["GET"])
@login_required
def assistant_history():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM assistant_messages WHERE user_id = ? ORDER BY id ASC LIMIT 100",
        (current_user.id,),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


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
    data = request.get_json(silent=True) or {}
    actions = data.get("actions", [])
    if not isinstance(actions, list):
        return jsonify({"error": "validation_failed", "message": "'actions' must be a list"}), 400
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

            # CRITICAL BUGFIX: this used to only log the alert + emit an
            # internal socket event — it never called send_sos_alert(), so
            # trusted contacts were never actually notified for an SOS that
            # happened to be raised while offline. That's the one scenario
            # offline queueing exists to cover.
            contacts = conn.execute(
                "SELECT * FROM contacts WHERE user_id = ?", (current_user.id,)
            ).fetchall()
            message = _format_sos_message(
                current_user.email, "offline_sync", lat, lng,
                accuracy_m=payload.get("accuracy_m"), location_source=payload.get("location_source"),
            )
            send_sos_alert(contacts, message)

            conn.execute(
                "INSERT INTO alerts (user_id, trigger_type, latitude, longitude, message, alert_type, created_at) "
                "VALUES (?, 'offline_sync', ?, ?, ?, 'sos', ?)",
                (current_user.id, lat, lng, message, datetime.utcnow().isoformat()),
            )
            conn.commit()
            offline_sos_payload = {
                "message": f"Offline SOS from {current_user.email} has just synced",
                "latitude": lat,
                "longitude": lng,
            }
            socketio.emit("sos_triggered", offline_sos_payload, room=f"user_{current_user.id}")
            socketio.emit("sos_triggered", offline_sos_payload, room=f"tracking_{current_user.id}")
            applied = True
            logger.info(
                "SOS triggered (offline sync): user=%s queue_id=%s contacts_notified=%d",
                hash_identifier(current_user.email), queue_id, len(contacts),
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
        # CRITICAL SECURITY FIX: this used to also do `join_room("sos_room")`
        # — a single global room every logged-in user joined. SOS events
        # (containing the triggering user's email + exact live GPS
        # coordinates) were broadcast to that room, meaning any other
        # user on the entire platform received a stranger's real-time
        # location + identity during an emergency. SOS notifications now
        # only go to the triggering user's own devices (`user_{id}`, joined
        # above) and to whoever is actively, authorizedly watching their
        # live tracking (`tracking_{id}`, joined via join_tracking below,
        # which already enforces the accepted-linked-contact check).


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
# ---------------------------------------------------------------------------
# TIER 3 FEATURE: Live tracking authorization + WebSocket location updates
# ---------------------------------------------------------------------------
# Guardian Dashboard support: an in-memory cache of each user's
# most-recently-reported battery level. Deliberately not persisted to the
# DB — this is a live "how reachable is this person right now" signal for
# whoever is actively watching, not history worth keeping (location_history
# already persists positions). Reset on server restart, which is fine:
# a fresh value arrives with that user's next location_update.
DEVICE_BATTERY_STATE = {}  # user_id -> {"battery_level": int|None, "updated_at": iso str}
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
    battery_level = data.get("battery_level")  # 0-100 int, optional — not every browser exposes this

    if lat is None or lng is None:
        return

    timestamp = datetime.utcnow().isoformat()

    if isinstance(battery_level, (int, float)) and 0 <= battery_level <= 100:
        DEVICE_BATTERY_STATE[current_user.id] = {"battery_level": round(battery_level), "updated_at": timestamp}

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
            "battery_level": DEVICE_BATTERY_STATE.get(current_user.id, {}).get("battery_level"),
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


if __name__ == "__main__":
    os.makedirs(os.path.dirname(app.config["DATABASE_PATH"]) or ".", exist_ok=True)
    init_db()
    logger.info(
        "Starting SafeHer (env=%s, debug=%s, host=%s, port=%s)",
        app.config.get("ENV_NAME", "unknown"), app.config["DEBUG"], app.config["HOST"], app.config["PORT"],
    )
    socketio.run(app, debug=app.config["DEBUG"], host=app.config["HOST"], port=app.config["PORT"])