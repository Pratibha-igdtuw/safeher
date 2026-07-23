"""
Trains the RandomForestRegressor used by utils/risk_predictor.py and
saves it to models/risk_predictor.pkl.

Usage:
    python scripts/train_risk_model.py
    python scripts/train_risk_model.py --db data/safeher.db   # blend in real audits if present

Data source
-----------
SafeHer has no historical incident dataset to train on yet, so this
script generates a SYNTHETIC training set that encodes the same
domain assumptions the original hand-written geofence rule used
(late night + low-lit/low-security areas + few other users nearby =
higher risk), plus noise, so the model learns a smooth, generalizing
version of that rule rather than a lookup table.

If a real SQLite DB with an `audits` table is passed via --db, real
audit rows are blended in as additional (weakly-labeled) training
examples: each real audit's own overall_score is used to derive a
plausible risk_score for its location. This lets the model improve
as real audit data accumulates without requiring a separate labeled
incident dataset.

Re-run this script periodically (e.g. weekly, via CI/cron) once real
data is flowing, so models/risk_predictor.pkl stays current.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys

import joblib
import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error, r2_score

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from utils.risk_predictor import FEATURE_NAMES, MODEL_PATH  # noqa: E402

RNG = np.random.default_rng(42)

# Roughly bounds a mid-size city so lat/lng features are realistic.
LAT_RANGE = (28.40, 28.80)
LNG_RANGE = (76.95, 77.35)


def _synthetic_row():
    lat = RNG.uniform(*LAT_RANGE)
    lng = RNG.uniform(*LNG_RANGE)
    hour = RNG.integers(0, 24)
    dow = RNG.integers(0, 7)
    nearby_audits_count = RNG.integers(0, 8)
    avg_nearby_score = RNG.uniform(10, 95) if nearby_audits_count else 70.0
    user_density = RNG.integers(0, 12)
    time_since_incident = RNG.choice(
        [RNG.uniform(0, 6), RNG.uniform(6, 48), RNG.uniform(48, 999)],
        p=[0.15, 0.25, 0.60],
    )

    # --- Ground-truth risk model (the "physics" the forest should learn) ---
    risk = 0.0
    risk += max(0.0, 60 - avg_nearby_score) * 0.55 if nearby_audits_count else 15.0
    if hour >= 22 or hour < 5:
        risk += 20
    elif 18 <= hour < 22:
        risk += 8
    risk += max(0, 6 - user_density) * 1.8
    if time_since_incident < 6:
        risk += 18
    elif time_since_incident < 24:
        risk += 8
    risk += RNG.normal(0, 6)  # noise
    risk = float(np.clip(risk, 0, 100))

    return {
        "latitude": lat,
        "longitude": lng,
        "time_of_day": hour,
        "day_of_week": dow,
        "nearby_audits_count": nearby_audits_count,
        "avg_nearby_score": avg_nearby_score,
        "user_density": user_density,
        "time_since_incident": min(time_since_incident, 999),
        "risk_score": risk,
    }


def generate_synthetic_dataset(n_rows=6000):
    return [_synthetic_row() for _ in range(n_rows)]


def load_real_audits(db_path):
    """Weakly-labeled real examples: derive a risk_score from each audit's
    own overall_score (inverse relationship) so real data nudges the model
    without needing separately-labeled incident outcomes."""
    if not db_path or not os.path.exists(db_path):
        return []

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM audits").fetchall()
    conn.close()

    dataset = []
    for r in rows:
        others = [dict(x) for x in rows if x["id"] != r["id"]]
        nearby = [
            o for o in others
            if abs(o["latitude"] - r["latitude"]) < 0.01 and abs(o["longitude"] - r["longitude"]) < 0.01
        ]
        avg_nearby_score = (
            sum(o["overall_score"] for o in nearby) / len(nearby) if nearby else r["overall_score"]
        )
        dataset.append({
            "latitude": r["latitude"],
            "longitude": r["longitude"],
            "time_of_day": 20,     # unknown at audit time -> assume evening (conservative)
            "day_of_week": 3,
            "nearby_audits_count": len(nearby),
            "avg_nearby_score": avg_nearby_score,
            "user_density": 2,
            "time_since_incident": 200,
            "risk_score": float(np.clip(100 - r["overall_score"], 0, 100)),
        })
    return dataset


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=None, help="Path to safeher.db to blend in real audits")
    parser.add_argument("--n-synthetic", type=int, default=6000)
    parser.add_argument("--out", default=MODEL_PATH)
    args = parser.parse_args()

    dataset = generate_synthetic_dataset(args.n_synthetic)
    real_rows = load_real_audits(args.db)
    if real_rows:
        # Oversample real rows a bit so they have visible influence even
        # when far outnumbered by synthetic rows.
        dataset += real_rows * max(1, args.n_synthetic // max(1, len(real_rows) * 20))

    X = np.array([[row[f] for f in FEATURE_NAMES] for row in dataset], dtype=np.float32)
    y = np.array([row["risk_score"] for row in dataset], dtype=np.float32)

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    model = RandomForestRegressor(
        n_estimators=200,
        max_depth=12,
        min_samples_leaf=5,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_train, y_train)

    preds = model.predict(X_test)
    mae = mean_absolute_error(y_test, preds)
    r2 = r2_score(y_test, preds)
    print(f"Trained on {len(X_train)} rows ({len(real_rows)} real, rest synthetic).")
    print(f"Held-out MAE: {mae:.2f} risk points | R^2: {r2:.3f}")

    importances = dict(zip(FEATURE_NAMES, model.feature_importances_))
    print("Feature importances:")
    for name, imp in sorted(importances.items(), key=lambda kv: -kv[1]):
        print(f"  {name:22s} {imp:.3f}")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    joblib.dump({"model": model, "feature_names": FEATURE_NAMES}, args.out)
    print(f"Saved model to {args.out}")


if __name__ == "__main__":
    main()
