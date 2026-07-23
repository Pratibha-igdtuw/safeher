"""Tests for Tier 2's offline-first sync queue: POST /api/offline-actions.
"""
import json

from conftest import signup


def _post(client, path, payload=None):
    return client.post(path, data=json.dumps(payload or {}), content_type="application/json")


def test_queued_sos_action_creates_a_real_alert_row(client):
    import app as app_module

    signup(client, email="alice@example.com")

    resp = _post(client, "/api/offline-actions", {
        "actions": [
            {
                "type": "sos",
                "payload": {
                    "latitude": 28.6139,
                    "longitude": 77.2090,
                    "message": "Offline SOS raised while out of signal",
                },
                "queued_at": "2026-07-23T08:00:00",
            }
        ]
    })

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "synced"
    assert len(body["results"]) == 1
    assert body["results"][0]["applied"] is True
    assert body["results"][0]["type"] == "sos"

    conn = app_module.get_db()
    user_id = conn.execute("SELECT id FROM users WHERE email = ?", ("alice@example.com",)).fetchone()["id"]

    alert = conn.execute(
        "SELECT * FROM alerts WHERE user_id = ? AND trigger_type = 'offline_sync'", (user_id,)
    ).fetchone()
    assert alert is not None
    assert alert["latitude"] == 28.6139
    assert alert["longitude"] == 77.2090
    assert alert["alert_type"] == "sos"
    assert "Offline SOS raised while out of signal" in alert["message"]
    conn.close()


def test_queued_sos_action_marks_queue_entry_synced(client):
    import app as app_module

    signup(client, email="alice@example.com")

    resp = _post(client, "/api/offline-actions", {
        "actions": [
            {"type": "sos", "payload": {"latitude": 1.1, "longitude": 2.2}, "queued_at": "2026-07-23T08:00:00"}
        ]
    })
    queue_id = resp.get_json()["results"][0]["queue_id"]

    conn = app_module.get_db()
    row = conn.execute("SELECT * FROM offline_queue WHERE id = ?", (queue_id,)).fetchone()
    conn.close()

    assert row is not None
    assert row["synced"] == 1
    assert row["synced_at"] is not None
    assert row["action_type"] == "sos"


def test_multiple_queued_actions_are_all_recorded(client):
    import app as app_module

    signup(client, email="alice@example.com")

    resp = _post(client, "/api/offline-actions", {
        "actions": [
            {"type": "sos", "payload": {"latitude": 1.0, "longitude": 1.0}, "queued_at": "2026-07-23T08:00:00"},
            {"type": "risk_report", "payload": {"note": "unlit alley"}, "queued_at": "2026-07-23T08:05:00"},
        ]
    })

    assert resp.status_code == 200
    results = resp.get_json()["results"]
    assert len(results) == 2
    assert results[0]["applied"] is True   # sos has a real handler
    assert results[1]["applied"] is False  # risk_report has no handler yet, just queued

    conn = app_module.get_db()
    user_id = conn.execute("SELECT id FROM users WHERE email = ?", ("alice@example.com",)).fetchone()["id"]
    queued_count = conn.execute(
        "SELECT COUNT(*) AS c FROM offline_queue WHERE user_id = ?", (user_id,)
    ).fetchone()["c"]
    conn.close()
    assert queued_count == 2


def test_offline_actions_endpoint_requires_login(client):
    resp = _post(client, "/api/offline-actions", {"actions": []})
    assert resp.status_code in (302, 401)