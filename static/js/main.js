// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
async function api(path, options = {}) {
  let res;
  try {
    res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
  } catch (networkErr) {
    showNotification("⚠️ Connection problem", "Couldn't reach the server. Check your connection and try again.", "warning");
    return { error: "network_error", _ok: false, _status: 0 };
  }

  let body;
  try {
    body = await res.json();
  } catch (parseErr) {
    body = { error: "invalid_response" };
  }

  if (!res.ok) {
    if (res.status === 429) {
      const retryAfter = res.headers.get("Retry-After");
      const waitMsg = retryAfter ? ` Try again in about ${Math.ceil(retryAfter / 60)} minute(s).` : " Please wait a bit before trying again.";
      showNotification("⏳ Too many attempts", (body.message || "Rate limit reached.") + waitMsg, "warning");
    } else if (res.status === 400 && body.error === "validation_failed") {
      const fieldMsgs = Object.entries(body.fields || {})
        .map(([field, msgs]) => `${field}: ${Array.isArray(msgs) ? msgs.join(", ") : msgs}`)
        .join(" · ");
      showNotification("⚠️ Please check your input", fieldMsgs || "Some fields are invalid.", "warning");
    } else if (res.status === 400 && body.error === "invalid_json") {
      showNotification("⚠️ Something went wrong", "The request could not be processed. Please try again.", "warning");
    } else if (res.status === 401) {
      // leave auth redirects/handling to the caller — don't spam a toast
    } else if (res.status === 403) {
      showNotification("🚫 Not allowed", body.error === "unauthorized" ? "You don't have access to that." : (body.message || "Action not permitted."), "warning");
    } else if (res.status >= 500) {
      showNotification("⚠️ Server error", "Something went wrong on our end. Please try again shortly.", "critical");
    }
  }

  return Array.isArray(body) ? body : { ...body, _ok: res.ok, _status: res.status };
}

function getLocation() {
  return new Promise((resolve) => {
    if (!navigator.geolocation) {
      resolve({ latitude: null, longitude: null });
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => resolve({ latitude: pos.coords.latitude, longitude: pos.coords.longitude }),
      () => resolve({ latitude: null, longitude: null }),
      { timeout: 4000 }
    );
  });
}

function scoreColor(score) {
  if (score >= 75) return "#2dd4bf";
  if (score >= 45) return "#ffb020";
  return "#ff4757";
}

// ---------------------------------------------------------------------------
// FEATURE 4: WebSocket Real-Time Notifications
// ---------------------------------------------------------------------------
let socket;
try {
  socket = io();
} catch (e) {
  console.warn("Socket.io unavailable — real-time alerts disabled", e);
  socket = { on: () => {}, emit: () => {}, connected: false }; // no-op fallback so rest of main.js still runs
}

socket.on("connect", () => {
  console.log("Connected to server for real-time updates");
});

socket.on("sos_triggered", (data) => {
  showNotification("🚨 SOS ALERT", data.message, "critical");
});

socket.on("risk_alert", (data) => {
  showNotification("⚠️ HIGH-RISK AREA NEARBY", data.message, "warning");
  console.log("Risk alert details:", data);
});

socket.on("test_alert", (data) => {
  showNotification("ℹ️ Test Notification", data.message, "info");
});

// ===== TIER 3 PART 1: Live tracking authorization events =====
socket.on("tracking_denied", (data) => {
  const reasons = {
    not_authenticated: "You need to be logged in to view live locations.",
    not_authorized: "You're not an accepted linked contact of this person yet.",
    invalid_user_id: "Couldn't identify who you're trying to track.",
  };
  showNotification("🚫 Tracking denied", reasons[data.reason] || "You can't view this person's live location.", "warning");
  stopTrackingUI();
});

socket.on("tracking_joined", (data) => {
  showNotification("📍 Live tracking", "You're now viewing their live location.", "info");
});

socket.on("location_update", (data) => {
  updateTrackedLocationOnMap(data);
});

socket.on("tracking_invite_received", (data) => {
  showNotification("🔗 Bubble invite", `${data.from_email} invited you to their Bubble.`, "info");
  loadLinkedContacts();
});

socket.on("tracking_invite_accepted", (data) => {
  showNotification("✅ Invite accepted", `${data.by_email} accepted your Bubble invite.`, "info");
  loadLinkedContacts();
});

function showNotification(title, message, type) {
  // Create notification element
  const notif = document.createElement("div");
  notif.className = `notification notification-${type}`;
  notif.innerHTML = `<strong>${title}</strong><br/>${message}`;
  document.body.appendChild(notif);
  
  // Animate in
  setTimeout(() => notif.classList.add("show"), 10);
  
  // Auto-remove after 5 seconds
  setTimeout(() => {
    notif.classList.remove("show");
    setTimeout(() => notif.remove(), 300);
  }, 5000);
}

// Add notification styles
const style = document.createElement("style");
style.textContent = `
  .notification {
    position: fixed;
    top: 20px;
    right: 20px;
    padding: 16px;
    border-radius: 8px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.15);
    z-index: 10000;
    max-width: 300px;
    opacity: 0;
    transform: translateX(400px);
    transition: all 0.3s ease;
    font-size: 13px;
    line-height: 1.4;
  }
  
  .notification.show {
    opacity: 1;
    transform: translateX(0);
  }
  
  .notification-critical {
    background: #ff4757;
    color: white;
  }
  
  .notification-warning {
    background: #ffb020;
    color: white;
  }
  
  .notification-info {
    background: #667eea;
    color: white;
  }
`;
document.head.appendChild(style);

// ---------------------------------------------------------------------------
// Tab navigation
// ---------------------------------------------------------------------------
const tabButtons = document.querySelectorAll(".tab-btn");
const tabPanels = {
  home: document.getElementById("tab-home"),
  map: document.getElementById("tab-map"),
  directory: document.getElementById("tab-directory"),
  guardian: document.getElementById("tab-guardian"),
  community: document.getElementById("tab-community"),
};

