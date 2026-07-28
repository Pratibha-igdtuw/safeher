/**
 * SafeHer — MapLibre GL JS / Leaflet compatibility shim.
 *
 * WHY THIS FILE EXISTS (migration strategy):
 * main.js and safety-map.js have ~100 call sites built against Leaflet's
 * API (L.map, L.marker, L.circle, L.circleMarker, L.divIcon, L.polyline,
 * L.latLngBounds, map.flyTo/.flyToBounds/.getCenter/.on/.removeLayer,
 * marker.bindPopup/.openPopup, ...) across Journey Mode, route comparison,
 * the Safety Audit heatmap, Safety Layers panel, nearby-services list,
 * favourites, and long-press reporting. Rewriting every one of those call
 * sites to MapLibre's native GeoJSON-source/layer model in one pass would
 * touch a huge, tightly-coupled surface with real regression risk for a
 * safety-critical feature (Journey Mode, SOS integration).
 *
 * Instead: this file implements just enough of Leaflet's object API, on
 * top of MapLibre GL JS underneath, that every existing call site keeps
 * working *verbatim* — same method names, same argument shapes, same
 * [lat, lng]-array and {lat, lng}-object conventions Leaflet used. The
 * app gets MapLibre's real benefits (hardware-accelerated vector
 * rendering, smoother zoom/pan, proper dark-mode map styling, GPU-backed
 * marker rendering) without a risky wholesale rewrite. New Safety Map v2
 * features (search autocomplete, saved places, reverse geocoding) are
 * built directly against MapLibre's native API in map/places-ui.js —
 * this shim is only for keeping the EXISTING code working.
 *
 * Must load after maplibre-gl.js and before main.js.
 */
