"""
Route safety scoring.

Two modes:
1. Text-only (`get_route_safety_score`) — the original demo heuristic
   (time-of-day + a seeded-random incident penalty), used when the caller
   only has place names, no coordinates. Kept as-is for backward
   compatibility with any existing caller.
2. Coordinate-based (`fetch_osrm_routes` + `sample_route_points`) — real
   routing via the free, keyless OSRM public demo server, which returns
   actual road geometry (and alternative routes, for Route Comparison).
   The corridor is then sampled and scored against real risk-zone data
   (audits + risk_alerts) by app.py's `_score_route_geometry`, since that
   needs DB access this module doesn't have.

Both external calls are best-effort: short timeout, narrow exception
handling, and an explicit `None`/`[]` return on any failure so callers can
fall back to the offline heuristic — the same "degrades gracefully instead
of crashing" pattern used by utils/audio_classifier.py and utils/push.py.
Note: the OSRM/Overpass public demo servers are free for light,
non-commercial use and are rate-limited — fine for a hackathon demo, not a
substitute for a paid routing provider in production.
"""

import random
from datetime import datetime

try:
    import requests
except ImportError:  # pragma: no cover - requests should always be present per requirements.txt
    requests = None

OSRM_BASE_URL = "https://router.project-osrm.org"
OSRM_TIMEOUT_SECONDS = 5

# Simulated incident density per area name (demo data only, text-only mode)
SIMULATED_INCIDENT_DATA = {
    "default": 3,
}


def _time_of_day_penalty(at_hour=None):
    hour = at_hour if at_hour is not None else datetime.now().hour
    if 22 <= hour or hour < 5:
        return 35  # late night, higher risk
    if 18 <= hour < 22:
        return 15  # evening
    return 5  # daytime


def score_to_rating(score):
    if score >= 75:
        return "Safe", "green"
    if score >= 45:
        return "Caution", "orange"
    return "High Risk", "red"


def get_route_safety_score(origin, destination):
    """Text-only heuristic fallback — no coordinates available, so this is
    necessarily approximate (repeatable per route-name via a string-seeded
    RNG, not a real safety measurement)."""
    base_score = 100
    base_score -= _time_of_day_penalty()

    seed_value = sum(ord(c) for c in (origin + destination)) or 1
    rng = random.Random(seed_value)
    incident_penalty = rng.randint(5, 25)
    base_score -= incident_penalty

    score = max(0, min(100, base_score))
    rating, color = score_to_rating(score)

    return {
        "origin": origin,
        "destination": destination,
        "score": score,
        "rating": rating,
        "color": color,
        "mode": "heuristic",
        "note": "Approximate score based on time-of-day only — no coordinates were provided, so real route/risk-zone data couldn't be used.",
    }


