/**
 * SafeHer — Safety Map v2 additions: reverse geocoding, search history.
 *
 * This file is additive, same philosophy as safety-map.js's own header
 * comment: it reuses globals already defined in main.js/safety-map.js
 * (leafletMap, escapeHtml, showNotification, getLocation) and never
 * removes or rewires any existing audit/route click handling — it just
 * layers a second, independent click listener onto the same map for the
 * new "tap anywhere for address + quick actions" behaviour, and a small
 * recent-searches list under the search bar.
 *
 * Search autocomplete itself lives in safety-map.js's initMapSearch()
 * (now pointed at /api/places/autocomplete) — not duplicated here.
 */
(function () {
  "use strict";

  function toast(title, message, type) {
    if (typeof showNotification === "function") showNotification(title, message, type || "info");
  }

  // ---------------------------------------------------------------------
  // Search history (localStorage, recent searches shown below the bar)
  //
  // Mutual exclusivity with the suggestions dropdown (#mapSearchSuggestions,
  // owned by safety-map.js's initMapSearch()) is enforced from here via
  // window.safeherShowSearchHistory()/safeherHideSearchHistory() — the two
  // panels never rely on independently-set "hidden" classes racing each
  // other; showing one always explicitly hides the other first.
  // ---------------------------------------------------------------------
  const HISTORY_KEY = "safeher_search_history";
  const HISTORY_MAX = 8;

  // Dismissed via the × button — resets on page reload (session-only), as
  // opposed to Clear, which deletes the underlying localStorage history.
  let dismissedThisSession = false;

  function loadHistory() {
    try {
      return JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]");
    } catch (e) {
      return [];
    }
  }

  function saveHistory(list) {
    try {
      localStorage.setItem(HISTORY_KEY, JSON.stringify(list));
    } catch (e) {
      /* private-mode/quota — history just won't persist this session */
    }
  }

  // Exposed globally so safety-map.js's initMapSearch() can call it right
  // after a search actually returns results (see the small addition in
  // that function), without this file needing to touch the search input
  // itself.
  window.recordSearchHistory = function (query) {
    if (!query || query.trim().length < 3) return;
    const trimmed = query.trim();
    let list = loadHistory().filter((q) => q.toLowerCase() !== trimmed.toLowerCase());
    list.unshift(trimmed);
    saveHistory(list.slice(0, HISTORY_MAX));
  };

  function renderHistoryMarkup() {
    const el = document.getElementById("mapSearchHistory");
    if (!el) return false;
    const list = loadHistory();
    if (!list.length) {
      el.innerHTML = "";
      return false;
    }
    el.innerHTML =
      `<button type="button" id="mapSearchHistoryCloseBtn" class="map-search-history-close" aria-label="Dismiss recent searches">×</button>
      <div class="map-search-history-header">
        <span class="muted" style="font-size:11px;">Recent searches</span>
        <button type="button" id="mapSearchHistoryClear" class="map-search-history-clear">Clear</button>
      </div>` +
      list
        .map(
          (q, i) =>
            `<button type="button" class="map-search-history-chip" data-history-index="${i}">
              <span aria-hidden="true">🕘</span> ${escapeHtml(q)}
            </button>`
        )
        .join("");
    return true;
  }

  // The ONLY place either panel's "hidden" class gets removed — both
  // safety-map.js (for suggestions) and the code below (for history) call
  // through these two functions rather than toggling classes directly, so
  // the two dropdowns can never both be visible at once.
  window.safeherShowSearchHistory = function () {
    if (dismissedThisSession) return;
    const historyEl = document.getElementById("mapSearchHistory");
    const suggestionsEl = document.getElementById("mapSearchSuggestions");
    if (!historyEl) return;
    suggestionsEl?.classList.add("hidden"); // enforce exclusivity from this side too
    const hasHistory = renderHistoryMarkup();
    historyEl.classList.toggle("hidden", !hasHistory);
  };

  window.safeherHideSearchHistory = function () {
    document.getElementById("mapSearchHistory")?.classList.add("hidden");
  };

  function initSearchHistory() {
    const el = document.getElementById("mapSearchHistory");
    const input = document.getElementById("mapSearchInput");
    if (!el || !input) return;

    el.addEventListener("click", (e) => {
      if (e.target.closest("#mapSearchHistoryCloseBtn")) {
        dismissedThisSession = true;
        window.safeherHideSearchHistory();
        return;
      }
      if (e.target.closest("#mapSearchHistoryClear")) {
        saveHistory([]);
        window.safeherHideSearchHistory(); // nothing left to show
        return;
      }
      const chip = e.target.closest("[data-history-index]");
      if (!chip) return;
      const q = loadHistory()[parseInt(chip.dataset.historyIndex, 10)];
      if (q) {
        input.value = q;
        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.focus();
      }
    });

    // Focus with an empty box -> recent searches (Google/Apple Maps-style);
    // typing anything is handled entirely by safety-map.js's own input
    // listener, which calls safeherHideSearchHistory()/safeherShowSearchHistory()
    // as appropriate — not duplicated here.
    input.addEventListener("focus", () => {
      if (!input.value) window.safeherShowSearchHistory();
    });

    document.addEventListener("click", (e) => {
      if (!e.target.closest(".map-search-bar")) el.classList.add("hidden");
    });
  }

  // ---------------------------------------------------------------------
  // Reverse geocoding on tap — a second, independent click listener.
  // Runs alongside (not instead of) the existing audit-mode /
  // route-mode click handling in main.js's initMap(), so nothing about
  // those flows changes; this just adds an address + quick-actions
  // popup near wherever the user tapped.
  // ---------------------------------------------------------------------
  let reverseGeocodeDebounce = null;

  async function showReverseGeocodePopup(lat, lng) {
    if (!leafletMap || typeof maplibregl === "undefined") return;

    const popup = new maplibregl.Popup({ closeOnClick: true, className: "safeher-reverse-geocode-popup" })
      .setLngLat([lng, lat])
      .setHTML(`<div class="rg-popup-body"><span class="muted">Looking up address…</span></div>`)
      .addTo(leafletMap._maplibreMap);

    let addressLine = `${lat.toFixed(5)}, ${lng.toFixed(5)}`;
    let placeName = "Selected location";
    try {
      const resp = await fetch(`/api/places/reverse?lat=${lat}&lng=${lng}`, { headers: { Accept: "application/json" } });
      const data = await resp.json();
      if (data.available && data.display_name) {
        addressLine = data.display_name;
        placeName = data.address_line1 || data.display_name.split(",")[0] || placeName;
      }
    } catch (err) {
      // Reverse geocoding unavailable — the coordinate fallback above is still honest and useful.
    }

    const safeName = (window.escapeHtml || ((s) => s))(placeName);
    const safeAddr = (window.escapeHtml || ((s) => s))(addressLine);

    popup.setHTML(`
      <div class="rg-popup-body">
        <strong class="rg-popup-title">${safeName}</strong>
        <div class="rg-popup-address muted">${safeAddr}</div>
        <div class="rg-popup-coords muted">${lat.toFixed(5)}, ${lng.toFixed(5)}</div>
        <div class="rg-popup-actions">
          <button type="button" class="btn secondary rg-action-journey">🧭 Start Journey</button>
          <button type="button" class="btn secondary rg-action-share">📤 Share</button>
          <button type="button" class="btn secondary rg-action-save">⭐ Save</button>
        </div>
      </div>
    `);

    const el = popup.getElement && popup.getElement();
    if (!el) return;

    el.querySelector(".rg-action-journey")?.addEventListener("click", () => {
      popup.remove();
      if (typeof window.safeherGoToJourneyStart === "function") window.safeherGoToJourneyStart();
      const destInput = document.getElementById("journeyDestination");
      if (destInput) destInput.value = placeName;
      toast("Destination set", `"${placeName}" — set an ETA to start your journey.`, "info");
    });

    el.querySelector(".rg-action-share")?.addEventListener("click", async () => {
      const shareText = `${placeName}\n${addressLine}\nhttps://www.openstreetmap.org/?mlat=${lat}&mlon=${lng}#map=17/${lat}/${lng}`;
      try {
        if (navigator.share) {
          await navigator.share({ title: placeName, text: shareText });
        } else {
          await navigator.clipboard.writeText(shareText);
          toast("Copied", "Location details copied — paste them anywhere to share.", "info");
        }
      } catch (err) {
        // User cancelled the native share sheet, or clipboard access was denied — either way, no error toast needed.
      }
    });

    el.querySelector(".rg-action-save")?.addEventListener("click", () => {
      if (typeof window.safeherSaveToFavorites === "function") {
        window.safeherSaveToFavorites(placeName, lat, lng);
      }
      popup.remove();
    });
  }

  function initReverseGeocodeOnTap() {
    function whenMapReady(fn) {
      if (typeof leafletMap !== "undefined" && leafletMap) fn();
      else setTimeout(() => whenMapReady(fn), 100);
    }

    whenMapReady(() => {
      leafletMap.on("click", (e) => {
        if (window.__mapLongPressHandled) return; // let the long-press "Report an Issue" flow own this tap
        if (typeof mapMode !== "undefined" && mapMode === "route") return; // Route Mode's A/B tap flow owns this tap
        clearTimeout(reverseGeocodeDebounce);
        reverseGeocodeDebounce = setTimeout(() => showReverseGeocodePopup(e.latlng.lat, e.latlng.lng), 50);
      });
    });
  }

  // ---------------------------------------------------------------------
  // Boot — mirrors safety-map.js's own DOMContentLoaded pattern
  // ---------------------------------------------------------------------
  function boot() {
    initSearchHistory();
    initReverseGeocodeOnTap();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();