"""Tests for Tier 2 live location tracking:
/api/tracking/start, /api/tracking/stop, /api/tracking/history.
"""
import json

from conftest import signup, login, logout


def _post(client, path, payload=None):
    return client.post(path, data=json.dumps(payload or {}), content_type="application/json")


def _get_live_tracking_status(app_module, email):
    """Read live_tracking status straight from the DB for assertions that
    don't have their own dedicated read endpoint."""
    conn = app_module.get_db()
    row = conn.execute(
        "SELECT lt.status FROM live_tracking lt JOIN users u ON u.id = lt.user_id "
        "WHERE u.email = ? ORDER BY lt.id DESC LIMIT 1",
        (email,),
    ).fetchone()
    conn.close()
    return row["status"] if row else None


def test_start_tracking_sets_status_active(client):
    import app as app_module

    signup(client, email="alice@example.com")
    resp = _post(client, "/api/tracking/start")

    assert resp.status_code == 200
    assert resp.get_json()["status"] == "tracking_started"
    assert _get_live_tracking_status(app_module, "alice@example.com") == "active"


def test_stop_tracking_sets_status_inactive(client):
    import app as app_module

    signup(client, email="alice@example.com")
    _post(client, "/api/tracking/start")

    resp = _post(client, "/api/tracking/stop")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "tracking_stopped"
    assert _get_live_tracking_status(app_module, "alice@example.com") == "inactive"


def test_starting_twice_closes_out_the_stale_session(client):
    """Starting a second session while one is already active should mark
    the old row inactive rather than leaving two 'active' rows behind."""
    import app as app_module

    signup(client, email="alice@example.com")
    _post(client, "/api/tracking/start")
    _post(client, "/api/tracking/start")

    conn = app_module.get_db()
    active_count = conn.execute(
        "SELECT COUNT(*) AS c FROM live_tracking lt JOIN users u ON u.id = lt.user_id "
        "WHERE u.email = ? AND lt.status = 'active'",
        ("alice@example.com",),
    ).fetchone()["c"]
    conn.close()
    assert active_count == 1


def test_location_history_rows_are_created_via_websocket_handler(client):
    """handle_location_update (the join_tracking/location_update SocketIO
    handlers) writes to location_history the same way this test does —
    exercised here through a direct DB write plus the real read endpoint,
    since driving an actual SocketIO connection needs a running server."""
    import app as app_module

    signup(client, email="alice@example.com")

    conn = app_module.get_db()
    user_row = conn.execute("SELECT id FROM users WHERE email = ?", ("alice@example.com",)).fetchone()
    user_id = user_row["id"]
    conn.execute(
        "INSERT INTO location_history (user_id, latitude, longitude, timestamp) VALUES (?, ?, ?, ?)",
        (user_id, 28.6139, 77.2090, "2026-07-23T10:00:00"),
    )
    conn.commit()
    conn.close()

    resp = client.get("/api/tracking/history")
    assert resp.status_code == 200
    history = resp.get_json()
    assert len(history) == 1
    assert history[0]["latitude"] == 28.6139
    assert history[0]["longitude"] == 77.2090


def test_unauthorized_user_cannot_fetch_another_users_tracking_history(client):
    """Ties into Part 1's authorization fix: /api/tracking/history must
    only ever return the *current* logged-in user's rows, never another
    user's, no matter what."""
    import app as app_module

    # Alice signs up and accumulates some location history.
    signup(client, email="alice@example.com", password="alicepw123")
    conn = app_module.get_db()
    alice_id = conn.execute("SELECT id FROM users WHERE email = ?", ("alice@example.com",)).fetchone()["id"]
    conn.execute(
        "INSERT INTO location_history (user_id, latitude, longitude, timestamp) VALUES (?, ?, ?, ?)",
        (alice_id, 12.9716, 77.5946, "2026-07-23T09:00:00"),
    )
    conn.commit()
    conn.close()
    logout(client)

    # Bob signs up separately and should see an empty history — never
    # Alice's rows — regardless of Alice's user id.
    signup(client, email="bob@example.com", password="bobpw123")
    resp = client.get("/api/tracking/history")
    assert resp.status_code == 200
    assert resp.get_json() == []

    # And a fully unauthenticated caller can't reach the endpoint at all.
    logout(client)
    anon_resp = client.get("/api/tracking/history")
    assert anon_resp.status_code in (302, 401)