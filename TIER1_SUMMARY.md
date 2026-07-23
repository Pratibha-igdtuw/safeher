# SafeHer v2 — Tier 1 Implementation Summary

## What was added

| # | Feature | Files |
|---|---|---|
| 1 | Real audio ML deployment | `utils/audio_classifier.py`, `/api/audio-classify` in `app.py`, mic-capture + mel-spectrogram pipeline in `static/js/main.js` |
| 2 | ML-powered risk prediction | `utils/risk_predictor.py`, `scripts/train_risk_model.py`, `models/risk_predictor.pkl`, integrated into `/api/check-location-risk` |
| 3 | Test suite | `tests/test_features.py` (69 tests), `requirements-dev.txt`, `.github/workflows/tests.yml` |

## Test results

```
69 passed
TOTAL coverage: 87% (target: 85%+)
```

Per-module breakdown:

| Module | Coverage |
|---|---|
| `app.py` | 94% |
| `utils/risk_predictor.py` | 97% |
| `utils/distress_detector.py` | 100% |
| `utils/safety_services.py` | 95% |
| `utils/route_safety.py` | 74% |
| `utils/audio_classifier.py` | 66%* |
| `utils/alerts.py` | 48%* |

\* Lower on these two because the real-TensorFlow / real-Twilio code paths
aren't exercised in an environment without TF-Hub network access or Twilio
credentials — both are intentionally mocked/skipped rather than faked. See
"Honest limitations" below.

Full interactive report: `htmlcov/index.html` (open in a browser).

## Two pre-existing bugs found and fixed

These were in the original codebase, not introduced by Tier 1 work — surfaced
while writing tests against real request flows:

1. **`signup()` crashed on every call.** `conn.lastrowid` was called on the
   `sqlite3.Connection` object, which doesn't have that attribute (it lives
   on the cursor `execute()` returns). Every signup attempt threw a 500.
   Fixed in `app.py`.
2. **`static/js/main.js` had a syntax error.** A Python-style triple-quoted
   docstring (`"""..."""`) was left inside `checkLocationRisk()`, which is
   not valid JavaScript — this broke parsing for the *entire* file in any
   browser. Verified with `node --check` before and after the fix.

## Honest limitations (please read before demoing)

**Feature 1 (audio ML):** Real YAMNet inference requires downloading ~15MB
of pretrained weights from `tfhub.dev` at runtime. In a sandboxed
environment without outbound access to that domain, `utils/audio_classifier.py`
automatically falls back to a documented signal-processing heuristic
(spectral energy + onset sharpness) instead of failing. Every API response
is tagged `"engine": "yamnet"` or `"engine": "heuristic_fallback"` so this
is never silently hidden. On a machine with normal internet access,
`pip install tensorflow tensorflow-hub` + first request will trigger the
one-time weight download and the real model takes over automatically — no
code changes needed.

**Feature 2 (risk prediction):** The RandomForestRegressor is real and
genuinely trained (held-out MAE 4.79 risk points, R²=0.84), but on a
*synthetic* dataset (`scripts/train_risk_model.py`) that encodes the same
domain assumptions the original geofence rule used (late night + low
safety-audit scores + few nearby users = higher risk), since there's no
real historical incident dataset to train on yet. The pipeline is fully
real and ready to retrain — run
`python scripts/train_risk_model.py --db data/safeher.db` once real audit
data accumulates, and it will blend real rows into training automatically.

**CI:** The GitHub Actions workflow deliberately skips installing
`tensorflow`/`tensorflow-hub` (CI runners typically can't reach `tfhub.dev`
either), so it always tests the heuristic-fallback path plus everything
else. This mirrors how the app is designed to behave in any offline/locked
environment.

## How to verify locally

```bash
pip install -r requirements-dev.txt
python scripts/train_risk_model.py        # regenerates models/risk_predictor.pkl
pytest --cov=app --cov=utils --cov-report=html -v
open htmlcov/index.html                   # or xdg-open on Linux
python app.py                             # run the app itself
```
