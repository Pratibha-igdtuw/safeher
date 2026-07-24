"""
Web Push (VAPID) delivery — TIER 3 PART 3: Real Push Notifications.

Sends native OS notifications to subscribed browsers/devices even when
every SafeHer tab is closed, via the standard Push API + VAPID auth
(no Apple/Google/proprietary push service account needed — every modern
browser's push service accepts VAPID-signed requests directly).

Configuration (see .env.example):
    VAPID_PUBLIC_KEY   - base64url public key, given to browsers to subscribe
    VAPID_PRIVATE_KEY  - base64url private key, used to sign push requests
    VAPID_CLAIM_EMAIL  - contact address push services may use if you're
                         seen sending abusive volumes of push (required by
                         the VAPID spec, doesn't need to be real to work)

Generate a keypair with:
    python -c "from py_vapid import Vapid; v = Vapid(); v.generate_keys(); \
        print(v.public_key, v.private_key)"
or simply:
    vapid --gen

If pywebpush isn't installed, or no VAPID keys are configured, every
function here degrades to a documented no-op so the rest of the app keeps
working — SOS/risk/check-in alerts just won't get a native push, and
callers get a `{"sent": 0, "reason": ...}` result instead of a traceback.
This mirrors the fallback pattern already used in utils/audio_classifier.py
for TensorFlow.
"""

import base64
import json
import logging
import os

security_logger = logging.getLogger("safeher.push")

try:
    from pywebpush import webpush, WebPushException

    PYWEBPUSH_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised in envs without pywebpush installed
    PYWEBPUSH_AVAILABLE = False

VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_CLAIM_EMAIL = os.environ.get("VAPID_CLAIM_EMAIL", "mailto:admin@example.com")


def push_configured():
    """True if we have everything needed to actually send a push."""
    return PYWEBPUSH_AVAILABLE and bool(VAPID_PUBLIC_KEY) and bool(VAPID_PRIVATE_KEY)


def get_vapid_public_key():
    """Returned to the frontend so it can call pushManager.subscribe()."""
    return VAPID_PUBLIC_KEY


def send_push_to_subscription(subscription, payload):
    """
    Send one push message to one stored subscription row.

    subscription: dict-like with 'endpoint', 'p256dh', 'auth'
    payload: dict — JSON-serialized and delivered to the service worker's
             'push' event (see static/service-worker.js)

    Returns (success: bool, should_delete: bool, error: str | None).
    should_delete is True when the push service told us the subscription
    is permanently gone (HTTP 404/410) and we should stop trying it.
    """
    if not push_configured():
        return False, False, "push_not_configured"

    try:
        webpush(
            subscription_info={
                "endpoint": subscription["endpoint"],
                "keys": {
                    "p256dh": subscription["p256dh"],
                    "auth": subscription["auth"],
                },
            },
            data=json.dumps(payload),
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims={"sub": VAPID_CLAIM_EMAIL},
        )
        return True, False, None
    except WebPushException as exc:  # noqa: BLE001
        status_code = getattr(exc.response, "status_code", None)
        should_delete = status_code in (404, 410)
        security_logger.warning("Web push failed (status=%s): %s", status_code, exc)
        return False, should_delete, str(exc)
    except Exception as exc:  # noqa: BLE001 - never let a push failure crash a request
        security_logger.warning("Web push failed unexpectedly: %s", exc)
        return False, False, str(exc)


def send_push_to_user(conn, user_id, title, body, url="/", tag="safeher-alert", critical=False):
    """
    Sends a push to every device the given user has subscribed on.
    Prunes subscriptions the push service reports as gone.

    Returns a summary dict: {"sent": n, "failed": n, "configured": bool}
    """
    if not push_configured():
        return {"sent": 0, "failed": 0, "configured": False}

    rows = conn.execute(
        "SELECT id, endpoint, p256dh, auth FROM push_subscriptions WHERE user_id = ?",
        (user_id,),
    ).fetchall()

    payload = {"title": title, "body": body, "url": url, "tag": tag, "critical": critical}

    sent = 0
    failed = 0
    for row in rows:
        ok, should_delete, _err = send_push_to_subscription(row, payload)
        if ok:
            sent += 1
        else:
            failed += 1
            if should_delete:
                conn.execute("DELETE FROM push_subscriptions WHERE id = ?", (row["id"],))
    conn.commit()

    return {"sent": sent, "failed": failed, "configured": True}


def send_push_to_users(conn, user_ids, title, body, url="/", tag="safeher-alert", critical=False):
    """Convenience wrapper for send_push_to_user over multiple recipients
    (e.g. the SOS-raiser plus every accepted Bubble member who can track
    them)."""
    totals = {"sent": 0, "failed": 0, "configured": push_configured()}
    for user_id in set(user_ids):
        result = send_push_to_user(conn, user_id, title, body, url=url, tag=tag, critical=critical)
        totals["sent"] += result["sent"]
        totals["failed"] += result["failed"]
    return totals