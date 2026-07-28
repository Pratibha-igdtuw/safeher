"""
Geoapify API client — Search Autocomplete, Reverse Geocoding, and Places
(nearby amenities), for the Safety Map's upgraded mapping stack.

Every function here follows the exact same contract as the existing
Nominatim/OSRM/Overpass helpers in utils/route_safety.py:
  - short timeout (5s)
  - narrow, best-effort exception handling
  - an explicit "no result" return ([] or None) on ANY failure — missing
    API key, network error, timeout, rate limit, malformed response —
    so callers can fall back to the free/keyless path instead of crashing
    or surfacing a raw error to the user.

This module never raises. It also never talks to the browser directly —
app.py's /api/places/* routes proxy these calls server-side, same
CSP-friendly, correctly-User-Agent'd pattern already used for Nominatim/
OSRM/Overpass, and the reason those routes exist at all (see /api/geocode's
docstring in app.py).
"""

try:
    import requests
except ImportError:  # pragma: no cover - requests should always be present per requirements.txt
    requests = None

GEOAPIFY_TIMEOUT_SECONDS = 5
GEOAPIFY_BASE_URL = "https://api.geoapify.com/v1"

# Our own category names -> Geoapify Places API category strings.
# https://apidocs.geoapify.com/docs/places/#categories
GEOAPIFY_CATEGORY_MAP = {
    "police": "service.police",
    "hospital": "healthcare.hospital",
    "pharmacy": "healthcare.pharmacy",
    "metro": "public_transport.subway,public_transport.train",
    "toilet": "amenity.toilet",
    "shelter": "social_facility",
    "cab_stand": "amenity.taxi",
    "atm": "service.financial.atm",
    # OSM has no single reliable global tag for "women's help desk"; we
    # honestly map this to the same real-world facility type the app
    # already uses for its helpline directory (see OSM_CATEGORY_TAGS /
    # MOCK_SERVICES in app.py) rather than inventing an unverifiable one.
    "helpline": "social_facility",
}


def _have_key(api_key):
    return bool(requests) and bool(api_key)


def autocomplete_search(query, api_key, lat=None, lng=None, limit=6):
    """Forward-geocode / autocomplete via Geoapify's Autocomplete API.

    Returns a list shaped exactly like Nominatim's response
    (display_name/lat/lon) — the same shape utils/route_safety.py's
    geocode_search() already returns — plus a couple of extra, purely
    additive fields (category, address_line1/2) that older callers can
    just ignore. This means the existing frontend rendering code in
    safety-map.js's initMapSearch() doesn't need to change at all, only
    the URL it's pointed at.

    Returns [] if no API key is configured, or on any failure, so the
    caller (app.py's /api/places/autocomplete) can fall back to
    geocode_search() (Nominatim).
    """
    if not _have_key(api_key) or not query or not query.strip():
        return []

    query = query.strip()[:200]
    params = {
        "text": query,
        "apiKey": api_key,
        "limit": max(1, min(int(limit or 6), 10)),
        "format": "json",
    }
    if lat is not None and lng is not None:
        # Soft-bias toward the map's current location (Geoapify's
        # "proximity" bias, not a hard filter) — same intent as the
        # viewbox parameter geocode_search() already supports.
        params["bias"] = f"proximity:{lng},{lat}"

    try:
        resp = requests.get(
            f"{GEOAPIFY_BASE_URL}/geocode/autocomplete",
            params=params,
            timeout=GEOAPIFY_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        out = []
        for r in results:
            if r.get("lat") is None or r.get("lon") is None:
                continue
            out.append({
                "display_name": r.get("formatted", r.get("address_line1", query)),
                "lat": r["lat"],
                "lon": r["lon"],
                "category": (r.get("result_type") or (r.get("categories") or [None])[0]),
                "address_line1": r.get("address_line1"),
                "address_line2": r.get("address_line2"),
                "source": "geoapify",
            })
        return out
    except Exception:
        return []


def reverse_geocode(lat, lng, api_key):
    """Reverse-geocode a tapped map point via Geoapify.

    Returns {display_name, address_line1, address_line2, city, area,
    lat, lon} or None on any failure (no key, network error, no result).
    """
    if not _have_key(api_key) or lat is None or lng is None:
        return None

    try:
        resp = requests.get(
            f"{GEOAPIFY_BASE_URL}/geocode/reverse",
            params={"lat": lat, "lon": lng, "apiKey": api_key, "format": "json"},
            timeout=GEOAPIFY_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if not results:
            return None
        r = results[0]
        return {
            "display_name": r.get("formatted"),
            "address_line1": r.get("address_line1"),
            "address_line2": r.get("address_line2"),
            "city": r.get("city"),
            "area": r.get("suburb") or r.get("district") or r.get("county"),
            "lat": r.get("lat", lat),
            "lon": r.get("lon", lng),
        }
    except Exception:
        return None


def nearby_places(lat, lng, category, api_key, radius_m=1500, limit=20):
    """Real nearby-places data via Geoapify's Places API.

    Returns a list shaped exactly like
    utils/route_safety.py's fetch_nearby_amenities_osm() output
    (name/type/lat/lng/phone/source/open_status/address) so app.py's
    existing _lookup_nearby_services() fallback chain and the frontend's
    existing card rendering both keep working unchanged — Geoapify is
    just a better-coverage option ahead of Overpass/mock in that chain,
    not a replacement for the whole pipeline.

    Returns [] if no API key, an unmapped category, or on any failure.
    """
    geoapify_category = GEOAPIFY_CATEGORY_MAP.get(category)
    if not _have_key(api_key) or lat is None or lng is None or not geoapify_category:
        return []

    try:
        resp = requests.get(
            f"{GEOAPIFY_BASE_URL}/places",
            params={
                "categories": geoapify_category,
                "filter": f"circle:{lng},{lat},{radius_m}",
                "bias": f"proximity:{lng},{lat}",
                "limit": max(1, min(int(limit or 20), 40)),
                "apiKey": api_key,
            },
            timeout=GEOAPIFY_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        features = resp.json().get("features", [])
        out = []
        for f in features:
            props = f.get("properties", {})
            name = props.get("name")
            if not name:
                continue  # unnamed nodes aren't useful in a directory list — same rule fetch_nearby_amenities_osm() uses
            coords = (f.get("geometry") or {}).get("coordinates") or [None, None]
            out.append({
                "name": name,
                "type": category,
                "lat": props.get("lat", coords[1]),
                "lng": props.get("lon", coords[0]),
                "phone": props.get("contact", {}).get("phone", "N/A") if isinstance(props.get("contact"), dict) else "N/A",
                "address": props.get("formatted"),
                "source": "geoapify",
                "open_status": {"status": "hours_vary", "label": "Hours vary — call to confirm"}
                if not props.get("opening_hours") else {"status": "hours_vary", "label": props.get("opening_hours")},
            })
        return out
    except Exception:
        return []