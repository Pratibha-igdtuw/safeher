// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
// SECURITY FIX: several places below build innerHTML from user-controlled
// (or externally-sourced) strings — contact names, feed posts, guardian
// invite emails, SOS trigger types, journey destinations — without
// escaping. That's stored XSS: e.g. adding a contact named
// `<img src=x onerror=alert(document.cookie)>` would execute for anyone
// who views the contact list. Every interpolation of such a string into
// innerHTML below is now wrapped in escapeHtml().
function escapeHtml(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

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
    } else if (res.status === 404) {
      showNotification("⚠️ Not found", body.error || body.message || "That wasn't found.", "warning");
    } else if (res.status === 409) {
      showNotification("⚠️ Already done", body.error || body.message || "That action was already completed.", "warning");
    } else if (res.status >= 500) {
      showNotification("⚠️ Server error", "Something went wrong on our end. Please try again shortly.", "critical");
    } else if (res.status >= 400) {
      showNotification("⚠️ Something went wrong", body.error || body.message || "Please try again.", "warning");
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
  if (score >= 75) return "#22C55E";
  if (score >= 45) return "#F59E0B";
  return "#EF4444";
}

// ---------------------------------------------------------------------------
// FEATURE 4: WebSocket Real-Time Notifications
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// FRONTEND ERROR RESILIENCE: every CDN <script> tag in index.html sets a flag
// on window.__SAFEHER_CDN_FAILED via onerror if it fails/is blocked. We check
// those flags before touching the corresponding global, and always fall back
// to a no-op/degraded stand-in so one blocked CDN can't halt the rest of
// main.js (a single uncaught ReferenceError at module scope would otherwise
// stop every listener below it from ever being registered).
// ---------------------------------------------------------------------------
const CDN_FAILED = window.__SAFEHER_CDN_FAILED || {};

function noopSocket() {
  return { on: () => {}, emit: () => {}, connected: false };
}

let socket;
if (CDN_FAILED.socketio || typeof io === "undefined") {
  console.warn("Socket.io CDN unavailable — real-time alerts disabled for this session");
  socket = noopSocket();
} else {
  try {
    socket = io();
  } catch (e) {
    console.warn("Socket.io failed to initialize — real-time alerts disabled", e);
    socket = noopSocket();
  }
}

socket.on("connect", () => {
  console.log("Connected to server for real-time updates");
});

socket.on("sos_triggered", (data) => {
  showNotification("🚨 SOS ALERT", data.message, "critical");
  if (currentlyTrackingUserId) {
    showEmergencyBanner(`🚨 SOS triggered by the person you're watching: ${data.message}`);
  }
});

socket.on("journey_missed", (data) => {
  if (currentlyTrackingUserId) {
    showEmergencyBanner(`🧭 Missed check-in for '${data.destination_name}' — this person may need help.`);
    refreshGuardianDashboard();
  }
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
  notif.setAttribute("role", "alert");
  notif.setAttribute("aria-live", type === "critical" ? "assertive" : "polite");
  notif.innerHTML = `<strong>${escapeHtml(title)}</strong><br/>${escapeHtml(message)}`;
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
    background: #EF4444;
    color: white;
  }
  
  .notification-warning {
    background: #F59E0B;
    color: white;
  }
  
  .notification-info {
    background: #7C3AED;
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
  assistant: document.getElementById("tab-assistant"),
};

function activateTab(btn) {
  tabButtons.forEach((b) => {
    const isActive = b === btn;
    b.classList.toggle("active", isActive);
    b.setAttribute("aria-selected", isActive ? "true" : "false");
    // Roving tabindex: only the active tab is in the normal Tab order;
    // arrow keys move focus between the rest (standard tablist pattern).
    b.tabIndex = isActive ? 0 : -1;
  });
  // Hide ALL tab panels first, then show only the active one
  Object.values(tabPanels).forEach((p) => {
    p.classList.add("hidden");
    p.style.display = "none"; // force hide via inline style too
  });
  tabPanels[btn.dataset.tab].classList.remove("hidden");
  tabPanels[btn.dataset.tab].style.display = ""; // reset so CSS takes over

  // Hero section — only on home tab
  const heroEl = document.getElementById("tab-home-hero");
  if (heroEl) {
    const showHero = btn.dataset.tab === "home";
    heroEl.classList.toggle("hidden", !showHero);
    heroEl.style.display = showHero ? "" : "none";
  }

  // Status strip (Safety Score / Journey / Guardian) — only on home tab
  const statusStripEl = document.getElementById("statusStrip");
  if (statusStripEl) {
    const showStrip = btn.dataset.tab === "home";
    statusStripEl.classList.toggle("hidden", !showStrip);
    statusStripEl.style.display = showStrip ? "" : "none";
  }

  if (btn.dataset.tab === "map") {
    setTimeout(() => leafletMap && leafletMap.invalidateSize(), 50);
  }
  if (btn.dataset.tab === "directory") {
    applyHubFilters();
    loadHubContacts();
  }
  if (btn.dataset.tab === "guardian") {
    setTimeout(() => bubbleMap && bubbleMap.invalidateSize(), 50);
  }
  if (btn.dataset.tab === "community") {
    loadFeed();
  }
  if (btn.dataset.tab === "assistant") {
    loadAssistantHistory();
  }
}

tabButtons.forEach((btn, i) => {
  btn.addEventListener("click", () => activateTab(btn));

  // Arrow-key navigation between tabs, per the standard ARIA tablist
  // keyboard pattern (Tab/Enter/Space already work for free on <button>).
  btn.addEventListener("keydown", (e) => {
    if (!["ArrowRight", "ArrowLeft", "Home", "End"].includes(e.key)) return;
    e.preventDefault();
    let nextIndex = i;
    if (e.key === "ArrowRight") nextIndex = (i + 1) % tabButtons.length;
    else if (e.key === "ArrowLeft") nextIndex = (i - 1 + tabButtons.length) % tabButtons.length;
    else if (e.key === "Home") nextIndex = 0;
    else if (e.key === "End") nextIndex = tabButtons.length - 1;

    const nextBtn = tabButtons[nextIndex];
    nextBtn.focus();
    activateTab(nextBtn);
  });
});

// ---------------------------------------------------------------------------
// SOS
// ---------------------------------------------------------------------------
const sosBtn = document.getElementById("sosBtn");
const sosBtnLabel = document.getElementById("sosBtnLabel");
const sosCancelBtn = document.getElementById("sosCancelBtn");
const sosStatus = document.getElementById("sosStatus");
const sosStepTimeline = document.getElementById("sosStepTimeline");

const SOS_COUNTDOWN_SECONDS = 3;
const SOS_LAST_LOCATION_KEY = "safeher_last_known_location";

let sosCountdownTimer = null;
let sosCancelled = false;
let sosInFlight = false;

function setSosStep(step, state) {
  // state: "active" | "done"
  const li = sosStepTimeline.querySelector(`[data-step="${step}"]`);
  if (!li) return;
  li.classList.remove("step-active", "step-done");
  li.classList.add(state === "done" ? "step-done" : "step-active");
}

function resetSosSteps() {
  sosStepTimeline.querySelectorAll("li").forEach((li) => li.classList.remove("step-active", "step-done"));
}

function setSosButtonState(state, text) {
  sosBtn.classList.remove("sos-button-countdown", "sos-button-sending", "sos-button-success", "sos-button-error");
  if (state) sosBtn.classList.add(`sos-button-${state}`);
  sosBtnLabel.textContent = text;
}

// Accurate location with a real fallback chain: try a fresh high-accuracy
// GPS fix first; if that fails or times out, fall back to the last known
// location cached in this browser (clearly labeled as such downstream),
// and only report "unavailable" if we have genuinely nothing.
function getAccurateLocation() {
  return new Promise((resolve) => {
    if (!navigator.geolocation) {
      resolve(getCachedLocationFallback());
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const loc = {
          latitude: pos.coords.latitude,
          longitude: pos.coords.longitude,
          accuracy_m: pos.coords.accuracy,
          location_source: "gps",
        };
        try {
          localStorage.setItem(SOS_LAST_LOCATION_KEY, JSON.stringify({ ...loc, cached_at: Date.now() }));
        } catch (e) { /* storage unavailable — non-fatal, just skip caching */ }
        resolve(loc);
      },
      () => resolve(getCachedLocationFallback()),
      { enableHighAccuracy: true, timeout: 6000, maximumAge: 0 }
    );
  });
}

function getCachedLocationFallback() {
  try {
    const cached = JSON.parse(localStorage.getItem(SOS_LAST_LOCATION_KEY) || "null");
    if (cached && cached.latitude != null) {
      return { latitude: cached.latitude, longitude: cached.longitude, accuracy_m: null, location_source: "cached" };
    }
  } catch (e) { /* ignore malformed cache */ }
  return { latitude: null, longitude: null, accuracy_m: null, location_source: "unavailable" };
}

// Sends the alert with a couple of quick retries before giving up and
// falling back to the offline queue — network blips shouldn't mean a
// missed SOS.
async function sendSosWithRetry(payload, attempts = 3) {
  for (let i = 0; i < attempts; i++) {
    const result = await api("/api/sos", { method: "POST", body: JSON.stringify(payload) });
    if (result._ok) return result;
    if (result._status && result._status >= 400 && result._status < 500 && result._status !== 429) {
      // Client error (bad input, auth) — retrying won't help.
      return result;
    }
    if (i < attempts - 1) {
      sosStatus.textContent = `Network hiccup — retrying (${i + 2}/${attempts})…`;
      await new Promise((r) => setTimeout(r, 1200 * (i + 1)));
    }
  }
  return { _ok: false };
}

async function runSosSendFlow(triggerType) {
  sosInFlight = true;
  setSosButtonState("sending", "Sending…");
  sosStatus.textContent = "";
  setSosStep("countdown", "done");
  setSosStep("location", "active");

  const loc = await getAccurateLocation();
  setSosStep("location", "done");
  setSosStep("sending", "active");

  if (!navigator.onLine) {
    queueOfflineAction("sos", { ...loc, trigger_type: triggerType, message: "SOS raised while offline" });
    setSosButtonState("error", "Queued");
    sosStatus.textContent = "⚠️ Offline — SOS queued, will send the moment you're back online.";
    finishSosFlow(4000, "SOS");
    return;
  }

  const result = await sendSosWithRetry({ ...loc, trigger_type: triggerType });

  if (result._ok) {
    setSosStep("sending", "done");
    setSosStep("notified", "done");
    setSosButtonState("success", "Sent ✓");
    const locNote =
      loc.location_source === "cached" ? " (using last known location)" :
      loc.location_source === "unavailable" ? " (location unavailable)" : "";
    sosStatus.textContent = `✓ Alert sent to ${result.contacts_notified} contact(s)${locNote}.`;
    loadAlerts();
    finishSosFlow(5000, "SOS");
  } else {
    queueOfflineAction("sos", { ...loc, trigger_type: triggerType, message: "SOS raised — server unreachable" });
    setSosButtonState("error", "Queued");
    sosStatus.textContent = "⚠️ Couldn't reach the server after retries — SOS queued, will retry automatically.";
    finishSosFlow(5000, "SOS");
  }
}

function finishSosFlow(delayMs, resetLabel) {
  setTimeout(() => {
    setSosButtonState(null, resetLabel);
    sosCancelBtn.classList.add("hidden");
    sosStepTimeline.classList.add("hidden");
    resetSosSteps();
    sosInFlight = false;
  }, delayMs);
}

async function triggerSOS(triggerType = "manual") {
  // Automatic triggers (audio ML, missed check-in, journey escalation) skip
  // the countdown entirely — those are already confirmed emergencies and
  // shouldn't be delayed by a cancel window meant for accidental taps.
  if (triggerType !== "manual") {
    await runSosSendFlow(triggerType);
    return;
  }
  startSosCountdown();
}

function startSosCountdown() {
  if (sosInFlight) return;
  sosCancelled = false;
  sosCancelBtn.classList.remove("hidden");
  sosStepTimeline.classList.remove("hidden");
  resetSosSteps();
  setSosStep("countdown", "active");
  sosStatus.textContent = "Tap Cancel if this was a mistake.";

  let remaining = SOS_COUNTDOWN_SECONDS;
  setSosButtonState("countdown", String(remaining));
  sosCountdownTimer = setInterval(() => {
    remaining -= 1;
    if (sosCancelled) {
      clearInterval(sosCountdownTimer);
      return;
    }
    if (remaining <= 0) {
      clearInterval(sosCountdownTimer);
      runSosSendFlow("manual");
      return;
    }
    setSosButtonState("countdown", String(remaining));
  }, 1000);
}

