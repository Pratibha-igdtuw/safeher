# SafeHer — Setup Guide

Covers install, environment variables, a full demo walkthrough (Tier 1 +
Tier 2 + Tier 3 features), database management, accessibility testing, and
troubleshooting. For a feature overview and known limitations, see
[README.md](README.md).

## 1. Install & run

```bash
git clone <this repo>
cd safeher
pip install -r requirements.txt
python app.py
```

Server starts at `http://127.0.0.1:5000`. Open it in **Chrome** for the
best support of Web Speech (recognition + synthesis), Web Audio, and Web
Push.

First visit redirects to `/login` → click "Sign up" → create an account
with email + password. No demo/seed accounts are pre-created.

Optional (for local development, linting, and the test suite):
```bash
pip install -r requirements-dev.txt
```

## 2. Environment variables

Copy `.env.example` to `.env` and fill in real values before deploying
anywhere besides your own machine:

```bash
cp .env.example .env
```

| Variable | Default | Purpose |
|---|---|---|
| `FLASK_ENV` | `development` | `production` turns on secure-only cookies, HSTS, and force-HTTPS. Leave as `development` for local `http://` testing. |
| `SECRET_KEY` | placeholder | Signs session cookies. Generate: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `CORS_ALLOWED_ORIGINS` | `http://127.0.0.1:5000,http://localhost:5000` | Comma-separated list of origins allowed to open a Socket.IO connection. |
| `RATELIMIT_STORAGE_URI` | `memory://` | Rate-limit counter storage. Point at Redis (`redis://host:6379/0`) for any multi-worker deployment — `memory://` does **not** share state across workers. |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_FROM_NUMBER` | unset | Only needed if you flip `MOCK_MODE = False` in `utils/alerts.py` to send real SMS. |
| `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` | unset | Web Push signing keypair. Without these, push silently no-ops (see below) — every other feature keeps working. |
| `VAPID_CLAIM_EMAIL` | `mailto:admin@example.com` | Contact address push services may surface if they ever flag abusive send volume. Doesn't need to be a real inbox to function. |

## 3. Push notifications

Real Web Push (native OS notifications, delivered even when every SafeHer
tab is closed) needs a VAPID keypair. Generate one:

```bash
pip install py-vapid
python -c "from py_vapid import Vapid; v = Vapid(); v.generate_keys(); print('PUBLIC:', v.public_key); print('PRIVATE:', v.private_key)"
```

or with the `vapid` CLI that ships with `py-vapid`:

```bash
vapid --gen
```

Put the resulting values in `.env` as `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY`,
restart the server, then in the app:

1. Go to the **Home** tab.
2. Under "Push Notifications", click **Enable Push Notifications** and
   accept the browser's permission prompt.
3. Trigger an SOS, a high-risk-area detection, or let a check-in timer
   expire — a native notification should appear even if you switch away
   from the tab (try closing it entirely to confirm).

If `VAPID_PUBLIC_KEY`/`VAPID_PRIVATE_KEY` aren't set, `GET
/api/push/vapid-public-key` returns a `503 push_not_configured` and the
Home-tab button will show that push isn't configured — nothing else in the
app is affected.

## 4. Demo walkthrough (all tiers)

### Setup: create two accounts
```
Account 1: alice@example.com / password123
Account 2: bob@example.com   / password123
```

### Tier 1 — Core safety features
1. **Multi-user login** — sign up as Alice, log out, sign up as Bob, note
   each account's data (contacts, alerts) is isolated.
2. **Trusted contacts** — Guardian tab → add a contact (Name/Phone/Relation).
3. **Predictive risk alert** — Safety Map tab → tap a spot → submit an
   audit with all sliders low (~1) and an area name → go to Guardian tab →
   "Start Sharing" near that spot → see the real-time "⚠️ high-risk area"
   toast (now also arrives as a push notification if enabled).
4. **Voice distress detection** — Home tab → "Start Listening" → say
   "help me" (or type it in the transcript box) → "Analyze Transcript" →
   see "🚨 DISTRESS DETECTED"; above the confidence threshold this
   auto-triggers SOS.
5. **Real audio ML monitoring** — Home tab → "Start Audio ML Monitoring" —
   captures mic audio, computes a spectrogram in-browser, and classifies it
   server-side via YAMNet (or the documented fallback — see
   `utils/audio_classifier.py`).
6. **SOS button** — Home tab → tap **SOS** → see "Alert sent to N
   contact(s)" plus a real-time WebSocket toast and (if configured) a push
   notification.
7. **Admin analytics dashboard** — promote a user to admin:
   ```sql
   sqlite3 data/safeher.db
   UPDATE users SET is_admin = 1 WHERE email = 'alice@example.com';
   ```
   Log out/in, then open the **Admin** link in the top nav.

### Tier 2 — Security & offline
8. **Two-Factor Authentication** — Home tab → "Account Security" card →
   "Enable 2FA" → scan the QR code in an authenticator app → confirm with
   the 6-digit code. Log out and back in to see the TOTP prompt.
9. **Offline-first** — open DevTools → Network → set to "Offline" → tap
   **SOS** → see it queue with "will retry automatically" → go back online
   → see it auto-sync (also shows up as `offline_sync` in `/api/alerts`).
10. **Rate limiting** — try 6 wrong-password login attempts within 15
    minutes on the same account → see a 429 with a "please wait" message.

### Tier 3 — Real-time, live tracking, Bubble, community
11. **Bubble members** — as Alice, Guardian tab → "Bubble Members" → invite
    `bob@example.com` → log in as Bob → accept the invite → as Alice,
    "Start Live Tracking" → as Bob, select Alice from the dropdown →
    "View Live Location" → watch Alice's breadcrumb trail update live.
12. **Community feed** — Community tab → post a safety alert / safe-spot
    tip → see it appear instantly.
13. **Push notifications** — see [section 3](#3-push-notifications) above.
14. **Accessibility** — unplug your mouse: Tab through the tab bar (arrow
    keys move between tabs, Enter/Space activates), trigger the fake call
    and confirm focus is trapped inside the overlay and returns to the
    button that opened it on close.
15. **Frontend error resilience** — in DevTools → Network, block requests
    to `cdn.socket.io` (or `unpkg.com` for Leaflet) and reload. Real-time
    alerts (or the map) should show a clear "unavailable" message instead
    of a blank broken page, and everything else should keep working.
    Any JS error anywhere in the app is also POSTed to
    `/api/client-error` — check the server log (`safeher.push`/root
    logger output) to see it land.

## 5. Database management

<<<<<<< HEAD
> **Note:** the database now runs in WAL mode (see "Database robustness &
> using PostgreSQL" above), so you'll see `safeher.db-wal` and
> `safeher.db-shm` files alongside `safeher.db` in `data/` — that's normal
> and they're safe to ignore (SQLite manages them automatically).

### View database
=======
>>>>>>> 12f0b65 (Updated SafeHer features and UI)
```bash
sqlite3 data/safeher.db
```

**Create admin user:**
```sql
UPDATE users SET is_admin = 1 WHERE email = 'admin@example.com';
```

**View all users:**
```sql
SELECT id, email, is_admin, totp_enabled FROM users;
```

**View high-risk audits:**
```sql
SELECT area_name, overall_score FROM audits WHERE overall_score < 45;
```

**View SOS alerts (last 7 days):**
```sql
SELECT user_id, trigger_type, created_at FROM alerts
WHERE created_at > datetime('now', '-7 days');
```

**View risk alerts:**
```sql
SELECT * FROM risk_alerts;
```

**View push subscriptions:**
```sql
SELECT user_id, endpoint, created_at FROM push_subscriptions;
```

<<<<<<< HEAD
## ⚙️ Environment-based configuration (Tier 3)

The app now loads all sensitive/environment-specific values from environment
variables instead of being hardcoded — `SECRET_KEY`, the database location,
the `DEBUG` flag, allowed WebSocket CORS origins, log verbosity, and the
host/port it binds to.

### 1. Create your `.env` file
```bash
cp .env.example .env
```
Every variable in `.env.example` has a comment explaining what it does and
a safe local default, so the app runs fine even if you don't edit anything
— **except** `SECRET_KEY`, which you should always set to something random
before doing anything beyond a quick local demo:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```
Paste the output in as `SECRET_KEY=...` in `.env`.

