"""
SafeHer Assistant.

HONESTY NOTE (please read before "upgrading" this): this is a rule-based
intent classifier + curated-content responder, NOT a large language model.
No API key, no network call, no generative model — regex/keyword intent
matching against a fixed set of handlers, several of which call back into
this app's *real* data (live nearby-services lookups, the real route-safety
scorer) so its answers are grounded in actual app state rather than
plausible-sounding invention. That's a deliberate, defensible design for a
safety product: a generative model can hallucinate a wrong emergency
number or wrong legal claim with total confidence, which is a much worse
failure mode here than "the assistant didn't understand a rephrased
question." If this is ever swapped for a real LLM (e.g. the Anthropic
API), keep the emergency-guidance and legal-advice content grounded/
citation-checked rather than fully generative, and keep the disclaimer.

Swappable by design: generate_reply() is the only entry point app.py
calls, so the implementation can change without touching routes/frontend.
"""

import re

QUICK_SUGGESTIONS = [
    "What should I do if I feel unsafe?",
    "Find police near me",
    "Plan a safe route home",
    "Is walking alone at night illegal to report?",
    "How do I use the SOS button?",
    "What counts as harassment?",
]

SAFETY_TIPS = [
    "Share your live location with a trusted contact before heading out, especially at night — SafeHer's Guardian tab does this in one tap.",
    "Trust your gut. If a place or person feels wrong, it's okay to leave immediately without explaining yourself.",
    "Keep your phone charged above 20% when you're out — Journey Mode and SOS both need it to actually reach anyone.",
    "Walk facing traffic on roads without sidewalks, and stick to well-lit, populated routes even if they're longer.",
    "Tell a friend your plan: where you're going, who with, and when you expect to be back — or better, start a Journey in SafeHer so it's automatic.",
]

EMERGENCY_STEPS = (
    "If you're in immediate danger right now:\n"
    "1. Call your local emergency number (police 100 / ambulance 102 / women's helpline 1091 in India — check your country's numbers if elsewhere).\n"
    "2. If you can't safely call, use SafeHer's SOS button — it alerts your trusted contacts with your live location.\n"
    "3. Move toward people, light, and open businesses if you can do so safely.\n"
    "4. If you can't speak, texting/messaging a contact your situation is safer than staying silent.\n\n"
    "This is general guidance, not a substitute for local emergency services — please contact them directly for anything urgent."
)

LEGAL_ADVICE_DISCLAIMER = (
    "I'm not a lawyer and this isn't legal advice for your specific situation — please consult a local lawyer, "
    "legal aid organization, or your local women's helpline for anything you plan to act on. "
)

LEGAL_TOPICS = {
    "harassment": (
        LEGAL_ADVICE_DISCLAIMER
        + "In most jurisdictions, unwelcome physical contact, stalking, persistent unwanted "
        "communication, and public indecent behavior toward you can be reported to police, and many places "
        "have specific laws covering workplace or street harassment. Documenting what happened (time, place, "
        "description, witnesses, and — if it's safe — a SafeHer Anonymous Recording) makes any report much stronger."
    ),
    "stalking": (
        LEGAL_ADVICE_DISCLAIMER
        + "Repeated unwanted following, contacting, or monitoring is treated as stalking in most legal systems, "
        "often with specific statutes separate from general harassment. Keep a timestamped log of incidents — "
        "SafeHer's Emergency History and Community Feed posts can help build that record — and consider a "
        "restraining/protection order where available."
    ),
    "default": (
        LEGAL_ADVICE_DISCLAIMER
        + "For specific legal questions — filing a police report, restraining orders, workplace complaints, or "
        "your rights in a given situation — a local lawyer or legal aid clinic will give you accurate, "
        "jurisdiction-specific guidance that I can't responsibly provide here."
    ),
}


def _matches_any(text, patterns):
    return any(re.search(p, text) for p in patterns)


