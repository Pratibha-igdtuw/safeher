"""
Distress detection "AI model".

In the full product this would run an audio classification model
(e.g. a fine-tuned YAMNet/CNN) on the live microphone stream to detect
screaming, shouting, or distress keywords, entirely on-device for privacy.

For the hackathon demo, the browser's Web Speech API converts speech to
text on the frontend (static/js/main.js), and this module plays the role
of the "AI model" that classifies the resulting transcript. This keeps
the same interface (transcript in -> distress classification out) so the
mock logic here can be swapped for a real audio model without touching
app.py or the frontend.
"""

DISTRESS_KEYWORDS = [
    "help",
    "bachao",
    "madad",
    "stop",
    "leave me",
    "chodo",
    "don't touch",
    "call police",
    "save me",
]

HIGH_URGENCY_KEYWORDS = ["help", "bachao", "madad", "save me"]


def check_distress(transcript: str) -> dict:
    text = transcript.lower().strip()

    if not text:
        return {"distress_detected": False, "confidence": 0.0, "matched": []}

    matched = [kw for kw in DISTRESS_KEYWORDS if kw in text]
    high_urgency_matched = [kw for kw in HIGH_URGENCY_KEYWORDS if kw in text]

    if not matched:
        return {"distress_detected": False, "confidence": 0.05, "matched": []}

    # Simple confidence heuristic: more matches / high-urgency words -> higher confidence
    confidence = min(0.99, 0.5 + 0.15 * len(matched) + 0.2 * len(high_urgency_matched))

    return {
        "distress_detected": True,
        "confidence": round(confidence, 2),
        "matched": matched,
        "auto_trigger_sos": confidence >= 0.7,
    }
