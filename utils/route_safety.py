"""
Route safety scoring.

Demo version: produces a heuristic safety score (0-100) using time-of-day
and a small simulated "incident density" table, so it works with zero
external API keys.

To go live: plug in the Google Maps Directions API to get the actual route,
and replace SIMULATED_INCIDENT_DATA with a real crowd-sourced /
police-reported incident dataset keyed by area.
"""

import random
from datetime import datetime

# Simulated incident density per area name (demo data only)
SIMULATED_INCIDENT_DATA = {
    "default": 3,
}


def _time_of_day_penalty():
    hour = datetime.now().hour
    if 22 <= hour or hour < 5:
        return 35  # late night, higher risk
    if 18 <= hour < 22:
        return 15  # evening
    return 5  # daytime


def get_route_safety_score(origin, destination):
    """
    Returns a safety score and simple color-coded rating for a route.
    In production this would call Google Maps Directions API for the
    actual path, then score each segment using lighting/CCTV/crowd data.
    """
    base_score = 100
    base_score -= _time_of_day_penalty()

    # Simulated random incident variance seeded by route string so the
    # same route gives a consistent (repeatable) score during a demo
    seed_value = sum(ord(c) for c in (origin + destination)) or 1
    rng = random.Random(seed_value)
    incident_penalty = rng.randint(5, 25)
    base_score -= incident_penalty

    score = max(0, min(100, base_score))

    if score >= 75:
        rating = "Safe"
        color = "green"
    elif score >= 45:
        rating = "Caution"
        color = "orange"
    else:
        rating = "High Risk"
        color = "red"

    return {
        "origin": origin,
        "destination": destination,
        "score": score,
        "rating": rating,
        "color": color,
        "note": "Score based on time-of-day and simulated incident data (demo mode).",
    }