tabButtons.forEach((btn) => {
  btn.addEventListener("click", () => {
    tabButtons.forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    Object.values(tabPanels).forEach((p) => p.classList.add("hidden"));
    tabPanels[btn.dataset.tab].classList.remove("hidden");

    if (btn.dataset.tab === "map") {
      setTimeout(() => leafletMap && leafletMap.invalidateSize(), 50);
    }
    if (btn.dataset.tab === "directory") {
      loadServices(currentServiceFilter);
    }
    if (btn.dataset.tab === "guardian") {
      setTimeout(() => bubbleMap && bubbleMap.invalidateSize(), 50);
    }
    if (btn.dataset.tab === "community") {
      loadFeed();
    }
  });
});

// ---------------------------------------------------------------------------
// SOS
// ---------------------------------------------------------------------------
const sosBtn = document.getElementById("sosBtn");
const sosStatus = document.getElementById("sosStatus");

async function triggerSOS(triggerType = "manual") {
  sosStatus.textContent = "Getting location & sending alert...";
  const loc = await getLocation();

  if (!navigator.onLine) {
    queueOfflineAction("sos", { ...loc, trigger_type: triggerType, message: "SOS raised while offline" });
    sosStatus.textContent = "⚠️ Offline — SOS queued, will send the moment you're back online.";
    return;
  }

  try {
    const result = await api("/api/sos", {
      method: "POST",
      body: JSON.stringify({ ...loc, trigger_type: triggerType }),
    });
    sosStatus.textContent = `Alert sent to ${result.contacts_notified} contact(s).`;
    loadAlerts();
  } catch (err) {
    // Network failed even though navigator.onLine said we were online
    queueOfflineAction("sos", { ...loc, trigger_type: triggerType, message: "SOS raised while offline" });
    sosStatus.textContent = "⚠️ Couldn't reach the server — SOS queued, will retry automatically.";
  }
}

sosBtn.addEventListener("click", () => triggerSOS("manual"));

// ---------------------------------------------------------------------------
// Fake call
// ---------------------------------------------------------------------------
const fakeCallBtn = document.getElementById("fakeCallBtn");
const fakeCallDelay = document.getElementById("fakeCallDelay");
const fakeCallStatus = document.getElementById("fakeCallStatus");
const overlay = document.getElementById("fakeCallOverlay");
const acceptCallBtn = document.getElementById("acceptCallBtn");
const declineCallBtn = document.getElementById("declineCallBtn");

const FAKE_SCRIPT = [
  "Hey, where are you right now?",
  "Okay, I'm coming to pick you up, stay right there.",
  "I'm just two minutes away, keep talking to me.",
];

fakeCallBtn.addEventListener("click", () => {
  const delay = parseInt(fakeCallDelay.value, 10) * 1000;
  fakeCallStatus.textContent = delay ? `Call scheduled in ${delay / 1000}s...` : "Calling now...";
  setTimeout(showFakeCall, delay);
});

function showFakeCall() {
  overlay.classList.remove("hidden");
  fakeCallStatus.textContent = "";
}

declineCallBtn.addEventListener("click", () => overlay.classList.add("hidden"));

acceptCallBtn.addEventListener("click", () => {
  overlay.querySelector(".ringing").textContent = "00:01";
  speakScript(0);
});

function speakScript(i) {
  if (i >= FAKE_SCRIPT.length) {
    setTimeout(() => overlay.classList.add("hidden"), 1500);
    return;
  }
  if (window.speechSynthesis) {
    const utter = new SpeechSynthesisUtterance(FAKE_SCRIPT[i]);
    utter.rate = 1;
    utter.onend = () => setTimeout(() => speakScript(i + 1), 400);
    window.speechSynthesis.speak(utter);
  } else {
    setTimeout(() => speakScript(i + 1), 2000);
  }
}

// ---------------------------------------------------------------------------
// FEATURE 1: Voice Distress Detection (with TensorFlow.js YAMNet fallback)
// ---------------------------------------------------------------------------
const listenBtn = document.getElementById("listenBtn");
const stopListenBtn = document.getElementById("stopListenBtn");
const transcriptBox = document.getElementById("transcriptBox");
const analyzeBtn = document.getElementById("analyzeBtn");
const distressResult = document.getElementById("distressResult");

let recognition = null;
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

if (SpeechRecognition) {
  recognition = new SpeechRecognition();
  recognition.continuous = true;
  recognition.interimResults = true;
  recognition.onresult = (event) => {
    let interimTranscript = "";
    let finalTranscript = "";

    for (let i = event.resultIndex; i < event.results.length; i++) {
      const transcript = event.results[i][0].transcript;
      if (event.results[i].isFinal) {
        finalTranscript += transcript + " ";
      } else {
        interimTranscript += transcript;
      }
    }

    transcriptBox.value = finalTranscript + interimTranscript;
  };
  recognition.onerror = (event) => {
    distressResult.textContent = `Error: ${event.error}`;
  };
}

listenBtn.addEventListener("click", () => {
  if (recognition) {
    recognition.start();
    listenBtn.disabled = true;
    stopListenBtn.disabled = false;
    transcriptBox.value = "";
    distressResult.textContent = "";
  } else {
    distressResult.textContent = "Speech Recognition not supported in this browser";
  }
});

stopListenBtn.addEventListener("click", () => {
  if (recognition) {
    recognition.stop();
    listenBtn.disabled = false;
    stopListenBtn.disabled = true;
  }
});