### 2. Pick an environment
`FLASK_ENV` selects which config class from `config.py` is used
(`DevConfig` / `ProdConfig` / `TestConfig`):
```bash
FLASK_ENV=development   # default — safe local defaults
FLASK_ENV=production    # for real deployments
```
`DEBUG` is read independently of `FLASK_ENV` and **defaults to `False`** if
it's not set at all — the Flask/SocketIO dev server's debug mode (auto
reload + interactive debugger, which is unsafe to expose publicly) is only
ever on if you explicitly set `DEBUG=True`.

### 3. Environment variables reference

| Variable | Default | Purpose |
|---|---|---|
| `FLASK_ENV` | `development` | Selects `DevConfig` / `ProdConfig` / `TestConfig` |
| `SECRET_KEY` | insecure dev placeholder | Flask session/cookie signing key — **always override in real deployments** |
| `DEBUG` | `False` | Flask/SocketIO debug mode (auto-reload, interactive debugger) |
| `DATABASE_URL` | `sqlite:///data/safeher.db` | Where the app stores its data — see "Using PostgreSQL" below |
| `CORS_ORIGINS` | `*` | Allowed origins for WebSocket connections (Flask-SocketIO `cors_allowed_origins`) |
| `LOG_DIR` | `logs` | Directory for the rotating `app.log` file |
| `LOG_LEVEL` | `INFO` | Minimum log level written to file + console |
| `LOG_MAX_BYTES` | `1000000` | Rotate `app.log` once it hits this size |
| `LOG_BACKUP_COUNT` | `5` | How many rotated `app.log.N` files to keep |
| `HOST` | `127.0.0.1` | Bind address for the dev server |
| `PORT` | `5000` | Bind port for the dev server |
| `DB_BUSY_TIMEOUT_MS` | `5000` | SQLite `PRAGMA busy_timeout` — how long to retry before raising "database is locked" |
| `DB_CONNECT_TIMEOUT_S` | `10` | `sqlite3.connect(..., timeout=...)` |

