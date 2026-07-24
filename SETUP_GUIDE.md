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

## 6. Accessibility testing

An automated pass with [axe-core](https://github.com/dequelabs/axe-core)
is the fastest way to catch regressions:

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

## 8. Troubleshooting

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
- Create an audit with a low score (< 45) first on the Safety Map.
- Share location in the Guardian tab near that same spot.
- The geofence/ML feature radius is roughly 500m — adjust test coordinates
  if needed.