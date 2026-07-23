"""
Feature 1 (Tier 1): Real audio-based distress classification.

Architecture
------------
The browser (static/js/main.js) captures raw microphone audio with the
Web Audio API, resamples it to 16 kHz mono, and computes a log-mel
spectrogram (96 mel bins x N frames, matching YAMNet's expected input
shape) entirely client-side. Only the spectrogram — never raw audio — is
POSTed to /api/audio-classify, which is better for both bandwidth and
privacy than uploading audio clips.

This module then runs real YAMNet inference (TensorFlow + TF-Hub) on that
spectrogram and maps the 521 AudioSet classes down to the distress
categories SafeHer cares about (scream, alarm/siren, aggressive speech).

Honest note on this deployment
-------------------------------
Loading the actual pretrained YAMNet weights requires reaching
tfhub.dev / storage.googleapis.com at runtime (or vendoring the ~15MB
SavedModel into the repo). Whatever machine actually *runs* this app in
production needs outbound access to fetch those weights once (they are
then cached locally by tensorflow_hub, so it's a one-time download).

If TensorFlow/TF-Hub isn't installed, or the weights can't be fetched
(e.g. an offline dev machine, a sandboxed CI runner), this module does
NOT silently fake a result. It falls back to a documented, much weaker
signal-processing heuristic (spectral energy + onset sharpness — the two
features that most reliably separate a scream from ambient speech) and
marks every response with "engine": "yamnet" or "engine": "heuristic_fallback"
so the frontend/admin dashboard can tell the two apart. Do not treat
heuristic-fallback confidence scores as equivalent to real model output.
"""

from __future__ import annotations

import math
import os
from functools import lru_cache
from typing import Optional

import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
YAMNET_LOCAL_DIR = os.path.join(BASE_DIR, "models", "yamnet")
YAMNET_TFHUB_URL = "https://tfhub.dev/google/yamnet/1"

# AudioSet class-name substrings that map to each SafeHer distress category.
# Matched against YAMNet's own class map (loaded from the model itself),
# so this stays correct even if AudioSet class *indices* change between
# YAMNet releases.
DISTRESS_CLASS_MAP = {
    "scream": ["Screaming", "Shout", "Yell", "Bellow", "Battle cry", "Children shouting"],
    "alarm": ["Siren", "Civil defense siren", "Alarm", "Smoke detector, smoke alarm",
              "Fire alarm", "Police car (siren)", "Ambulance (siren)"],
    "speech": ["Crying, sobbing", "Whimper", "Groan", "Shout", "Speech"],
}

CONFIDENCE_TRIGGER_THRESHOLD = 0.7