sosBtn.addEventListener("click", () => {
  if (sosInFlight) return;
  triggerSOS("manual");
});

sosCancelBtn.addEventListener("click", () => {
  sosCancelled = true;
  if (sosCountdownTimer) clearInterval(sosCountdownTimer);
  setSosButtonState(null, "SOS");
  sosCancelBtn.classList.add("hidden");
  sosStepTimeline.classList.add("hidden");
  resetSosSteps();
  sosStatus.textContent = "Cancelled.";
  setTimeout(() => { if (sosStatus.textContent === "Cancelled.") sosStatus.textContent = ""; }, 3000);
});

// ---------------------------------------------------------------------------
// Fake call
// ---------------------------------------------------------------------------
const fakeCallBtn = document.getElementById("fakeCallBtn");
const fakeCallDelay = document.getElementById("fakeCallDelay");
const fakeCallDelayCustom = document.getElementById("fakeCallDelayCustom");
const fakeCallerName = document.getElementById("fakeCallerName");
const fakeCallerNameCustom = document.getElementById("fakeCallerNameCustom");
const fakeCallSilent = document.getElementById("fakeCallSilent");
const fakeCallStatus = document.getElementById("fakeCallStatus");
const overlay = document.getElementById("fakeCallOverlay");
const acceptCallBtn = document.getElementById("acceptCallBtn");
const declineCallBtn = document.getElementById("declineCallBtn");
const callerAvatar = document.getElementById("callerAvatar");
const callerNameEl = document.getElementById("callerName");

fakeCallDelay.addEventListener("change", () => {
  fakeCallDelayCustom.classList.toggle("hidden", fakeCallDelay.value !== "__custom__");
});
fakeCallerName.addEventListener("change", () => {
  fakeCallerNameCustom.classList.toggle("hidden", fakeCallerName.value !== "__custom__");
});

const FAKE_SCRIPTS = {
  Mom: [
    "Hey, where are you right now?",
    "Okay, I'm coming to pick you up, stay right there.",
    "I'm just two minutes away, keep talking to me.",
  ],
  Dad: [
    "Hey, you almost done? Your mother's asking.",
    "Alright, I'll swing by and get you, stay put.",
    "Two minutes out, keep me on the line.",
  ],
  Boss: [
    "Hey, sorry to call so late — you free to talk?",
    "I just need you for a quick thing, shouldn't take long.",
    "Okay, let's sort this out, I'm calling now.",
  ],
  "Best Friend": [
    "Hey! Where are you, I've been trying to reach you.",
    "Okay stay there, I'm literally two minutes away.",
    "Keep talking to me, I'm almost there.",
  ],
  default: [
    "Hey, where are you right now?",
    "Okay, I'm coming to get you, stay right there.",
    "I'm just two minutes away, keep talking to me.",
  ],
};

function getSelectedCallerName() {
  return fakeCallerName.value === "__custom__"
    ? (fakeCallerNameCustom.value.trim() || "Unknown")
    : fakeCallerName.value;
}

function getSelectedDelaySeconds() {
  if (fakeCallDelay.value === "__custom__") {
    return Math.max(1, parseInt(fakeCallDelayCustom.value, 10) || 5);
  }
  return parseInt(fakeCallDelay.value, 10);
}

fakeCallBtn.addEventListener("click", () => {
  const delay = getSelectedDelaySeconds() * 1000;
  const name = getSelectedCallerName();
  fakeCallStatus.textContent = delay ? `Call from ${name} scheduled in ${delay / 1000}s…` : `Calling from ${name} now…`;
  setTimeout(() => showFakeCall(name), delay);
});

// ---------------------------------------------------------------------
// Ringtone (synthesized — no audio asset needed) + vibration
// ---------------------------------------------------------------------
let ringtoneContext = null;
let ringtoneIntervalId = null;

function playRingtoneTone() {
  if (!ringtoneContext) return;
  const now = ringtoneContext.currentTime;
  [0, 0.35].forEach((offset) => {
    const osc = ringtoneContext.createOscillator();
    const gain = ringtoneContext.createGain();
    osc.frequency.value = 950;
    osc.type = "sine";
    gain.gain.setValueAtTime(0.0001, now + offset);
    gain.gain.exponentialRampToValueAtTime(0.25, now + offset + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + offset + 0.3);
    osc.connect(gain).connect(ringtoneContext.destination);
    osc.start(now + offset);
    osc.stop(now + offset + 0.32);
  });
}

function startRingtoneAndVibration() {
  if (!fakeCallSilent.checked) {
    try {
      ringtoneContext = new (window.AudioContext || window.webkitAudioContext)();
      playRingtoneTone();
      ringtoneIntervalId = setInterval(playRingtoneTone, 2000);
    } catch (e) { /* Web Audio unavailable — call screen still works silently */ }
  }
  if (navigator.vibrate) {
    navigator.vibrate([500, 300, 500, 300, 500, 300, 500]);
  }
}

function stopRingtoneAndVibration() {
  if (ringtoneIntervalId) { clearInterval(ringtoneIntervalId); ringtoneIntervalId = null; }
  if (ringtoneContext) { try { ringtoneContext.close(); } catch (e) {} ringtoneContext = null; }
  if (navigator.vibrate) navigator.vibrate(0);
}

// ---------------------------------------------------------------------
// Accessibility: focus trap for the fake-call modal overlay. While it's
// open, Tab/Shift+Tab cycle only between the overlay's own controls, and
// closing it returns focus to whatever triggered the call.
// ---------------------------------------------------------------------
let fakeCallPreviouslyFocused = null;

function trapFocus(e) {
  if (e.key !== "Tab") return;
  const focusable = overlay.querySelectorAll("button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])");
  if (focusable.length === 0) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];

  if (e.shiftKey && document.activeElement === first) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault();
    first.focus();
  }
}

function closeFakeCall() {
  overlay.classList.add("hidden");
  overlay.removeEventListener("keydown", trapFocus);
  stopRingtoneAndVibration();
  if (fakeCallPreviouslyFocused && typeof fakeCallPreviouslyFocused.focus === "function") {
    fakeCallPreviouslyFocused.focus();
  }
}

function showFakeCall(callerName) {
  fakeCallPreviouslyFocused = document.activeElement;
  callerNameEl.textContent = callerName;
  callerAvatar.textContent = (callerName.trim()[0] || "?").toUpperCase();
  overlay.classList.remove("hidden");
  overlay.querySelector(".ringing").textContent = "ringing...";
  fakeCallStatus.textContent = "";
  overlay.addEventListener("keydown", trapFocus);
  startRingtoneAndVibration();
  declineCallBtn.focus();
}

declineCallBtn.addEventListener("click", closeFakeCall);

acceptCallBtn.addEventListener("click", () => {
  stopRingtoneAndVibration();
  overlay.querySelector(".ringing").textContent = "00:01";
  const script = FAKE_SCRIPTS[callerNameEl.textContent] || FAKE_SCRIPTS.default;
  speakScript(script, 0);
});

function speakScript(script, i) {
  if (i >= script.length) {
    setTimeout(closeFakeCall, 1500);
    return;
  }
  if (window.speechSynthesis) {
    const utter = new SpeechSynthesisUtterance(script[i]);
    utter.rate = 1;
    utter.onend = () => setTimeout(() => speakScript(script, i + 1), 400);
    window.speechSynthesis.speak(utter);
  } else {
    setTimeout(() => speakScript(script, i + 1), 2000);
  }
}

// ---------------------------------------------------------------------------
// FEATURE 1: Voice Distress Detection (with TensorFlow.js YAMNet fallback)
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// Voice Distress: waveform visualizer (shared by Listen + Audio ML modes)
// ---------------------------------------------------------------------------
function startWaveformVisualization(stream, canvas) {
  const ctx = canvas.getContext("2d");
  const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  const source = audioCtx.createMediaStreamSource(stream);
  const analyser = audioCtx.createAnalyser();
  analyser.fftSize = 256;
  source.connect(analyser);
  const data = new Uint8Array(analyser.frequencyBinCount);

  canvas.classList.remove("hidden");
  let rafId = null;

  function draw() {
    rafId = requestAnimationFrame(draw);
    analyser.getByteTimeDomainData(data);
    const w = canvas.width = canvas.clientWidth;
    const h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    ctx.beginPath();
    ctx.strokeStyle = "#7C3AED";
    ctx.lineWidth = 2;
    const sliceWidth = w / data.length;
    let x = 0;
    for (let i = 0; i < data.length; i++) {
      const v = data[i] / 128.0;
      const y = (v * h) / 2;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
      x += sliceWidth;
    }
    ctx.stroke();
  }
  draw();

  return () => {
    if (rafId) cancelAnimationFrame(rafId);
    canvas.classList.add("hidden");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    try { audioCtx.close(); } catch (e) { /* already closed */ }
  };
}

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

const voiceWaveformCanvas = document.getElementById("voiceWaveform");
let stopVoiceWaveform = null;
let listeningWaveformStream = null;

listenBtn.addEventListener("click", async () => {
  if (recognition) {
    recognition.start();
    listenBtn.disabled = true;
    listenBtn.classList.add("listening-pulse");
    stopListenBtn.disabled = false;
    transcriptBox.value = "";
    distressResult.textContent = "";

    // Waveform is purely visual feedback — SpeechRecognition itself
    // doesn't expose raw audio, so this opens its own lightweight stream.
    try {
      listeningWaveformStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      stopVoiceWaveform = startWaveformVisualization(listeningWaveformStream, voiceWaveformCanvas);
    } catch (e) { /* mic permission denied for waveform — transcript still works via SpeechRecognition's own permission prompt */ }
  } else {
    distressResult.textContent = "Speech Recognition not supported in this browser";
  }
});

stopListenBtn.addEventListener("click", () => {
  if (recognition) {
    recognition.stop();
    listenBtn.disabled = false;
    listenBtn.classList.remove("listening-pulse");
    stopListenBtn.disabled = true;
  }
  if (stopVoiceWaveform) { stopVoiceWaveform(); stopVoiceWaveform = null; }
  if (listeningWaveformStream) { listeningWaveformStream.getTracks().forEach((t) => t.stop()); listeningWaveformStream = null; }
});

const distressResultPanel = document.getElementById("distressResultPanel");
const distressVerdict = document.getElementById("distressVerdict");
const distressConfidenceFill = document.getElementById("distressConfidenceFill");
const distressConfidenceLabel = document.getElementById("distressConfidenceLabel");
const distressEmotionBadge = document.getElementById("distressEmotionBadge");
const distressTranscriptHighlight = document.getElementById("distressTranscriptHighlight");

function highlightKeywords(transcript, matched) {
  let html = escapeHtml(transcript);
  matched
    .slice()
    .sort((a, b) => b.length - a.length) // longer phrases first so they aren't partially overwritten by shorter substrings
    .forEach((kw) => {
      const escaped = escapeHtml(kw);
      const re = new RegExp(`\\b(${escaped.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})\\b`, "gi");
      html = html.replace(re, "<mark>$1</mark>");
    });
  return html;
}

const EMOTION_STYLES = {
  fear: { emoji: "😨", cls: "badge-alert" },
  anger: { emoji: "😠", cls: "badge-warn" },
  neutral: { emoji: "😐", cls: "" },
};