def classify_intent(message):
    text = message.lower().strip()

    if _matches_any(text, [r"\b(hi|hello|hey)\b", r"^good (morning|evening|afternoon)"]):
        return "greeting"
    if _matches_any(text, [r"\bunsafe\b", r"\bscared\b", r"\bfollow(ing|ed)?\b", r"\bin danger\b", r"\bhelp me\b", r"emergency"]):
        return "emergency_guidance"
    if _matches_any(text, [r"\bsos\b", r"how.*(sos|alert)"]):
        return "sos_howto"
    if _matches_any(text, [r"\b(police|hospital|pharmacy|helpline)\b.*near", r"near me", r"nearby (help|police|hospital)"]):
        return "nearby_help"
    if _matches_any(text, [r"\broute\b", r"\bjourney\b", r"safe way (to|home)", r"plan.*(trip|route|walk)"]):
        return "journey_planning"
    if _matches_any(text, [r"\blegal\b", r"\blaw\b", r"\brights?\b", r"\bharassment\b", r"\bstalking\b", r"report (him|her|them|someone)", r"file a (case|complaint|report)"]):
        return "legal_advice"
    if _matches_any(text, [r"\btip", r"\badvice\b", r"stay safe", r"safety"]):
        return "safety_tips"
    return "general"


def generate_reply(message, context=None):
    """context (optional dict) can carry: nearby_services (list, pre-fetched
    by app.py from the real /api/nearby-services logic), route_result
    (dict, pre-fetched from the real route-safety scorer). This module
    stays free of DB/network access itself — app.py fetches real data and
    hands it in, keeping this module a pure, easily-testable function."""
    context = context or {}
    intent = classify_intent(message)

    if intent == "greeting":
        reply = "Hi, I'm the SafeHer Assistant. I can help with safety tips, emergency guidance, finding nearby help, planning a safer route, or general legal questions. What's going on?"

    elif intent == "emergency_guidance":
        reply = EMERGENCY_STEPS

    elif intent == "sos_howto":
        reply = (
            "Tap the SOS button on your Home tab — it starts a 3-second countdown (tap Cancel if it was an accident), "
            "then sends your live location to every trusted contact you've added, with retries if your connection is spotty. "
            "You can add or manage contacts from the Guardian tab."
        )

    elif intent == "nearby_help":
        services = context.get("nearby_services") or []
        if services:
            lines = [f"- {s['name']} ({s['type']}), {s.get('distance_km', '?')} km away" for s in services[:5]]
            reply = "Here's what's closest to you right now:\n" + "\n".join(lines) + "\n\nYou can call or navigate to any of these from the Directory tab."
        else:
            reply = "I don't have your location yet — allow location access and check the Directory tab for nearby police, hospitals, pharmacies, and helplines with one-tap calling and navigation."

    elif intent == "journey_planning":
        route = context.get("route_result")
        if route and route.get("routes"):
            best = max(route["routes"], key=lambda r: r["score"])
            reply = (
                f"Based on your route: {best['label']} scores {best['score']}/100 ({best['rating']}), "
                f"crossing {best['risk_zones_crossed']} known risk zone(s). "
                "For a live view with alternatives, use Route Mode on the Safety Map tab, or start a Journey "
                "from the Home tab so a guardian is automatically alerted if you don't check in."
            )
        else:
            reply = (
                "I can help plan a safer route — open the Safety Map tab, switch to Route Mode, and tap your "
                "start and end points; I'll compare routes by real risk-zone data. Or start a full Journey from "
                "the Home tab for live tracking with automatic check-in alerts."
            )

    elif intent == "legal_advice":
        topic = "harassment" if "harass" in message.lower() else "stalking" if "stalk" in message.lower() else "default"
        reply = LEGAL_TOPICS[topic]

    elif intent == "safety_tips":
        reply = "A few things that actually help:\n" + "\n".join(f"- {t}" for t in SAFETY_TIPS[:4])

    else:
        reply = (
            "I'm not sure I caught that — I can help with safety tips, what to do in an emergency, finding "
            "nearby help, planning a safer route, or general legal questions. Try one of the suggestions below, "
            "or rephrase what you need."
        )

    return {"reply": reply, "intent": intent, "suggestions": QUICK_SUGGESTIONS}