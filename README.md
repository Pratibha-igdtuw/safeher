# SafeHer — AI Guardian for Women's Safety

SafeHer is a "predictive + passive" personal safety companion. Instead of
relying only on a user to manually raise an alarm, it can detect distress,
warn people before they enter risky areas, and keep a trusted circle
informed automatically — while staying honest about what's a real
integration versus a demo/mock one (see [Known limitations](#known-limitations)
below).

This project started as a hackathon prototype and has since had three
rounds of hardening (`TIER1_SUMMARY.md`, `FEATURES_V2.md`, and this
README/`SETUP_GUIDE.md`) adding real ML, security, offline support, and
now push notifications + accessibility. It is **not** a finished,
production-audited product — see the limitations section before deploying
it anywhere real users depend on it.

## Feature checklist

### Core safety features
- [x] **SOS button** — one tap sends live location + an alert message to
      trusted contacts (SMS via Twilio, or mock/console mode by default)
      and to any accepted Bubble members via WebSocket + Web Push.
- [x] **Fake call** — schedules a realistic incoming call with a
      text-to-speech script, to help exit an uncomfortable situation.
- [x] **Voice distress detection** — Web Speech API transcript is scanned by
      a keyword/pattern classifier; auto-triggers SOS above a confidence
      threshold.
- [x] **Real audio ML distress detection** — captures raw mic audio in the
      browser, computes a log-mel spectrogram client-side (no raw audio
      ever leaves the device), and runs real YAMNet inference server-side
      in `utils/audio_classifier.py` — with a documented heuristic fallback
      if TensorFlow/TF-Hub aren't available.
- [x] **Safe check-in timer** — set a timer before walking somewhere; if you
      don't confirm safety before it runs out, an SOS auto-fires.
- [x] **Route safety score** — 0–100 heuristic score for a route.
- [x] **Anonymous record** — silently captures audio evidence; nothing is
      uploaded, playback/download only, stays on-device.
- [x] **Safety Map** (SafetiPin-style) — tap any spot to run a 6-parameter
      crowd-sourced Safety Audit; pins are color-coded by score.
- [x] **ML-powered predictive risk alerts** — a RandomForest model
      (`utils/risk_predictor.py`, trained via `scripts/train_risk_model.py`)
      scores risk from location, time-of-day, nearby audit density/severity,
      and active-user density, and proactively warns before you enter a
      high-risk area.
- [x] **Nearby safety directory** — closest police/hospital/pharmacy/helpline.
- [x] **Guardian (Bubble) live location sharing** — continuous live tracking
      with a breadcrumb trail, Private Mode to pause without ending the
      session, and invite-based Bubble members (registered SafeHer accounts
      that must accept before they can view/track you).
- [x] **Community safety feed** — crowd-sourced alerts, safe-spot tips, and
      incident reports tagged by area.

### Account & security
- [x] **Multi-user accounts** — email + password, hashed with werkzeug,
      session-based via Flask-Login.
- [x] **Two-Factor Authentication (TOTP)** — enable/disable via QR code
      (`pyotp` + `qrcode`), enforced on login.
- [x] **Rate limiting** — login, signup, and client-error reporting are
      rate-limited (Flask-Limiter); see [Rate limits](#rate-limits) below.
- [x] **Security headers** — CSP / HSTS / X-Frame-Options via
      `flask-talisman` (falls back to manual headers if not installed).
- [x] **Input validation** — every POST body is validated with marshmallow
      schemas (`validators.py`) instead of trusting raw JSON.

### Real-time & offline
- [x] **WebSocket real-time notifications** — SOS/risk alerts push instantly
      to any open tab (Flask-SocketIO), no polling.
- [x] **Real Web Push notifications** — SOS, high-risk-area, and check-in-
      expiry alerts also reach subscribed devices via the native Push API
      (through `pywebpush` + VAPID), **even when every SafeHer tab is
      closed**. See [Setting up push notifications](SETUP_GUIDE.md#push-notifications).
- [x] **Offline-first PWA** — app shell + static assets cached by a service
      worker; SOS raised while offline is queued in `localStorage` and
      synced automatically the moment connectivity returns.
- [x] **Admin analytics dashboard** — heatmap of all audits, stats, and a
      7-day alert timeline, gated behind `is_admin`.

### Reliability & accessibility (this round)
- [x] **Frontend error resilience** — every CDN `<script>` (socket.io,
      TensorFlow.js, Leaflet) degrades its one feature gracefully instead of
      halting the rest of the app if the CDN is blocked or fails to load; a
      global `window.onerror` / `unhandledrejection` handler reports
      client-side errors to `/api/client-error` so failures on real users'
      devices are visible to us instead of invisible.
- [x] **Accessibility (a11y)** — proper `aria-live` regions, `role="tablist"`
      / `role="tab"` / `aria-selected` tab navigation with full keyboard
      support (Tab, Enter/Space, arrow keys, Home/End), a real focus trap +
      `aria-modal` on the fake-call overlay, visible focus outlines, and
      `role="alert"` on toast notifications. Verified with an automated
      axe-core pass targeting zero critical WCAG2A/AA violations (see
      [Accessibility](#accessibility) below).

## Tech stack

- **Backend:** Flask + Flask-SocketIO + SQLite
- **Frontend:** HTML/CSS/vanilla JS, Web Speech API, Web Audio API,
  Leaflet.js + OpenStreetMap, a Service Worker (offline cache + Web Push)
- **ML:** TensorFlow/TensorFlow Hub (YAMNet) for audio distress detection,
  scikit-learn (RandomForest) for location risk prediction
- **Push:** `pywebpush` + VAPID (standards-based Web Push, no
  proprietary push-service account required)
- **Pluggable integrations:** Twilio (SMS), Google Maps/Places (routing,
  nearby services) — both currently run in mock mode by default

## Setup

See **[SETUP_GUIDE.md](SETUP_GUIDE.md)** for full setup, environment
variables, a demo walkthrough, and troubleshooting. Quick start:

```bash
cd safeher
pip install -r requirements.txt
python app.py
```

Open **http://127.0.0.1:5000** in Chrome (best support for speech
recognition, speech synthesis, and Web Push).

No API keys are required to run the app — SMS delivery, ML risk
prediction, and audio classification all have documented mock/fallback
paths. Web Push does need a VAPID keypair (free, self-generated, two
minutes — see SETUP_GUIDE.md) to actually deliver notifications; without
one it silently no-ops instead of erroring.

## Known limitations

Being upfront about what's *not* production-ready:

- **Database:** SQLite by default. Fine for a single-process demo; not
  suited to multiple workers/instances without moving to Postgres/MySQL.
- **Rate limiting storage:** in-memory by default (`RATELIMIT_STORAGE_URI`),
  which does **not** share counters across multiple gunicorn/uwsgi workers —
  point it at Redis in any multi-worker deployment.
- **No SMS fallback in real mode without Twilio credentials** —
  `utils/alerts.py` runs in `MOCK_MODE = True` (prints to console) unless
  you supply real Twilio credentials; there's no other SMS provider wired
  in and no automatic retry/backoff if Twilio itself is down.
- **No push delivery guarantee** — Web Push depends on the browser's own
  push service (e.g. Mozilla's, Google's) being reachable; there's no
  server-side retry queue if a send fails for a reason other than a dead
  subscription (which we do prune automatically).
- **Audio ML fallback is heuristic, not a real model** — if TensorFlow /
  the YAMNet weights aren't available (e.g. no outbound network to
  tfhub.dev on first run), `utils/audio_classifier.py` degrades to a
  documented heuristic instead of real inference. This is intentional and
  logged, not silently wrong, but it is a materially weaker detector.
- **ML risk model is trained on synthetic data, not real incidents** —
  there's no real historical incident dataset in this project;
  `scripts/train_risk_model.py` trains the RandomForest on a synthetic
  dataset designed to mirror plausible real-world patterns (see the
  docstring in `utils/risk_predictor.py`). The pipeline and integration
  are real and ready to retrain, but the specific risk numbers it ships
  with today are not learned from real incidents — re-run training against
  exported real audit/alert history before relying on this operationally.
- **Route safety scoring is heuristic**, not backed by a real routing API
  or a real incident dataset — see `utils/route_safety.py`.
- **Nearby services directory is a small mock dataset**, not a live Google
  Places / OSM Overpass query — see `utils/safety_services.py`.
- **No account recovery flow** — there's no "forgot password" or 2FA
  recovery-codes flow; losing your password or authenticator app means
  losing the account (or manual DB surgery, see SETUP_GUIDE.md).
- **No push subscription expiry/renewal notice to the user** — if a
  browser silently drops a subscription, the user finds out by *not*
  getting a notification, not from an in-app warning.
- **Single admin flag, no granular roles/audit log** — `is_admin` is a
  single boolean per user with no permission tiers and no record of who
  viewed what in the admin dashboard.
- **Test coverage is uneven** — see `TIER1_SUMMARY.md` for a per-module
  breakdown; the mocked/fallback code paths (no-TensorFlow, no-Twilio) are
  intentionally not exercised by the same tests as the real paths.

## Rate limits

| Endpoint | Limit | Notes |
|---|---|---|
| `POST /login` | 5 per 15 minutes | keyed by IP + attempted email |
| `POST /signup` | 10 per hour | keyed by IP |
| `POST /api/client-error` | 30 per minute | keyed by IP; stops a broken client from flooding logs |

All other API routes are currently unlimited beyond normal Flask request
handling — see [Known limitations](#known-limitations) re: in-memory
storage if you add more limits behind a multi-worker deployment.

## Accessibility

Run automatically against the rendered templates with
[axe-core](https://github.com/dequelabs/axe-core) (via `jsdom` + Node,
`wcag2a`/`wcag2aa` rule sets): **0 violations** across `index.html`,
`login.html`, `signup.html`, `admin.html`, and `offline.html` at the time
of this round of changes. Keyboard support (Tab/Shift+Tab, Enter/Space,
arrow keys + Home/End on the tab bar, a focus trap on the fake-call modal)
was verified manually since axe-core can't fully exercise interaction
behavior on its own. If you add new interactive markup, re-run the same
check before merging — see `CONTRIBUTING.md`.

## Going from demo to production

Every "fake"/mock integration point is isolated in one file, so it's a
clean upgrade path:

- `utils/alerts.py` — set `MOCK_MODE = False` and add Twilio credentials to
  send real SMS instead of printing to the console.
- `utils/route_safety.py` — swap the simulated incident table for a real
  Google Maps Directions API call + a real crowd-sourced incident dataset.
- `utils/distress_detector.py` / `utils/audio_classifier.py` — already real
  YAMNet-based ML with a keyword-only fallback; the fallback path can be
  removed once TF-Hub connectivity is guaranteed in your deployment.
- `utils/safety_services.py` — currently a small mock directory. Swap in a
  real Google Places "nearby search" call for live results anywhere.
- `utils/push.py` — already a real `pywebpush`/VAPID integration; just
  needs `VAPID_PUBLIC_KEY`/`VAPID_PRIVATE_KEY` set (see SETUP_GUIDE.md).
- The Safety Map already uses real OpenStreetMap tiles via Leaflet (no API
  key needed) — the `audits` table is ready to scale into a real
  crowd-sourced dataset like SafetiPin's.

## Why this fits Social Impact & Inclusion

- Addresses a direct, everyday real-world safety problem.
- Voice-based interaction makes it accessible to low-literacy users; the
  accessibility pass in this round makes it usable by keyboard-only and
  screen-reader users too.
- Works offline/mock for demo, but the architecture (real ML, real push,
  real rate limiting, real input validation) is built to scale toward
  real deployment at campus or city scale.
- Community angle: incident data model can grow into a crowd-sourced
  safety heatmap over time.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for local dev setup, test running,
and code style notes.