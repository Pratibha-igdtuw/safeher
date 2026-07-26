"""
Distress detection from a speech-to-text transcript (keyword + light NLP
heuristics — a separate, independent signal from the raw-audio YAMNet
path in utils/audio_classifier.py, not a "fallback" for it as an earlier
version of this docstring incorrectly implied).

The browser's Web Speech API converts speech to text client-side
(static/js/main.js), and this module classifies the resulting transcript.
Kept as transcript-in -> classification-out so it's swappable for a real
NLP model without touching app.py or the frontend.

BUGFIX: keyword matching used to be plain substring search (`kw in text`),
which is a real false-positive source — e.g. "nonstop" contains "stop",
"bachaoge" contains "bachao". Now uses word-boundary regex matching.
"""

import re

DISTRESS_KEYWORDS = [
    "help", "bachao", "madad", "stop", "leave me", "chodo",
    "don't touch", "call police", "save me", "let go", "get away",
]

HIGH_URGENCY_KEYWORDS = ["help", "bachao", "madad", "save me", "call police"]

# Very small, explicitly-heuristic lexicons for a lightweight tone signal —
# NOT a trained emotion-classification model. Good enough to distinguish
# "scared" vs "angry" vs "just talking" at a glance; not a clinical or
# forensic claim about the speaker's emotional state.
FEAR_WORDS = ["scared", "afraid", "help", "please", "don't hurt", "bachao", "madad", "save me"]
ANGER_WORDS = ["stop", "leave", "chodo", "get away", "let go", "don't touch"]

# False-positive reduction: word-boundary matching alone still leaves one
# common failure mode — a single generic word like "stop" appearing in
# ordinary conversation ("please stop the car here"). A single non-urgent
# keyword match is treated as low-confidence; auto-trigger requires either
# a high-urgency word OR multiple distinct matches.
AUTO_TRIGGER_CONFIDENCE_THRESHOLD = 0.7


def _keyword_matches(text, keywords):
    return [kw for kw in keywords if re.search(r"\b" + re.escape(kw) + r"\b", text)]


def _detect_emotion(text, matched_fear, matched_anger):
    """Heuristic-only tone signal from lexicon hits + surface features
    (exclamation density, all-caps ratio) — explicitly not a trained
    emotion classifier. Returns {label, intensity} where intensity is a
    rough 0-1 signal, not a calibrated probability."""
    exclamations = text.count("!")
    letters = [c for c in text if c.isalpha()]
    caps_ratio = (sum(1 for c in letters if c.isupper()) / len(letters)) if letters else 0

    surface_intensity = min(1.0, 0.15 * exclamations + caps_ratio)

    if matched_fear and len(matched_fear) >= len(matched_anger):
        label = "fear"
        lexicon_intensity = min(1.0, 0.3 + 0.2 * len(matched_fear))
    elif matched_anger:
        label = "anger"
        lexicon_intensity = min(1.0, 0.3 + 0.2 * len(matched_anger))
    else:
        label = "neutral"
        lexicon_intensity = 0.0

    intensity = round(min(1.0, max(surface_intensity, lexicon_intensity)), 2)
    if intensity < 0.15:
        label = "neutral"
    return {"label": label, "intensity": intensity}


def check_distress(transcript: str) -> dict:
    text = transcript.lower().strip()

    if not text:
        return {
            "distress_detected": False, "confidence": 0.0, "matched": [],
            "emotion": {"label": "neutral", "intensity": 0.0},
        }

    matched = _keyword_matches(text, DISTRESS_KEYWORDS)
    high_urgency_matched = _keyword_matches(text, HIGH_URGENCY_KEYWORDS)
    matched_fear = _keyword_matches(text, FEAR_WORDS)
    matched_anger = _keyword_matches(text, ANGER_WORDS)
    emotion = _detect_emotion(text, matched_fear, matched_anger)

    if not matched:
        return {
            "distress_detected": False, "confidence": 0.05, "matched": [],
            "emotion": emotion,
        }

    # Confidence: base on match count/urgency, nudged slightly by the tone
    # signal (a fearful tone alongside a keyword match is a bit more
    # convincing than a flat/neutral reading of the same words).
    confidence = 0.5 + 0.15 * len(matched) + 0.2 * len(high_urgency_matched)
    if emotion["label"] == "fear":
        confidence += 0.05 * emotion["intensity"]
    confidence = round(min(0.99, confidence), 2)

    # False-positive reduction: a single, non-urgent keyword match (e.g.
    # just "stop" with nothing else) is reported but NOT auto-escalated —
    # too easy to trip on ordinary conversation. Auto-trigger requires
    # either a high-urgency word or corroborating multiple matches, on
    # top of the confidence threshold.
    strong_signal = bool(high_urgency_matched) or len(matched) >= 2
    auto_trigger_sos = strong_signal and confidence >= AUTO_TRIGGER_CONFIDENCE_THRESHOLD

    return {
        "distress_detected": True,
        "confidence": confidence,
        "matched": matched,
        "emotion": emotion,
        "auto_trigger_sos": auto_trigger_sos,
    }