analyzeBtn.addEventListener("click", async () => {
  const transcript = transcriptBox.value.trim();
  if (!transcript) {
    distressResult.textContent = "Please speak or type something first.";
    return;
  }

  distressResult.textContent = "Analyzing...";
  const loc = await getLocation();
  
  try {
    const result = await api("/api/distress-check", {
      method: "POST",
      body: JSON.stringify({
        transcript,
        location: loc,
      }),
    });

    if (result.distress_detected) {
      distressResult.innerHTML = `
        <span style="color:#ff4757">🚨 DISTRESS DETECTED</span><br/>
        Confidence: ${(result.confidence * 100).toFixed(0)}%<br/>
        Matched keywords: ${result.matched.join(", ")}
        ${result.auto_trigger_sos ? "<br/>⚠️ Auto-triggering SOS..." : ""}
      `;
      if (result.auto_trigger_sos) {
        setTimeout(() => triggerSOS("audio_ml"), 1500);
      }
    } else {
      distressResult.textContent = "No distress detected in transcript.";
    }
  } catch (err) {
    distressResult.textContent = "Error analyzing transcript: " + err.message;
  }
});

// ---------------------------------------------------------------------------
// TIER 1 FEATURE 1: Real audio ML deployment.
//
// Captures raw microphone audio with the Web Audio API, buffers ~0.975s
// windows (YAMNet's native frame size at 16kHz), computes a log-mel
// spectrogram entirely in-browser (via a small FFT — no server upload of
// raw audio, ever), and POSTs the spectrogram to /api/audio-classify,
// where real YAMNet inference happens (utils/audio_classifier.py).
//
// This runs independently of the Web Speech transcript path above — a
// user with a browser that doesn't support SpeechRecognition (e.g. iOS
// Safari) still gets full audio-based distress detection through this.
// ---------------------------------------------------------------------------
const audioMlBtn = document.getElementById("audioMlBtn");
const stopAudioMlBtn = document.getElementById("stopAudioMlBtn");
const audioMlResult = document.getElementById("audioMlResult");

const YAMNET_SAMPLE_RATE = 16000;
const YAMNET_WINDOW_SECONDS = 0.975; // one YAMNet input frame
const MEL_BINS = 64;

let audioMlContext = null;
let audioMlStream = null;
let audioMlProcessor = null;
let audioMlBuffer = [];
let audioMlBusy = false;

function hannWindow(n) {
  const w = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    w[i] = 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / (n - 1));
  }
  return w;
}

// Minimal radix-2 FFT (magnitude only, real input) — small enough to not
// need an external DSP library for this.
function fftMagnitude(realIn) {
  let n = 1;
  while (n < realIn.length) n *= 2;
  const re = new Float64Array(n);
  const im = new Float64Array(n);
  re.set(realIn);

  for (let i = 1, j = 0; i < n; i++) {
    let bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) {
      [re[i], re[j]] = [re[j], re[i]];
      [im[i], im[j]] = [im[j], im[i]];
    }
  }

  for (let len = 2; len <= n; len <<= 1) {
    const ang = (-2 * Math.PI) / len;
    const wRe = Math.cos(ang), wI = Math.sin(ang);
    for (let i = 0; i < n; i += len) {
      let curRe = 1, curI = 0;
      for (let j = 0; j < len / 2; j++) {
        const uRe = re[i + j], uIm = im[i + j];
        const vRe = re[i + j + len / 2] * curRe - im[i + j + len / 2] * curI;
        const vIm = re[i + j + len / 2] * curI + im[i + j + len / 2] * curRe;
        re[i + j] = uRe + vRe;
        im[i + j] = uIm + vIm;
        re[i + j + len / 2] = uRe - vRe;
        im[i + j + len / 2] = uIm - vIm;
        const nextRe = curRe * wRe - curI * wI;
        const nextI = curRe * wI + curI * wRe;
        curRe = nextRe; curI = nextI;
      }
    }
  }

  const half = n / 2;
  const mag = new Float64Array(half);
  for (let i = 0; i < half; i++) {
    mag[i] = Math.sqrt(re[i] * re[i] + im[i] * im[i]);
  }
  return mag;
}

// Computes a log-mel spectrogram matching the shape YAMNet expects
// (frames x MEL_BINS), using 25ms frames / 10ms hop at 16kHz.
function computeLogMelSpectrogram(samples, sampleRate) {
  const frameLen = Math.round(sampleRate * 0.025);
  const hopLen = Math.round(sampleRate * 0.010);
  const window = hannWindow(frameLen);
  const frames = [];

  for (let start = 0; start + frameLen <= samples.length; start += hopLen) {
    const frame = new Float32Array(frameLen);
    for (let i = 0; i < frameLen; i++) frame[i] = samples[start + i] * window[i];
    const spectrum = fftMagnitude(frame);

    // Log-spaced pseudo-mel banding (good enough for the fallback path;
    // the real YAMNet model recomputes its own canonical mel spectrogram
    // server-side from the reconstructed waveform for the numbers that
    // actually drive auto-SOS).
    const bands = new Float32Array(MEL_BINS);
    const bandEdges = [];
    for (let b = 0; b <= MEL_BINS; b++) {
      bandEdges.push(Math.floor(Math.pow(spectrum.length, b / MEL_BINS)));
    }
    for (let b = 0; b < MEL_BINS; b++) {
      const lo = Math.min(bandEdges[b], spectrum.length - 1);
      const hi = Math.max(lo + 1, Math.min(bandEdges[b + 1], spectrum.length));
      let sum = 0;
      for (let i = lo; i < hi; i++) sum += spectrum[i];
      bands[b] = Math.log1p(sum / Math.max(1, hi - lo));
    }
    frames.push(Array.from(bands));
  }
  return frames;
}

