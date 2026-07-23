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