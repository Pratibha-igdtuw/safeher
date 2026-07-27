"""Tests for the account-recovery "forgot password" flow:
/forgot-password (request) -> emailed time-limited token -> /api/reset-password
(reset form) -> old sessions invalidated.
"""
import json
from datetime import datetime, timedelta

import app as app_module
from conftest import signup, login, logout


def _post(client, path, payload=None):
    return client.post(path, data=json.dumps(payload or {}), content_type="application/json")


def _latest_raw_reset_token(capsys, email):
    """Mock mode (utils/alerts.EMAIL_MOCK_MODE) prints the reset email to
    stdout instead of actually sending it — pull the raw token out of the
    reset link captured there, the same way a user would copy it out of
    their inbox."""
    captured = capsys.readouterr().out
    assert email in captured
    for line in captured.splitlines():
        if "/reset-password/" in line:
            return line.strip().rsplit("/reset-password/", 1)[1]
    raise AssertionError(f"No reset link found in captured output:\n{captured}")


def _get_token_row(email):
    conn = app_module.get_db()
    row = conn.execute(
        "SELECT prt.* FROM password_reset_tokens prt JOIN users u ON u.id = prt.user_id WHERE u.email = ? "
        "ORDER BY prt.id DESC LIMIT 1",
        (email,),
    ).fetchone()
    conn.close()
    return row


# ---------------------------------------------------------------------------
# Request step: /forgot-password
# ---------------------------------------------------------------------------
def test_forgot_password_for_existing_account_sends_email_and_creates_token(client, capsys):
    signup(client, email="alice@example.com")
    logout(client)

    resp = _post(client, "/forgot-password", {"email": "alice@example.com"})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "if_account_exists_email_sent"

    _latest_raw_reset_token(capsys, "alice@example.com")  # asserts an email went out
    token_row = _get_token_row("alice@example.com")
    assert token_row is not None
    assert token_row["used_at"] is None


def test_forgot_password_for_unknown_email_gives_identical_response(client, capsys):
    """No account exists for this address, but the response must be
    indistinguishable from the "account exists" case — otherwise this
    endpoint becomes an email-enumeration oracle."""
    resp = _post(client, "/forgot-password", {"email": "nobody@example.com"})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "if_account_exists_email_sent"

    captured = capsys.readouterr().out
    assert "nobody@example.com" not in captured  # no email actually sent


def test_forgot_password_rejects_malformed_email(client):
    resp = _post(client, "/forgot-password", {"email": "not-an-email"})
    assert resp.status_code == 400


def test_forgot_password_is_rate_limited(client):
    signup(client, email="alice@example.com")
    logout(client)

    for _ in range(5):
        ok = _post(client, "/forgot-password", {"email": "alice@example.com"})
        assert ok.status_code == 200

    blocked = _post(client, "/forgot-password", {"email": "alice@example.com"})
    assert blocked.status_code == 429


# ---------------------------------------------------------------------------
# Reset step: /api/reset-password
# ---------------------------------------------------------------------------
def test_reset_password_with_valid_token_changes_password(client, capsys):
    signup(client, email="alice@example.com", password="oldpassword123")
    logout(client)

    _post(client, "/forgot-password", {"email": "alice@example.com"})
    token = _latest_raw_reset_token(capsys, "alice@example.com")

    resp = _post(client, "/api/reset-password", {"token": token, "password": "newpassword456"})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "password_reset"

    # Old password no longer works, new one does.
    old_login = login(client, email="alice@example.com", password="oldpassword123")
    assert old_login.status_code == 401

    new_login = login(client, email="alice@example.com", password="newpassword456")
    assert new_login.status_code == 200
    assert new_login.get_json()["status"] == "logged_in"


def test_reset_password_page_renders_without_consuming_token(client, capsys):
    """Visiting the reset-password link (GET) must not itself burn the
    token — some email clients prefetch links, which would otherwise lock
    a legitimate user out of their own reset email."""
    signup(client, email="alice@example.com")
    logout(client)
    _post(client, "/forgot-password", {"email": "alice@example.com"})
    token = _latest_raw_reset_token(capsys, "alice@example.com")

    page = client.get(f"/reset-password/{token}")
    assert page.status_code == 200

    resp = _post(client, "/api/reset-password", {"token": token, "password": "brandnewpass1"})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "password_reset"