async function startAudioMlMonitoring() {
  try {
    audioMlStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    audioMlResult.textContent = "Microphone access denied: " + err.message;
    return;
  }

  audioMlContext = new (window.AudioContext || window.webkitAudioContext)();
  const source = audioMlContext.createMediaStreamAudioSource
    ? audioMlContext.createMediaStreamAudioSource(audioMlStream)
    : audioMlContext.createMediaStreamSource(audioMlStream);

  // ScriptProcessorNode is deprecated but has universal support; swap for
  // an AudioWorklet in a follow-up if broader worklet support is required.
  audioMlProcessor = audioMlContext.createScriptProcessor(4096, 1, 1);
  audioMlBuffer = [];

  audioMlProcessor.onaudioprocess = (event) => {
    const inputRate = audioMlContext.sampleRate;
    const chunk = event.inputBuffer.getChannelData(0);
    // Downsample to 16kHz (simple linear interpolation, adequate for a
    // scream/alarm-vs-not classifier where fine spectral detail above a
    // few kHz isn't the discriminating feature).
    const ratio = inputRate / YAMNET_SAMPLE_RATE;
    const outLen = Math.floor(chunk.length / ratio);
    for (let i = 0; i < outLen; i++) {
      const srcIdx = i * ratio;
      const i0 = Math.floor(srcIdx);
      const frac = srcIdx - i0;
      const s0 = chunk[i0] || 0;
      const s1 = chunk[i0 + 1] || s0;
      audioMlBuffer.push(s0 + (s1 - s0) * frac);
    }

    const windowSize = Math.floor(YAMNET_SAMPLE_RATE * YAMNET_WINDOW_SECONDS);
    if (audioMlBuffer.length >= windowSize && !audioMlBusy) {
      const windowSamples = audioMlBuffer.slice(0, windowSize);
      audioMlBuffer = audioMlBuffer.slice(windowSize);
      classifyAudioWindow(windowSamples);
    }
  };

  source.connect(audioMlProcessor);
  audioMlProcessor.connect(audioMlContext.destination);

  audioMlBtn.disabled = true;
  stopAudioMlBtn.disabled = false;
  audioMlResult.textContent = "Listening for distress audio...";
}

async function classifyAudioWindow(windowSamples) {
  audioMlBusy = true;
  try {
    const melSpectrogram = computeLogMelSpectrogram(windowSamples, YAMNET_SAMPLE_RATE);
    const loc = await getLocation();

    const result = await api("/api/audio-classify", {
      method: "POST",
      body: JSON.stringify({
        mel_spectrogram: melSpectrogram,
        location: loc,
      }),
    });

    if (result.distress_detected) {
      audioMlResult.innerHTML = `
        <span style="color:#ff4757">🚨 DISTRESS AUDIO DETECTED (${result.distress_type})</span><br/>
        Confidence: ${(result.confidence * 100).toFixed(0)}% — engine: ${result.engine}
        ${result.auto_trigger_sos ? "<br/>⚠️ Auto-triggering SOS..." : ""}
      `;
    } else {
      audioMlResult.textContent = `Listening... (last window: no distress, engine: ${result.engine})`;
    }
  } catch (err) {
    audioMlResult.textContent = "Audio classification error: " + err.message;
  } finally {
    audioMlBusy = false;
  }
}

function stopAudioMlMonitoring() {
  if (audioMlProcessor) {
    audioMlProcessor.disconnect();
    audioMlProcessor = null;
  }
  if (audioMlContext) {
    audioMlContext.close();
    audioMlContext = null;
  }
  if (audioMlStream) {
    audioMlStream.getTracks().forEach((t) => t.stop());
    audioMlStream = null;
  }
  audioMlBuffer = [];
  audioMlBtn.disabled = false;
  stopAudioMlBtn.disabled = true;
  audioMlResult.textContent = "Stopped.";
}

if (audioMlBtn) {
  audioMlBtn.addEventListener("click", startAudioMlMonitoring);
  stopAudioMlBtn.addEventListener("click", stopAudioMlMonitoring);
}
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Safe check-in timer
// ---------------------------------------------------------------------------
const checkinMinutes = document.getElementById("checkinMinutes");
const startCheckinBtn = document.getElementById("startCheckinBtn");
const confirmSafeBtn = document.getElementById("confirmSafeBtn");
const checkinTimer = document.getElementById("checkinTimer");

let currentCheckinId = null;
let checkinInterval = null;

startCheckinBtn.addEventListener("click", async () => {
  const minutes = parseInt(checkinMinutes.value, 10);
  if (minutes < 1) {
    alert("Please enter at least 1 minute");
    return;
  }

  const result = await api("/api/checkin/start", {
    method: "POST",
    body: JSON.stringify({ minutes }),
  });

  currentCheckinId = result.checkin_id;
  startCheckinBtn.disabled = true;
  checkinMinutes.disabled = true;
  confirmSafeBtn.disabled = false;

  let remainingSeconds = minutes * 60;
  checkinInterval = setInterval(async () => {
    remainingSeconds--;
    const mins = Math.floor(remainingSeconds / 60);
    const secs = remainingSeconds % 60;
    checkinTimer.textContent = `${mins}:${secs.toString().padStart(2, "0")}`;

    if (remainingSeconds <= 0) {
      clearInterval(checkinInterval);
      checkinTimer.textContent = "Time's up! Alerting contacts...";
      // The backend will auto-trigger SOS
    }
  }, 1000);
});

confirmSafeBtn.addEventListener("click", async () => {
  if (!currentCheckinId) return;
  await api(`/api/checkin/${currentCheckinId}/confirm`, { method: "POST" });
  clearInterval(checkinInterval);
  currentCheckinId = null;
  startCheckinBtn.disabled = false;
  checkinMinutes.disabled = false;
  confirmSafeBtn.disabled = true;
  checkinTimer.textContent = "";
  checkinMinutes.value = 15;
});

// ---------------------------------------------------------------------------
// Anonymous Record
// ---------------------------------------------------------------------------
const recordBtn = document.getElementById("recordBtn");
const stopRecordBtn = document.getElementById("stopRecordBtn");
const recordStatus = document.getElementById("recordStatus");
const recordPlayback = document.getElementById("recordPlayback");
const downloadRecordingLink = document.getElementById("downloadRecordingLink");

let mediaRecorder = null;
let recordedChunks = [];

