"""
SOS alert delivery.

For the hackathon demo this runs in MOCK MODE by default so judges can see
it work with zero setup (no API keys needed). To send REAL SMS messages,
fill in your Twilio credentials below and set MOCK_MODE = False.

Get free Twilio trial credentials at https://www.twilio.com/try-twilio

Email delivery uses plain SMTP (works with Gmail, Outlook, or any SMTP
provider). Fill in the SMTP_* settings below and set EMAIL_MOCK_MODE = False
to actually send emails. Until then, emails are just printed to the console
so you can see the feature working without any setup.
"""

import os
import smtplib
from email.mime.text import MIMEText

MOCK_MODE = True

# --- Fill these in for real SMS delivery (Twilio) -------------------------
TWILIO_ACCOUNT_SID = ""
TWILIO_AUTH_TOKEN = ""
TWILIO_FROM_NUMBER = ""
# ---------------------------------------------------------------------------

# --- Email (SMTP) settings for SOS alerts ----------------------------------
# You can either fill these in directly, or (recommended) set them as
# environment variables so you don't commit real credentials:
#   SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, SMTP_FROM_EMAIL
#
# For Gmail: use smtp.gmail.com, port 587, and a 16-character "App Password"
# (not your normal Gmail password) generated from your Google account
# security settings — Gmail blocks plain password logins for this.
EMAIL_MOCK_MODE = os.environ.get("SMTP_HOST", "") == ""

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM_EMAIL = os.environ.get("SMTP_FROM_EMAIL", SMTP_USERNAME)
# ---------------------------------------------------------------------------


def send_sos_alert(contacts, message):
    """
    Sends the SOS message to every trusted contact, by SMS (if a phone is
    on file) and by email (if an email is on file).

    contacts: list of sqlite3.Row objects with 'name', 'phone', and
              optionally 'email' fields
    message: the alert text to send

    Returns a list of per-contact delivery results so the frontend can show
    "sent to mom, sent to roommate" etc.
    """
    results = []

    results.extend(_send_sms(contacts, message))
    results.extend(_send_emails(contacts, message))

    return results


def _send_sms(contacts, message):
    results = []

    if MOCK_MODE or not TWILIO_ACCOUNT_SID:
        for contact in contacts:
            print(f"[MOCK SMS] To: {contact['name']} ({contact['phone']}) -> {message}")
            results.append(
                {"contact": contact["name"], "phone": contact["phone"], "status": "sent (mock)"}
            )
        return results

    # --- Real Twilio send path --------------------------------------------
    try:
        from twilio.rest import Client

        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        for contact in contacts:
            try:
                client.messages.create(
                    body=message, from_=TWILIO_FROM_NUMBER, to=contact["phone"]
                )
                results.append(
                    {"contact": contact["name"], "phone": contact["phone"], "status": "sent"}
                )
            except Exception as e:  # noqa: BLE001
                results.append(
                    {
                        "contact": contact["name"],
                        "phone": contact["phone"],
                        "status": f"failed: {e}",
                    }
                )
    except ImportError:
        results.append({"status": "twilio package not installed, run: pip install twilio"})

    return results


def _send_emails(contacts, message):
    """Sends the SOS message by email to every contact that has an email
    on file. Skips contacts with no email set."""
    results = []

    # sqlite3.Row doesn't support .get(), so guard with try/except instead
    emailable = []
    for c in contacts:
        try:
            addr = c["email"]
        except (IndexError, KeyError):
            addr = None
        if addr:
            emailable.append(c)

    if not emailable:
        return results

    if EMAIL_MOCK_MODE:
        for contact in emailable:
            print(f"[MOCK EMAIL] To: {contact['name']} <{contact['email']}> -> {message}")
            results.append(
                {"contact": contact["name"], "email": contact["email"], "status": "sent (mock)"}
            )
        return results

    # --- Real SMTP send path ------------------------------------------------
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            for contact in emailable:
                try:
                    mail = MIMEText(message)
                    mail["Subject"] = "🚨 SafeHer SOS Alert"
                    mail["From"] = SMTP_FROM_EMAIL
                    mail["To"] = contact["email"]
                    server.sendmail(SMTP_FROM_EMAIL, [contact["email"]], mail.as_string())
                    results.append(
                        {"contact": contact["name"], "email": contact["email"], "status": "sent"}
                    )
                except Exception as e:  # noqa: BLE001
                    results.append(
                        {
                            "contact": contact["name"],
                            "email": contact["email"],
                            "status": f"failed: {e}",
                        }
                    )
    except Exception as e:  # noqa: BLE001
        results.append({"status": f"SMTP connection failed: {e}"})

    return results