---

## 🗄️ Database robustness & using PostgreSQL

### SQLite hardening (default, no setup needed)
`app.py`'s `get_db()` now opens every SQLite connection in **WAL mode**
(`PRAGMA journal_mode=WAL`) with a busy timeout (`PRAGMA busy_timeout`,
`DB_BUSY_TIMEOUT_MS` above). WAL mode lets reads proceed while a write is
in flight instead of blocking everything, and the busy timeout makes
SQLite retry for a few seconds instead of immediately throwing
`database is locked` the moment two requests write at the same time. This
is a meaningful improvement for a small/medium deployment but SQLite is
still a single-file, single-writer-at-a-time database — for real
multi-region or high-concurrency production use, move to PostgreSQL.

### Using PostgreSQL instead
The data-access layer in `app.py` is currently raw `sqlite3` calls (kept
that way intentionally in Tier 3 Part 2 so existing query behavior stays
byte-for-byte identical — see the PR/commit notes). `DATABASE_URL` already
supports being pointed at a `postgres://` URL from a configuration
standpoint (`config.py` reads it), but actually talking to Postgres
requires swapping the raw `sqlite3.connect(...)` calls in `get_db()` for
SQLAlchemy. That migration looks like:

1. **Add the dependency**
   ```bash
   pip install SQLAlchemy psycopg2-binary
   ```
2. **Set `DATABASE_URL` to your Postgres instance**
   ```bash
   DATABASE_URL=postgresql+psycopg2://user:password@host:5432/safeher
   ```
