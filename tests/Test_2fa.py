"""Tests for the TOTP-based Two-Factor Authentication flow
(/api/2fa/setup, /api/2fa/confirm, /api/2fa/verify-login, /api/2fa/disable).
"""
import json

import pyotp

from conftest import signup, login, logout


def _post(client, path, payload=None):
    return client.post(path, data=json.dumps(payload or {}), content_type="application/json")


def test_2fa_setup_generates_a_valid_secret(client):
    signup(client, email="alice@example.com")

    resp = _post(client, "/api/2fa/setup")
    assert resp.status_code == 200

    data = resp.get_json()
    assert "secret" in data and data["secret"]
    assert "qr_code" in data and data["qr_code"].startswith("data:image/png;base64,")

    # The returned secret must be a real usable TOTP secret.
    totp = pyotp.TOTP(data["secret"])
    assert totp.now().isdigit()

    # 2FA isn't enabled yet just from calling setup — confirm() is required.
    status = client.get("/api/2fa/status").get_json()
    assert status["enabled"] is False


def test_wrong_totp_code_is_rejected(client):
    signup(client, email="alice@example.com")
    setup_data = _post(client, "/api/2fa/setup").get_json()

    # Deliberately wrong 6-digit code.
    resp = _post(client, "/api/2fa/confirm", {"code": "000000"})
    assert resp.status_code == 401
    assert "error" in resp.get_json()

    # Still not enabled after a bad code.
    status = client.get("/api/2fa/status").get_json()
    assert status["enabled"] is False


def test_correct_totp_code_enables_2fa(client):
    signup(client, email="alice@example.com")
    setup_data = _post(client, "/api/2fa/setup").get_json()

    totp = pyotp.TOTP(setup_data["secret"])
    resp = _post(client, "/api/2fa/confirm", {"code": totp.now()})

    assert resp.status_code == 200
    assert resp.get_json()["status"] == "2fa_enabled"

    status = client.get("/api/2fa/status").get_json()
    assert status["enabled"] is True


def test_login_requires_2fa_after_enabling(client):
    signup(client, email="alice@example.com", password="password123")
    setup_data = _post(client, "/api/2fa/setup").get_json()
    totp = pyotp.TOTP(setup_data["secret"])
    _post(client, "/api/2fa/confirm", {"code": totp.now()})

    logout(client)

    # Correct password alone should NOT log the user in anymore.
    resp = login(client, email="alice@example.com", password="password123")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "2fa_required"

    # A protected endpoint should still be unauthenticated at this point.
    protected = client.get("/api/contacts")
    assert protected.status_code in (302, 401)

    # Supplying the correct TOTP code completes the login.
    verify_resp = _post(client, "/api/2fa/verify-login", {"code": totp.now()})
    assert verify_resp.status_code == 200
    assert verify_resp.get_json()["status"] == "logged_in"

    # Now the protected endpoint should work.
    protected_after = client.get("/api/contacts")
    assert protected_after.status_code == 200


def test_login_with_wrong_2fa_code_is_rejected(client):
    signup(client, email="alice@example.com", password="password123")
    setup_data = _post(client, "/api/2fa/setup").get_json()
    totp = pyotp.TOTP(setup_data["secret"])
    _post(client, "/api/2fa/confirm", {"code": totp.now()})
    logout(client)

    login(client, email="alice@example.com", password="password123")
    resp = _post(client, "/api/2fa/verify-login", {"code": "123456"})
    assert resp.status_code == 401

    # Still not authenticated.
    protected = client.get("/api/contacts")
    assert protected.status_code in (302, 401)