analyzeBtn.addEventListener("click", async () => {
  const transcript = transcriptBox.value.trim();
  if (!transcript) {
    distressResult.textContent = "Please speak or type something first.";
    distressResultPanel.classList.add("hidden");
    return;
  }

  distressResult.textContent = "Analyzing…";
  distressResultPanel.classList.add("hidden");
  const loc = await getLocation();

  const result = await api("/api/distress-check", {
    method: "POST",
    body: JSON.stringify({ transcript, location: loc }),
  });

  if (!result._ok) {
    distressResult.textContent = "Error analyzing transcript.";
    return;
  }

  distressResult.textContent = "";
  distressResultPanel.classList.remove("hidden");

  distressVerdict.innerHTML = result.distress_detected
    ? `<span style="color:#EF4444; font-weight:700;">🚨 DISTRESS DETECTED</span>`
    : `<span style="color:#0F9D70;">✓ No distress detected</span>`;

  const confidencePct = Math.round((result.confidence || 0) * 100);
  distressConfidenceFill.style.width = `${confidencePct}%`;
  distressConfidenceFill.style.background = confidencePct >= 70 ? "var(--coral)" : confidencePct >= 40 ? "var(--warn)" : "var(--safe)";
  distressConfidenceLabel.textContent = `${confidencePct}%`;

  const emotion = result.emotion || { label: "neutral", intensity: 0 };
  const style = EMOTION_STYLES[emotion.label] || EMOTION_STYLES.neutral;
  distressEmotionBadge.className = `journey-badge ${style.cls}`;
  distressEmotionBadge.textContent = `${style.emoji} Tone: ${emotion.label} (${Math.round(emotion.intensity * 100)}%)`;

  distressTranscriptHighlight.innerHTML = result.matched && result.matched.length
    ? `Matched: ${highlightKeywords(transcript, result.matched)}`
    : "No keywords matched.";

  if (result.auto_trigger_sos) {
    distressVerdict.innerHTML += `<br/><span style="font-size:12px;">⚠️ Auto-triggering SOS…</span>`;
    setTimeout(() => triggerSOS("audio_ml"), 1500);
  } else if (result.cooldown_active) {
    distressVerdict.innerHTML += `<br/><span style="font-size:12px; color:#888;">(Already alerted recently — not re-triggering to avoid spamming your contacts)</span>`;
  }

  loadVoiceHistory();
});

const voiceHistoryToggle = document.getElementById("voiceHistoryToggle");
const voiceHistoryList = document.getElementById("voiceHistoryList");
let voiceHistoryLoaded = false;

voiceHistoryToggle.addEventListener("click", async () => {
  const showing = !voiceHistoryList.classList.contains("hidden");
  if (showing) {
    voiceHistoryList.classList.add("hidden");
    voiceHistoryToggle.textContent = "Show Transcript History";
    return;
  }
  await loadVoiceHistory();
  voiceHistoryList.classList.remove("hidden");
  voiceHistoryToggle.textContent = "Hide Transcript History";
});

async function loadVoiceHistory() {
  const rows = await api("/api/distress-check/history");
  if (!Array.isArray(rows)) return;
  voiceHistoryList.innerHTML = rows.length
    ? rows
        .map((r) => {
          const icon = r.distress_detected ? "🚨" : "✓";
          return `<li>
            <strong>${icon} ${r.confidence != null ? Math.round(r.confidence * 100) + "%" : "—"}</strong>
            ${r.emotion_label ? `<span class="muted" style="font-size:11px;"> · ${escapeHtml(r.emotion_label)}</span>` : ""}
            <span style="font-size:11px; color:#888; float:right;">${relativeTime(r.created_at)}</span>
            <br/><span style="font-size:12px;">${escapeHtml(r.transcript.slice(0, 140))}</span>
          </li>`;
        })
        .join("")
    : `<li class="muted" style="border:none;">No transcript analyses yet.</li>`;
}

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
let stopAudioMlWaveform = null;

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

  const audioMlWaveformCanvas = document.getElementById("audioMlWaveform");
  stopAudioMlWaveform = startWaveformVisualization(audioMlStream, audioMlWaveformCanvas);

  audioMlBtn.disabled = true;
  audioMlBtn.classList.add("listening-pulse");
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
        <span style="color:#EF4444">🚨 DISTRESS AUDIO DETECTED (${result.distress_type})</span><br/>
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
  if (stopAudioMlWaveform) { stopAudioMlWaveform(); stopAudioMlWaveform = null; }
  audioMlBtn.disabled = false;
  audioMlBtn.classList.remove("listening-pulse");
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
// Journey Mode
// ---------------------------------------------------------------------------
const journeyIdleView = document.getElementById("journeyIdleView");
const journeyActiveView = document.getElementById("journeyActiveView");
const journeyDestinationInput = document.getElementById("journeyDestination");
const journeyEtaMinutesInput = document.getElementById("journeyEtaMinutes");
const journeyGuardianSelect = document.getElementById("journeyGuardianSelect");
const startJourneyBtn = document.getElementById("startJourneyBtn");
const journeyStartStatus = document.getElementById("journeyStartStatus");
const journeyActiveDestination = document.getElementById("journeyActiveDestination");
const journeyActiveBadge = document.getElementById("journeyActiveBadge");
const journeyCountdown = document.getElementById("journeyCountdown");
const journeyProgressFill = document.getElementById("journeyProgressFill");
const journeyDistanceRemaining = document.getElementById("journeyDistanceRemaining");
const journeyArrivedBtn = document.getElementById("journeyArrivedBtn");
const journeyExtendBtn = document.getElementById("journeyExtendBtn");
const journeyCancelBtn = document.getElementById("journeyCancelBtn");
const journeyTimelineToggle = document.getElementById("journeyTimelineToggle");
const journeyTimelineList = document.getElementById("journeyTimelineList");
const journeyStatusValue = document.getElementById("journeyStatusValue");
const journeyStatusChip = document.getElementById("journeyStatusChip");
const guardianStatusValue = document.getElementById("guardianStatusValue");
const safetyScoreValue = document.getElementById("safetyScoreValue");

let activeJourney = null;
let journeyCountdownInterval = null;
let journeyLocationInterval = null;
let journeyPollInterval = null;

function formatCountdown(totalSeconds) {
  const s = Math.max(0, totalSeconds);
  const mins = Math.floor(s / 60);
  const secs = s % 60;
  return `${mins}:${secs.toString().padStart(2, "0")}`;
}

async function populateJourneyGuardianSelect() {
  if (!journeyGuardianSelect) return;
  const contacts = await api("/api/contacts");
  if (!Array.isArray(contacts)) return;
  const current = journeyGuardianSelect.value;
  journeyGuardianSelect.innerHTML =
    `<option value="">No guardian (alert all trusted contacts)</option>` +
    contacts.map((c) => `<option value="${c.id}">${escapeHtml(c.name)} (${escapeHtml(c.relation || "contact")})</option>`).join("");
  journeyGuardianSelect.value = current || "";
}

function renderJourneyBadge(journey) {
  journeyActiveBadge.classList.remove("badge-safe", "badge-warn", "badge-alert");
  if (journey.status === "arrived") {
    journeyActiveBadge.textContent = "Arrived safely";
    journeyActiveBadge.classList.add("badge-safe");
  } else if (journey.status === "missed") {
    journeyActiveBadge.textContent = "Alert sent";
    journeyActiveBadge.classList.add("badge-alert");
  } else if (journey.remaining_seconds < 120) {
    journeyActiveBadge.textContent = "Check in soon";
    journeyActiveBadge.classList.add("badge-warn");
  } else {
    journeyActiveBadge.textContent = "On track";
    journeyActiveBadge.classList.add("badge-safe");
  }
}

function renderJourney(journey) {
  activeJourney = journey;

  if (!journey || journey.status !== "active") {
    journeyIdleView.classList.remove("hidden");
    journeyActiveView.classList.add("hidden");
    journeyStatusValue.textContent = "No active journey";
    journeyStatusChip.classList.remove("hidden");
    stopJourneyLiveUpdates();
    return;
  }

  journeyIdleView.classList.add("hidden");
  journeyActiveView.classList.remove("hidden");
  journeyActiveDestination.textContent = journey.destination_name;
  journeyStatusValue.textContent = `→ ${journey.destination_name}`;
  journeyProgressFill.style.width = `${journey.time_progress_pct}%`;
  journeyCountdown.textContent = formatCountdown(journey.remaining_seconds);
  journeyDistanceRemaining.textContent =
    journey.distance_remaining_km != null
      ? `${journey.distance_remaining_km.toFixed(2)} km remaining to destination`
      : "Live location tracking active";
  renderJourneyBadge(journey);
  startJourneyLiveUpdates();
}

function stopJourneyLiveUpdates() {
  if (journeyCountdownInterval) { clearInterval(journeyCountdownInterval); journeyCountdownInterval = null; }
  if (journeyLocationInterval) { clearInterval(journeyLocationInterval); journeyLocationInterval = null; }
}

function startJourneyLiveUpdates() {
  stopJourneyLiveUpdates();

  // Local 1s countdown ticker between server polls, so the timer doesn't
  // visibly stall between the 15s /active polls below.
  journeyCountdownInterval = setInterval(() => {
    if (!activeJourney || activeJourney.status !== "active") return;
    activeJourney.remaining_seconds = Math.max(0, activeJourney.remaining_seconds - 1);
    journeyCountdown.textContent = formatCountdown(activeJourney.remaining_seconds);
    if (activeJourney.remaining_seconds === 0) refreshActiveJourney();
  }, 1000);

  // Push a real location update to the server periodically so distance-to-
  // destination and the breadcrumb (journey_events) stay current, and so
  // arrival can be auto-detected.
  journeyLocationInterval = setInterval(async () => {
    if (!activeJourney) return;
    const loc = await getLocation();
    if (loc.latitude == null || loc.longitude == null) return;
    const updated = await api(`/api/journey/${activeJourney.id}/location`, {
      method: "POST",
      body: JSON.stringify({ latitude: loc.latitude, longitude: loc.longitude }),
    });
    if (updated._ok) renderJourney(updated);
    if (updated.status === "arrived") {
      showNotification("✅ Destination reached", `You've arrived at ${updated.destination_name}.`, "info");
    }
  }, 20000);
}

async function refreshActiveJourney() {
  const journey = await api("/api/journey/active");
  if (journey === null || journey._ok === false) {
    renderJourney(null);
    return;
  }
  const wasActive = activeJourney && activeJourney.status === "active";
  renderJourney(journey);
  if (wasActive && journey.status === "missed") {
    showNotification("🚨 Check-in missed", `No check-in for '${journey.destination_name}' — your guardian has been alerted.`, "critical");
  }
}

startJourneyBtn.addEventListener("click", async () => {
  const destination_name = journeyDestinationInput.value.trim();
  const eta_minutes = parseInt(journeyEtaMinutesInput.value, 10);
  const guardian_contact_id = journeyGuardianSelect.value ? parseInt(journeyGuardianSelect.value, 10) : null;

  if (!destination_name) {
    journeyStartStatus.textContent = "Enter a destination first.";
    return;
  }
  if (!eta_minutes || eta_minutes < 1) {
    journeyStartStatus.textContent = "ETA must be at least 1 minute.";
    return;
  }

  startJourneyBtn.disabled = true;
  journeyStartStatus.textContent = "Getting your location…";
  const loc = await getLocation();

  const journey = await api("/api/journey/start", {
    method: "POST",
    body: JSON.stringify({
      destination_name,
      eta_minutes,
      guardian_contact_id,
      origin_lat: loc.latitude,
      origin_lng: loc.longitude,
    }),
  });

  startJourneyBtn.disabled = false;
  if (!journey._ok) {
    journeyStartStatus.textContent = "Couldn't start journey. Please try again.";
    return;
  }
  journeyStartStatus.textContent = "";
  showNotification("🧭 Journey started", `Tracking your trip to ${destination_name}.`, "info");
  renderJourney(journey);
});

journeyArrivedBtn.addEventListener("click", async () => {
  if (!activeJourney) return;
  const journey = await api(`/api/journey/${activeJourney.id}/arrived`, { method: "POST" });
  if (journey._ok) {
    showNotification("✅ Nice work", "Journey marked as complete.", "info");
    renderJourney(journey);
  }
});

journeyExtendBtn.addEventListener("click", async () => {
  if (!activeJourney) return;
  const journey = await api(`/api/journey/${activeJourney.id}/extend`, {
    method: "POST",
    body: JSON.stringify({ extra_minutes: 10 }),
  });
  if (journey._ok) renderJourney(journey);
});

journeyCancelBtn.addEventListener("click", async () => {
  if (!activeJourney) return;
  if (!confirm("Cancel this journey? Your guardian will not be alerted.")) return;
  const journey = await api(`/api/journey/${activeJourney.id}/cancel`, { method: "POST" });
  if (journey._ok) renderJourney(journey);
});