recordBtn.addEventListener("click", async () => {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    mediaRecorder = new MediaRecorder(stream);
    recordedChunks = [];

    mediaRecorder.ondataavailable = (event) => {
      recordedChunks.push(event.data);
    };

    mediaRecorder.onstop = () => {
      const blob = new Blob(recordedChunks, { type: "audio/webm" });
      const url = URL.createObjectURL(blob);
      recordPlayback.src = url;
      recordPlayback.classList.remove("hidden");
      downloadRecordingLink.href = url;
      downloadRecordingLink.classList.remove("hidden");
      recordStatus.textContent = "Recording saved locally. Download if needed.";
    };

    mediaRecorder.start();
    recordBtn.disabled = true;
    stopRecordBtn.disabled = false;
    recordStatus.textContent = "Recording... (privacy mode)";
  } catch (err) {
    recordStatus.textContent = "Microphone access denied: " + err.message;
  }
});

stopRecordBtn.addEventListener("click", () => {
  if (mediaRecorder) {
    mediaRecorder.stop();
    recordBtn.disabled = false;
    stopRecordBtn.disabled = true;
  }
});

// ---------------------------------------------------------------------------
// Recent Alerts
// ---------------------------------------------------------------------------
const alertsList = document.getElementById("alertsList");

async function loadAlerts() {
  const rows = await api("/api/alerts");
  alertsList.innerHTML = rows
    .map(
      (r) =>
        `<li><strong>${r.trigger_type}</strong> - ${r.message} <span style="font-size:11px; color:#888;">${new Date(r.created_at).toLocaleString()}</span></li>`
    )
    .join("");
}

loadAlerts();

// ---------------------------------------------------------------------------
// FEATURE 2: Predictive Risk Alert (check location when sharing)
// ---------------------------------------------------------------------------
// Called when user shares location in Guardian tab.
// Returns proactive risk warning if near low-safety area.
async function checkLocationRisk(lat, lng) {
  try {
    const result = await api("/api/check-location-risk", {
      method: "POST",
      body: JSON.stringify({ latitude: lat, longitude: lng }),
    });

    if (result.risk_detected) {
      showNotification(
        "⚠️ HIGH-RISK AREA",
        `${result.area} (Safety Score: ${result.score}/100)\nConsider a different route.`,
        "warning"
      );
      console.log("Risk details:", result);
    }
  } catch (err) {
    console.error("Error checking location risk:", err);
  }
}

// ---------------------------------------------------------------------------
// Map (Safety Score Map)
// ---------------------------------------------------------------------------
let leafletMap = null;
let markers = [];
let selectedLocation = null;

function initMap() {
  leafletMap = L.map("leafletMap").setView([28.7041, 77.1025], 12); // Default: Delhi
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "© OpenStreetMap contributors",
    maxZoom: 19,
  }).addTo(leafletMap);

  leafletMap.on("click", (e) => {
    selectedLocation = e.latlng;
    if (markers.length > 0) {
      leafletMap.removeLayer(markers[markers.length - 1]);
      markers.pop();
    }
    const marker = L.circleMarker([e.latlng.lat, e.latlng.lng], {
      radius: 8,
      fillColor: "#667eea",
      color: "#fff",
      weight: 2,
      opacity: 1,
      fillOpacity: 0.8,
    }).addTo(leafletMap);
    markers.push(marker);

    // Show audit modal for this location
    setTimeout(showAuditModal, 300);
  });

  loadAndDisplayAudits();
}

async function loadAndDisplayAudits() {
  const audits = await api("/api/audits");
  markers.forEach((m) => leafletMap.removeLayer(m));
  markers = [];

  audits.forEach((audit) => {
    const color = scoreColor(audit.overall_score);
    const marker = L.circleMarker([audit.latitude, audit.longitude], {
      radius: 6,
      fillColor: color,
      color: "#fff",
      weight: 1,
      opacity: 1,
      fillOpacity: 0.7,
    })
      .addTo(leafletMap)
      .bindPopup(
        `<strong>${audit.area_name}</strong><br/>Score: ${audit.overall_score}/100<br/>Timestamp: ${new Date(audit.created_at).toLocaleString()}`
      );
    markers.push(marker);
  });
}

// Audit modal
const auditModal = document.getElementById("auditModal");
const submitAuditBtn = document.getElementById("submitAuditBtn");
const cancelAuditBtn = document.getElementById("cancelAuditBtn");

function showAuditModal() {
  auditModal.classList.remove("hidden");
}

cancelAuditBtn.addEventListener("click", () => {
  auditModal.classList.add("hidden");
});

submitAuditBtn.addEventListener("click", async () => {
  if (!selectedLocation) return;

  const data = {
    latitude: selectedLocation.lat,
    longitude: selectedLocation.lng,
    area_name: document.getElementById("auditAreaName").value,
    lighting: parseInt(document.getElementById("s_lighting").value),
    openness: parseInt(document.getElementById("s_openness").value),
    walkpath: parseInt(document.getElementById("s_walkpath").value),
    security: parseInt(document.getElementById("s_security").value),
    transport: parseInt(document.getElementById("s_transport").value),
    crowd: parseInt(document.getElementById("s_crowd").value),
    comment: document.getElementById("auditComment").value,
  };

  const result = await api("/api/audits", {
    method: "POST",
    body: JSON.stringify(data),
  });

  auditModal.classList.add("hidden");
  showNotification("✓ Audit Saved", `Safety Score: ${result.overall_score}/100`, "info");
  loadAndDisplayAudits();

  // Clear form
  document.getElementById("auditAreaName").value = "";
  document.getElementById("auditComment").value = "";
});

// Route safety check
const originInput = document.getElementById("originInput");
const destInput = document.getElementById("destInput");
const routeCheckBtn = document.getElementById("routeCheckBtn");
const routeResult = document.getElementById("routeResult");