def test_disable_2fa_requires_correct_password(client):
    signup(client, email="alice@example.com", password="password123")
    setup_data = _post(client, "/api/2fa/setup").get_json()
    totp = pyotp.TOTP(setup_data["secret"])
    _post(client, "/api/2fa/confirm", {"code": totp.now()})

    # Wrong password → refused, 2FA stays enabled.
    wrong = _post(client, "/api/2fa/disable", {"password": "not-the-password"})
    assert wrong.status_code == 401
    assert client.get("/api/2fa/status").get_json()["enabled"] is True

    # Correct password → disabled.
    right = _post(client, "/api/2fa/disable", {"password": "password123"})
    assert right.status_code == 200
    assert right.get_json()["status"] == "2fa_disabled"
    assert client.get("/api/2fa/status").get_json()["enabled"] is False


# ---------------------------------------------------------------------------
# 2FA recovery codes (account recovery: enrollment, regeneration, and
# using a code to log in + disable 2FA when the authenticator is lost)
# ---------------------------------------------------------------------------
def _enroll_2fa(client, email="alice@example.com", password="password123"):
    """Signs up, enables 2FA, and returns (totp, recovery_codes) — the
    plaintext codes returned by /api/2fa/confirm at enrollment time."""
    signup(client, email=email, password=password)
    setup_data = _post(client, "/api/2fa/setup").get_json()
    totp = pyotp.TOTP(setup_data["secret"])
    confirm_resp = _post(client, "/api/2fa/confirm", {"code": totp.now()})
    return totp, confirm_resp.get_json()["recovery_codes"]


def test_enabling_2fa_returns_a_batch_of_recovery_codes(client):
    _, codes = _enroll_2fa(client)

    assert isinstance(codes, list)
    assert len(codes) == 10  # RECOVERY_CODES_COUNT default
    assert len(set(codes)) == 10  # all distinct

    status = client.get("/api/2fa/recovery-codes/status").get_json()
    assert status["remaining"] == 10


def test_regenerate_recovery_codes_requires_correct_password(client):
    _enroll_2fa(client)

    wrong = _post(client, "/api/2fa/recovery-codes/regenerate", {"password": "not-the-password"})
    assert wrong.status_code == 401

    right = _post(client, "/api/2fa/recovery-codes/regenerate", {"password": "password123"})
    assert right.status_code == 200
    new_codes = right.get_json()["recovery_codes"]
    assert len(new_codes) == 10


def test_regenerating_recovery_codes_invalidates_the_old_batch(client):
    _, old_codes = _enroll_2fa(client)

    regen = _post(client, "/api/2fa/recovery-codes/regenerate", {"password": "password123"})
    new_codes = regen.get_json()["recovery_codes"]
    assert set(new_codes).isdisjoint(set(old_codes))

    logout(client)
    login(client, email="alice@example.com", password="password123")

    # An old (pre-regeneration) code no longer works for recovery login...
    old_attempt = _post(client, "/api/2fa/recover", {"recovery_code": old_codes[0]})
    assert old_attempt.status_code == 401

    # ...but a new one does.
    new_attempt = _post(client, "/api/2fa/recover", {"recovery_code": new_codes[0]})
    assert new_attempt.status_code == 200
    assert new_attempt.get_json()["status"] == "logged_in"