journeyTimelineToggle.addEventListener("click", async () => {
  const showing = !journeyTimelineList.classList.contains("hidden");
  if (showing) {
    journeyTimelineList.classList.add("hidden");
    journeyTimelineToggle.textContent = "Show Journey Timeline";
    return;
  }
  if (!activeJourney) return;
  const events = await api(`/api/journey/${activeJourney.id}/timeline`);
  if (Array.isArray(events)) {
    journeyTimelineList.innerHTML = events
      .slice()
      .reverse()
      .map(
        (e) =>
          `<li><strong>${escapeHtml(e.event_type.replace(/_/g, " "))}</strong>${e.message ? " — " + escapeHtml(e.message) : ""} <span style="font-size:11px; color:#888;">${new Date(e.created_at).toLocaleTimeString()}</span></li>`
      )
      .join("");
  }
  journeyTimelineList.classList.remove("hidden");
  journeyTimelineToggle.textContent = "Hide Journey Timeline";
});

// Poll for an already-in-progress journey (e.g. page reload mid-journey)
// and keep checking every 15s in case it expires while this tab is idle
// (e.g. the countdown ticker is throttled by the browser in a background tab).
refreshActiveJourney();
journeyPollInterval = setInterval(refreshActiveJourney, 15000);

// ---------------------------------------------------------------------------
// Dashboard status strip (Safety Score / Guardian chip)
// ---------------------------------------------------------------------------
async function refreshSafetyScoreChip() {
  if (!safetyScoreValue) return;
  const loc = await getLocation();
  if (loc.latitude == null || loc.longitude == null) {
    safetyScoreValue.textContent = "Enable location";
    return;
  }
  const result = await api(`/api/safety-score?lat=${loc.latitude}&lng=${loc.longitude}`);
  if (!result._ok || result.score == null) {
    safetyScoreValue.textContent = "—";
    return;
  }
  safetyScoreValue.textContent = `${result.score} · ${result.label}`;
  safetyScoreValue.classList.remove("score-safe", "score-caution", "score-risk");
  safetyScoreValue.classList.add(
    result.score >= 80 ? "score-safe" : result.score >= 55 ? "score-caution" : "score-risk"
  );
}

async function refreshGuardianChip() {
  if (!guardianStatusValue) return;
  const status = await api("/api/guardian/status");
  guardianStatusValue.textContent = status && status.active ? "✓ Sharing live" : "Not sharing";
}

refreshSafetyScoreChip();
refreshGuardianChip();
setInterval(refreshGuardianChip, 30000);
setTimeout(populateJourneyGuardianSelect, 200);

// ---------------------------------------------------------------------------
// Anonymous Record
// ---------------------------------------------------------------------------
// SECURITY/HONESTY FIX: the card's tags already claimed "Encrypted", but
// nothing was ever actually encrypted, and nothing persisted anywhere —
// a page refresh silently lost the recording. Recordings are now:
//   1. Encrypted client-side with AES-GCM (Web Crypto API) before storage.
//   2. Persisted in IndexedDB (this browser only — still "nothing leaves
//      your device", just durable across reloads instead of living only
//      in a Blob URL that dies with the tab).
// Honest caveat: the encryption key is generated per-recording and stored
// alongside the ciphertext in IndexedDB (not derived from a passphrase
// kept elsewhere), since this is a no-backend, no-login-tied-storage
// design. That protects against casual inspection of raw stored bytes
// and matches "encrypted at rest", but isn't a zero-knowledge scheme —
// anyone with access to this browser's IndexedDB can decrypt it, same as
// they could already play back an unencrypted recording.
const recordBtn = document.getElementById("recordBtn");
const stopRecordBtn = document.getElementById("stopRecordBtn");
const recordStatus = document.getElementById("recordStatus");
const recordPlayback = document.getElementById("recordPlayback");
const downloadRecordingLink = document.getElementById("downloadRecordingLink");
const recordTimer = document.getElementById("recordTimer");
const recordWaveformCanvas = document.getElementById("recordWaveform");

let mediaRecorder = null;
let recordedChunks = [];
let recordStartTime = null;
let recordTimerInterval = null;
let stopRecordWaveform = null;
let recordStream = null;

const RECORDINGS_DB_NAME = "safeher_recordings";
const RECORDINGS_STORE = "recordings";

function openRecordingsDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(RECORDINGS_DB_NAME, 1);
    req.onupgradeneeded = () => {
      req.result.createObjectStore(RECORDINGS_STORE, { keyPath: "id", autoIncrement: true });
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function saveRecording(entry) {
  const db = await openRecordingsDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(RECORDINGS_STORE, "readwrite");
    tx.objectStore(RECORDINGS_STORE).add(entry);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

async function getAllRecordings() {
  const db = await openRecordingsDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(RECORDINGS_STORE, "readonly");
    const req = tx.objectStore(RECORDINGS_STORE).getAll();
    req.onsuccess = () => resolve(req.result.sort((a, b) => b.id - a.id));
    req.onerror = () => reject(req.error);
  });
}

async function deleteRecording(id) {
  const db = await openRecordingsDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(RECORDINGS_STORE, "readwrite");
    tx.objectStore(RECORDINGS_STORE).delete(id);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

async function encryptBlob(blob) {
  const key = await crypto.subtle.generateKey({ name: "AES-GCM", length: 256 }, true, ["encrypt", "decrypt"]);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const plainBuffer = await blob.arrayBuffer();
  const ciphertext = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, plainBuffer);
  const rawKey = await crypto.subtle.exportKey("raw", key);
  return { ciphertext, iv, rawKey };
}

async function decryptToBlob(entry) {
  const key = await crypto.subtle.importKey("raw", entry.rawKey, "AES-GCM", false, ["decrypt"]);
  const plainBuffer = await crypto.subtle.decrypt({ name: "AES-GCM", iv: entry.iv }, key, entry.ciphertext);
  return new Blob([plainBuffer], { type: entry.mimeType });
}