routeCheckBtn.addEventListener("click", async () => {
  const origin = originInput.value.trim();
  const destination = destInput.value.trim();
  if (!origin || !destination) {
    routeResult.textContent = "Please enter both origin and destination.";
    return;
  }

  routeResult.textContent = "Checking route safety...";
  const result = await api("/api/route-safety", {
    method: "POST",
    body: JSON.stringify({ origin, destination }),
  });

  routeResult.innerHTML = `
    <strong>Route Safety Assessment:</strong><br/>
    Distance: ${result.distance} km<br/>
    Estimated Safety Score: ${result.estimated_score}/100<br/>
    Status: <span style="color: ${result.estimated_score >= 75 ? "#2dd4bf" : result.estimated_score >= 45 ? "#ffb020" : "#ff4757"}">
      ${result.estimated_score >= 75 ? "Safe" : result.estimated_score >= 45 ? "Caution" : "High Risk"}
    </span>
  `;
});

// Initialize map when tab loads
setTimeout(initMap, 100);

// ---------------------------------------------------------------------------
// Directory / Nearby Services
// ---------------------------------------------------------------------------
const servicesList = document.getElementById("servicesList");
const filterBtns = document.querySelectorAll(".filter-btn");
let currentServiceFilter = "";

filterBtns.forEach((btn) => {
  btn.addEventListener("click", () => {
    filterBtns.forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    currentServiceFilter = btn.dataset.type;
    loadServices(currentServiceFilter);
  });
});

async function loadServices(serviceType) {
  const loc = await getLocation();
  if (!loc.latitude || !loc.longitude) {
    servicesList.innerHTML = '<li>Location not available</li>';
    return;
  }

  const res = await fetch(
    `/api/nearby-services?lat=${loc.latitude}&lng=${loc.longitude}${serviceType ? "&type=" + serviceType : ""}`
  );
  const results = await res.json();

  servicesList.innerHTML = results
    .map(
      (s) =>
        `<li><strong>${s.name}</strong> (${s.type})<br/><span style="font-size:11px; color:#888;">${s.address}</span></li>`
    )
    .join("");
}

// ---------------------------------------------------------------------------
// Guardian / Bubble Location Sharing
// ---------------------------------------------------------------------------
let bubbleMap = null;
const shareLocationBtn = document.getElementById("shareLocationBtn");
const stopSharingBtn = document.getElementById("stopSharingBtn");
const guardianStatus = document.getElementById("guardianStatus");
const contactName = document.getElementById("contactName");
const contactPhone = document.getElementById("contactPhone");
const contactRelation = document.getElementById("contactRelation");
const addContactBtn = document.getElementById("addContactBtn");
const contactsList = document.getElementById("contactsList");

function initBubbleMap() {
  bubbleMap = L.map("bubbleMap").setView([28.7041, 77.1025], 12);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "© OpenStreetMap contributors",
  }).addTo(bubbleMap);
}

async function loadContacts() {
  const contacts = await api("/api/contacts");
  contactsList.innerHTML = contacts
    .map(
      (c) =>
        `<li><strong>${c.name}</strong> (${c.relation || "contact"})<br/><span style="font-size:11px; color:#888;">${c.phone}</span>
        <button style="float:right; padding:4px 8px; font-size:11px; cursor:pointer;" onclick="deleteContact(${c.id})">Delete</button></li>`
    )
    .join("");
}

window.deleteContact = async (id) => {
  await api(`/api/contacts/${id}`, { method: "DELETE" });
  loadContacts();
};

addContactBtn.addEventListener("click", async () => {
  const name = contactName.value.trim();
  const phone = contactPhone.value.trim();
  const relation = contactRelation.value.trim();

  if (!name || !phone) {
    alert("Name and phone are required");
    return;
  }

  await api("/api/contacts", {
    method: "POST",
    body: JSON.stringify({ name, phone, relation }),
  });

  contactName.value = "";
  contactPhone.value = "";
  contactRelation.value = "";
  loadContacts();
});

shareLocationBtn.addEventListener("click", async () => {
  const loc = await getLocation();
  if (!loc.latitude || !loc.longitude) {
    guardianStatus.textContent = "Location not available";
    return;
  }

  await api("/api/guardian/share", {
    method: "POST",
    body: JSON.stringify(loc),
  });

  guardianStatus.textContent = "✓ Sharing active";
  shareLocationBtn.classList.add("hidden");
  stopSharingBtn.classList.remove("hidden");

  // FEATURE 2: Check for risk when sharing location
  checkLocationRisk(loc.latitude, loc.longitude);

  // Update map
  if (bubbleMap) {
    const marker = L.circleMarker([loc.latitude, loc.longitude], {
      radius: 10,
      fillColor: "#2dd4bf",
      color: "#fff",
      weight: 2,
      opacity: 1,
      fillOpacity: 0.8,
    }).addTo(bubbleMap);
    bubbleMap.setView([loc.latitude, loc.longitude], 14);
  }
});

stopSharingBtn.addEventListener("click", async () => {
  await api("/api/guardian/stop", { method: "POST" });
  guardianStatus.textContent = "Sharing stopped";
  shareLocationBtn.classList.remove("hidden");
  stopSharingBtn.classList.add("hidden");
});

// ---------------------------------------------------------------------------
// TIER 2 FEATURE: Live Location Tracking (continuous, not one-time)
// ---------------------------------------------------------------------------
const startTrackingBtn = document.getElementById("startTrackingBtn");
const stopTrackingBtn = document.getElementById("stopTrackingBtn");
const trackingStatus = document.getElementById("trackingStatus");

let trackingIntervalId = null;
let breadcrumbTrail = []; // [ [lat, lng], ... ]
let breadcrumbPolyline = null;
let liveDotMarker = null;

function drawBreadcrumb() {
  if (!bubbleMap || breadcrumbTrail.length === 0) return;

  if (breadcrumbPolyline) bubbleMap.removeLayer(breadcrumbPolyline);
  breadcrumbPolyline = L.polyline(breadcrumbTrail, { color: "#667eea", weight: 3, opacity: 0.7 }).addTo(bubbleMap);

  const latest = breadcrumbTrail[breadcrumbTrail.length - 1];
  if (liveDotMarker) bubbleMap.removeLayer(liveDotMarker);
  liveDotMarker = L.circleMarker(latest, {
    radius: 8,
    fillColor: "#2dd4bf",
    color: "#fff",
    weight: 2,
    opacity: 1,
    fillOpacity: 0.9,
  }).addTo(bubbleMap);
  bubbleMap.setView(latest, 15);
}