(function (global) {
  "use strict";

  if (typeof maplibregl === "undefined") {
    console.warn("MapLibre GL JS unavailable — Safety Map disabled for this session");
    return; // main.js's CDN_FAILED.leaflet check + initMap()'s own `typeof L === "undefined"` guard handles this gracefully
  }

  // -------------------------------------------------------------------
  // Coordinate helpers — Leaflet accepts both [lat, lng] arrays and
  // {lat, lng} objects almost everywhere; MapLibre wants [lng, lat].
  // -------------------------------------------------------------------
  function toLngLat(latlng) {
    if (Array.isArray(latlng)) return [latlng[1], latlng[0]];
    if (latlng && typeof latlng.lat === "number") return [latlng.lng, latlng.lat];
    return latlng;
  }
  function toLatLngObj(lngLatArr) {
    return { lat: lngLatArr[1], lng: lngLatArr[0] };
  }

  let uidCounter = 0;
  function nextId(prefix) {
    uidCounter += 1;
    return `${prefix}-${uidCounter}`;
  }

  // -------------------------------------------------------------------
  // Popup wrapper — shared by every marker/circle/polyline type below.
  // -------------------------------------------------------------------
  function makePopupCapable(handle) {
    let popupHtml = null;
    let maplibrePopup = null;

    handle.bindPopup = function (html) {
      popupHtml = html;
      return handle;
    };
    handle.setContent = function (html) {
      popupHtml = html;
      if (maplibrePopup) maplibrePopup.setHTML(html);
      return handle;
    };
    handle.openPopup = function () {
      if (popupHtml == null || !handle._map) return handle;
      maplibrePopup = new maplibregl.Popup({ closeOnClick: true })
        .setLngLat(handle._lngLat)
        .setHTML(popupHtml)
        .addTo(handle._map._maplibreMap);
      return handle;
    };
    handle._attachClickPopup = function () {
      if (handle._maplibreMarker) {
        handle._maplibreMarker.getElement().addEventListener("click", () => handle.openPopup());
      }
    };
    return handle;
  }

  // -------------------------------------------------------------------
  // L.marker(latlng, {icon}) — HTML/emoji markers via maplibregl.Marker
  // -------------------------------------------------------------------
  function marker(latlng, options) {
    const handle = makePopupCapable({});
    handle._lngLat = toLngLat(latlng);
    handle._options = options || {};

    handle.addTo = function (map) {
      handle._map = map;
      const el = document.createElement("div");
      if (handle._options.icon && handle._options.icon._html != null) {
        el.innerHTML = handle._options.icon._html;
        el.className = handle._options.icon._className || "";
        const size = handle._options.icon._size || [28, 28];
        el.style.width = `${size[0]}px`;
        el.style.height = `${size[1]}px`;
        el.style.display = "flex";
        el.style.alignItems = "center";
        el.style.justifyContent = "center";
        el.style.fontSize = `${Math.round(size[0] * 0.75)}px`;
        el.style.lineHeight = "1";
        el.style.cursor = "pointer";
      }
      handle._maplibreMarker = new maplibregl.Marker({ element: el.innerHTML ? el : undefined })
        .setLngLat(handle._lngLat)
        .addTo(map._maplibreMap);
      handle._attachClickPopup();
      return handle;
    };
    handle.setLatLng = function (latlng) {
      handle._lngLat = toLngLat(latlng);
      if (handle._maplibreMarker) handle._maplibreMarker.setLngLat(handle._lngLat);
      return handle;
    };
    handle.getLatLng = function () {
      return toLatLngObj(handle._lngLat);
    };
    handle._removeFromMap = function () {
      if (handle._maplibreMarker) handle._maplibreMarker.remove();
    };
    return handle;
  }

  function divIcon(opts) {
    return { _html: opts.html, _className: opts.className || "", _size: opts.iconSize || [24, 24] };
  }

  // -------------------------------------------------------------------
  // L.circle / L.circleMarker — rendered as a GeoJSON circle-approximation
  // polygon fill layer (circle) or a fixed-pixel circle layer (circleMarker,
  // which in Leaflet has a pixel radius that doesn't scale with zoom).
  // -------------------------------------------------------------------
  function circleGeoJSON(centerLngLat, radiusMeters, points) {
    points = points || 48;
    const coords = [];
    const [lng, lat] = centerLngLat;
    const latRad = (lat * Math.PI) / 180;
    const metersPerDegLat = 111320;
    const metersPerDegLng = 111320 * Math.cos(latRad);
    for (let i = 0; i <= points; i++) {
      const angle = (i / points) * 2 * Math.PI;
      coords.push([lng + (Math.cos(angle) * radiusMeters) / metersPerDegLng, lat + (Math.sin(angle) * radiusMeters) / metersPerDegLat]);
    }
    return { type: "Feature", geometry: { type: "Polygon", coordinates: [coords] }, properties: {} };
  }

  function makeShapeLayer(kind, latlng, options) {
    const handle = makePopupCapable({});
    handle._lngLat = toLngLat(latlng);
    handle._options = options || {};
    handle._sourceId = nextId(`shim-${kind}-src`);
    handle._fillLayerId = `${handle._sourceId}-fill`;
    handle._lineLayerId = `${handle._sourceId}-line`;

    handle.addTo = function (map) {
      handle._map = map;
      const mm = map._maplibreMap;
      const radiusMeters = kind === "circle" ? (options.radius || 100) : null;
      const feature = kind === "circle"
        ? circleGeoJSON(handle._lngLat, radiusMeters)
        : { type: "Feature", geometry: { type: "Point", coordinates: handle._lngLat }, properties: {} };

      mm.addSource(handle._sourceId, { type: "geojson", data: feature });

      if (kind === "circle") {
        mm.addLayer({
          id: handle._fillLayerId, type: "fill", source: handle._sourceId,
          paint: { "fill-color": options.fillColor || options.color || "#7C3AED", "fill-opacity": options.fillOpacity ?? 0.25 },
        });
        mm.addLayer({
          id: handle._lineLayerId, type: "line", source: handle._sourceId,
          paint: { "line-color": options.color || "#7C3AED", "line-width": options.weight ?? 1 },
        });
      } else {
        // circleMarker: fixed pixel radius regardless of zoom, matching Leaflet's L.circleMarker semantics.
        mm.addLayer({
          id: handle._fillLayerId, type: "circle", source: handle._sourceId,
          paint: {
            "circle-radius": options.radius ?? 8,
            "circle-color": options.fillColor || options.color || "#7C3AED",
            "circle-opacity": options.fillOpacity ?? 0.8,
            "circle-stroke-color": options.color || "#fff",
            "circle-stroke-width": options.weight ?? 1,
          },
        });
      }

      mm.on("click", handle._fillLayerId, () => handle.openPopup());
      return handle;
    };
    handle.setLatLng = function (latlng) {
      handle._lngLat = toLngLat(latlng);
      const mm = handle._map && handle._map._maplibreMap;
      if (!mm) return handle;
      const feature = kind === "circle"
        ? circleGeoJSON(handle._lngLat, options.radius || 100)
        : { type: "Feature", geometry: { type: "Point", coordinates: handle._lngLat }, properties: {} };
      const src = mm.getSource(handle._sourceId);
      if (src) src.setData(feature);
      return handle;
    };
    handle._removeFromMap = function () {
      const mm = handle._map && handle._map._maplibreMap;
      if (!mm) return;
      if (mm.getLayer(handle._fillLayerId)) mm.removeLayer(handle._fillLayerId);
      if (kind === "circle" && mm.getLayer(handle._lineLayerId)) mm.removeLayer(handle._lineLayerId);
      if (mm.getSource(handle._sourceId)) mm.removeSource(handle._sourceId);
    };
    return handle;
  }

  function circle(latlng, options) { return makeShapeLayer("circle", latlng, options || {}); }
  function circleMarker(latlng, options) { return makeShapeLayer("circleMarker", latlng, options || {}); }

  // -------------------------------------------------------------------
  // L.polyline — route lines (Journey Mode's Safest/Fastest/Balanced paths)
  // -------------------------------------------------------------------
  function polyline(latlngs, options) {
    const handle = makePopupCapable({});
    handle._latlngs = latlngs;
    handle._options = options || {};
    handle._sourceId = nextId("shim-line-src");
    handle._layerId = `${handle._sourceId}-layer`;

    handle.addTo = function (map) {
      handle._map = map;
      const mm = map._maplibreMap;
      const coords = latlngs.map((p) => toLngLat(p));
      mm.addSource(handle._sourceId, {
        type: "geojson",
        data: { type: "Feature", geometry: { type: "LineString", coordinates: coords }, properties: {} },
      });
      mm.addLayer({
        id: handle._layerId, type: "line", source: handle._sourceId,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": options.color || "#7C3AED", "line-width": options.weight ?? 4, "line-opacity": options.opacity ?? 0.85 },
      });
      return handle;
    };
    handle._removeFromMap = function () {
      const mm = handle._map && handle._map._maplibreMap;
      if (!mm) return;
      if (mm.getLayer(handle._layerId)) mm.removeLayer(handle._layerId);
      if (mm.getSource(handle._sourceId)) mm.removeSource(handle._sourceId);
    };
    return handle;
  }

  // -------------------------------------------------------------------
  // L.latLngBounds — used once, for flyToBounds() after route comparison
  // -------------------------------------------------------------------
  function latLngBounds(latlngsArray) {
    const lngLats = latlngsArray.map((p) => toLngLat(p));
    let bounds = new maplibregl.LngLatBounds(lngLats[0], lngLats[0]);
    lngLats.forEach((ll) => bounds.extend(ll));
    return {
      isValid: () => lngLats.length > 0,
      _maplibreBounds: bounds,
    };
  }

  // -------------------------------------------------------------------
  // L.popup() — the one standalone-popup call site (nearby-list "open" action)
  // -------------------------------------------------------------------
  function standalonePopup() {
    let lngLat = null;
    let html = "";
    const handle = {
      setLatLng: (latlng) => { lngLat = toLngLat(latlng); return handle; },
      setContent: (h) => { html = h; return handle; },
      openOn: (map) => {
        new maplibregl.Popup().setLngLat(lngLat).setHTML(html).addTo(map._maplibreMap);
        return handle;
      },
    };
    return handle;
  }

  // -------------------------------------------------------------------
  // L.map(elementId) / L.tileLayer(url) — the map itself + its base layer.
  // Picks a real MapLibre vector style if window.GEOAPIFY_API_KEY and
  // window.MAP_STYLE_PROVIDER === "geoapify" are set, otherwise builds a
  // plain raster style pointing at the same free OSM tile servers the
  // Leaflet version used — zero new config required, same as every other
  // optional integration in this app.
  // -------------------------------------------------------------------
  function buildDefaultStyle(tileUrlTemplate) {
    // Leaflet's {s} placeholder load-balances across a/b/c subdomains.
    // MapLibre's raster source takes an array of equivalent URLs instead
    // of a placeholder — and critically, our CSP's connect-src only
    // allow-lists https://*.tile.openstreetmap.org (a wildcard, which
    // requires an actual subdomain to match — it does NOT match the bare
    // https://tile.openstreetmap.org). So real subdomains here aren't
    // just for load-balancing parity with the original Leaflet config,
    // they're required for the tiles to load at all under CSP.
    const tiles = tileUrlTemplate.includes("{s}")
      ? ["a", "b", "c"].map((s) => tileUrlTemplate.replace("{s}", s))
      : [tileUrlTemplate];
    return {
      version: 8,
      sources: {
        "shim-raster": {
          type: "raster",
          tiles,
          tileSize: 256,
          attribution: "© OpenStreetMap contributors",
        },
      },
      layers: [{ id: "shim-raster-layer", type: "raster", source: "shim-raster" }],
    };
  }

  const DEFAULT_TILE_URL = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";

  function mapFactory(elementId, options) {
    const handle = {};
    let center = [77.1025, 28.7041]; // MapLibre wants [lng, lat]; Leaflet's setView([lat,lng]) is translated below
    let zoom = 12;

    handle._pendingTileUrl = null;

    handle.setView = function (latlng, z) {
      center = toLngLat(latlng);
      zoom = z ?? zoom;
      if (handle._maplibreMap) handle._maplibreMap.jumpTo({ center, zoom });
      else handle._initialize();
      return handle;
    };

    handle._initialize = function () {
      if (handle._maplibreMap) return;
      const useGeoapify = global.MAP_STYLE_PROVIDER === "geoapify" && global.GEOAPIFY_API_KEY;
      const initialTileUrl = handle._pendingTileUrl || DEFAULT_TILE_URL;
      handle._activeTileUrl = initialTileUrl;
      const style = useGeoapify
        ? `https://maps.geoapify.com/v1/styles/osm-bright/style.json?apiKey=${encodeURIComponent(global.GEOAPIFY_API_KEY)}`
        : buildDefaultStyle(initialTileUrl);

      handle._maplibreMap = new maplibregl.Map({
        container: elementId,
        style,
        center,
        zoom,
        attributionControl: true,
      });
      handle._maplibreMap.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");

      handle._eventQueue.forEach(({ evt, fn }) => handle._wireEvent(evt, fn));
    };

    // Called by tileLayer().addTo(map) — see below. In this app,
    // L.map(id).setView(...) is always immediately followed by
    // L.tileLayer(url).addTo(map) in the SAME synchronous tick, so by the
    // time this runs, handle._activeTileUrl (set in _initialize() above)
    // already equals `urlTemplate` and this is a genuine no-op — avoiding
    // a real risk: calling map.setStyle() *after* other code has started
    // adding its own sources/layers (risk zones, route lines, markers)
    // could wipe them out mid-flight. Only a genuinely different URL
    // (not exercised by this app today, but kept for API completeness)
    // takes the slower, deferred-until-loaded path below.
    handle._applyTileUrl = function (urlTemplate) {
      if (!handle._maplibreMap) {
        handle._pendingTileUrl = urlTemplate;
        return;
      }
      if (urlTemplate === handle._activeTileUrl) return; // already showing this exact source — nothing to do
      if (useGeoapifyStyle()) return; // a real vector style is active; swapping in a raster source would be a regression, not a fix
      handle._activeTileUrl = urlTemplate;
      const mm = handle._maplibreMap;
      const style = buildDefaultStyle(urlTemplate);
      if (mm.isStyleLoaded()) mm.setStyle(style);
      else mm.once("load", () => mm.setStyle(style));
    };
    function useGeoapifyStyle() {
      return global.MAP_STYLE_PROVIDER === "geoapify" && global.GEOAPIFY_API_KEY;
    }

    handle._eventQueue = [];
    handle._wireEvent = function (evt, fn) {
      if (evt === "click") {
        handle._maplibreMap.on("click", (e) => fn({ latlng: toLatLngObj([e.lngLat.lng, e.lngLat.lat]) }));
      } else if (evt === "moveend") {
        handle._maplibreMap.on("moveend", fn);
      } else {
        handle._maplibreMap.on(evt, fn);
      }
    };
    handle.on = function (evt, fn) {
      if (handle._maplibreMap) handle._wireEvent(evt, fn);
      else handle._eventQueue.push({ evt, fn });
      return handle;
    };

    handle.flyTo = function (latlng, z, opts) {
      if (!handle._maplibreMap) return handle;
      handle._maplibreMap.flyTo({ center: toLngLat(latlng), zoom: z, duration: ((opts && opts.duration) || 1) * 1000 });
      return handle;
    };
    handle.flyToBounds = function (bounds, opts) {
      if (!handle._maplibreMap || !bounds || !bounds._maplibreBounds) return handle;
      handle._maplibreMap.fitBounds(bounds._maplibreBounds, {
        padding: (opts && opts.padding && opts.padding[0]) || 30,
        duration: ((opts && opts.duration) || 0.8) * 1000,
      });
      return handle;
    };
    handle.getCenter = function () {
      if (!handle._maplibreMap) return toLatLngObj(center);
      const c = handle._maplibreMap.getCenter();
      return { lat: c.lat, lng: c.lng };
    };
    handle.removeLayer = function (layerHandle) {
      if (layerHandle && typeof layerHandle._removeFromMap === "function") layerHandle._removeFromMap();
    };
    handle.invalidateSize = function () {
      if (handle._maplibreMap) handle._maplibreMap.resize();
      return handle;
    };
    handle.getContainer = function () {
      return (handle._maplibreMap && handle._maplibreMap.getContainer()) || document.getElementById(elementId);
    };
    // Used by safety-map.js's long-press-to-report flow: converts a
    // client (viewport) point to a point relative to the map container.
    handle.mouseEventToContainerPoint = function (evtLike) {
      const rect = handle.getContainer().getBoundingClientRect();
      return { x: evtLike.clientX - rect.left, y: evtLike.clientY - rect.top };
    };
    handle.containerPointToLatLng = function (point) {
      if (!handle._maplibreMap) return { lat: 0, lng: 0 };
      const ll = handle._maplibreMap.unproject([point.x, point.y]);
      return { lat: ll.lat, lng: ll.lng };
    };

    // Initialize immediately with whatever's known so far (matches
    // Leaflet's behaviour, where the map renders as soon as L.map() runs,
    // before .setView()/tileLayer() are even called) — but ALWAYS with
    // DEFAULT_TILE_URL as the placeholder if no real one has arrived yet,
    // and _applyTileUrl() above re-patches the style once the real one
    // does arrive via tileLayer().addTo(map), regardless of ordering.
    handle._initialize();
    return handle;
  }

  function tileLayer(urlTemplate, options) {
    return {
      addTo: function (map) {
        map._applyTileUrl(urlTemplate);
        return this;
      },
    };
  }

  global.L = {
    map: mapFactory,
    tileLayer,
    marker,
    divIcon,
    circle,
    circleMarker,
    polyline,
    latLngBounds,
    popup: standalonePopup,
  };
})(window);