def test_regenerate_recovery_codes_requires_2fa_enabled(client):
    signup(client, email="alice@example.com", password="password123")
    resp = _post(client, "/api/2fa/recovery-codes/regenerate", {"password": "password123"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Using a recovery code to log in (and disable 2FA) when the authenticator
# is lost
# ---------------------------------------------------------------------------
def test_recovery_code_logs_in_and_disables_2fa(client):
    _, codes = _enroll_2fa(client)
    logout(client)

    login_resp = login(client, email="alice@example.com", password="password123")
    assert login_resp.get_json()["status"] == "2fa_required"

    recover_resp = _post(client, "/api/2fa/recover", {"recovery_code": codes[0]})
    assert recover_resp.status_code == 200
    data = recover_resp.get_json()
    assert data["status"] == "logged_in"
    assert data["2fa_disabled"] is True

    # Actually logged in now.
    protected = client.get("/api/contacts")
    assert protected.status_code == 200

    # 2FA is off, and a fresh /login (no code needed) works from here on.
    logout(client)
    plain_login = login(client, email="alice@example.com", password="password123")
    assert plain_login.status_code == 200
    assert plain_login.get_json()["status"] == "logged_in"

    # The recovery-code batch was cleared along with 2FA — nothing left to
    # "remaining" for regeneration to build on top of.
    status = client.get("/api/2fa/recovery-codes/status").get_json()
    assert status["remaining"] == 0


def test_recovery_code_is_single_use(client):
    _, codes = _enroll_2fa(client)
    logout(client)

    login(client, email="alice@example.com", password="password123")
    first = _post(client, "/api/2fa/recover", {"recovery_code": codes[0]})
    assert first.status_code == 200

    # Re-enroll 2FA so there's a "pending 2FA" session to attack again...
    totp = pyotp.TOTP(_post(client, "/api/2fa/setup").get_json()["secret"])
    _post(client, "/api/2fa/confirm", {"code": totp.now()})
    logout(client)
    login(client, email="alice@example.com", password="password123")

    # ...and replay the SAME (already-used) code from the original batch.
    # Even though 2FA was re-enrolled since, that code must never work again.
    replay = _post(client, "/api/2fa/recover", {"recovery_code": codes[0]})
    assert replay.status_code == 401


def test_wrong_recovery_code_is_rejected(client):
    _enroll_2fa(client)
    logout(client)
    login(client, email="alice@example.com", password="password123")

    resp = _post(client, "/api/2fa/recover", {"recovery_code": "AAAA-AAAA"})
    assert resp.status_code == 401

    # Still not authenticated — the bad attempt didn't log anyone in.
    protected = client.get("/api/contacts")
    assert protected.status_code in (302, 401)


def test_recovery_code_requires_pending_2fa_login(client):
    """Can't call /api/2fa/recover out of the blue without having gone
    through the password step of login first."""
    signup(client, email="alice@example.com", password="password123")
    resp = _post(client, "/api/2fa/recover", {"recovery_code": "AAAA-AAAA"})
    assert resp.status_code == 400


def test_recovery_code_invalidates_other_standing_sessions(client):
    """Recovering via a code is treated like a security-sensitive account
    change (same as a password reset): it should log out any other
    session that was already active on this account — simulated here with
    a second, independent test client (own cookie jar) standing in for a
    second device/browser."""
    import app as app_module

    _, codes = _enroll_2fa(client)

    # A second, separately-authenticated session on the SAME account
    # (e.g. a browser on another device), established before recovery.
    # Deliberately NOT opened via `with ... as other_device:` — Werkzeug's
    # test client only keeps a request context alive across calls in that
    # mode, and two clients simultaneously doing so against the same app
    # corrupts the shared context stack. A plain client (context popped
    # after each call) avoids that and is all this test needs.
    other_device = app_module.app.test_client()
    login(other_device, email="alice@example.com", password="password123")
    conn = app_module.get_db()
    totp_secret = conn.execute(
        "SELECT totp_secret FROM users WHERE email = ?", ("alice@example.com",)
    ).fetchone()["totp_secret"]
    conn.close()
    totp = pyotp.TOTP(totp_secret)
    _post(other_device, "/api/2fa/verify-login", {"code": totp.now()})
    assert other_device.get("/api/contacts").status_code == 200

    # Meanwhile, the primary `client` uses a recovery code (lost
    # authenticator) to log in instead.
    logout(client)
    login(client, email="alice@example.com", password="password123")
    recover_resp = _post(client, "/api/2fa/recover", {"recovery_code": codes[0]})
    assert recover_resp.status_code == 200

    # The other device's still-open session must now be logged out.
    after = other_device.get("/api/contacts")
    assert after.status_code in (302, 401)

    # The recovering client's own new session remains valid.
    assert client.get("/api/contacts").status_code == 200