3. **Replace `get_db()` / `init_db()`** with a SQLAlchemy `Engine` +
   `sessionmaker` (or `scoped_session` for thread-safety under
   Flask-SocketIO's worker model), and rewrite the `CREATE TABLE IF NOT
   EXISTS` / `PRAGMA` statements in `init_db()` as SQLAlchemy `Table`
   definitions or an `alembic` migration — Postgres doesn't understand
   SQLite's `PRAGMA` statements or `AUTOINCREMENT` syntax (`SERIAL` /
   `IDENTITY` is the Postgres equivalent).
4. Every `conn.execute("... ?", (...))` call in `app.py` uses SQLite's
   `?` placeholder style; SQLAlchemy Core/raw-SQL execution with
   `psycopg2` expects `%s` (or named `:param` placeholders if you use
   SQLAlchemy's `text()` with bound parameters, which is the recommended
   approach and avoids the placeholder-style mismatch entirely).
5. Point a local Postgres at it quickly for testing with Docker:
   ```bash
   docker run --name safeher-postgres -e POSTGRES_PASSWORD=devpass \
     -e POSTGRES_DB=safeher -p 5432:5432 -d postgres:16
   ```

This is deliberately documented as the upgrade path rather than
implemented outright, since a full ORM swap touches every one of the
~40 query call sites in `app.py` and deserves its own reviewed change
rather than being bundled silently into an infra/logging update.

---

## 📋 Structured logging & audit trail (Tier 3)

All `print()` statements have been replaced with Python's `logging`
module. Logs are written to both the console and a rotating file at
`logs/app.log` (`LOG_DIR`/`LOG_MAX_BYTES`/`LOG_BACKUP_COUNT` above).

- **INFO** — normal request/connect events (WebSocket connect/disconnect,
  successful logins, successful 2FA, tracking start/stop)
- **WARNING** — failed logins, invalid 2FA codes (setup, login, disable)
- **ERROR** — unhandled exceptions (caught by a global Flask error handler
  and logged with a full traceback, `exc_info=True`)

Every SOS trigger (manual, auto-triggered, or synced from the offline
queue) and every failed login/2FA attempt is logged with a timestamp and
a **hashed** user identifier (`hash_identifier()` in `app.py` — SHA-256,
truncated) rather than a raw email address, so `logs/app.log` doesn't
become a second copy of your users table while still letting you
correlate a given user's events across log lines.

```bash
tail -f logs/app.log
```

---

## 🔐 Security notes (before production)
=======
## 6. Accessibility testing
>>>>>>> 12f0b65 (Updated SafeHer features and UI)

An automated pass with [axe-core](https://github.com/dequelabs/axe-core)
is the fastest way to catch regressions:

<<<<<<< HEAD
1. **Secret key** — now read from the `SECRET_KEY` environment variable
   (see `.env.example` / the "Environment-based configuration" section
   above) instead of being hardcoded in `app.py`. Generate a random one:
   ```bash
   python -c "import secrets; print(secrets.token_hex(32))"
   ```

2. **Database:** Use PostgreSQL instead of SQLite for multi-region — see
   "Using PostgreSQL" above.
=======
```bash
npm install --no-save jsdom axe-core
node -e "
const fs = require('fs');
const { JSDOM } = require('jsdom');
const axeSource = fs.readFileSync(require.resolve('axe-core/axe.min.js'), 'utf8');
const html = fs.readFileSync('rendered.html', 'utf8'); // a Jinja-stripped copy of the template you're testing
(async () => {
  const dom = new JSDOM(html, { runScripts: 'outside-only', resources: 'usable', url: 'http://localhost/' });
  dom.window.eval(axeSource);
  const results = await dom.window.axe.run(dom.window.document, { runOnly: { type: 'tag', values: ['wcag2a','wcag2aa'] } });
  console.log('violations:', results.violations.length);
  results.violations.forEach(v => console.log(v.impact, v.id, v.help));
})();
"
```

This catches missing labels, invalid ARIA structure, and contrast/name
issues automatically, but **cannot** verify actual keyboard behavior
(focus traps, arrow-key navigation, tab order) — test those manually, or
with a real browser extension (axe DevTools / Lighthouse) against the
running app for a fuller pass, since jsdom doesn't execute layout/paint.
>>>>>>> 12f0b65 (Updated SafeHer features and UI)

## 7. Security notes before deploying anywhere real

⚠️ **Change these first:**

1. **Secret key** — set a real `SECRET_KEY` in `.env` (see section 2).
2. **VAPID keys** — generate your own; never reuse an example keypair.
3. **Database** — move off SQLite for multi-worker/multi-region deployment.
4. **Rate limit storage** — point `RATELIMIT_STORAGE_URI` at Redis.
5. **WebSocket** — use a production-grade eventlet/gevent worker:
   ```bash
   pip install eventlet
   gunicorn --worker-class eventlet -w 1 app:app
   ```
6. **HTTPS** — deploy with real TLS (e.g. Let's Encrypt) and
   `FLASK_ENV=production`.
7. **Twilio SMS** — add real credentials to `.env` / `utils/alerts.py` to
   replace mock SMS with real delivery.

See [README.md's Known limitations](README.md#known-limitations) for a
fuller list of what's still mock/heuristic vs. real.

<<<<<<< HEAD
5. **Twilio SMS:** Add credentials to `utils/alerts.py` to send real SMS

6. **CORS:** Set `CORS_ORIGINS` to your real frontend origin(s) instead of
   the default `*` before exposing the WebSocket endpoint publicly.

7. **`DEBUG`:** Make sure `DEBUG` is unset or `False` in production — it
   now defaults to `False`, but double-check your deployment's `.env`
   doesn't have `DEBUG=True` left over from local development.

---

## 🧪 Testing checklist

- [ ] Signup works (new email)
- [ ] Login works (correct email + password)
- [ ] Logout clears session
- [ ] Add/delete contacts
- [ ] Safety audit creates pins on map
- [ ] Location sharing triggers risk alert
- [ ] SOS sends to contacts (mock SMS shown in console)
- [ ] Admin dashboard loads (if is_admin = 1)
- [ ] WebSocket notifications appear (top-right toast)

---

## 📝 File structure

```
safeher_enhanced/
├── app.py                    # Main Flask app
├── config.py                 # NEW: Config / DevConfig / ProdConfig / TestConfig
├── .env.example               # NEW: environment variable reference (copy to .env)
├── requirements.txt          # Dependencies (updated: python-dotenv)
├── requirements-dev.txt      # NEW: test-only dependencies (pytest, pytest-cov)
├── SETUP_GUIDE.md            # This file
├── FEATURES_V2.md            # Feature documentation
├── data/
│   └── safeher.db          # SQLite (auto-created, WAL mode)
├── logs/
│   └── app.log              # NEW: rotating structured log file
├── tests/
│   ├── conftest.py           # NEW: shared pytest fixtures (isolated per-test DB)
│   ├── test_features.py      # Tier 1 test suite
│   ├── test_2fa.py           # NEW: 2FA setup/confirm/login/disable
│   ├── test_tracking.py      # NEW: live tracking start/stop/history + authorization
│   └── test_offline_sync.py  # NEW: offline queue → real SOS alert
├── .github/workflows/
│   └── tests.yml             # UPDATED: runs the full pytest suite on every push
├── templates/
│   ├── index.html          # Main dashboard
│   ├── login.html          # Login page
│   ├── signup.html         # Signup page
│   └── admin.html          # Admin analytics dashboard
├── static/
│   ├── css/
│   │   └── style.css
│   └── js/
│       └── main.js
└── utils/
    ├── alerts.py            # SMS sending
    ├── distress_detector.py # Keyword fallback
    ├── route_safety.py      # Route scoring
    └── safety_services.py   # Nearby services
```

---

## 🚨 Troubleshooting
=======
## 8. Troubleshooting
>>>>>>> 12f0b65 (Updated SafeHer features and UI)

### WebSocket connection fails
- Check the browser console — if socket.io's CDN was blocked, the app now
  shows a console warning and disables just the real-time-alert feature
  instead of breaking; other features (SOS, push, offline queue) still work.
- Ensure `Flask-SocketIO` is installed: `pip install flask-socketio`.

### Push notifications don't arrive
- Confirm `VAPID_PUBLIC_KEY`/`VAPID_PRIVATE_KEY` are set and the server was
  restarted after adding them.
- Confirm the browser granted permission (`Notification.permission ===
  "granted"`) and that a service worker is registered
  (`navigator.serviceWorker.getRegistrations()` in DevTools console).
- Closing the tab is expected/required to test "works when closed" — but
  the browser process itself usually needs to keep running in the
  background depending on OS/browser push wake-up support.

### Admin dashboard shows 403
- Make sure the user has `is_admin = 1` in the database, then log out and
  back in to refresh the session.

### Map doesn't load
- If Leaflet's CDN (`unpkg.com`) is blocked, the app now shows an inline
  "map unavailable" message in the map panel instead of a blank broken
  page — check the browser console for the CDN warning to confirm that's
  the cause, and check your network egress rules if it's unexpected.

### Risk alert doesn't trigger
<<<<<<< HEAD
- Create audit with low score (< 45) first on map
- Share location in Guardian tab
- Geofence is 500m (0.5 km) — adjust test coordinates if needed

---

## 🎓 Demo script (for judges)

```
"SafeHer v2 adds 5 production-ready features to the hackathon prototype:

1. REAL AI-BASED DISTRESS DETECTION
   - Browser-side TensorFlow.js + YAMNet audio classification
   - Currently using keyword fallback for demo
   - Auto-triggers SOS at 70%+ confidence
   [Tap "Start Listening" + say "help"]

2. PREDICTIVE RISK ALERT
   - Automatic geofence check when location shared
   - Users warned if entering high-risk zone (< 45 score)
   - WebSocket push (no polling!)
   [Share location in Guardian tab → see warning]

3. MULTI-USER LOGIN
   - Signup/login with email + password
   - Hashed passwords, session-based auth
   - Each user's data isolated
   [Logout + signup new account]

4. REAL-TIME CONTACT NOTIFICATION
   - WebSocket replaces polling
   - SOS alerts push instantly
   - 0 page refresh needed
   [Trigger SOS → see notification top-right]

5. ANALYTICS DASHBOARD
   - City-scale heatmap of audits
   - Risk zone identification
   - Alert timeline + statistics
   [Login as admin → click Admin link]

All features follow existing code style + integrate seamlessly with v1.
Fully scalable for production deployment!"
```

---

Happy demoing! 🛡️ 💪
=======
- Create an audit with a low score (< 45) first on the Safety Map.
- Share location in the Guardian tab near that same spot.
- The geofence/ML feature radius is roughly 500m — adjust test coordinates
  if needed.
>>>>>>> 12f0b65 (Updated SafeHer features and UI)
