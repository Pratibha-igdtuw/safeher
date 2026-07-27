/**
 * SafeHer — Safety Map & Guardian enhancements.
 *
 * This file is additive: it reuses globals already defined in main.js
 * (api, escapeHtml, showNotification, scoreColor, getLocation, leafletMap,
 * handleRouteModeClick, routePoints, mapMode, SERVICE_ICONS, activeJourney,
 * getBatteryLevel) rather than redefining them, and it never touches
 * backend logic or removes any existing route/feature.
 *
 * Sections:
 *   1. Shared helpers (distance/ETA formatting, toast wrapper)
 *   2. Location search + autocomplete (Nominatim — free, no API key)
 *   3. "My Location" button
 *   4. Favourite places (localStorage; TODO backend sync)
 *   5. Safety Layers panel (heatmap, police/hospital/pharmacy, lighting,
 *      women's safety, safe zones, community reports, crime*, traffic*)
 *      (*crime/traffic have no connected data source yet — honest
 *      placeholders per the brief's "TODO instead of removing" instruction)
 *   6. Nearby Services list (sorted by real distance, with ETA)
 *   7. Long-press "Report an Issue" + its modal
 *   8. Journey Info Card (floating, live-updating)
 *   9. Left info panel (safety score / weather / nearest help / device
 *      signals)
 *  10. Guardian tab device-signal enrichment
 *  11. AI proactive safety suggestions
 *  12. FAB action hooks (window.safeher*) used by premium-enhancements.js
 */
