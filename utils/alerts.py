"""
SOS alert delivery.

For the hackathon demo this runs in MOCK MODE by default so judges can see
it work with zero setup (no API keys needed). To send REAL SMS messages,
fill in your Twilio credentials below and set MOCK_MODE = False.

Get free Twilio trial credentials at https://www.twilio.com/try-twilio
"""

MOCK_MODE = True

# --- Fill these in for real SMS delivery (Twilio) -------------------------
TWILIO_ACCOUNT_SID = ""
TWILIO_AUTH_TOKEN = ""
TWILIO_FROM_NUMBER = ""
# ---------------------------------------------------------------------------


def send_sos_alert(contacts, message):
    """
    Sends the SOS message to every trusted contact.

    contacts: list of sqlite3.Row objects with a 'phone' and 'name' field
    message: the alert text to send

    Returns a list of per-contact delivery results so the frontend can show
    "sent to mom, sent to roommate" etc.
    """
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