async function sendLocationUpdate() {
  const loc = await getLocation();
  if (loc.latitude == null || loc.longitude == null) return;

  breadcrumbTrail.push([loc.latitude, loc.longitude]);
  if (breadcrumbTrail.length > 50) breadcrumbTrail.shift();
  drawBreadcrumb();

  if (socket && socket.connected) {
    socket.emit("location_update", { latitude: loc.latitude, longitude: loc.longitude });
  }
}

startTrackingBtn.addEventListener("click", async () => {
  await api("/api/tracking/start", { method: "POST" });
  trackingStatus.textContent = "✓ Live tracking active — updating every 10s";
  startTrackingBtn.classList.add("hidden");
  stopTrackingBtn.classList.remove("hidden");

  sendLocationUpdate(); // send one immediately
  trackingIntervalId = setInterval(sendLocationUpdate, 10000);
});

stopTrackingBtn.addEventListener("click", async () => {
  await api("/api/tracking/stop", { method: "POST" });
  trackingStatus.textContent = "Live tracking stopped";
  startTrackingBtn.classList.remove("hidden");
  stopTrackingBtn.classList.add("hidden");

  if (trackingIntervalId) {
    clearInterval(trackingIntervalId);
    trackingIntervalId = null;
  }
});

// A watcher (e.g. this same account viewing on another device, or a future
// contact-account feature) can join a user's tracking room to get updates:
// socket.emit("join_tracking", { user_id: <id> });
socket.on("contact_location_update", (data) => {
  breadcrumbTrail.push([data.latitude, data.longitude]);
  if (breadcrumbTrail.length > 50) breadcrumbTrail.shift();
  drawBreadcrumb();
});

// Initialize Guardian map
setTimeout(() => {
  initBubbleMap();
  loadContacts();
  loadLinkedContacts();
}, 100);

// ---------------------------------------------------------------------------
// TIER 3 PART 1: Bubble members (linked SafeHer accounts) + live tracking
// ---------------------------------------------------------------------------
const inviteEmailInput = document.getElementById("inviteEmailInput");
const sendInviteBtn = document.getElementById("sendInviteBtn");
const incomingInvitesList = document.getElementById("incomingInvitesList");
const canTrackMeList = document.getElementById("canTrackMeList");
const trackableSelect = document.getElementById("trackableSelect");
const startTrackingBtn = document.getElementById("startTrackingBtn");
const stopTrackingBtn = document.getElementById("stopTrackingBtn");

let currentlyTrackingUserId = null;
let trackedMarker = null;

async function loadLinkedContacts() {
  if (!incomingInvitesList) return; // Guardian tab markup not present on this page

  const data = await api("/api/contacts/linked");
  if (!data._ok) return;

  const incoming = (data.people_i_can_track || []).filter((r) => r.status === "pending");
  const accepted = (data.people_i_can_track || []).filter((r) => r.status === "accepted");
  const viewers = data.people_who_can_track_me || [];

  incomingInvitesList.innerHTML = incoming.length
    ? incoming
        .map(
          (r) => `<li>${r.owner_email} invited you to their Bubble
            <button class="btn" style="padding:2px 8px;font-size:11px;" onclick="respondToInvite(${r.id}, true)">Accept</button>
            <button class="btn secondary" style="padding:2px 8px;font-size:11px;" onclick="respondToInvite(${r.id}, false)">Decline</button></li>`
        )
        .join("")
    : `<li class="muted">No pending invites</li>`;

  canTrackMeList.innerHTML = viewers.length
    ? viewers
        .map((r) => `<li>${r.contact_email} <span class="muted" style="font-size:11px;">(${r.status})</span></li>`)
        .join("")
    : `<li class="muted">No one can see your live location yet</li>`;

  trackableSelect.innerHTML = accepted.length
    ? accepted.map((r) => `<option value="${r.owner_user_id}">${r.owner_email}</option>`).join("")
    : `<option value="">No accepted Bubble members yet</option>`;
  startTrackingBtn.disabled = accepted.length === 0;
}

window.respondToInvite = async (inviteId, accept) => {
  await api(`/api/contacts/invite/${inviteId}/${accept ? "accept" : "decline"}`, { method: "POST" });
  loadLinkedContacts();
};