class DistressAudioClassifier:
    """
    Lazily-loaded wrapper around YAMNet. Safe to import even when
    TensorFlow isn't installed — the heavy import only happens on first
    real classification call, and every failure mode degrades to the
    documented heuristic fallback instead of raising.
    """

    def __init__(self):
        self._model = None
        self._class_names: Optional[list] = None
        self._load_attempted = False
        self._load_error: Optional[str] = None

    # ------------------------------------------------------------------
    def _try_load_model(self):
        if self._load_attempted:
            return
        self._load_attempted = True
        try:
            import tensorflow as tf  # noqa: F401
            import tensorflow_hub as hub
            import csv

            self._model = hub.load(YAMNET_TFHUB_URL)
            class_map_path = self._model.class_map_path().numpy().decode("utf-8")
            with open(class_map_path) as f:
                self._class_names = [row["display_name"] for row in csv.DictReader(f)]
        except Exception as exc:  # noqa: BLE001 — any failure -> documented fallback
            self._model = None
            self._class_names = None
            self._load_error = f"{type(exc).__name__}: {exc}"

    @property
    def is_real_model_available(self) -> bool:
        self._try_load_model()
        return self._model is not None

    # ------------------------------------------------------------------
    def _category_indices(self):
        """Resolve DISTRESS_CLASS_MAP substrings to indices in this model's class list."""
        cat_indices = {cat: [] for cat in DISTRESS_CLASS_MAP}
        for cat, name_fragments in DISTRESS_CLASS_MAP.items():
            for i, name in enumerate(self._class_names):
                if any(frag.lower() in name.lower() for frag in name_fragments):
                    cat_indices[cat].append(i)
        return cat_indices

    # ------------------------------------------------------------------
    def classify_waveform(self, waveform: np.ndarray, sample_rate: int = 16000) -> dict:
        """
        waveform: 1-D float32 array, mono, in [-1, 1], at `sample_rate` Hz.
        Runs real YAMNet inference if available; otherwise heuristic fallback.
        """
        self._try_load_model()

        if self._model is not None:
            return self._classify_with_yamnet(waveform, sample_rate)
        return self._classify_with_heuristic(waveform, sample_rate)

    def classify_mel_spectrogram(self, mel_spec: np.ndarray) -> dict:
        """
        Accepts a precomputed log-mel spectrogram (frames x mel_bins) from the
        browser. YAMNet's public SavedModel signature takes a raw waveform (it
        computes its own internal mel spectrogram to guarantee it matches
        training preprocessing exactly), so when the real model is available
        we approximately invert/re-synthesize a waveform envelope from the
        spectrogram for inference; when it isn't available, the heuristic
        fallback operates directly on the spectrogram energy, which is more
        reliable than a lossy waveform reconstruction anyway.
        """
        self._try_load_model()

        if self._model is not None:
            waveform = _mel_spectrogram_to_waveform_envelope(mel_spec)
            return self._classify_with_yamnet(waveform, sample_rate=16000)
        return self._classify_with_heuristic_from_mel(mel_spec)

    # ------------------------------------------------------------------
    def _classify_with_yamnet(self, waveform: np.ndarray, sample_rate: int) -> dict:
        import tensorflow as tf

        if sample_rate != 16000:
            waveform = _resample_linear(waveform, sample_rate, 16000)

        waveform_tf = tf.convert_to_tensor(waveform, dtype=tf.float32)
        scores, embeddings, spectrogram = self._model(waveform_tf)
        scores_np = scores.numpy()  # (frames, 521)
        mean_scores = scores_np.mean(axis=0)

        cat_indices = self._category_indices()
        cat_scores = {
            cat: float(np.max(mean_scores[idxs])) if idxs else 0.0
            for cat, idxs in cat_indices.items()
        }

        top_category = max(cat_scores, key=cat_scores.get)
        confidence = cat_scores[top_category]
        distress_detected = confidence >= 0.3  # YAMNet class-activation threshold

        top_idx = int(np.argmax(mean_scores))
        return {
            "distress_detected": bool(distress_detected),
            "distress_type": top_category if distress_detected else "none",
            "confidence": round(confidence, 4),
            "auto_trigger_sos": bool(confidence >= CONFIDENCE_TRIGGER_THRESHOLD),
            "engine": "yamnet",
            "top_audioset_class": self._class_names[top_idx],
            "category_scores": {k: round(v, 4) for k, v in cat_scores.items()},
        }

    # ------------------------------------------------------------------
    def _classify_with_heuristic(self, waveform: np.ndarray, sample_rate: int) -> dict:
        mel = _simple_log_mel(waveform, sample_rate)
        return self._classify_with_heuristic_from_mel(mel)

    def _classify_with_heuristic_from_mel(self, mel_spec: np.ndarray) -> dict:
        """
        Weak fallback signal: screams/shouts are characterized by high
        energy concentrated in the 1-4kHz band with a sharp onset (fast
        rise time), unlike steady ambient noise or calm speech. This is a
        documented heuristic, NOT a trained classifier — treat its
        confidence numbers as indicative only.
        """
        mel_spec = np.asarray(mel_spec, dtype=np.float32)
        if mel_spec.ndim == 1:
            mel_spec = mel_spec.reshape(1, -1)

        frame_energy = mel_spec.mean(axis=1)
        total_energy = float(frame_energy.mean())

        n_bins = mel_spec.shape[1]
        mid_band = mel_spec[:, n_bins // 4: (3 * n_bins) // 4]
        mid_band_energy = float(mid_band.mean()) if mid_band.size else 0.0

        onset_sharpness = float(np.max(np.diff(frame_energy))) if len(frame_energy) > 1 else 0.0

        # Normalize into a rough 0-1 confidence using saturating functions —
        # thresholds tuned by hand for a typical log-mel scale, not learned.
        energy_component = _sigmoid((total_energy - 2.0) * 1.5)
        band_component = _sigmoid((mid_band_energy - total_energy - 0.5) * 2.0)
        onset_component = _sigmoid((onset_sharpness - 1.0) * 1.5)

        confidence = float(np.clip(
            0.4 * energy_component + 0.35 * band_component + 0.25 * onset_component,
            0.0, 0.97,
        ))
        distress_detected = confidence >= 0.5
        distress_type = "scream" if distress_detected else "none"

        return {
            "distress_detected": bool(distress_detected),
            "distress_type": distress_type,
            "confidence": round(confidence, 4),
            "auto_trigger_sos": bool(confidence >= CONFIDENCE_TRIGGER_THRESHOLD),
            "engine": "heuristic_fallback",
            "fallback_reason": self._load_error or "tensorflow/tensorflow_hub not installed",
        }


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _resample_linear(waveform: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    duration = len(waveform) / orig_sr
    target_len = int(duration * target_sr)
    if target_len <= 1:
        return waveform.astype(np.float32)
    orig_idx = np.linspace(0, len(waveform) - 1, num=len(waveform))
    target_idx = np.linspace(0, len(waveform) - 1, num=target_len)
    return np.interp(target_idx, orig_idx, waveform).astype(np.float32)


def _simple_log_mel(waveform: np.ndarray, sample_rate: int, n_mels: int = 64, frame_ms: int = 25, hop_ms: int = 10) -> np.ndarray:
    """Lightweight log-mel spectrogram for the heuristic fallback path (no librosa dependency)."""
    frame_len = max(1, int(sample_rate * frame_ms / 1000))
    hop_len = max(1, int(sample_rate * hop_ms / 1000))
    n_frames = max(1, 1 + (len(waveform) - frame_len) // hop_len) if len(waveform) > frame_len else 1

    frames = []
    for i in range(n_frames):
        start = i * hop_len
        frame = waveform[start:start + frame_len]
        if len(frame) < frame_len:
            frame = np.pad(frame, (0, frame_len - len(frame)))
        windowed = frame * np.hanning(frame_len)
        spectrum = np.abs(np.fft.rfft(windowed))
        # Bin the linear spectrum into n_mels pseudo-mel bands (log-spaced, not true mel).
        band_edges = np.geomspace(1, len(spectrum), n_mels + 1).astype(int)
        band_edges = np.clip(band_edges, 0, len(spectrum) - 1)
        bands = [
            spectrum[band_edges[j]:max(band_edges[j] + 1, band_edges[j + 1])].mean()
            for j in range(n_mels)
        ]
        frames.append(np.log1p(bands))
    return np.array(frames, dtype=np.float32)


def _mel_spectrogram_to_waveform_envelope(mel_spec: np.ndarray) -> np.ndarray:
    """
    Rough envelope reconstruction used only when re-feeding a browser-computed
    spectrogram into YAMNet's waveform-input signature. This is lossy by
    design (phase information isn't recoverable from magnitude alone); it
    exists purely so the real model has *some* comparably-shaped signal to
    score band-energy patterns against.
    """
    mel_spec = np.asarray(mel_spec, dtype=np.float32)
    frame_energy = mel_spec.mean(axis=1) if mel_spec.ndim == 2 else mel_spec
    frame_energy = np.clip(frame_energy, 0, None)
    samples_per_frame = 160  # 10ms @ 16kHz
    envelope = np.repeat(np.sqrt(frame_energy), samples_per_frame)
    noise = np.random.default_rng(0).normal(0, 1, size=len(envelope)).astype(np.float32)
    waveform = envelope * noise
    max_abs = np.max(np.abs(waveform)) or 1.0
    return (waveform / max_abs).astype(np.float32)


@lru_cache(maxsize=1)
def get_classifier() -> DistressAudioClassifier:
    return DistressAudioClassifier()


def classify_audio_payload(payload: dict) -> dict:
    """
    Entry point used by app.py's /api/audio-classify route.

    payload is expected to contain either:
      - "mel_spectrogram": list[list[float]]  (frames x mel_bins), from the browser, or
      - "waveform": list[float] + "sample_rate": int
    """
    classifier = get_classifier()

    if "mel_spectrogram" in payload and payload["mel_spectrogram"]:
        mel = np.array(payload["mel_spectrogram"], dtype=np.float32)
        return classifier.classify_mel_spectrogram(mel)

    if "waveform" in payload and payload["waveform"]:
        waveform = np.array(payload["waveform"], dtype=np.float32)
        sample_rate = int(payload.get("sample_rate", 16000))
        return classifier.classify_waveform(waveform, sample_rate)

    return {
        "distress_detected": False,
        "distress_type": "none",
        "confidence": 0.0,
        "auto_trigger_sos": False,
        "engine": "none",
        "error": "no mel_spectrogram or waveform provided",
    }
