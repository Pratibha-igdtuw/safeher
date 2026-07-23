"""
Feature 2 (Tier 1 enhancement): ML-powered location risk prediction.

Replaces the pure geofence rule ("is there a low-score audit within
500m?") with a RandomForestRegressor trained on features that combine
location, time context, and the density/severity of nearby safety
audits, so risk is predicted continuously (0-100) instead of being a
hard in/out-of-radius flag.

Honest note on training data
-----------------------------
There is no real historical incident dataset available in this
hackathon project — `audits` is user-submitted and typically small.
scripts/train_risk_model.py trains on a *synthetic* dataset generated
to mirror plausible real-world patterns (higher risk late at night, in
low-lit/low-crowd areas, near existing low-score audits) so the
pipeline, feature schema, and integration are all genuinely real and
ready to retrain — but the specific numbers this ships with are not
learned from real incidents. Before relying on this in production,
re-run scripts/train_risk_model.py against exported real audit +
alert history.
"""

from __future__ import annotations

import math
import os
from functools import lru_cache
from typing import Optional

import joblib
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH = os.path.join(BASE_DIR, "models", "risk_predictor.pkl")

FEATURE_NAMES = [
    "latitude",
    "longitude",
    "time_of_day",        # hour, 0-23
    "day_of_week",         # 0=Monday .. 6=Sunday
    "nearby_audits_count", # audits within 500m
    "avg_nearby_score",    # mean overall_score of those audits (0-100)
    "user_density",        # active guardian_shares within 500m in last hour
    "time_since_incident", # hours since most recent alert within 500m (capped)
]


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return 2 * R * math.asin(math.sqrt(a))


class RiskPredictor:
    """Wraps a trained RandomForestRegressor. Falls back to the original
    geofence heuristic if no trained model file is present, so the app
    still runs correctly before `scripts/train_risk_model.py` has ever
    been executed."""

    def __init__(self, model_path: str = MODEL_PATH):
        self.model_path = model_path
        self._model = None
        self._feature_names = FEATURE_NAMES
        self._load_attempted = False

    def _load(self):
        if self._load_attempted:
            return
        self._load_attempted = True
        if os.path.exists(self.model_path):
            bundle = joblib.load(self.model_path)
            self._model = bundle["model"]
            self._feature_names = bundle.get("feature_names", FEATURE_NAMES)

    @property
    def is_model_loaded(self) -> bool:
        self._load()
        return self._model is not None

    # ------------------------------------------------------------------
    def build_features(self, lat, lng, dt, nearby_audits, nearby_user_count=0, hours_since_incident=999) -> dict:
        """
        nearby_audits: list of dicts/rows with 'latitude','longitude','overall_score'
        computed by the caller (app.py) from the audits table, already
        filtered to within a reasonable search radius (e.g. 1km) so this
        function just does the final 500m distance + aggregation math.
        """
        within_500m = [
            a for a in nearby_audits
            if haversine_km(lat, lng, a["latitude"], a["longitude"]) <= 0.5
        ]
        nearby_count = len(within_500m)
        avg_score = (
            sum(a["overall_score"] for a in within_500m) / nearby_count
            if nearby_count else 70.0  # neutral-safe prior when no data nearby
        )

        return {
            "latitude": lat,
            "longitude": lng,
            "time_of_day": dt.hour,
            "day_of_week": dt.weekday(),
            "nearby_audits_count": nearby_count,
            "avg_nearby_score": avg_score,
            "user_density": nearby_user_count,
            "time_since_incident": min(hours_since_incident, 999),
        }

    def _features_to_vector(self, features: dict) -> np.ndarray:
        return np.array([[features[name] for name in self._feature_names]], dtype=np.float32)

    # ------------------------------------------------------------------
    def predict(self, features: dict) -> dict:
        self._load()

        if self._model is None:
            return self._predict_fallback(features)

        vec = self._features_to_vector(features)
        risk_score = float(np.clip(self._model.predict(vec)[0], 0, 100))

        # Confidence proxied by agreement across the forest's individual trees
        # (low variance across trees -> the forest is confident).
        tree_preds = np.array([est.predict(vec)[0] for est in self._model.estimators_])
        spread = float(tree_preds.std())
        confidence = float(np.clip(1.0 - (spread / 50.0), 0.05, 0.98))

        return {
            "risk_score": round(risk_score, 1),
            "confidence": round(confidence, 2),
            "engine": "random_forest",
            "factors": self._explain(features, risk_score),
        }

    def _predict_fallback(self, features: dict) -> dict:
        """No trained model on disk yet — approximate with the same signal
        the original geofence rule used, so behavior degrades gracefully."""
        avg_score = features["avg_nearby_score"]
        nearby_count = features["nearby_audits_count"]
        night_penalty = 15 if (features["time_of_day"] >= 22 or features["time_of_day"] < 5) else 0

        base_risk = max(0.0, 100 - avg_score) if nearby_count else 20.0
        risk_score = float(np.clip(base_risk + night_penalty, 0, 100))

        return {
            "risk_score": round(risk_score, 1),
            "confidence": 0.3,
            "engine": "geofence_fallback",
            "factors": self._explain(features, risk_score),
        }

    # ------------------------------------------------------------------
    def _explain(self, features: dict, risk_score: float) -> dict:
        """Human-readable factor breakdown + recommendation, used both in
        the API response and the ml_risk_alert WebSocket payload."""
        reasons = []
        if features["time_of_day"] >= 22 or features["time_of_day"] < 5:
            reasons.append("Late-night hours (10 PM-5 AM) historically correlate with higher risk.")
        if features["nearby_audits_count"] > 0 and features["avg_nearby_score"] < 45:
            reasons.append(
                f"{features['nearby_audits_count']} nearby safety audit(s) average "
                f"{features['avg_nearby_score']:.0f}/100."
            )
        if features["user_density"] == 0:
            reasons.append("Few or no other SafeHer users currently active nearby.")
        if features["time_since_incident"] < 24:
            reasons.append("A safety alert was logged near this location in the last 24 hours.")
        if not reasons:
            reasons.append("No strong individual risk factors detected; score reflects overall context.")

        if risk_score >= 70:
            recommendation = "Avoid this area if possible. Share your live location and consider an alternate route."
        elif risk_score >= 45:
            recommendation = "Stay alert, keep your phone accessible, and consider sharing your live location."
        else:
            recommendation = "No elevated risk detected for this location right now."

        return {"reasons": reasons, "recommendation": recommendation}


@lru_cache(maxsize=1)
def get_predictor() -> RiskPredictor:
    return RiskPredictor()
