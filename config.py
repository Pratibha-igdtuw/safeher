"""
Environment-based configuration for SafeHer v2.

app.py picks a config class based on FLASK_ENV (development / production /
testing) and loads it with `app.config.from_object(get_config())`. Every
value has a safe local default so the app still runs out of the box with
no .env file, but anything sensitive (SECRET_KEY especially) should be
overridden via environment variables / a real .env file for any deployment
that isn't "just running on my laptop for a demo".

See .env.example for the full list of variables this reads, and
SETUP_GUIDE.md for how to point DATABASE_URL at PostgreSQL instead of the
bundled SQLite file.
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _bool_env(name, default=False):
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def database_path_from_url(database_url):
    """Translate a `sqlite:///...` DATABASE_URL into a filesystem path.

    app.py's data-access layer is raw sqlite3 (wrapped with WAL mode + a
    busy timeout in `get_db()` — see app.py), so only sqlite:// URLs are
    understood here. Pointing DATABASE_URL at postgres:// is a supported
    *deployment intent* (documented in SETUP_GUIDE.md under "Using
    PostgreSQL"), but actually talking to Postgres requires swapping this
    data-access layer for SQLAlchemy, which is the documented upgrade path
    rather than something this file silently pretends to do.
    """
    if database_url.startswith("sqlite:///"):
        path = database_url[len("sqlite:///"):]
        if not path or path == ":memory:":
            return path or ":memory:"
        if not os.path.isabs(path):
            path = os.path.join(BASE_DIR, path)
        return path
    if database_url.startswith("sqlite://"):
        # e.g. sqlite://:memory:
        remainder = database_url[len("sqlite://"):]
        return remainder or ":memory:"

    raise ValueError(
        f"DATABASE_URL={database_url!r} is not a sqlite:// URL. "
        "The built-in data access layer only speaks SQLite directly; "
        "see SETUP_GUIDE.md 'Using PostgreSQL' for the SQLAlchemy "
        "migration path required to point this at a real Postgres server."
    )


class Config:
    """Base config. Every field can be overridden by an environment
    variable of the same name (loaded from .env in app.py via
    python-dotenv). Defaults here are intentionally safe for local/dev
    use and are NOT appropriate for a real deployment as-is."""

    ENV_NAME = "base"

    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-secret-key-change-in-production")

    # DEBUG defaults to False no matter which config class is active unless
    # an operator explicitly opts in via the DEBUG env var.
    DEBUG = _bool_env("DEBUG", default=False)
    TESTING = False

    DATABASE_URL = os.environ.get(
        "DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'data', 'safeher.db')}"
    )
    DATABASE_PATH = database_path_from_url(DATABASE_URL)

    LOG_DIR = os.environ.get("LOG_DIR", os.path.join(BASE_DIR, "logs"))
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
    LOG_MAX_BYTES = int(os.environ.get("LOG_MAX_BYTES", 1_000_000))
    LOG_BACKUP_COUNT = int(os.environ.get("LOG_BACKUP_COUNT", 5))

    HOST = os.environ.get("HOST", "127.0.0.1")
    PORT = int(os.environ.get("PORT", 5000))

    # SQLite connection hardening (see get_db() in app.py)
    DB_BUSY_TIMEOUT_MS = int(os.environ.get("DB_BUSY_TIMEOUT_MS", 5000))
    DB_CONNECT_TIMEOUT_S = int(os.environ.get("DB_CONNECT_TIMEOUT_S", 10))


class DevConfig(Config):
    ENV_NAME = "development"


class ProdConfig(Config):
    ENV_NAME = "production"


class TestConfig(Config):
    ENV_NAME = "testing"
    TESTING = True
    DEBUG = False
    # Individual test modules override DATABASE_URL/DATABASE_PATH on the
    # Flask app.config at runtime (each test gets its own temp DB file) —
    # this class-level value is just a safe placeholder.
    DATABASE_URL = "sqlite:///" + os.path.join(BASE_DIR, "data", "test.db")
    DATABASE_PATH = database_path_from_url(DATABASE_URL)


CONFIG_MAP = {
    "development": DevConfig,
    "production": ProdConfig,
    "testing": TestConfig,
}


def get_config(name=None):
    """Resolve a config class from FLASK_ENV (or an explicit name).
    Unknown/unset values fall back to DevConfig, matching Flask's own
    convention — but note DEBUG itself still defaults to False regardless
    of which config class is chosen (see Config.DEBUG above)."""
    name = name or os.environ.get("FLASK_ENV", "development")
    return CONFIG_MAP.get(name, DevConfig)