sendInviteBtn?.addEventListener("click", async () => {
  const email = inviteEmailInput.value.trim();
  if (!email) {
    showNotification("⚠️ Email required", "Enter the SafeHer account email you want to invite.", "warning");
    return;
  }
  const result = await api("/api/contacts/invite", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
  if (result._ok) {
    showNotification("✅ Invite sent", `Bubble invite sent to ${email}.`, "info");
    inviteEmailInput.value = "";
    loadLinkedContacts();
  }
});

startTrackingBtn?.addEventListener("click", () => {
  const targetUserId = parseInt(trackableSelect.value, 10);
  if (!targetUserId) return;
  currentlyTrackingUserId = targetUserId;
  socket.emit("join_tracking", { user_id: targetUserId });
  startTrackingBtn.classList.add("hidden");
  stopTrackingBtn.classList.remove("hidden");
});

stopTrackingBtn?.addEventListener("click", () => {
  stopTrackingUI();
});

function stopTrackingUI() {
  if (currentlyTrackingUserId) {
    socket.emit("leave_tracking", { user_id: currentlyTrackingUserId });
  }
  currentlyTrackingUserId = null;
  if (trackedMarker && bubbleMap) {
    bubbleMap.removeLayer(trackedMarker);
    trackedMarker = null;
  }
  startTrackingBtn?.classList.remove("hidden");
  stopTrackingBtn?.classList.add("hidden");
}

function updateTrackedLocationOnMap(data) {
  if (!bubbleMap || data.user_id !== currentlyTrackingUserId) return;
  if (data.latitude == null || data.longitude == null) return;

  if (trackedMarker) {
    trackedMarker.setLatLng([data.latitude, data.longitude]);
  } else {
    trackedMarker = L.circleMarker([data.latitude, data.longitude], {
      radius: 10,
      fillColor: "#ff4757",
      color: "#fff",
      weight: 2,
      opacity: 1,
      fillOpacity: 0.8,
    }).addTo(bubbleMap);
  }
  bubbleMap.setView([data.latitude, data.longitude], 14);
}

// ---------------------------------------------------------------------------
// Community Safety Feed
// ---------------------------------------------------------------------------
const feedPostType = document.getElementById("feedPostType");
const feedAreaName = document.getElementById("feedAreaName");
const feedMessage = document.getElementById("feedMessage");
const postFeedBtn = document.getElementById("postFeedBtn");
const feedList = document.getElementById("feedList");

async function loadFeed() {
  const posts = await api("/api/feed");
  feedList.innerHTML = posts
    .map(
      (p) =>
        `<li>
      <strong>${p.post_type.toUpperCase()}</strong> - ${p.message}
      ${p.area_name ? `<br/><span style="font-size:11px; color:#888;">📍 ${p.area_name}</span>` : ""}
      <span style="font-size:11px; color:#888; float:right;">${new Date(p.created_at).toLocaleString()}</span>
    </li>`
    )
    .join("");
}

postFeedBtn.addEventListener("click", async () => {
  const message = feedMessage.value.trim();
  if (!message) {
    alert("Please write a message");
    return;
  }

  const loc = await getLocation();
  await api("/api/feed", {
    method: "POST",
    body: JSON.stringify({
      message,
      post_type: feedPostType.value,
      area_name: feedAreaName.value,
      latitude: loc.latitude,
      longitude: loc.longitude,
    }),
  });

  feedMessage.value = "";
  feedAreaName.value = "";
  loadFeed();
});

loadFeed();

// ---------------------------------------------------------------------------
// TIER 2 FEATURE: 2FA setup UI (account settings card on Home tab)
// ---------------------------------------------------------------------------
const setup2faBtn = document.getElementById("setup2faBtn");
const confirm2faBtn = document.getElementById("confirm2faBtn");
const disable2faBtn = document.getElementById("disable2faBtn");
const twoFaStatus = document.getElementById("twoFaStatus");
const twoFaDisabledView = document.getElementById("twoFaDisabledView");
const twoFaSetupView = document.getElementById("twoFaSetupView");
const twoFaEnabledView = document.getElementById("twoFaEnabledView");
const twoFaQr = document.getElementById("twoFaQr");
const twoFaConfirmCode = document.getElementById("twoFaConfirmCode");
const twoFaDisablePassword = document.getElementById("twoFaDisablePassword");

function show2faView(view) {
  twoFaDisabledView.classList.add("hidden");
  twoFaSetupView.classList.add("hidden");
  twoFaEnabledView.classList.add("hidden");
  view.classList.remove("hidden");
}

async function refresh2faStatus() {
  const res = await api("/api/2fa/status");
  show2faView(res.enabled ? twoFaEnabledView : twoFaDisabledView);
}

setup2faBtn.addEventListener("click", async () => {
  const res = await api("/api/2fa/setup", { method: "POST" });
  twoFaQr.src = res.qr_code;
  twoFaStatus.textContent = "Scan the QR code, then confirm with a code.";
  show2faView(twoFaSetupView);
});

confirm2faBtn.addEventListener("click", async () => {
  const code = twoFaConfirmCode.value.trim();
  if (!code) return;

  const res = await fetch("/api/2fa/confirm", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code }),
  });
  const data = await res.json();

  if (res.ok) {
    twoFaStatus.textContent = "✓ 2FA enabled.";
    show2faView(twoFaEnabledView);
  } else {
    twoFaStatus.textContent = "✗ " + (data.error || "invalid code");
  }
});

disable2faBtn.addEventListener("click", async () => {
  const password = twoFaDisablePassword.value;
  if (!password) return;

  const res = await fetch("/api/2fa/disable", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
  const data = await res.json();

  if (res.ok) {
    twoFaStatus.textContent = "2FA disabled.";
    twoFaDisablePassword.value = "";
    show2faView(twoFaDisabledView);
  } else {
    twoFaStatus.textContent = "✗ " + (data.error || "could not disable 2FA");
  }
});

refresh2faStatus();

// ---------------------------------------------------------------------------
// TIER 2 FEATURE: Offline-first — banner, action queue, auto-sync
// ---------------------------------------------------------------------------
const OFFLINE_QUEUE_KEY = "safeher_offline_queue";
const offlineBanner = document.getElementById("offlineBanner");

function queueOfflineAction(type, payload) {
  const queue = JSON.parse(localStorage.getItem(OFFLINE_QUEUE_KEY) || "[]");
  queue.push({ type, payload, queued_at: new Date().toISOString() });
  localStorage.setItem(OFFLINE_QUEUE_KEY, JSON.stringify(queue));
}

async function syncOfflineQueue() {
  const queue = JSON.parse(localStorage.getItem(OFFLINE_QUEUE_KEY) || "[]");
  if (queue.length === 0) return;

  try {
    await api("/api/offline-actions", {
      method: "POST",
      body: JSON.stringify({ actions: queue }),
    });
    localStorage.removeItem(OFFLINE_QUEUE_KEY);
    console.log(`Synced ${queue.length} queued offline action(s).`);
  } catch (err) {
    console.warn("Offline queue sync failed, will retry next time we're online.", err);
  }
}

function updateOfflineBanner() {
  if (navigator.onLine) {
    offlineBanner.classList.add("hidden");
  } else {
    offlineBanner.classList.remove("hidden");
  }
}

window.addEventListener("offline", updateOfflineBanner);
window.addEventListener("online", () => {
  updateOfflineBanner();
  syncOfflineQueue();
});

updateOfflineBanner();
syncOfflineQueue(); // in case there were queued actions from a previous offline session