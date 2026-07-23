"""
Nearby safety services directory (police, hospital, pharmacy, etc).

Demo version returns a small mock dataset scored by distance from the
user's location using the haversine formula, so it works with zero
external API keys.

To go live: replace MOCK_SERVICES with a real Google Places "nearby search"
call (type=police / hospital / pharmacy) centered on the user's coordinates.
"""

import math

# Mock directory — demo data around a sample city area.
# Each entry: name, type, lat, lng, phone
MOCK_SERVICES = [
    {"name": "Central Police Station", "type": "police", "lat": 28.6139, "lng": 77.2090, "phone": "100"},
    {"name": "North Police Outpost", "type": "police", "lat": 28.6200, "lng": 77.2150, "phone": "100"},
    {"name": "City General Hospital", "type": "hospital", "lat": 28.6155, "lng": 77.2100, "phone": "102"},
    {"name": "Wellness Multispecialty Hospital", "type": "hospital", "lat": 28.6100, "lng": 77.2050, "phone": "102"},
    {"name": "Apollo Pharmacy", "type": "pharmacy", "lat": 28.6145, "lng": 77.2080, "phone": "N/A"},
    {"name": "MedPlus Pharmacy", "type": "pharmacy", "lat": 28.6180, "lng": 77.2120, "phone": "N/A"},
    {"name": "Women Helpline Center", "type": "helpline", "lat": 28.6160, "lng": 77.2070, "phone": "1091"},
]


def _haversine_km(lat1, lng1, lat2, lng2):
    R = 6371  # earth radius km
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def get_nearby_services(lat, lng, service_type=None, limit=10):
    """
    Returns nearby services sorted by distance.
    If lat/lng are None, falls back to the demo city center so the feature
    still works without location permission granted.
    """
    if lat is None or lng is None:
        lat, lng = 28.6139, 77.2090  # demo city center fallback

    services = MOCK_SERVICES
    if service_type:
        services = [s for s in services if s["type"] == service_type]

    scored = []
    for s in services:
        dist = _haversine_km(lat, lng, s["lat"], s["lng"])
        scored.append({**s, "distance_km": round(dist, 2)})

    scored.sort(key=lambda x: x["distance_km"])
    return scored[:limit]
