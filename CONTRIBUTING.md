# Contributing to SafeHer

Thanks for working on this. A few conventions the codebase already follows
— please keep following them so it stays consistent as more people touch it.

## Getting set up

```bash
git clone <this repo>
cd safeher
pip install -r requirements-dev.txt   # includes requirements.txt
cp .env.example .env                  # fill in SECRET_KEY at minimum
python app.py
```

See [SETUP_GUIDE.md](SETUP_GUIDE.md) for the full walkthrough, env vars,
and how to generate VAPID keys for push notifications.

## Running tests

```bash
pytest --cov=app --cov=utils --cov-report=term-missing
```

CI runs the same suite on Python 3.11 and 3.12 (`.github/workflows/tests.yml`).
`TIER1_SUMMARY.md` has a per-module coverage breakdown and explains which
paths are intentionally not covered (real-TensorFlow / real-Twilio code
paths that need external network access or credentials this repo doesn't
ship with).

When you add a route or feature, add tests to `tests/test_features.py`
following the existing "Feature N — ..." docstring grouping at the top of
that file, rather than starting a new test file per feature.

## Code conventions

- **Every POST body goes through `validators.py`.** Add a new
  `marshmallow.Schema` there and use the `@validate_json(YourSchema)`
  decorator — don't call `request.get_json(force=True)` directly in a new
  route. A handful of existing routes still do (`/api/2fa/confirm`,
  `/api/2fa/disable`, `/api/2fa/verify-login`, `/api/offline-actions`) for
  historical reasons rather than deliberate design — bringing those onto
  `validate_json` with a proper schema (see `TOTPVerifySchema`, already
  defined and unused, as a starting point for the 2FA ones) is a good,
  contained cleanup PR. `/api/audio-classify` uses
  `request.get_json(silent=True)` plus manual checks instead, because its
  payload includes a large embedded spectrogram array that doesn't fit
  marshmallow's normal field-by-field validation well.
- **Mock/fallback integrations stay isolated in `utils/`.** If you're
  wiring up a new external service, follow the pattern in `utils/alerts.py`
  (a `MOCK_MODE` flag) or `utils/push.py` / `utils/audio_classifier.py` (an
  `ImportError`-guarded optional dependency with a documented fallback) —
  don't let a missing API key or package crash the app.
- **New DB tables go in `init_db()`** as `CREATE TABLE IF NOT EXISTS`, with
  a migration block below (see the `PRAGMA table_info` pattern already
  used for `risk_alerts`/`users`) if you're adding a column to an existing
  table instead.
- **Socket.IO events**: server → client events are named as past-tense
  facts (`sos_triggered`, `risk_alert`, `tracking_joined`); client → server
  are named as commands (`join_tracking`, `location_update`). Keep new
  events consistent with that split.
- **Web Push payloads** go through `utils/push.py`'s `send_push_to_user(s)`
  helpers — don't call `pywebpush.webpush(...)` directly from `app.py`, so
  subscription pruning (dead endpoints returning 404/410) stays centralized.

## Frontend conventions

- **No new CDN `<script>` tag without an `onerror` fallback.** Follow the
  pattern in `templates/index.html` (`window.__SAFEHER_CDN_FAILED.<name> =
  true`) and check that flag before using the corresponding global in
  `main.js`, so a blocked/failed CDN degrades one feature instead of
  halting the whole script.
- **Accessibility is not optional for new interactive markup.** At minimum:
  - Every form control needs a visible `<label>` or an `aria-label`.
  - Every custom widget (modal, tab set, menu) needs the matching ARIA
    role/state (`role="dialog"` + `aria-modal`, `role="tablist"`/`"tab"` +
    `aria-selected`, etc.) — see `templates/index.html` for the existing
    patterns to copy.
  - Anything that opens a modal-like overlay needs a focus trap and needs
    to restore focus to the triggering element on close (see
    `trapFocus`/`closeFakeCall` in `static/js/main.js`).
  - Run the axe-core check described in
    [SETUP_GUIDE.md § Accessibility testing](SETUP_GUIDE.md#6-accessibility-testing)
    against any template you touch before opening a PR — target zero
    `wcag2a`/`wcag2aa` violations. It won't catch keyboard-only issues by
    itself; test Tab/Shift+Tab/Enter/Space/arrow keys by hand too.
- **Client-side errors are reported automatically** via the
  `window.onerror`/`unhandledrejection` listener in `index.html` →
  `POST /api/client-error`. Don't add competing global error handlers that
  swallow errors before that one sees them.

## Known rough edges (also see README's "Known limitations")

If you're picking up one of these, it's a good, contained first PR:

- SQLite → Postgres migration path.
- A real "forgot password" / 2FA-recovery-codes flow.
- Moving `RATELIMIT_STORAGE_URI` docs/defaults toward Redis-by-default for
  local dev too, so the memory-vs-Redis gap doesn't surprise anyone in prod.
- Replacing the mock `utils/safety_services.py` directory with a real
  Google Places / OSM Overpass call.
- Bringing `/api/2fa/confirm`, `/api/2fa/disable`, and
  `/api/2fa/verify-login` onto `validators.py`/`validate_json` like every
  other POST route (see the note above).

## Commit / PR etiquette

- Keep the mock-vs-real distinction honest in commit messages and PR
  descriptions — if a feature is a heuristic/fallback rather than the real
  thing, say so, the way `utils/audio_classifier.py`'s docstring does.
- If you touch `app.py`'s DB schema, bump/extend the migration block in
  `init_db()` rather than assuming a fresh `data/safeher.db` — existing
  installs need to upgrade in place.