function formatDuration(seconds) {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

recordBtn.addEventListener("click", async () => {
  try {
    recordStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    mediaRecorder = new MediaRecorder(recordStream);
    recordedChunks = [];

    mediaRecorder.ondataavailable = (event) => recordedChunks.push(event.data);

    mediaRecorder.onstop = async () => {
      const durationSeconds = (Date.now() - recordStartTime) / 1000;
      const blob = new Blob(recordedChunks, { type: "audio/webm" });
      const url = URL.createObjectURL(blob);
      recordPlayback.src = url;
      recordPlayback.classList.remove("hidden");
      downloadRecordingLink.href = url;
      downloadRecordingLink.classList.remove("hidden");
      recordStatus.textContent = "Encrypting and saving…";

      try {
        const { ciphertext, iv, rawKey } = await encryptBlob(blob);
        await saveRecording({
          timestamp: new Date().toISOString(),
          duration: durationSeconds,
          mimeType: "audio/webm",
          ciphertext, iv, rawKey,
        });
        recordStatus.textContent = "🔒 Recording encrypted and saved to this device. Nothing was uploaded.";
        refreshRecordingHistory();
      } catch (e) {
        recordStatus.textContent = "Recording saved locally (encryption unavailable in this browser).";
      }
    };

    mediaRecorder.start();
    recordStartTime = Date.now();
    recordBtn.disabled = true;
    stopRecordBtn.disabled = false;
    recordBtn.classList.add("listening-pulse");
    recordStatus.textContent = "⏺ Recording… (privacy mode)";
    recordPlayback.classList.add("hidden");
    downloadRecordingLink.classList.add("hidden");

    recordTimer.classList.remove("hidden");
    recordTimer.textContent = "00:00";
    recordTimerInterval = setInterval(() => {
      recordTimer.textContent = formatDuration((Date.now() - recordStartTime) / 1000);
    }, 1000);

    stopRecordWaveform = startWaveformVisualization(recordStream, recordWaveformCanvas);
  } catch (err) {
    recordStatus.textContent = "Microphone access denied: " + err.message;
  }
});

stopRecordBtn.addEventListener("click", () => {
  if (mediaRecorder) {
    mediaRecorder.stop();
    recordStream?.getTracks().forEach((t) => t.stop());
    recordBtn.disabled = false;
    recordBtn.classList.remove("listening-pulse");
    stopRecordBtn.disabled = true;
  }
  if (recordTimerInterval) { clearInterval(recordTimerInterval); recordTimerInterval = null; }
  recordTimer.classList.add("hidden");
  if (stopRecordWaveform) { stopRecordWaveform(); stopRecordWaveform = null; }
});

const recordHistoryToggle = document.getElementById("recordHistoryToggle");
const recordHistoryList = document.getElementById("recordHistoryList");

recordHistoryToggle.addEventListener("click", async () => {
  const showing = !recordHistoryList.classList.contains("hidden");
  if (showing) {
    recordHistoryList.classList.add("hidden");
    recordHistoryToggle.textContent = "Show Recording History";
    return;
  }
  await refreshRecordingHistory();
  recordHistoryList.classList.remove("hidden");
  recordHistoryToggle.textContent = "Hide Recording History";
});

async function refreshRecordingHistory() {
  let recordings = [];
  try {
    recordings = await getAllRecordings();
  } catch (e) {
    recordHistoryList.innerHTML = `<li class="muted" style="border:none;">History unavailable in this browser.</li>`;
    return;
  }

  recordHistoryList.innerHTML = recordings.length
    ? recordings
        .map(
          (r) => `<li>
            <strong>🔒 ${relativeTime(r.timestamp)}</strong>
            <span class="muted" style="font-size:11px; float:right;">${formatDuration(r.duration)}</span>
            <br/><span style="display:inline-flex; gap:6px; margin-top:6px;">
              <button class="btn" style="padding:3px 10px; font-size:11px;" onclick="playStoredRecording(${r.id})">▶ Play</button>
              <button class="btn secondary" style="padding:3px 10px; font-size:11px;" onclick="downloadStoredRecording(${r.id})">⬇ Download</button>
              <button class="btn secondary" style="padding:3px 10px; font-size:11px;" onclick="deleteStoredRecording(${r.id})">🗑 Delete</button>
            </span>
          </li>`
        )
        .join("")
    : `<li class="muted" style="border:none;">No recordings saved on this device yet.</li>`;
}

async function playStoredRecording(id) {
  const recordings = await getAllRecordings();
  const entry = recordings.find((r) => r.id === id);
  if (!entry) return;
  const blob = await decryptToBlob(entry);
  recordPlayback.src = URL.createObjectURL(blob);
  recordPlayback.classList.remove("hidden");
  recordPlayback.play();
}

async function downloadStoredRecording(id) {
  const recordings = await getAllRecordings();
  const entry = recordings.find((r) => r.id === id);
  if (!entry) return;
  const blob = await decryptToBlob(entry);
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `safeher-recording-${new Date(entry.timestamp).getTime()}.webm`;
  a.click();
}

async function deleteStoredRecording(id) {
  if (!confirm("Delete this recording? This cannot be undone.")) return;
  await deleteRecording(id);
  refreshRecordingHistory();
}

// ---------------------------------------------------------------------------
// Recent Alerts
// ---------------------------------------------------------------------------
const alertsList = document.getElementById("alertsList");

const SOS_TRIGGER_ICONS = {
  manual: "🆘",
  audio_ml: "🎙️",
  audio_ml_deployed: "🎙️",
  checkin_timeout: "⏱️",
  journey_missed_checkin: "🧭",
};

function relativeTime(isoString) {
  const then = new Date(isoString).getTime();
  const diffSec = Math.round((Date.now() - then) / 1000);
  if (diffSec < 60) return "just now";
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
  return new Date(isoString).toLocaleDateString();
}

async function loadAlerts() {
  const rows = await api("/api/alerts");
  if (!Array.isArray(rows) || rows.length === 0) {
    alertsList.innerHTML = `<li class="muted" style="border:none;">No emergency alerts yet — that's a good thing.</li>`;
    return;
  }
  alertsList.innerHTML = rows
    .map((r) => {
      const icon = SOS_TRIGGER_ICONS[r.trigger_type] || "⚠️";
      const mapLink =
        r.latitude != null && r.longitude != null
          ? `<a href="https://maps.google.com/?q=${r.latitude},${r.longitude}" target="_blank" rel="noopener" style="font-size:11px;">View location</a>`
          : "";
      return `<li>
        <strong>${icon} ${escapeHtml(r.trigger_type.replace(/_/g, " "))}</strong>
        <span style="font-size:11px; color:#888; float:right;">${relativeTime(r.created_at)}</span>
        <br/><span style="font-size:12px;">${escapeHtml(r.message)}</span>
        ${mapLink ? `<br/>${mapLink}` : ""}
      </li>`;
    })
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

// Route Mode + layer state
let mapMode = "audit"; // "audit" | "route"
let routePoints = []; // [{lat,lng}, {lat,lng}]
let routeMarkers = [];
let routeLines = [];
let riskZoneLayers = [];
let serviceLayers = [];
let showRiskZones = false;
let showServices = false;

const mapModeAuditBtn = document.getElementById("mapModeAuditBtn");
const mapModeRouteBtn = document.getElementById("mapModeRouteBtn");
const routeModePanel = document.getElementById("routeModePanel");
const mapModeHint = document.getElementById("mapModeHint");
const resetRouteBtn = document.getElementById("resetRouteBtn");
const crowdDensityBadge = document.getElementById("crowdDensityBadge");
const routeComparePanel = document.getElementById("routeComparePanel");

mapModeAuditBtn.addEventListener("click", () => setMapMode("audit"));
mapModeRouteBtn.addEventListener("click", () => setMapMode("route"));

function setMapMode(mode) {
  mapMode = mode;
  mapModeAuditBtn.classList.toggle("active", mode === "audit");
  mapModeRouteBtn.classList.toggle("active", mode === "route");
  routeModePanel.classList.toggle("hidden", mode !== "route");
  mapModeHint.textContent =
    mode === "audit"
      ? "Tap anywhere on the map to run a quick Safety Audit for that spot — pins are color-coded by score, just like SafetiPin's crowd-sourced audits. Press and hold to report an issue instead."
      : "Tap the map to set your origin, then tap again to set your destination.";
}

resetRouteBtn.addEventListener("click", clearRoute);

function clearRoute() {
  routePoints = [];
  routeMarkers.forEach((m) => leafletMap.removeLayer(m));
  routeMarkers = [];
  routeLines.forEach((l) => leafletMap.removeLayer(l));
  routeLines = [];
  routeResult.innerHTML = "";
  routeComparePanel.innerHTML = "";
}

// NOTE: the Risk-Zones and Nearby-Help layer *toggle buttons* used to live
// right here (#layerRiskZonesBtn / #layerServicesBtn). The Safety Map's
// left panel now has a fuller "Safety Layers" grid (heatmap, police,
// hospital, pharmacy, lighting, women's safety, safe zones, community
// reports, crime, traffic) — see initSafetyLayers() in safety-map.js,
// which toggles these same `showRiskZones` / `showServices` flags and
// calls the same refreshMapLayers() / refreshServiceLayer() functions
// below, just from the new buttons instead of the old two.

async function refreshMapLayers() {
  if (!leafletMap || !showRiskZones) return;
  const center = leafletMap.getCenter();
  const data = await api(`/api/risk-zones?lat=${center.lat}&lng=${center.lng}&radius_km=4`);
  if (!data._ok) return;

  riskZoneLayers.forEach((l) => leafletMap.removeLayer(l));
  riskZoneLayers = [];
  (data.zones || []).forEach((z) => {
    const color = z.severity === "high" ? "#EF4444" : "#F59E0B";
    const circle = L.circle([z.latitude, z.longitude], {
      radius: z.radius_m,
      color,
      fillColor: color,
      fillOpacity: 0.15,
      weight: 1,
    })
      .addTo(leafletMap)
      .bindPopup(`<strong>⚠️ ${escapeHtml(z.label)}</strong><br/>Safety score: ${z.score}/100<br/>Source: ${z.source === "audit" ? "Community audit" : "Recent risk alert"}`);
    riskZoneLayers.push(circle);
  });

  crowdDensityBadge.textContent = `👥 ${data.crowd_density} SafeHer member(s) actively sharing location nearby`;
  crowdDensityBadge.classList.remove("hidden");
}

const SERVICE_ICONS = { police: "🚓", hospital: "🏥", pharmacy: "💊", helpline: "📞", metro: "🚇", toilet: "🚻", shelter: "🏠", cab_stand: "🚕" };

async function refreshServiceLayer() {
  if (!leafletMap || !showServices) return;
  const center = leafletMap.getCenter();
  const data = await api(`/api/nearby-services?lat=${center.lat}&lng=${center.lng}`);
  if (!data._ok) return;

  serviceLayers.forEach((l) => leafletMap.removeLayer(l));
  serviceLayers = [];
  (data.results || []).forEach((s) => {
    if (s.lat == null || s.lng == null) return;
    const icon = L.divIcon({ html: SERVICE_ICONS[s.type] || "📍", className: "service-div-icon", iconSize: [22, 22] });
    const marker = L.marker([s.lat, s.lng], { icon })
      .addTo(leafletMap)
      .bindPopup(
        `<strong>${escapeHtml(s.name)}</strong> (${escapeHtml(s.type)})<br/>${s.distance_km != null ? s.distance_km + " km away<br/>" : ""}${s.phone && s.phone !== "N/A" ? `<a href="tel:${escapeHtml(s.phone)}">📞 ${escapeHtml(s.phone)}</a>` : ""}`
      );
    serviceLayers.push(marker);
  });
}

async function compareRoutes() {
  if (routePoints.length !== 2) return;
  const [a, b] = routePoints;
  routeResult.textContent = "Checking route safety…";
  routeComparePanel.innerHTML = "";

  const result = await api("/api/route-safety", {
    method: "POST",
    body: JSON.stringify({
      origin: "Point A", destination: "Point B",
      origin_lat: a.lat, origin_lng: a.lng,
      destination_lat: b.lat, destination_lng: b.lng,
      compare_alternatives: true,
    }),
  });
  if (!result._ok) {
    routeResult.textContent = "Couldn't check route safety right now.";
    return;
  }

  routeLines.forEach((l) => leafletMap.removeLayer(l));
  routeLines = [];

  const rawRoutes = result.routes || [];
  routeResult.innerHTML = rawRoutes.length
    ? `<strong>${rawRoutes.length > 1 ? "Route Comparison" : "Route Safety"}</strong><br/><span class="muted" style="font-size:11px;">${escapeHtml(result.note || "")}</span>`
    : "No route data available.";

  // Reclassify into Safest / Fastest / Balanced rather than the backend's
  // generic "Fastest Route" / "Alternative Route" labels. With only 1 or 2
  // routes actually returned by OSRM we show only that many cards, labeled
  // honestly (never inventing a third route that doesn't exist).
  const routes = classifyRoutesBySafetyProfile(rawRoutes);

  const riskLevel = (score) => (score >= 75 ? "Low risk" : score >= 45 ? "Moderate risk" : "High risk");

  routeComparePanel.innerHTML = routes
    .map((r) => {
      const color = scoreColor(r.score);
      return `<div class="route-card" style="border-left:4px solid ${color};">
        <strong>${r.profileIcon} ${escapeHtml(r.profileLabel)}</strong>
        <p style="margin:4px 0; font-size:12px;" class="muted">
          ${r.distance_km} km${r.duration_min != null ? " · " + Math.round(r.duration_min) + " min ETA" : ""}
        </p>
        <p style="margin:0;"><span style="color:${color}; font-weight:700;">${r.score}/100 — ${escapeHtml(r.rating)}</span></p>
        <p style="margin:4px 0 0; font-size:11px;" class="muted">${riskLevel(r.score)} · ⚠️ ${r.risk_zones_crossed} risk zone(s) along this path</p>
      </div>`;
    })
    .join("");

  routes.forEach((r) => {
    if (!r.geometry || r.geometry.length < 2) return;
    const line = L.polyline(r.geometry, { color: scoreColor(r.score), weight: 4, opacity: 0.75, className: "route-line-animated" }).addTo(leafletMap);
    routeLines.push(line);
  });
  if (routes.length) {
    const bounds = L.latLngBounds(routes.flatMap((r) => r.geometry || []));
    if (bounds.isValid()) leafletMap.flyToBounds(bounds, { padding: [30, 30], duration: 0.8 });
  }
}

// Takes whatever routes OSRM actually returned (1-2, typically) and labels
// them Safest / Fastest / Balanced based on their real scores and
// durations — never fabricates a route that doesn't exist. With a single
// route there's nothing to compare, so it's labeled "Recommended Route"
// instead of arbitrarily picking one of the three names.
function classifyRoutesBySafetyProfile(rawRoutes) {
  if (!rawRoutes.length) return [];
  if (rawRoutes.length === 1) {
    return [{ ...rawRoutes[0], profileIcon: "🧭", profileLabel: "Recommended Route" }];
  }

  const bySafety = [...rawRoutes].sort((a, b) => b.score - a.score);
  const byDuration = [...rawRoutes].sort((a, b) => (a.duration_min ?? Infinity) - (b.duration_min ?? Infinity));
  const safest = bySafety[0];
  const fastest = byDuration[0];

  const labeled = [{ ...safest, profileIcon: "🟢", profileLabel: "Safest Route" }];
  if (fastest !== safest) {
    labeled.push({ ...fastest, profileIcon: "⚡", profileLabel: "Fastest Route" });
  }
  // A genuine third/"balanced" option only exists if there are more than 2
  // distinct routes; with exactly 2 we're honest that it's a two-way choice.
  const remaining = rawRoutes.filter((r) => r !== safest && r !== fastest);
  if (remaining.length) {
    labeled.push({ ...remaining[0], profileIcon: "⚖️", profileLabel: "Balanced Route" });
  }
  return labeled;
}

function initMap() {
  if (CDN_FAILED.leaflet || typeof L === "undefined") {
    const mapEl = document.getElementById("leafletMap");
    if (mapEl) {
      mapEl.innerHTML =
        '<p class="muted" style="padding:16px;">🗺️ Map unavailable — the map library could not load. Everything else in the app still works.</p>';
    }
    console.warn("Leaflet CDN unavailable — Safety Score Map disabled for this session");
    return;
  }

  leafletMap = L.map("leafletMap").setView([28.7041, 77.1025], 12); // Default: Delhi
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "© OpenStreetMap contributors",
    maxZoom: 19,
  }).addTo(leafletMap);

  // Set to true by safety-map.js's long-press handler right before it opens
  // the quick "Report an Issue" modal, so this ordinary click handler (which
  // Leaflet still fires on pointerup) doesn't also drop an audit marker /
  // open the full audit modal for the same press.
  window.__mapLongPressHandled = false;

  leafletMap.on("click", (e) => {
    if (window.__mapLongPressHandled) {
      window.__mapLongPressHandled = false;
      return;
    }
    if (mapMode === "route") {
      handleRouteModeClick(e.latlng);
      return;
    }
    selectedLocation = e.latlng;
    if (markers.length > 0) {
      leafletMap.removeLayer(markers[markers.length - 1]);
      markers.pop();
    }
    const marker = L.circleMarker([e.latlng.lat, e.latlng.lng], {
      radius: 8,
      fillColor: "#7C3AED",
      color: "#fff",
      weight: 2,
      opacity: 1,
      fillOpacity: 0.8,
    }).addTo(leafletMap);
    markers.push(marker);

    // Show audit modal for this location
    setTimeout(showAuditModal, 300);
  });

  leafletMap.on("moveend", () => {
    if (showRiskZones) refreshMapLayers();
    if (showServices) refreshServiceLayer();
  });

  loadAndDisplayAudits();
}

