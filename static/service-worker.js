// ---------------------------------------------------------------------------
// SafeHer Service Worker — offline-first shell caching + SOS action queue
// ---------------------------------------------------------------------------
const CACHE_NAME = "safeher-shell-v1";
const OFFLINE_URL = "/offline";

// Everything needed to render the app shell without a network connection.
const APP_SHELL = [
  "/",
  "/offline",
  "/static/css/style.css",
  "/static/js/main.js",
  "/static/manifest.json",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

// ---------------------------------------------------------------------------
// Install: pre-cache the app shell
// ---------------------------------------------------------------------------
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL)).catch((err) => {
      // Don't let one missing/uncachable asset (e.g. a 3rd-party CDN script)
      // block the whole install.
      console.warn("SafeHer SW: partial cache failure", err);
    })
  );
  self.skipWaiting();
});

// ---------------------------------------------------------------------------
// Activate: clean up old cache versions
// ---------------------------------------------------------------------------
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// ---------------------------------------------------------------------------
// Fetch: cache-first for the app shell/static assets, network-first with
// offline fallback for page navigations, and pass through API calls
// (those are handled by the queueing logic in main.js instead).
// ---------------------------------------------------------------------------
self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);

  // Page navigations: try network, fall back to cached shell, then /offline
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req).catch(() =>
        caches.match(req).then((cached) => cached || caches.match(OFFLINE_URL))
      )
    );
    return;
  }

  // Static assets: cache-first
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.match(req).then((cached) => {
        if (cached) return cached;
        return fetch(req)
          .then((res) => {
            const clone = res.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(req, clone));
            return res;
          })
          .catch(() => cached);
      })
    );
    return;
  }

  // Everything else (API calls, socket.io polling, etc.) — just let it hit
  // the network normally. If it fails, main.js's offline-queue logic
  // (window 'offline' event + queued fetch wrapper) is what handles it,
  // not the service worker.
});

// ---------------------------------------------------------------------------
// TIER 3 PART 3: Real Web Push — shows a native OS notification even when
// every SafeHer tab is closed. The server (utils/push.py via app.py) sends
// a JSON payload through pywebpush on SOS / high-risk-area / check-in-
// expiry events; this is what turns that payload into a visible alert.
// ---------------------------------------------------------------------------
self.addEventListener("push", (event) => {
  let payload = { title: "SafeHer alert", body: "You have a new safety alert.", url: "/" };

  if (event.data) {
    try {
      payload = { ...payload, ...event.data.json() };
    } catch (err) {
      // Non-JSON push payload (shouldn't happen from our own server, but
      // don't let a malformed payload crash the push handler).
      payload.body = event.data.text() || payload.body;
    }
  }

  const options = {
    body: payload.body,
    icon: "/static/icons/icon-192.png",
    badge: "/static/icons/icon-192.png",
    tag: payload.tag || "safeher-alert",
    data: { url: payload.url || "/" },
    requireInteraction: payload.critical === true,
  };

  event.waitUntil(self.registration.showNotification(payload.title, options));
});

// Clicking the native notification focuses an existing SafeHer tab if one
// is open, otherwise opens a new one.
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const targetUrl = (event.notification.data && event.notification.data.url) || "/";

  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if (client.url.includes(self.location.origin) && "focus" in client) {
          return client.focus();
        }
      }
      if (self.clients.openWindow) {
        return self.clients.openWindow(targetUrl);
      }
    })
  );
});