def fetch_osrm_routes(origin_lat, origin_lng, dest_lat, dest_lng, alternatives=False):
    """Real road-network routing via OSRM's free public demo server.
    Returns a list of route dicts: {distance_km, duration_min, geometry:
    [[lat, lng], ...]}, most-direct first. Returns [] on any failure
    (network, timeout, malformed response, no route found) so the caller
    can fall back to a straight-line estimate."""
    if requests is None:
        return []
    try:
        url = (
            f"{OSRM_BASE_URL}/route/v1/driving/"
            f"{origin_lng},{origin_lat};{dest_lng},{dest_lat}"
        )
        resp = requests.get(
            url,
            params={
                "overview": "full",
                "geometries": "geojson",
                "alternatives": "true" if alternatives else "false",
            },
            timeout=OSRM_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "Ok":
            return []

        routes = []
        for route in data.get("routes", []):
            coords = route.get("geometry", {}).get("coordinates", [])  # [lng, lat] pairs
            geometry = [[lat, lng] for lng, lat in coords]
            if not geometry:
                continue
            routes.append({
                "distance_km": round(route.get("distance", 0) / 1000, 2),
                "duration_min": round(route.get("duration", 0) / 60, 1),
                "geometry": geometry,
            })
        return routes
    except Exception:
        # Network error, timeout, rate-limited, malformed JSON, etc. — all
        # treated the same: no real route data, caller falls back.
        return []


def sample_route_points(geometry, num_samples=6):
    """Evenly-spaced sample points [[lat, lng], ...] along a route's
    geometry, used to check the corridor against risk-zone data without
    scoring every single vertex (which can be hundreds of points)."""
    if not geometry:
        return []
    if len(geometry) <= num_samples:
        return geometry
    step = (len(geometry) - 1) / (num_samples - 1)
    return [geometry[round(i * step)] for i in range(num_samples)]


def derive_open_status(service_type, opening_hours_tag=None):
    """Best-effort open/closed signal. Deliberately conservative: parsing
    the full OSM opening_hours mini-language correctly (holidays, split
    shifts, etc.) is its own small project, and guessing wrong in a safety
    directory is worse than saying 'call to confirm'. So this only claims
    24/7 when the source data actually says so (or for police/emergency
    helplines, where that's true almost universally) — everything else
    gets an honest 'hours vary' rather than a fabricated Open/Closed Now.
    """
    if opening_hours_tag and "24/7" in opening_hours_tag:
        return {"status": "open_24_7", "label": "Open 24/7"}
    if service_type in ("police", "helpline"):
        return {"status": "open_24_7", "label": "Typically 24/7"}
    if service_type == "hospital":
        return {"status": "open_24_7", "label": "Emergency dept. typically 24/7"}
    return {"status": "hours_vary", "label": "Hours vary — call to confirm"}


def fetch_nearby_amenities_osm(lat, lng, categories, radius_m=1500):
    """Real nearby-service data from OpenStreetMap via the free Overpass
    API — no API key required.

    `categories` accepts either:
      - a list of raw OSM `amenity=` tag values (original call style, e.g.
        ["police", "hospital", "pharmacy"]), or
      - a dict of {category_name: [(tag_key, tag_value), ...]} for
        categories that aren't a plain `amenity=` tag (metro stations use
        `railway`/`station`, public toilets/shelters/taxi stands vary too).

    Every result is tagged with `type` = our own category name (not the
    raw OSM tag), so callers never have to know which underlying OSM key
    matched. Returns [] on any failure so the caller can fall back to
    MOCK_SERVICES."""
    if requests is None or not categories:
        return []

    tag_filters = categories if isinstance(categories, dict) else {c: [("amenity", c)] for c in categories}

    try:
        filter_parts = []
        # Recovers which of our friendlier category names a raw (key, value)
        # OSM tag pair belongs to, since a tag like railway=station doesn't
        # carry "metro" anywhere in it on its own.
        category_for_tag = {}
        for category, tag_pairs in tag_filters.items():
            for key, value in tag_pairs:
                filter_parts.append(f'node["{key}"="{value}"](around:{radius_m},{lat},{lng});')
                category_for_tag[(key, value)] = category

        query = f"[out:json][timeout:{OSRM_TIMEOUT_SECONDS}];({''.join(filter_parts)});out center 30;"
        resp = requests.post(
            "https://overpass-api.de/api/interpreter",
            data={"data": query},
            timeout=OSRM_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        elements = resp.json().get("elements", [])

        results = []
        for el in elements:
            tags = el.get("tags", {})
            name = tags.get("name")
            if not name:
                continue  # skip unnamed nodes — not useful in a directory list

            matched_category = next(
                (category for (key, value), category in category_for_tag.items() if tags.get(key) == value),
                None,
            )
            if matched_category is None:
                continue

            results.append({
                "name": name,
                "type": matched_category,
                "lat": el.get("lat"),
                "lng": el.get("lon"),
                "phone": tags.get("phone") or tags.get("contact:phone") or "N/A",
                "source": "osm",
                "open_status": derive_open_status(matched_category, tags.get("opening_hours")),
            })
        return results
    except Exception:
        return []