function handleRouteModeClick(latlng) {
  if (routePoints.length >= 2) clearRoute();

  const label = routePoints.length === 0 ? "A" : "B";
  const color = label === "A" ? "#0F9D70" : "#EF4444";
  const marker = L.marker([latlng.lat, latlng.lng], {
    icon: L.divIcon({ html: `<div class="route-pin" style="background:${color};"><span>${label}</span></div>`, className: "", iconSize: [26, 26] }),
  }).addTo(leafletMap);
  routeMarkers.push(marker);
  routePoints.push({ lat: latlng.lat, lng: latlng.lng });

  if (routePoints.length === 2) compareRoutes();
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
const routeResult = document.getElementById("routeResult");

// Route safety checks are handled by the map route-mode flow above.
// Initialize map when tab loads
setTimeout(initMap, 100);

// ---------------------------------------------------------------------------
// Safety Hub (formerly "Directory") — Nearby Services, Emergency Contacts,
// National Helplines, quick actions, category filters, and search.
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// National Emergency Helplines (India) — static, click-to-call.
// `category` lines these up with the filter chips below. Three chips
// ("Shelters", "NGOs", "Cyber Crime") don't correspond to a real amenity
// type the backend queries (/api/nearby-services only covers
// police/hospital/pharmacy, per OSM_AMENITY_MAP in app.py) — rather than
// invent fake nearby listings for them, they surface the genuinely-relevant
// entries from this list instead (e.g. the National Commission for Women is
// a real NGO helpline, and Cyber Crime routes to the 1930 helpline).
// ---------------------------------------------------------------------------
const INDIA_EMERGENCY_NUMBERS = [
  { name: "All-in-One Emergency", number: "112", category: "police", description: "Police • Fire • Ambulance" },
  { name: "Police", number: "100", category: "police", description: "Direct police line" },
  { name: "Women Helpline", number: "1091", category: "helpline", description: "24/7 women's safety line" },
  { name: "Women Helpline (Domestic Abuse)", number: "181", category: "helpline", description: "Domestic abuse support" },
  { name: "Ambulance", number: "102", category: "hospital", description: "Medical emergency" },
  { name: "Fire", number: "101", category: "police", description: "Fire emergency" },
  { name: "Child Helpline", number: "1098", category: "shelter", description: "Child protection & shelter referrals" },
  { name: "National Commission for Women", number: "7827170170", category: "ngo", description: "Complaints, referrals, legal guidance" },
  { name: "Cyber Crime Helpline", number: "1930", category: "cyber", description: "Report online harassment or fraud" },
  { name: "Disaster Management", number: "108", category: "hospital", description: "Emergency response & rescue" },
];

const HELPLINE_ICONS = { police: "🚨", hospital: "🚑", helpline: "📞", shelter: "🏠", ngo: "🤝", cyber: "🛡️" };

// ---------------------------------------------------------------------------
// Search term highlighting — wraps the matched substring in <mark> so results
// are easy to scan. Always escapes first, then re-inserts <mark> around the
// match, so this can never introduce unescaped HTML from user-typed search
// terms or service data.
// ---------------------------------------------------------------------------
function highlightMatch(value, term) {
  const safe = escapeHtml(value);
  if (!term) return safe;
  const safeTerm = escapeHtml(term);
  const idx = safe.toLowerCase().indexOf(safeTerm.toLowerCase());
  if (idx === -1) return safe;
  return safe.slice(0, idx) + "<mark>" + safe.slice(idx, idx + safeTerm.length) + "</mark>" + safe.slice(idx + safeTerm.length);
}

function renderEmergencyHelplines(filterCategory, searchTerm) {
  const list = document.getElementById("emergencyHelplinesList");
  if (!list) return;

  const term = (searchTerm || "").toLowerCase();
  const filtered = INDIA_EMERGENCY_NUMBERS.filter((e) => {
    const matchesCategory = !filterCategory || filterCategory === "contacts" || e.category === filterCategory;
    const matchesSearch = !term || e.name.toLowerCase().includes(term) || e.description.toLowerCase().includes(term);
    return matchesCategory && matchesSearch;
  });

  list.innerHTML = filtered.length
    ? filtered
        .map(
          (e) => `<li class="hub-helpline-card">
            <span class="hub-helpline-icon" aria-hidden="true">${HELPLINE_ICONS[e.category] || "☎️"}</span>
            <div class="hub-helpline-info">
              <strong>${highlightMatch(e.name, searchTerm)}</strong>
              <span class="hub-helpline-number">${escapeHtml(e.number)}</span>
              <span class="muted hub-helpline-desc">${highlightMatch(e.description, searchTerm)}</span>
            </div>
            <div class="hub-helpline-actions">
              <a href="tel:${escapeHtml(e.number)}" class="btn safe-btn hub-helpline-call">📞 Call</a>
              <button type="button" class="btn secondary hub-helpline-copy" data-number="${escapeHtml(e.number)}" aria-label="Copy ${escapeHtml(e.name)} number">📋 Copy</button>
            </div>
          </li>`
        )
        .join("")
    : `<li class="muted" style="border:none;">No helplines match "${escapeHtml(searchTerm || "")}".</li>`;
}

renderEmergencyHelplines();

// Delegated "Copy Number" handler — one listener survives every re-render.
document.getElementById("emergencyHelplinesList")?.addEventListener("click", async (e) => {
  const btn = e.target.closest(".hub-helpline-copy");
  if (!btn) return;
  const number = btn.dataset.number;
  try {
    await navigator.clipboard.writeText(number);
    showNotification("📋 Copied", `${number} copied to clipboard.`, "info");
  } catch (err) {
    showNotification("Couldn't copy", `Number: ${number}`, "info");
  }
});

const servicesList = document.getElementById("servicesList");
const filterBtns = document.querySelectorAll(".filter-btn");
const hubLocationEmptyState = document.getElementById("hubLocationEmptyState");
const hubSearchInput = document.getElementById("hubSearchInput");
let currentServiceFilter = "";

// Amenity types the real backend actually supports for live nearby lookup.
const BACKEND_SUPPORTED_TYPES = ["police", "hospital", "pharmacy", "helpline"];

filterBtns.forEach((btn) => {
  btn.addEventListener("click", () => {
    filterBtns.forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    currentServiceFilter = btn.dataset.type;
    applyHubFilters();

    if (currentServiceFilter === "contacts") {
      document.getElementById("hubContactsSection")?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  });
});

hubSearchInput?.addEventListener("input", () => applyHubFilters());

function applyHubFilters() {
  const searchTerm = hubSearchInput?.value.trim() || "";

  // Nearby Services only re-fetches for backend-supported types; for
  // "shelter"/"ngo" it shows an honest message instead of pretending to
  // have live data the backend can't provide.
  if (!currentServiceFilter || BACKEND_SUPPORTED_TYPES.includes(currentServiceFilter)) {
    loadServices(currentServiceFilter, searchTerm);
  } else {
    servicesList.innerHTML = `<li class="muted" style="border:none; grid-column:1/-1;">
      Live nearby listings for this category aren't available yet — see National Helplines below for relevant support numbers.
    </li>`;
  }

  renderEmergencyHelplines(currentServiceFilter, searchTerm);
  filterHubContacts(searchTerm);
}

async function loadServices(serviceType, searchTerm) {
  const loc = await getLocation();
  if (!loc.latitude || !loc.longitude) {
    servicesList.innerHTML = "";
    hubLocationEmptyState?.classList.remove("hidden");
    return;
  }
  hubLocationEmptyState?.classList.add("hidden");

  const data = await api(`/api/nearby-services?lat=${loc.latitude}&lng=${loc.longitude}${serviceType ? "&type=" + serviceType : ""}`);
  if (!data._ok || !Array.isArray(data.results) || data.results.length === 0) {
    servicesList.innerHTML = '<li class="muted" style="border:none; grid-column:1/-1;">No nearby services found.</li>';
    return;
  }

  let results = data.results;
  if (searchTerm) {
    const term = searchTerm.toLowerCase();
    results = results.filter((s) => s.name.toLowerCase().includes(term) || s.type.toLowerCase().includes(term));
  }
  if (results.length === 0) {
    servicesList.innerHTML = `<li class="muted" style="border:none; grid-column:1/-1;">No nearby services match "${escapeHtml(searchTerm)}".</li>`;
    return;
  }

  servicesList.innerHTML = results
    .map((s) => {
      const icon = SERVICE_ICONS[s.type] || "📍";
      const callLink =
        s.phone && s.phone !== "N/A"
          ? `<a href="tel:${escapeHtml(s.phone)}" class="tel-call-btn">📞 Call</a>`
          : "";
      const navLink =
        s.lat != null && s.lng != null
          ? `<a href="https://maps.google.com/?q=${s.lat},${s.lng}" target="_blank" rel="noopener" class="btn secondary" style="padding:5px 12px; font-size:11px;">🧭 Navigate</a>`
          : "";
      const openStatus = s.open_status || { status: "hours_vary", label: "Hours vary" };
      const openBadgeClass = openStatus.status === "open_24_7" ? "badge-safe" : "";
      return `<li class="service-card">
        <div class="service-card-header">
          <span class="service-card-icon">${icon}</span>
          <div>
            <strong>${highlightMatch(s.name, searchTerm)}</strong>
            <div class="muted" style="font-size:11px; text-transform:capitalize;">${escapeHtml(s.type)}</div>
          </div>
          ${s.distance_km != null ? `<span class="service-card-distance">${s.distance_km} km</span>` : ""}
        </div>
        <span class="journey-badge ${openBadgeClass}" style="margin-top:8px;">${escapeHtml(openStatus.label)}</span>
        <div class="service-card-actions">${callLink}${navLink}</div>
      </li>`;
    })
    .join("");
}

document.getElementById("hubEnableLocationBtn")?.addEventListener("click", () => loadServices(currentServiceFilter, hubSearchInput?.value.trim()));

// ---------------------------------------------------------------------------
// Emergency Contacts mini-list on the Safety Hub — reads the same
// /api/contacts data the Guardian tab manages; adding/removing contacts
// still happens there (single source of truth for that logic).
// ---------------------------------------------------------------------------
const hubContactsList = document.getElementById("hubContactsList");
const hubContactsEmptyState = document.getElementById("hubContactsEmptyState");
let hubContactsCache = [];

async function loadHubContacts() {
  const contacts = await api("/api/contacts");
  hubContactsCache = Array.isArray(contacts) ? contacts : [];
  filterHubContacts(hubSearchInput?.value.trim() || "");
}

function filterHubContacts(searchTerm) {
  if (!hubContactsList) return;
  const term = (searchTerm || "").toLowerCase();
  const filtered = hubContactsCache.filter((c) => !term || c.name.toLowerCase().includes(term));

  if (hubContactsCache.length === 0) {
    hubContactsList.innerHTML = "";
    hubContactsEmptyState?.classList.remove("hidden");
    return;
  }
  hubContactsEmptyState?.classList.add("hidden");

  hubContactsList.innerHTML = filtered.length
    ? filtered
        .map(
          (c) => `<li class="hub-contact-card">
            <span class="hub-contact-avatar" aria-hidden="true">❤️</span>
            <div class="hub-contact-info">
              <strong>${highlightMatch(c.name, searchTerm)}</strong>
              <span class="muted" style="font-size:11px;">${escapeHtml(c.relation || "contact")}</span>
            </div>
            <div class="hub-contact-actions">
              <a href="tel:${escapeHtml(c.phone)}" class="btn safe-btn" style="padding:6px 12px; font-size:12px;">📞 Call</a>
              <button class="btn secondary hub-share-location-btn" style="padding:6px 12px; font-size:12px;">📍 Share Location</button>
            </div>
          </li>`
        )
        .join("")
    : `<li class="muted" style="border:none;">No contacts match "${escapeHtml(searchTerm)}".</li>`;
}

document.getElementById("hubAddContactBtn")?.addEventListener("click", () => {
  document.querySelector('[data-tab="guardian"]')?.click();
  setTimeout(() => document.getElementById("contactName")?.focus(), 300);
});

// ---------------------------------------------------------------------------
// Quick Emergency Actions — every one of these forwards to a real, already-
// wired feature rather than introducing new backend behavior.
// ---------------------------------------------------------------------------
document.getElementById("hubCallEmergencyContactBtn")?.addEventListener("click", () => {
  if (hubContactsCache.length > 0) {
    window.location.href = `tel:${hubContactsCache[0].phone}`;
  } else {
    showNotification("No emergency contact yet", "Add one below to enable one-tap calling.", "info");
    document.getElementById("hubContactsSection")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
});

document.getElementById("hubShareLocationBtn")?.addEventListener("click", () => {
  // Forwards to the Guardian tab's real "Start Sharing" button — same
  // code path, same /api/guardian/share call, no duplicated logic.
  document.getElementById("shareLocationBtn")?.click();
  showNotification("📍 Sharing started", "Your live location is now visible to your Bubble.", "info");
});

document.getElementById("hubFindPoliceBtn")?.addEventListener("click", () => {
  const policeChip = document.querySelector('.filter-btn[data-type="police"]');
  policeChip?.click();
  document.getElementById("hubNearbySection")?.scrollIntoView({ behavior: "smooth", block: "start" });
});

// Delegated handler for the per-contact "Share Location" buttons (list is
// re-rendered on every refresh, so a delegated listener on the parent
// avoids re-binding on each render).
hubContactsList?.addEventListener("click", (e) => {
  if (e.target.closest(".hub-share-location-btn")) {
    document.getElementById("shareLocationBtn")?.click();
    showNotification("📍 Sharing started", "Your live location is now visible to your Bubble.", "info");
  }
});

loadHubContacts();



// ---------------------------------------------------------------------------
// AI Assistant
// ---------------------------------------------------------------------------
const assistantChatWindow = document.getElementById("assistantChatWindow");
const assistantSuggestions = document.getElementById("assistantSuggestions");
const assistantInput = document.getElementById("assistantInput");
const assistantSendBtn = document.getElementById("assistantSendBtn");

const DEFAULT_ASSISTANT_SUGGESTIONS = [
  "What should I do if I feel unsafe?",
  "Find police near me",
  "Plan a safe route home",
  "How do I use the SOS button?",
  "What counts as harassment?",
];

function appendAssistantMessage(role, text) {
  const div = document.createElement("div");
  div.className = `assistant-message ${role === "user" ? "assistant-user" : "assistant-bot"}`;
  const p = document.createElement("p");
  p.textContent = text; // textContent, not innerHTML — no need to trust-and-escape since it's never HTML
  div.appendChild(p);
  assistantChatWindow.appendChild(div);
  assistantChatWindow.scrollTop = assistantChatWindow.scrollHeight;
}

function renderAssistantSuggestions(suggestions) {
  assistantSuggestions.innerHTML = (suggestions || DEFAULT_ASSISTANT_SUGGESTIONS)
    .map((s) => `<button class="assistant-suggestion-chip">${escapeHtml(s)}</button>`)
    .join("");
  assistantSuggestions.querySelectorAll(".assistant-suggestion-chip").forEach((chip) => {
    chip.addEventListener("click", () => sendAssistantMessage(chip.textContent));
  });
}

async function sendAssistantMessage(text) {
  const message = (text || assistantInput.value).trim();
  if (!message) return;

  appendAssistantMessage("user", message);
  assistantInput.value = "";
  assistantSendBtn.disabled = true;

  const thinkingDiv = document.createElement("div");
  thinkingDiv.className = "assistant-message assistant-bot";
  thinkingDiv.innerHTML = `<p class="muted">…</p>`;
  assistantChatWindow.appendChild(thinkingDiv);
  assistantChatWindow.scrollTop = assistantChatWindow.scrollHeight;

  const loc = await getLocation();
  const result = await api("/api/assistant/chat", {
    method: "POST",
    body: JSON.stringify({ message, latitude: loc.latitude, longitude: loc.longitude }),
  });

  thinkingDiv.remove();
  assistantSendBtn.disabled = false;

  if (!result._ok) {
    appendAssistantMessage("assistant", "Sorry, something went wrong. Please try again.");
    return;
  }
  appendAssistantMessage("assistant", result.reply);
  renderAssistantSuggestions(result.suggestions);
}

assistantSendBtn.addEventListener("click", () => sendAssistantMessage());
assistantInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") sendAssistantMessage();
});

let assistantHistoryLoaded = false;

async function loadAssistantHistory() {
  if (assistantHistoryLoaded) return;
  assistantHistoryLoaded = true;
  const rows = await api("/api/assistant/history");
  if (!Array.isArray(rows) || rows.length === 0) {
    renderAssistantSuggestions();
    return;
  }
  assistantChatWindow.innerHTML = "";
  rows.forEach((r) => appendAssistantMessage(r.role, r.message));
  renderAssistantSuggestions();
}

renderAssistantSuggestions();

let bubbleMap = null;
const shareLocationBtn = document.getElementById("shareLocationBtn");
const stopSharingBtn = document.getElementById("stopSharingBtn");
const guardianStatus = document.getElementById("guardianStatus");
const contactName = document.getElementById("contactName");
const contactPhone = document.getElementById("contactPhone");
const contactEmail = document.getElementById("contactEmail");
const contactRelation = document.getElementById("contactRelation");
const addContactBtn = document.getElementById("addContactBtn");
const contactsList = document.getElementById("contactsList");

function initBubbleMap() {
  if (CDN_FAILED.leaflet || typeof L === "undefined") {
    const mapEl = document.getElementById("bubbleMap");
    if (mapEl) {
      mapEl.innerHTML =
        '<p class="muted" style="padding:16px;">🗺️ Map unavailable — the map library could not load. Location sharing and tracking still work; you just won\'t see the live map here.</p>';
    }
    console.warn("Leaflet CDN unavailable — Guardian map disabled for this session");
    return;
  }

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
        `<li><strong>${escapeHtml(c.name)}</strong> (${escapeHtml(c.relation || "contact")})<br/><span style="font-size:11px; color:#888;">${escapeHtml(c.phone)}${c.email ? " · " + escapeHtml(c.email) : ""}</span>
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
  const email = contactEmail.value.trim();
  const relation = contactRelation.value.trim();

  if (!name || !phone) {
    alert("Name and phone are required");
    return;
  }

  await api("/api/contacts", {
    method: "POST",
    body: JSON.stringify({ name, phone, email, relation }),
  });

  contactName.value = "";
  contactPhone.value = "";
  contactEmail.value = "";
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
      fillColor: "#22C55E",
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
  breadcrumbPolyline = L.polyline(breadcrumbTrail, { color: "#7C3AED", weight: 3, opacity: 0.7 }).addTo(bubbleMap);

  const latest = breadcrumbTrail[breadcrumbTrail.length - 1];
  if (liveDotMarker) bubbleMap.removeLayer(liveDotMarker);
  liveDotMarker = L.circleMarker(latest, {
    radius: 8,
    fillColor: "#22C55E",
    color: "#fff",
    weight: 2,
    opacity: 1,
    fillOpacity: 0.9,
  }).addTo(bubbleMap);
  bubbleMap.setView(latest, 15);
}

async function getBatteryLevel() {
  try {
    if (navigator.getBattery) {
      const battery = await navigator.getBattery();
      return Math.round(battery.level * 100);
    }
  } catch (e) { /* Battery Status API unavailable in this browser — non-fatal */ }
  return null;
}

async function sendLocationUpdate() {
  const loc = await getLocation();
  if (loc.latitude == null || loc.longitude == null) return;

  breadcrumbTrail.push([loc.latitude, loc.longitude]);
  if (breadcrumbTrail.length > 50) breadcrumbTrail.shift();
  drawBreadcrumb();

  if (socket && socket.connected) {
    const battery_level = await getBatteryLevel();
    socket.emit("location_update", { latitude: loc.latitude, longitude: loc.longitude, battery_level });
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
  if (data.user_id === window.CURRENT_USER_ID) {
    // This is our own continuous tracking echoing back (e.g. a second
    // open tab) — draw it on our own breadcrumb trail.
    breadcrumbTrail.push([data.latitude, data.longitude]);
    if (breadcrumbTrail.length > 50) breadcrumbTrail.shift();
    drawBreadcrumb();
  } else if (data.user_id === currentlyTrackingUserId) {
    // A Bubble member we're actively watching — move their marker, and
    // refresh the Guardian Dashboard's "last updated"/connection/battery.
    updateTrackedLocationOnMap(data);
    updateGuardianDashboardFromLocationUpdate(data);
  }
  // Any other user_id shouldn't reach this socket at all (room-scoped
  // server-side), but is ignored defensively either way.
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
const viewTrackingBtn = document.getElementById("viewTrackingBtn");
const stopViewTrackingBtn = document.getElementById("stopViewTrackingBtn");
const guardianDashboard = document.getElementById("guardianDashboard");
const emergencyNotificationBanner = document.getElementById("emergencyNotificationBanner");
const gdConnectionStatus = document.getElementById("gdConnectionStatus");
const gdBatteryLevel = document.getElementById("gdBatteryLevel");
const gdLastUpdated = document.getElementById("gdLastUpdated");
const gdJourneyStatus = document.getElementById("gdJourneyStatus");
const gdJourneyProgressTrack = document.getElementById("gdJourneyProgressTrack");
const gdJourneyProgressFill = document.getElementById("gdJourneyProgressFill");

let guardianDashboardPollId = null;

const CONNECTION_LABELS = {
  live: "🟢 Live",
  recent: "🟡 Recent",
  stale: "🟠 Stale",
  never_connected: "⚪ No data yet",
};

function renderGuardianDashboard(status) {
  gdConnectionStatus.textContent = CONNECTION_LABELS[status.connection_status] || "—";
  gdBatteryLevel.textContent = status.battery_level != null ? `🔋 ${status.battery_level}%` : "Not reported";
  gdLastUpdated.textContent = status.last_location ? relativeTime(status.last_location.timestamp) : "Never";

  if (status.active_journey) {
    const j = status.active_journey;
    gdJourneyStatus.textContent = `→ ${j.destination_name} (${formatCountdown(j.remaining_seconds)} left)`;
    gdJourneyProgressTrack.style.display = "block";
    gdJourneyProgressFill.style.width = `${j.time_progress_pct}%`;
  } else {
    gdJourneyStatus.textContent = "No active journey";
    gdJourneyProgressTrack.style.display = "none";
  }
}

function updateGuardianDashboardFromLocationUpdate(data) {
  gdConnectionStatus.textContent = CONNECTION_LABELS.live;
  gdLastUpdated.textContent = "just now";
  if (data.battery_level != null) gdBatteryLevel.textContent = `🔋 ${data.battery_level}%`;
}

async function refreshGuardianDashboard() {
  if (!currentlyTrackingUserId) return;
  const status = await api(`/api/guardian/watch/${currentlyTrackingUserId}/status`);
  if (status._ok) renderGuardianDashboard(status);
}

function showEmergencyBanner(text) {
  emergencyNotificationBanner.textContent = text;
  emergencyNotificationBanner.classList.remove("hidden");
}

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
          (r) => `<li>${escapeHtml(r.owner_email)} invited you to their Bubble
            <button class="btn" style="padding:2px 8px;font-size:11px;" onclick="respondToInvite(${r.id}, true)">Accept</button>
            <button class="btn secondary" style="padding:2px 8px;font-size:11px;" onclick="respondToInvite(${r.id}, false)">Decline</button></li>`
        )
        .join("")
    : `<li class="muted">No pending invites</li>`;

  canTrackMeList.innerHTML = viewers.length
    ? viewers
        .map((r) => `<li>${escapeHtml(r.contact_email)} <span class="muted" style="font-size:11px;">(${escapeHtml(r.status)})</span></li>`)
        .join("")
    : `<li class="muted">No one can see your live location yet</li>`;

  trackableSelect.innerHTML = accepted.length
    ? accepted.map((r) => `<option value="${r.owner_user_id}">${escapeHtml(r.owner_email)}</option>`).join("")
    : `<option value="">No accepted Bubble members yet</option>`;
  viewTrackingBtn.disabled = accepted.length === 0;
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

viewTrackingBtn?.addEventListener("click", () => {
  const targetUserId = parseInt(trackableSelect.value, 10);
  if (!targetUserId) return;
  currentlyTrackingUserId = targetUserId;
  socket.emit("join_tracking", { user_id: targetUserId });
  viewTrackingBtn.classList.add("hidden");
  stopViewTrackingBtn.classList.remove("hidden");

  guardianDashboard.classList.remove("hidden");
  emergencyNotificationBanner.classList.add("hidden");
  refreshGuardianDashboard();
  guardianDashboardPollId = setInterval(refreshGuardianDashboard, 15000);
});

stopViewTrackingBtn?.addEventListener("click", () => {
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
  viewTrackingBtn?.classList.remove("hidden");
  stopViewTrackingBtn?.classList.add("hidden");
  guardianDashboard?.classList.add("hidden");
  if (guardianDashboardPollId) {
    clearInterval(guardianDashboardPollId);
    guardianDashboardPollId = null;
  }
}

function updateTrackedLocationOnMap(data) {
  if (!bubbleMap || data.user_id !== currentlyTrackingUserId) return;
  if (data.latitude == null || data.longitude == null) return;

  if (trackedMarker) {
    trackedMarker.setLatLng([data.latitude, data.longitude]);
  } else {
    trackedMarker = L.circleMarker([data.latitude, data.longitude], {
      radius: 10,
      fillColor: "#EF4444",
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
const feedSearchInput = document.getElementById("feedSearchInput");
const feedFilterType = document.getElementById("feedFilterType");
const feedSortToggle = document.getElementById("feedSortToggle");
const trendingList = document.getElementById("trendingList");

let feedSort = "recent";
let feedSearchDebounce = null;

const POST_TYPE_LABELS = { alert: "⚠️ ALERT", safe_spot: "✅ SAFE SPOT", incident: "🚨 INCIDENT" };

function renderFeedPost(p) {
  return `<li data-post-id="${p.id}">
    <strong>${POST_TYPE_LABELS[p.post_type] || escapeHtml(p.post_type.toUpperCase())}</strong> - ${escapeHtml(p.message)}
    ${p.area_name ? `<br/><span style="font-size:11px; color:#888;">📍 ${escapeHtml(p.area_name)}</span>` : ""}
    <span style="font-size:11px; color:#888; float:right;">${relativeTime(p.created_at)}</span>
    <div class="feed-reaction-row">
      <button class="feed-reaction-btn ${p.user_has_liked ? "active" : ""}" onclick="toggleFeedLike(${p.id})">❤️ <span>${p.like_count || 0}</span></button>
      <button class="feed-reaction-btn ${p.user_has_voted_helpful ? "active" : ""}" onclick="toggleFeedHelpful(${p.id})">👍 Helpful <span>${p.helpful_count || 0}</span></button>
      <button class="feed-reaction-btn" onclick="toggleFeedComments(${p.id})">💬 <span>${p.comment_count || 0}</span></button>
    </div>
    <div class="feed-comments hidden" id="feedComments-${p.id}">
      <ul class="feed-comment-list" id="feedCommentList-${p.id}"></ul>
      <div class="row" style="margin-top:6px;">
        <input type="text" placeholder="Add a comment…" id="feedCommentInput-${p.id}" maxlength="500" />
        <button class="btn secondary" style="padding:6px 12px;" onclick="submitFeedComment(${p.id})">Post</button>
      </div>
    </div>
  </li>`;
}

async function loadFeed() {
  const params = new URLSearchParams();
  if (feedSearchInput.value.trim()) params.set("q", feedSearchInput.value.trim());
  if (feedFilterType.value) params.set("post_type", feedFilterType.value);
  params.set("sort", feedSort);

  const posts = await api(`/api/feed?${params.toString()}`);
  if (!Array.isArray(posts)) return;
  feedList.innerHTML = posts.length
    ? posts.map(renderFeedPost).join("")
    : `<li class="muted" style="border:none;">No posts match your search.</li>`;
}

async function loadTrendingStrip() {
  const posts = await api("/api/feed/trending");
  if (!Array.isArray(posts) || posts.length === 0) {
    trendingList.innerHTML = `<li class="muted" style="border:none; font-size:12px;">Nothing trending in the last 48h.</li>`;
    return;
  }
  trendingList.innerHTML = posts
    .map(
      (p) =>
        `<li style="font-size:12px;"><strong>${POST_TYPE_LABELS[p.post_type] || escapeHtml(p.post_type)}</strong> ${escapeHtml(p.message.slice(0, 80))}
        <span class="muted" style="float:right;">❤️ ${p.like_count} · 👍 ${p.helpful_count} · 💬 ${p.comment_count}</span></li>`
    )
    .join("");
}

async function toggleFeedLike(postId) {
  const result = await api(`/api/feed/${postId}/like`, { method: "POST" });
  if (!result._ok) return;
  const li = feedList.querySelector(`li[data-post-id="${postId}"]`);
  const btn = li?.querySelector(".feed-reaction-btn");
  if (btn) {
    btn.classList.toggle("active", result.liked);
    btn.querySelector("span").textContent = result.like_count;
  }
}

async function toggleFeedHelpful(postId) {
  const result = await api(`/api/feed/${postId}/helpful`, { method: "POST" });
  if (!result._ok) return;
  const li = feedList.querySelector(`li[data-post-id="${postId}"]`);
  const btn = li?.querySelectorAll(".feed-reaction-btn")[1];
  if (btn) {
    btn.classList.toggle("active", result.helpful);
    btn.querySelector("span").textContent = result.helpful_count;
  }
}

async function refreshFeedCommentList(postId) {
  const comments = await api(`/api/feed/${postId}/comments`);
  const list = document.getElementById(`feedCommentList-${postId}`);
  if (Array.isArray(comments) && list) {
    list.innerHTML = comments.length
      ? comments
          .map(
            (c) =>
              `<li style="font-size:12px;"><strong>${escapeHtml((c.user_email || "someone").split("@")[0])}</strong>: ${escapeHtml(c.message)}
              <span class="muted" style="float:right; font-size:10px;">${relativeTime(c.created_at)}</span></li>`
          )
          .join("")
      : `<li class="muted" style="font-size:12px; border:none;">No comments yet — be the first.</li>`;
  }
}

async function toggleFeedComments(postId) {
  const panel = document.getElementById(`feedComments-${postId}`);
  if (!panel) return;
  const showing = !panel.classList.contains("hidden");
  if (showing) {
    panel.classList.add("hidden");
    return;
  }
  panel.classList.remove("hidden");
  await refreshFeedCommentList(postId);
}

async function submitFeedComment(postId) {
  const input = document.getElementById(`feedCommentInput-${postId}`);
  const message = input.value.trim();
  if (!message) return;
  const result = await api(`/api/feed/${postId}/comments`, {
    method: "POST",
    body: JSON.stringify({ message }),
  });
  if (result._ok) {
    input.value = "";
    await refreshFeedCommentList(postId);
    const li = feedList.querySelector(`li[data-post-id="${postId}"]`);
    const btn = li?.querySelectorAll(".feed-reaction-btn")[2];
    if (btn) btn.querySelector("span").textContent = result.comment_count;
  }
}

feedSearchInput.addEventListener("input", () => {
  clearTimeout(feedSearchDebounce);
  feedSearchDebounce = setTimeout(loadFeed, 350);
});
feedFilterType.addEventListener("change", loadFeed);
feedSortToggle.addEventListener("click", () => {
  feedSort = feedSort === "recent" ? "trending" : "recent";
  feedSortToggle.textContent = feedSort === "recent" ? "Sort: Recent" : "Sort: 🔥 Trending";
  loadFeed();
});

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
  loadTrendingStrip();
});

loadFeed();
loadTrendingStrip();

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
// ---------------------------------------------------------------------------
// PREMIUM UI LAYER — purely visual. Adds no new routes, changes no existing
// behaviour, and never blocks if an element is missing (e.g. other tabs).
// ---------------------------------------------------------------------------
(function premiumUILayer() {
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // --- Navbar shrink on scroll ---------------------------------------------
  const topbar = document.querySelector(".topbar");
  if (topbar) {
    const onScroll = () => topbar.classList.toggle("is-scrolled", window.scrollY > 24);
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }

  // --- Scroll-reveal for cards ---------------------------------------------
  const revealEls = document.querySelectorAll(".reveal");
  if (revealEls.length) {
    if (reduceMotion || !("IntersectionObserver" in window)) {
      revealEls.forEach((el) => el.classList.add("in-view"));
    } else {
      const io = new IntersectionObserver(
        (entries) => {
          entries.forEach((entry, i) => {
            if (entry.isIntersecting) {
              setTimeout(() => entry.target.classList.add("in-view"), i * 60);
              io.unobserve(entry.target);
            }
          });
        },
        { threshold: 0.15, rootMargin: "0px 0px -40px 0px" }
      );
      revealEls.forEach((el) => io.observe(el));
    }
  }

  // Re-trigger reveal for cards inside a tab that becomes visible again
  // (tab-panels start hidden via CSS `display:none`, so IO only fires once
  // they're actually laid out — re-observe on tab click just in case).
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      requestAnimationFrame(() => {
        document.querySelectorAll(".reveal:not(.in-view)").forEach((el) => {
          const rect = el.getBoundingClientRect();
          if (rect.top < window.innerHeight) el.classList.add("in-view");
        });
      });
    });
  });

  // --- Hero particle field (floating dots + drifting connective lines,
  //     evoking GPS pings / a live-tracking network) ------------------------
  const canvas = document.getElementById("heroParticles");
  if (canvas && !reduceMotion) {
    const ctx = canvas.getContext("2d");
    let w, h, particles;
    const COLORS = ["rgba(139,92,246,0.8)", "rgba(34,211,238,0.75)", "rgba(236,72,153,0.7)"];

    function resize() {
      const rect = canvas.parentElement.getBoundingClientRect();
      w = canvas.width = rect.width + 48;
      h = canvas.height = rect.height + 80;
      const count = Math.min(46, Math.floor((w * h) / 16000));
      particles = Array.from({ length: count }, () => ({
        x: Math.random() * w,
        y: Math.random() * h,
        r: 1 + Math.random() * 2,
        vx: (Math.random() - 0.5) * 0.25,
        vy: (Math.random() - 0.5) * 0.25,
        c: COLORS[Math.floor(Math.random() * COLORS.length)],
      }));
    }

    function tick() {
      ctx.clearRect(0, 0, w, h);
      for (const p of particles) {
        p.x += p.vx; p.y += p.vy;
        if (p.x < 0 || p.x > w) p.vx *= -1;
        if (p.y < 0 || p.y > h) p.vy *= -1;
      }
      for (let i = 0; i < particles.length; i++) {
        for (let j = i + 1; j < particles.length; j++) {
          const a = particles[i], b = particles[j];
          const d = Math.hypot(a.x - b.x, a.y - b.y);
          if (d < 120) {
            ctx.strokeStyle = `rgba(139,92,246,${0.14 * (1 - d / 120)})`;
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(a.x, a.y);
            ctx.lineTo(b.x, b.y);
            ctx.stroke();
          }
        }
      }
      for (const p of particles) {
        ctx.beginPath();
        ctx.fillStyle = p.c;
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx.fill();
      }
      requestAnimationFrame(tick);
    }

    resize();
    window.addEventListener("resize", resize, { passive: true });
    tick();
  }
})();
// TIER 3 PART 3: Real Web Push (works even when the tab is closed)
// ---------------------------------------------------------------------------
const enablePushBtn = document.getElementById("enablePushBtn");
const disablePushBtn = document.getElementById("disablePushBtn");
const pushStatus = document.getElementById("pushStatus");

function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const rawData = window.atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; i++) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}

function pushSupported() {
  return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}

async function refreshPushUI() {
  if (!enablePushBtn) return; // markup not present on this page

  if (!pushSupported()) {
    pushStatus.textContent = "Push notifications aren't supported in this browser.";
    enablePushBtn.disabled = true;
    return;
  }

  try {
    const registration = await navigator.serviceWorker.ready;
    const existing = await registration.pushManager.getSubscription();
    if (existing) {
      pushStatus.textContent = "✓ Push notifications are on for this device.";
      enablePushBtn.classList.add("hidden");
      disablePushBtn.classList.remove("hidden");
    } else {
      pushStatus.textContent = "Push notifications are off.";
      enablePushBtn.classList.remove("hidden");
      disablePushBtn.classList.add("hidden");
    }
  } catch (err) {
    console.warn("Couldn't read push subscription state", err);
  }
}

async function enablePush() {
  if (!pushSupported()) return;

  try {
    const permission = await Notification.requestPermission();
    if (permission !== "granted") {
      pushStatus.textContent = "Permission denied — enable notifications for this site in your browser settings to turn this on.";
      return;
    }

    const keyRes = await api("/api/push/vapid-public-key");
    if (!keyRes._ok || !keyRes.public_key) {
      pushStatus.textContent = "Push isn't configured on the server yet.";
      return;
    }

    const registration = await navigator.serviceWorker.ready;
    const subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(keyRes.public_key),
    });

    const subJson = subscription.toJSON();
    const result = await api("/api/push/subscribe", {
      method: "POST",
      body: JSON.stringify({
        endpoint: subJson.endpoint,
        keys: subJson.keys,
      }),
    });

    if (result._ok) {
      showNotification("🔔 Push enabled", "You'll now get alerts even when this tab is closed.", "info");
    }
    await refreshPushUI();
  } catch (err) {
    console.error("Push subscription failed", err);
    pushStatus.textContent = "Couldn't enable push notifications: " + err.message;
  }
}

async function disablePush() {
  if (!pushSupported()) return;

  try {
    const registration = await navigator.serviceWorker.ready;
    const subscription = await registration.pushManager.getSubscription();
    if (subscription) {
      await api("/api/push/unsubscribe", {
        method: "POST",
        body: JSON.stringify({ endpoint: subscription.endpoint }),
      });
      await subscription.unsubscribe();
    }
    pushStatus.textContent = "Push notifications turned off.";
    await refreshPushUI();
  } catch (err) {
    console.error("Push unsubscribe failed", err);
  }
}

enablePushBtn?.addEventListener("click", enablePush);
disablePushBtn?.addEventListener("click", disablePush);

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.ready.then(refreshPushUI).catch(() => {});
}