def test_reset_password_with_bogus_token_is_rejected(client):
    resp = _post(client, "/api/reset-password", {"token": "totally-made-up-token", "password": "somepassword1"})
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_reset_password_rejects_short_new_password(client, capsys):
    signup(client, email="alice@example.com")
    logout(client)
    _post(client, "/forgot-password", {"email": "alice@example.com"})
    token = _latest_raw_reset_token(capsys, "alice@example.com")

    resp = _post(client, "/api/reset-password", {"token": token, "password": "short"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Single-use enforcement
# ---------------------------------------------------------------------------
def test_reset_token_cannot_be_used_twice(client, capsys):
    signup(client, email="alice@example.com", password="firstpassword1")
    logout(client)
    _post(client, "/forgot-password", {"email": "alice@example.com"})
    token = _latest_raw_reset_token(capsys, "alice@example.com")

    first = _post(client, "/api/reset-password", {"token": token, "password": "secondpassword2"})
    assert first.status_code == 200

    # Replaying the exact same (now-used) token must fail, even though it
    # hasn't expired yet.
    second = _post(client, "/api/reset-password", {"token": token, "password": "thirdpassword33"})
    assert second.status_code == 400
    assert "error" in second.get_json()

    # And the password from the first (successful) reset is still the
    # active one — the replayed attempt had no effect.
    still_second_password = login(client, email="alice@example.com", password="secondpassword2")
    assert still_second_password.status_code == 200


def test_requesting_a_new_token_invalidates_older_unused_tokens(client, capsys):
    """Two reset emails requested back-to-back: using the newer one should
    also burn the older, still-unused one, so an old email sitting in an
    inbox (or a spied-on one) can't be used after a newer reset already
    happened."""
    signup(client, email="alice@example.com")
    logout(client)

    _post(client, "/forgot-password", {"email": "alice@example.com"})
    old_token = _latest_raw_reset_token(capsys, "alice@example.com")

    _post(client, "/forgot-password", {"email": "alice@example.com"})
    new_token = _latest_raw_reset_token(capsys, "alice@example.com")
    assert new_token != old_token

    # Use the newer token successfully.
    resp = _post(client, "/api/reset-password", {"token": new_token, "password": "brandnewpass2"})
    assert resp.status_code == 200

    # The older token, never used, is now dead too.
    stale = _post(client, "/api/reset-password", {"token": old_token, "password": "wontworkpass3"})
    assert stale.status_code == 400


# ---------------------------------------------------------------------------
# Expiry enforcement
# ---------------------------------------------------------------------------
def test_expired_reset_token_is_rejected(client, capsys):
    signup(client, email="alice@example.com")
    logout(client)
    _post(client, "/forgot-password", {"email": "alice@example.com"})
    token = _latest_raw_reset_token(capsys, "alice@example.com")

    # Backdate the token's expiry into the past, simulating time having
    # passed beyond PASSWORD_RESET_TOKEN_EXPIRY_MINUTES.
    conn = app_module.get_db()
    conn.execute(
        "UPDATE password_reset_tokens SET expires_at = ? WHERE token_hash = ?",
        (
            (datetime.utcnow() - timedelta(minutes=1)).isoformat(),
            app_module._hash_token(token),
        ),
    )
    conn.commit()
    conn.close()

    resp = _post(client, "/api/reset-password", {"token": token, "password": "somenewpass1"})
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_unexpired_reset_token_still_works(client, capsys):
    """Sanity check for the expiry test above: a token that's still within
    its validity window works normally."""
    signup(client, email="alice@example.com")
    logout(client)
    _post(client, "/forgot-password", {"email": "alice@example.com"})
    token = _latest_raw_reset_token(capsys, "alice@example.com")

    conn = app_module.get_db()
    conn.execute(
        "UPDATE password_reset_tokens SET expires_at = ? WHERE token_hash = ?",
        (
            (datetime.utcnow() + timedelta(minutes=1)).isoformat(),
            app_module._hash_token(token),
        ),
    )
    conn.commit()
    conn.close()

    resp = _post(client, "/api/reset-password", {"token": token, "password": "somenewpass1"})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Session invalidation
# ---------------------------------------------------------------------------
def test_password_reset_invalidates_other_standing_sessions(client, capsys):
    """The core "invalidate old sessions" requirement: a session that was
    already logged in before the reset must stop working immediately
    afterward, even though its cookie is still technically valid/signed."""
    signup(client, email="alice@example.com", password="originalpass1")

    # `client` is now logged in (signup() logs the user in automatically).
    still_logged_in = client.get("/api/contacts")
    assert still_logged_in.status_code == 200

    # Reset the password *without* logging this client out first — this
    # models an attacker (or the user, from another device) resetting the
    # password while the original session cookie is still sitting in the
    # first browser.
    _post(client, "/forgot-password", {"email": "alice@example.com"})
    token = _latest_raw_reset_token(capsys, "alice@example.com")
    reset_resp = _post(client, "/api/reset-password", {"token": token, "password": "newpassword22"})
    assert reset_resp.status_code == 200

    # The OLD session cookie (still attached to this same test client) must
    # no longer be treated as authenticated on the very next request.
    after_reset = client.get("/api/contacts")
    assert after_reset.status_code in (302, 401)


def test_password_reset_does_not_affect_a_fresh_login_afterward(client, capsys):
    """The new password_hash + a fresh /login afterward works normally and
    isn't itself immediately invalidated by the session-version bump."""
    signup(client, email="alice@example.com", password="originalpass1")
    logout(client)

    _post(client, "/forgot-password", {"email": "alice@example.com"})
    token = _latest_raw_reset_token(capsys, "alice@example.com")
    _post(client, "/api/reset-password", {"token": token, "password": "newpassword22"})

    login_resp = login(client, email="alice@example.com", password="newpassword22")
    assert login_resp.status_code == 200
    assert login_resp.get_json()["status"] == "logged_in"

    still_ok = client.get("/api/contacts")
    assert still_ok.status_code == 200