(function () {
  "use strict";

  // ---------------------------------------------------------------------
  // 1. Shared helpers
  // ---------------------------------------------------------------------

  /** Rough walking/driving ETA from a distance in km. Deliberately simple
   * (average urban speeds), always labeled "approx" in the UI — this is
   * not meant to compete with real routing ETAs (those come from OSRM via
   * /api/route-safety), just a quick glance for the nearby-services list. */
  function estimateEtaMinutes(distanceKm) {
    const walkMin = Math.round((distanceKm / 5) * 60); // ~5 km/h walking
    const driveMin = Math.max(1, Math.round((distanceKm / 22) * 60)); // ~22 km/h urban driving
    return { walkMin, driveMin };
  }

  function fmtDistance(km) {
    if (km == null) return "distance unknown";
    return km < 1 ? `${Math.round(km * 1000)} m` : `${km.toFixed(1)} km`;
  }

  function toast(title, message, type) {
    if (typeof showNotification === "function") showNotification(title, message, type || "info");
  }

  function safeApi(path, options) {
    return typeof api === "function" ? api(path, options) : Promise.resolve({ _ok: false });
  }

  // Waits for leafletMap (created by main.js's initMap(), ~100ms after
  // load) before wiring up anything that touches it.
  function whenMapReady(fn) {
    if (typeof leafletMap !== "undefined" && leafletMap) {
      fn();
    } else {
      setTimeout(() => whenMapReady(fn), 100);
    }
  }

  // ---------------------------------------------------------------------
  // 2. Location search + autocomplete
  // ---------------------------------------------------------------------
  function initMapSearch() {
    const input = document.getElementById("mapSearchInput");
    const suggestionsEl = document.getElementById("mapSearchSuggestions");
    const clearBtn = document.getElementById("mapSearchClearBtn");
    if (!input || !suggestionsEl) return;

    let debounceTimer = null;
    let activeResults = [];
    let searchMarker = null;

    function closeSuggestions() {
      suggestionsEl.classList.add("hidden");
      suggestionsEl.innerHTML = "";
    }

    async function runSearch(query) {
      if (!query || query.length < 3) {
        closeSuggestions();
        return;
      }
      try {
        // Geocoding goes through our own /api/geocode, which proxies
        // Nominatim (OpenStreetMap) server-side — the browser can't call
        // nominatim.openstreetmap.org directly because our CSP's
        // connect-src is locked to 'self' (see app.py). Biased toward the
        // current map view so "Central Hospital" near Delhi doesn't return
        // a same-named place in another country.
        const center = leafletMap ? leafletMap.getCenter() : { lat: 28.7041, lng: 77.1025 };
        const params = new URLSearchParams({ q: query });
        if (leafletMap) {
          params.set("min_lng", center.lng - 0.6);
          params.set("max_lat", center.lat + 0.6);
          params.set("max_lng", center.lng + 0.6);
          params.set("min_lat", center.lat - 0.6);
        }
        const resp = await fetch(`/api/geocode?${params.toString()}`, {
          headers: { Accept: "application/json" },
        });
        if (!resp.ok) throw new Error("geocode failed");
        activeResults = await resp.json();
        renderSuggestions();
      } catch (err) {
        suggestionsEl.innerHTML = `<li class="map-search-empty">Couldn't reach the search service right now.</li>`;
        suggestionsEl.classList.remove("hidden");
      }
    }

    function renderSuggestions() {
      if (!activeResults.length) {
        suggestionsEl.innerHTML = `<li class="map-search-empty">No matches. Try a fuller address or a nearby landmark.</li>`;
        suggestionsEl.classList.remove("hidden");
        return;
      }
      suggestionsEl.innerHTML = activeResults
        .map(
          (r, i) =>
            `<li class="map-search-suggestion" data-index="${i}" role="option">
              <span class="map-search-suggestion-icon" aria-hidden="true">📍</span>
              <span class="map-search-suggestion-text">${escapeHtml(r.display_name)}</span>
            </li>`
        )
        .join("");
      suggestionsEl.classList.remove("hidden");
    }

    function selectResult(result) {
      const lat = parseFloat(result.lat);
      const lng = parseFloat(result.lon);
      if (Number.isNaN(lat) || Number.isNaN(lng)) return;

      input.value = result.display_name;
      closeSuggestions();
      clearBtn?.classList.remove("hidden");

      if (!leafletMap) return;
      leafletMap.flyTo([lat, lng], 16, { duration: 1.1 });

      if (searchMarker) leafletMap.removeLayer(searchMarker);
      searchMarker = L.marker([lat, lng], {
        icon: L.divIcon({ html: "📍", className: "marker-drop-anim", iconSize: [28, 28] }),
      })
        .addTo(leafletMap)
        .bindPopup(`<strong>${escapeHtml(result.display_name)}</strong>`)
        .openPopup();

      // If Route Mode is active and the user already dropped point A,
      // treat this search result as point B and auto-calculate the route —
      // reuses the exact same click-handling path a manual tap would.
      if (typeof mapMode !== "undefined" && mapMode === "route" && typeof routePoints !== "undefined" && routePoints.length === 1) {
        handleRouteModeClick({ lat, lng });
      }
    }

    input.addEventListener("input", () => {
      clearBtn?.classList.toggle("hidden", !input.value);
      clearTimeout(debounceTimer);
      const query = input.value.trim();
      debounceTimer = setTimeout(() => runSearch(query), 350);
    });

    suggestionsEl.addEventListener("click", (e) => {
      const li = e.target.closest(".map-search-suggestion");
      if (!li) return;
      const result = activeResults[parseInt(li.dataset.index, 10)];
      if (result) selectResult(result);
    });

    clearBtn?.addEventListener("click", () => {
      input.value = "";
      clearBtn.classList.add("hidden");
      closeSuggestions();
      input.focus();
    });

    document.addEventListener("click", (e) => {
      if (!e.target.closest(".map-search-bar")) closeSuggestions();
    });
  }

  // ---------------------------------------------------------------------
  // 3. "My Location" button
  // ---------------------------------------------------------------------
  function initMyLocationButton() {
    const btn = document.getElementById("mapMyLocationBtn");
    if (!btn) return;

    btn.addEventListener("click", async () => {
      btn.classList.add("is-locating");
      const loc = await getLocation();
      btn.classList.remove("is-locating");

      if (loc.latitude == null || !leafletMap) {
        toast("Location unavailable", "Turn on location access for this site and try again.", "info");
        return;
      }
      leafletMap.flyTo([loc.latitude, loc.longitude], 16, { duration: 1 });
      const marker = L.circleMarker([loc.latitude, loc.longitude], {
        radius: 9,
        fillColor: "#7C3AED",
        color: "#fff",
        weight: 3,
        fillOpacity: 0.9,
        className: "marker-drop-anim",
      }).addTo(leafletMap);
      setTimeout(() => leafletMap.removeLayer(marker), 4000); // transient "you are here" ping
      refreshLeftPanel(loc);
    });
  }

  // ---------------------------------------------------------------------
  // 4. Favourite places
  // ---------------------------------------------------------------------
  const FAVORITES_KEY = "safeher_favorite_places"; // TODO: sync to backend (new /api/favorites) once available — localStorage only for now
  const FAVORITE_SLOTS = [
    { id: "home", icon: "🏠", label: "Home" },
    { id: "office", icon: "🏢", label: "Office" },
    { id: "college", icon: "🎓", label: "College" },
    { id: "hostel", icon: "🏨", label: "Hostel" },
  ];

  function loadFavorites() {
    try {
      return JSON.parse(localStorage.getItem(FAVORITES_KEY) || "{}");
    } catch (e) {
      return {};
    }
  }

  function saveFavorites(favs) {
    try {
      localStorage.setItem(FAVORITES_KEY, JSON.stringify(favs));
    } catch (e) {
      /* private-mode/quota — favorites just won't persist this session */
    }
  }

  function initFavorites() {
    const row = document.getElementById("mapFavoritesRow");
    if (!row) return;

    function render() {
      const favs = loadFavorites();
      row.innerHTML = FAVORITE_SLOTS.map((slot) => {
        const saved = favs[slot.id];
        return `<button type="button" class="map-favorite-chip ${saved ? "is-saved" : ""}" data-slot="${slot.id}" title="${saved ? "Go to " + slot.label : "Save current map center as " + slot.label}">
          <span aria-hidden="true">${slot.icon}</span> ${slot.label}${saved ? "" : " <em>(tap to save)</em>"}
        </button>`;
      }).join("");
    }

    row.addEventListener("click", (e) => {
      const chip = e.target.closest(".map-favorite-chip");
      if (!chip || !leafletMap) return;
      const slotId = chip.dataset.slot;
      const favs = loadFavorites();
      const slot = FAVORITE_SLOTS.find((s) => s.id === slotId);

      if (favs[slotId]) {
        leafletMap.flyTo([favs[slotId].lat, favs[slotId].lng], 16, { duration: 1 });
        toast(`${slot.icon} ${slot.label}`, "Jumped to your saved location.", "info");
      } else {
        const center = leafletMap.getCenter();
        favs[slotId] = { lat: center.lat, lng: center.lng };
        saveFavorites(favs);
        render();
        toast("Saved", `This map location is now your ${slot.label}.`, "info");
      }
    });

    render();
  }

  // ---------------------------------------------------------------------
  // 5. Safety Layers
  // ---------------------------------------------------------------------
  // Per-type nearby-service layers (police/hospital/pharmacy), independent
  // of the existing combined refreshServiceLayer() so each can be toggled
  // on its own. Reuses the exact same /api/nearby-services endpoint.
  const singleTypeLayerMarkers = {}; // { police: [markers], hospital: [...], ... }
  const activeSingleTypeLayers = new Set();

  async function refreshSingleTypeLayer(type) {
    if (!leafletMap || !activeSingleTypeLayers.has(type)) return;
    const center = leafletMap.getCenter();
    const data = await safeApi(`/api/nearby-services?lat=${center.lat}&lng=${center.lng}&type=${type}`);
    (singleTypeLayerMarkers[type] || []).forEach((m) => leafletMap.removeLayer(m));
    singleTypeLayerMarkers[type] = [];
    if (!data._ok) return;

    (data.results || []).forEach((s) => {
      if (s.lat == null || s.lng == null) return;
      const icon = L.divIcon({ html: SERVICE_ICONS[s.type] || "📍", className: "service-div-icon marker-drop-anim", iconSize: [22, 22] });
      const marker = L.marker([s.lat, s.lng], { icon })
        .addTo(leafletMap)
        .bindPopup(
          `<strong>${escapeHtml(s.name)}</strong> (${escapeHtml(s.type)})<br/>${s.distance_km != null ? fmtDistance(s.distance_km) + " away<br/>" : ""}${s.phone && s.phone !== "N/A" ? `<a href="tel:${escapeHtml(s.phone)}">📞 ${escapeHtml(s.phone)}</a>` : ""}`
        );
      singleTypeLayerMarkers[type].push(marker);
    });
  }

  // Street Lighting / Women's Safety / Safe Zones are all derived from the
  // real community-audit data already loaded via /api/audits (lighting and
  // security sub-scores, and overall_score for "safe" zones) — no new
  // backend endpoint needed, and no invented numbers.
  const auditDerivedLayerCircles = { lighting: [], womens_safety: [], safe_zones: [] };

  async function refreshAuditDerivedLayer(layerKey) {
    if (!leafletMap || !activeSingleTypeLayers.has(layerKey)) return;
    const data = await safeApi("/api/audits");
    (auditDerivedLayerCircles[layerKey] || []).forEach((c) => leafletMap.removeLayer(c));
    auditDerivedLayerCircles[layerKey] = [];
    if (!Array.isArray(data)) return;

    data.forEach((audit) => {
      let value, label, goodColor = "#22C55E", badColor = "#EF4444", midColor = "#F59E0B";
      if (layerKey === "lighting") { value = audit.lighting; label = "Lighting"; }
      else if (layerKey === "womens_safety") { value = audit.security; label = "Security presence"; }
      else if (layerKey === "safe_zones") { value = audit.overall_score >= 75 ? 4 : audit.overall_score >= 60 ? 3 : 0; label = "Overall safety"; }
      if (value == null) return;
      if (layerKey === "safe_zones" && value < 3) return; // only show genuinely safe spots on this layer

      const color = value >= 3 ? goodColor : value === 2 ? midColor : badColor;
      const circle = L.circle([audit.latitude, audit.longitude], {
        radius: 120,
        color,
        fillColor: color,
        fillOpacity: 0.25,
        weight: 1,
      })
        .addTo(leafletMap)
        .bindPopup(`<strong>${escapeHtml(audit.area_name || "Unnamed area")}</strong><br/>${label}: ${value}/4<br/>From a community safety audit`);
      auditDerivedLayerCircles[layerKey].push(circle);
    });
  }

  // Community reports (from the new long-press "Report an Issue" feature).
  let communityReportMarkers = [];
  async function refreshCommunityReportsLayer() {
    if (!leafletMap || !activeSingleTypeLayers.has("community_reports")) return;
    const center = leafletMap.getCenter();
    const data = await safeApi(`/api/map-reports?lat=${center.lat}&lng=${center.lng}&radius_km=6`);
    communityReportMarkers.forEach((m) => leafletMap.removeLayer(m));
    communityReportMarkers = [];
    if (!data._ok) return;

    (data.reports || []).forEach((r) => {
      const marker = L.marker([r.latitude, r.longitude], {
        icon: L.divIcon({ html: "⚠️", className: "service-div-icon marker-drop-anim", iconSize: [20, 20] }),
      })
        .addTo(leafletMap)
        .bindPopup(`<strong>${escapeHtml(r.label)}</strong><br/><span class="muted">Reported anonymously ${new Date(r.created_at).toLocaleDateString()}</span>${r.note ? `<br/>${escapeHtml(r.note)}` : ""}`);
      communityReportMarkers.push(marker);
    });
  }

  function initSafetyLayers() {
    const grid = document.getElementById("mapLayersGrid");
    const heatmapLegend = document.getElementById("mapHeatmapLegend");
    if (!grid) return;

    grid.addEventListener("click", (e) => {
      const btn = e.target.closest(".map-layer-btn");
      if (!btn) return;
      const layer = btn.dataset.layer;
      const nowActive = !btn.classList.contains("active");
      btn.classList.toggle("active", nowActive);

      if (layer === "heatmap") {
        showRiskZones = nowActive;
        heatmapLegend?.classList.toggle("hidden", !nowActive);
        if (nowActive) {
          refreshMapLayers();
        } else {
          riskZoneLayers.forEach((l) => leafletMap.removeLayer(l));
          riskZoneLayers = [];
          document.getElementById("crowdDensityBadge")?.classList.add("hidden");
        }
        return;
      }

      if (layer === "crime" || layer === "traffic") {
        // No live data source connected yet — an honest placeholder
        // instead of fabricating pins that would look like real safety
        // data. TODO: wire to a real incident-reports / traffic API.
        btn.classList.remove("active");
        toast(
          layer === "crime" ? "Crime Reports — not connected yet" : "Traffic — not connected yet",
          "This layer isn't backed by a live data source in this build. Community Reports and the Risk Heatmap use real, on-device data in the meantime.",
          "info"
        );
        return;
      }

      if (["police", "hospital", "pharmacy"].includes(layer)) {
        if (nowActive) { activeSingleTypeLayers.add(layer); refreshSingleTypeLayer(layer); }
        else {
          activeSingleTypeLayers.delete(layer);
          (singleTypeLayerMarkers[layer] || []).forEach((m) => leafletMap.removeLayer(m));
          singleTypeLayerMarkers[layer] = [];
        }
        return;
      }

      if (["lighting", "womens_safety", "safe_zones"].includes(layer)) {
        if (nowActive) { activeSingleTypeLayers.add(layer); refreshAuditDerivedLayer(layer); }
        else {
          activeSingleTypeLayers.delete(layer);
          (auditDerivedLayerCircles[layer] || []).forEach((c) => leafletMap.removeLayer(c));
          auditDerivedLayerCircles[layer] = [];
        }
        return;
      }

      if (layer === "community_reports") {
        if (nowActive) { activeSingleTypeLayers.add(layer); refreshCommunityReportsLayer(); }
        else {
          activeSingleTypeLayers.delete(layer);
          communityReportMarkers.forEach((m) => leafletMap.removeLayer(m));
          communityReportMarkers = [];
        }
      }
    });

    leafletMap.on("moveend", () => {
      ["police", "hospital", "pharmacy"].forEach((t) => activeSingleTypeLayers.has(t) && refreshSingleTypeLayer(t));
      ["lighting", "womens_safety", "safe_zones"].forEach((t) => activeSingleTypeLayers.has(t) && refreshAuditDerivedLayer(t));
      if (activeSingleTypeLayers.has("community_reports")) refreshCommunityReportsLayer();
    });
  }

  // ---------------------------------------------------------------------
  // 6. Nearby Services list (real distances, sorted, with ETA)
  // ---------------------------------------------------------------------
  let currentNearbyFilter = "";

  async function loadNearbyList() {
    const list = document.getElementById("mapNearbyList");
    const emptyState = document.getElementById("mapNearbyEmptyState");
    if (!list) return;

    list.innerHTML = `<li class="skeleton-item"></li><li class="skeleton-item"></li>`;
    const loc = await getLocation();
    if (loc.latitude == null) {
      list.innerHTML = "";
      emptyState?.classList.remove("hidden");
      return;
    }
    emptyState?.classList.add("hidden");

    const typeParam = currentNearbyFilter ? `&type=${currentNearbyFilter}` : "";
    const data = await safeApi(`/api/nearby-services?lat=${loc.latitude}&lng=${loc.longitude}${typeParam}`);
    if (!data._ok || !(data.results || []).length) {
      list.innerHTML = `<li class="muted" style="border:none;">No ${currentNearbyFilter ? escapeHtml(currentNearbyFilter) + " " : ""}services found nearby yet — coverage varies by area.</li>`;
      return;
    }

    // Already sorted by distance server-side; re-sort defensively in case a
    // filter combined multiple sources.
    const results = [...data.results].sort((a, b) => (a.distance_km ?? 999) - (b.distance_km ?? 999));

    list.innerHTML = results
      .map((s, i) => {
        const { walkMin, driveMin } = estimateEtaMinutes(s.distance_km ?? 0);
        const icon = SERVICE_ICONS[s.type] || "📍";
        return `<li class="map-nearby-item" data-index="${i}" tabindex="0" role="button">
          <span class="map-nearby-icon" aria-hidden="true">${icon}</span>
          <div class="map-nearby-info">
            <strong>${escapeHtml(s.name)}</strong>
            <span class="muted" style="font-size:11px; text-transform:capitalize;">${escapeHtml(s.type.replace("_", " "))}</span>
            <span class="map-nearby-meta">${fmtDistance(s.distance_km)} · 🚶 ~${walkMin} min · 🚗 ~${driveMin} min</span>
          </div>
          <a href="https://www.openstreetmap.org/directions?to=${s.lat}%2C${s.lng}" target="_blank" rel="noopener" class="btn secondary map-nearby-nav" aria-label="Navigate to ${escapeHtml(s.name)}">Navigate</a>
        </li>`;
      })
      .join("");

    list.querySelectorAll(".map-nearby-item").forEach((li) => {
      const openIt = () => {
        const s = results[parseInt(li.dataset.index, 10)];
        if (!s || !leafletMap || s.lat == null) return;
        leafletMap.flyTo([s.lat, s.lng], 17, { duration: 1 });
        L.popup().setLatLng([s.lat, s.lng]).setContent(`<strong>${escapeHtml(s.name)}</strong><br/>${fmtDistance(s.distance_km)} away`).openOn(leafletMap);
      };
      li.addEventListener("click", (e) => { if (!e.target.closest(".map-nearby-nav")) openIt(); });
      li.addEventListener("keydown", (e) => { if (e.key === "Enter") openIt(); });
    });
  }

  function initNearbyList() {
    const filterRow = document.getElementById("mapNearbyFilterRow");
    const enableBtn = document.getElementById("mapEnableLocationBtn");
    filterRow?.addEventListener("click", (e) => {
      const btn = e.target.closest(".map-nearby-filter-btn");
      if (!btn) return;
      filterRow.querySelectorAll(".map-nearby-filter-btn").forEach((b) => b.classList.toggle("active", b === btn));
      currentNearbyFilter = btn.dataset.type || "";
      loadNearbyList();
    });
    enableBtn?.addEventListener("click", loadNearbyList);
    loadNearbyList();
  }

  // ---------------------------------------------------------------------
  // 7. Long-press "Report an Issue"
  // ---------------------------------------------------------------------
  let pendingReportLatLng = null;

  function openReportModal(latlng) {
    pendingReportLatLng = latlng;
    const modal = document.getElementById("mapReportModal");
    const submitBtn = document.getElementById("submitReportBtn");
    document.querySelectorAll(".report-category-chip").forEach((c) => c.classList.remove("selected"));
    if (submitBtn) submitBtn.disabled = true;
    const note = document.getElementById("reportNote");
    if (note) note.value = "";
    modal?.classList.remove("hidden");
  }

  function initReportModal() {
    const modal = document.getElementById("mapReportModal");
    const grid = document.getElementById("reportCategoryGrid");
    const submitBtn = document.getElementById("submitReportBtn");
    const cancelBtn = document.getElementById("cancelReportBtn");
    if (!modal || !grid) return;

    let selectedCategory = null;

    grid.addEventListener("click", (e) => {
      const chip = e.target.closest(".report-category-chip");
      if (!chip) return;
      grid.querySelectorAll(".report-category-chip").forEach((c) => c.classList.remove("selected"));
      chip.classList.add("selected");
      selectedCategory = chip.dataset.category;
      if (submitBtn) submitBtn.disabled = false;
    });

    cancelBtn?.addEventListener("click", () => modal.classList.add("hidden"));

    submitBtn?.addEventListener("click", async () => {
      if (!selectedCategory || !pendingReportLatLng) return;
      submitBtn.disabled = true;
      const note = document.getElementById("reportNote")?.value || "";
      const result = await safeApi("/api/map-reports", {
        method: "POST",
        body: JSON.stringify({ category: selectedCategory, latitude: pendingReportLatLng.lat, longitude: pendingReportLatLng.lng, note }),
      });
      modal.classList.add("hidden");
      if (result._ok) {
        toast("Thanks — reported", "Your report helps keep other SafeHer users informed. It's shown anonymously.", "info");
        if (activeSingleTypeLayers.has("community_reports")) refreshCommunityReportsLayer();
      } else {
        toast("Couldn't submit", "Please check your connection and try again.", "info");
      }
    });
  }

  // Long-press detection on the Leaflet map container — independent of
  // Leaflet's own click/drag handling. A press that doesn't move for
  // ~500ms opens the report modal for that point; any real movement
  // (panning) cancels it, exactly like the existing SOS-FAB drag-arm
  // pattern used elsewhere in the app.
  function initLongPressReport() {
    const container = leafletMap.getContainer();
    const LONG_PRESS_MS = 500;
    const MOVE_CANCEL_PX = 8;
    let timer = null;
    let start = null;

    function cancel() {
      clearTimeout(timer);
      timer = null;
      start = null;
    }

    container.addEventListener("pointerdown", (e) => {
      if (e.button !== undefined && e.button !== 0) return;
      start = { x: e.clientX, y: e.clientY };
      timer = setTimeout(() => {
        if (!start) return;
        const latlng = leafletMap.containerPointToLatLng(
          leafletMap.mouseEventToContainerPoint({ clientX: start.x, clientY: start.y })
        );
        window.__mapLongPressHandled = true;
        if (navigator.vibrate) { try { navigator.vibrate(20); } catch (err) { /* no-op */ } }
        openReportModal(latlng);
        cancel();
      }, LONG_PRESS_MS);
    });
    container.addEventListener("pointermove", (e) => {
      if (!start) return;
      if (Math.abs(e.clientX - start.x) > MOVE_CANCEL_PX || Math.abs(e.clientY - start.y) > MOVE_CANCEL_PX) cancel();
    });
    container.addEventListener("pointerup", cancel);
    container.addEventListener("pointercancel", cancel);
  }

  // ---------------------------------------------------------------------
  // 8. Journey Info Card
  // ---------------------------------------------------------------------
  let journeyCardInterval = null;
  let lastKnownSpeedKmh = null;

  function startJourneySpeedWatch() {
    if (!navigator.geolocation || !navigator.geolocation.watchPosition) return null;
    return navigator.geolocation.watchPosition(
      (pos) => {
        if (pos.coords.speed != null && !Number.isNaN(pos.coords.speed)) {
          lastKnownSpeedKmh = Math.max(0, pos.coords.speed * 3.6);
        }
        window.__lastGpsAccuracy = pos.coords.accuracy;
      },
      () => {},
      { enableHighAccuracy: false, maximumAge: 10000, timeout: 8000 }
    );
  }
  const speedWatchId = startJourneySpeedWatch();
  void speedWatchId; // kept alive for the app's lifetime; nothing to clear it against

  async function updateJourneyCard() {
    const card = document.getElementById("mapJourneyCard");
    if (!card) return;

    if (typeof activeJourney === "undefined" || !activeJourney || activeJourney.status !== "active") {
      card.classList.add("hidden");
      return;
    }

    card.classList.remove("hidden");
    const battery = await (typeof getBatteryLevel === "function" ? getBatteryLevel() : Promise.resolve(null));
    const network = navigator.connection?.effectiveType?.toUpperCase() || (navigator.onLine ? "Online" : "Offline");
    const guardianText = document.getElementById("guardianStatus")?.textContent?.trim() || "Not sharing";
    const eta = activeJourney.remaining_seconds != null
      ? new Date(Date.now() + activeJourney.remaining_seconds * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
      : "—";

    card.innerHTML = `
      <div class="map-journey-card-header">
        <strong>🧭 Journey in progress</strong>
        <span class="map-journey-card-badge">${escapeHtml(activeJourney.status === "active" ? "Tracking" : activeJourney.status)}</span>
      </div>
      <div class="map-journey-card-row"><span>To</span><strong>${escapeHtml(activeJourney.destination_name || "—")}</strong></div>
      <div class="map-journey-card-row"><span>Distance left</span><strong>${activeJourney.distance_remaining_km != null ? activeJourney.distance_remaining_km.toFixed(2) + " km" : "—"}</strong></div>
      <div class="map-journey-card-row"><span>Arrival est.</span><strong>${eta}</strong></div>
      <div class="map-journey-card-row"><span>Guardian</span><strong>${escapeHtml(guardianText)}</strong></div>
      <div class="map-journey-card-row"><span>Live speed</span><strong>${lastKnownSpeedKmh != null ? Math.round(lastKnownSpeedKmh) + " km/h" : "—"}</strong></div>
      <div class="map-journey-card-row"><span>Battery</span><strong>${battery != null ? battery + "%" : "—"}</strong></div>
      <div class="map-journey-card-row"><span>Network</span><strong>${escapeHtml(network)}</strong></div>
    `;
  }

  function initJourneyCard() {
    updateJourneyCard();
    journeyCardInterval = setInterval(updateJourneyCard, 5000);
  }

  // ---------------------------------------------------------------------
  // 9. Left info panel (Safety Score / Weather / nearest help / signals)
  // ---------------------------------------------------------------------
  let lastLeftPanelLoc = null;

  async function refreshLeftPanel(loc) {
    const location = loc && loc.latitude != null ? loc : lastLeftPanelLoc || (await getLocation());
    if (location.latitude == null) return;
    lastLeftPanelLoc = location;

    // Nearest police / hospital, and an area safety score, all from the
    // same real endpoints already used elsewhere on this page.
    const [nearby, riskData] = await Promise.all([
      safeApi(`/api/nearby-services?lat=${location.latitude}&lng=${location.longitude}`),
      safeApi(`/api/risk-zones?lat=${location.latitude}&lng=${location.longitude}&radius_km=1.5`),
    ]);

    const policeEl = document.getElementById("statNearestPolice");
    const hospitalEl = document.getElementById("statNearestHospital");
    if (nearby._ok) {
      const police = (nearby.results || []).find((s) => s.type === "police");
      const hospital = (nearby.results || []).find((s) => s.type === "hospital");
      if (policeEl) policeEl.textContent = police ? `${escapeHtml(police.name)} · ${fmtDistance(police.distance_km)}` : "None found nearby";
      if (hospitalEl) hospitalEl.textContent = hospital ? `${escapeHtml(hospital.name)} · ${fmtDistance(hospital.distance_km)}` : "None found nearby";
    }

    const safetyEl = document.getElementById("statSafetyScore");
    if (safetyEl) {
      if (riskData._ok && (riskData.zones || []).length) {
        const worst = riskData.zones.reduce((min, z) => (z.score < min ? z.score : min), 100);
        safetyEl.textContent = `${worst}/100`;
        safetyEl.style.color = scoreColor(worst);
      } else {
        safetyEl.textContent = "No concerns nearby";
        safetyEl.style.color = scoreColor(100);
      }
    }

    // Weather — Open-Meteo, free and keyless, real current conditions.
    // Proxied through /api/weather (see app.py) since our CSP's connect-src
    // is locked to 'self' and doesn't allow direct browser calls out to
    // third-party APIs.
    const weatherEl = document.getElementById("statWeather");
    if (weatherEl) {
      try {
        const resp = await fetch(`/api/weather?lat=${location.latitude}&lng=${location.longitude}`);
        const wd = await resp.json();
        if (wd.current_weather) {
          weatherEl.textContent = `${Math.round(wd.current_weather.temperature)}°C · ${describeWeatherCode(wd.current_weather.weathercode)}`;
        }
      } catch (err) {
        weatherEl.textContent = "Unavailable";
      }
    }

    const accuracyEl = document.getElementById("statAccuracy");
    if (accuracyEl) {
      const acc = window.__lastGpsAccuracy;
      accuracyEl.textContent = acc != null ? `±${Math.round(acc)} m` : "Unknown";
    }
  }

  function describeWeatherCode(code) {
    // WMO weather codes, condensed to what's actually useful at a glance.
    if (code === 0) return "Clear";
    if ([1, 2, 3].includes(code)) return "Partly cloudy";
    if ([45, 48].includes(code)) return "Foggy";
    if ([51, 53, 55, 61, 63, 65, 80, 81, 82].includes(code)) return "Rainy";
    if ([71, 73, 75, 77, 85, 86].includes(code)) return "Snowy";
    if ([95, 96, 99].includes(code)) return "Thunderstorms";
    return "—";
  }

  function refreshDeviceOnlyStats() {
    const guardianEl = document.getElementById("statGuardian");
    if (guardianEl) guardianEl.textContent = document.getElementById("guardianStatus")?.textContent?.trim() || "Not sharing";

    const journeyEl = document.getElementById("statJourney");
    if (journeyEl) {
      journeyEl.textContent =
        typeof activeJourney !== "undefined" && activeJourney && activeJourney.status === "active"
          ? `→ ${activeJourney.destination_name}`
          : "No active journey";
    }

    const networkEl = document.getElementById("statNetwork");
    if (networkEl) networkEl.textContent = navigator.connection?.effectiveType?.toUpperCase() || (navigator.onLine ? "Online" : "Offline");

    if (typeof getBatteryLevel === "function") {
      const batteryEl = document.getElementById("statBattery");
      getBatteryLevel().then((b) => { if (batteryEl) batteryEl.textContent = b != null ? `${b}%` : "Not available"; });
    }
  }

  function initLeftInfoPanel() {
    refreshLeftPanel();
    refreshDeviceOnlyStats();
    setInterval(refreshDeviceOnlyStats, 8000);
    setInterval(() => refreshLeftPanel(), 60000); // weather/nearest-help don't need second-by-second refresh
  }

  // ---------------------------------------------------------------------
  // 10. Guardian tab device-signal enrichment
  // ---------------------------------------------------------------------
  function initGuardianExtraStats() {
    function update() {
      const netEl = document.getElementById("gdNetworkStatus");
      if (netEl) netEl.textContent = navigator.connection?.effectiveType?.toUpperCase() || (navigator.onLine ? "Online" : "Offline");
      const accEl = document.getElementById("gdLocationAccuracy");
      if (accEl) accEl.textContent = window.__lastGpsAccuracy != null ? `±${Math.round(window.__lastGpsAccuracy)} m` : "Unknown";
    }
    update();
    setInterval(update, 8000);
  }

  // ---------------------------------------------------------------------
  // 11. AI proactive safety suggestions
  // ---------------------------------------------------------------------
  // Pushes a suggestion into the Assistant tab's chat log (if present) and
  // as a toast, based on real signals only — never a fabricated claim
  // about the user's surroundings.
  let lastSuggestionAt = 0;
  async function maybePushAiSuggestion() {
    const now = Date.now();
    if (now - lastSuggestionAt < 90000) return; // don't nag more than ~once/90s

    const battery = typeof getBatteryLevel === "function" ? await getBatteryLevel() : null;
    if (battery != null && battery < 20) {
      pushSuggestion("🔋 Your battery is below 20%. Consider notifying your guardian or plugging in before you head out.");
      return;
    }

    if (typeof activeJourney !== "undefined" && activeJourney && activeJourney.status === "active") {
      const loc = await getLocation();
      if (loc.latitude != null) {
        const nearby = await safeApi(`/api/nearby-services?lat=${loc.latitude}&lng=${loc.longitude}&type=police`);
        const police = (nearby.results || [])[0];
        if (police && police.distance_km != null && police.distance_km < 0.5) {
          pushSuggestion(`🚓 You're close to ${police.name} — ${fmtDistance(police.distance_km)} away, in case you ever need it.`);
          return;
        }
        const risk = await safeApi(`/api/risk-zones?lat=${loc.latitude}&lng=${loc.longitude}&radius_km=1`);
        if (risk._ok && (risk.zones || []).some((z) => z.severity === "high")) {
          pushSuggestion("⚠️ You're entering an area with a lower community safety score. Consider a well-lit, populated route if possible.");
          return;
        }
      }
    }
  }

  function pushSuggestion(text) {
    lastSuggestionAt = Date.now();
    toast("💡 SafeHer AI", text, "info");
    if (typeof appendAssistantMessage === "function") {
      appendAssistantMessage("bot", text);
    }
  }

  function initAiSuggestions() {
    maybePushAiSuggestion();
    setInterval(maybePushAiSuggestion, 60000);
  }

  // ---------------------------------------------------------------------
  // 12. FAB action hooks (called from premium-enhancements.js's speed dial)
  // ---------------------------------------------------------------------
  window.safeherGoToJourneyStart = function () {
    document.querySelector('.tab-btn[data-tab="home"]')?.click();
    setTimeout(() => document.getElementById("journeyDestination")?.focus(), 300);
  };

  window.safeherCallGuardian = async function () {
    const contacts = await safeApi("/api/contacts");
    const first = Array.isArray(contacts) ? contacts[0] : null;
    if (first && first.phone) {
      window.location.href = `tel:${first.phone}`;
    } else {
      toast("No emergency contact yet", "Add one on the Guardian tab first.", "info");
      document.querySelector('.tab-btn[data-tab="guardian"]')?.click();
    }
  };

  window.safeherOpenReportFromFab = async function () {
    document.querySelector('.tab-btn[data-tab="map"]')?.click();
    const loc = await getLocation();
    if (loc.latitude == null) {
      toast("Location needed", "Enable location access to report an issue.", "info");
      return;
    }
    setTimeout(() => openReportModal({ lat: loc.latitude, lng: loc.longitude }), 300);
  };

  window.safeherShareLiveLocation = function () {
    document.querySelector('.tab-btn[data-tab="guardian"]')?.click();
    setTimeout(() => {
      const btn = document.getElementById("shareLocationBtn");
      if (btn && !btn.classList.contains("hidden")) btn.click();
    }, 300);
  };

  // ---------------------------------------------------------------------
  // Boot
  // ---------------------------------------------------------------------
  function boot() {
    whenMapReady(() => {
      initMapSearch();
      initMyLocationButton();
      initFavorites();
      initSafetyLayers();
      initNearbyList();
      initReportModal();
      initLongPressReport();
      initJourneyCard();
      initLeftInfoPanel();
      initAiSuggestions();
    });
    initGuardianExtraStats();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();