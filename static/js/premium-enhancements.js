// SafeHer — premium presentation-layer enhancements.
//
// Everything in this file is purely additive/visual: scroll progress bar,
// hero video parallax, the floating Quick-SOS shortcut, staggered list
// reveals, and the live "trust strip". It never duplicates SafeHer's real
// feature logic — it either reads already-rendered DOM state or forwards
// clicks to the real buttons that main.js already wires up. main.js itself
(function () {
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function ready(fn) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn);
    } else {
      fn();
    }
  }

  // -------------------------------------------------------------------
  // Scroll progress bar
  // -------------------------------------------------------------------
  function initScrollProgress() {
    const bar = document.getElementById("scrollProgress");
    if (!bar) return;
    let ticking = false;

    function update() {
      const scrollTop = window.scrollY || document.documentElement.scrollTop;
      const height = document.documentElement.scrollHeight - window.innerHeight;
      const pct = height > 0 ? Math.min(100, Math.max(0, (scrollTop / height) * 100)) : 0;
      bar.style.width = pct + "%";
      ticking = false;
    }

    window.addEventListener(
      "scroll",
      () => {
        if (!ticking) {
          requestAnimationFrame(update);
          ticking = true;
        }
      },
      { passive: true }
    );
    update();
  }

  // -------------------------------------------------------------------
  // Hero video parallax — subtle scroll-driven depth on the SAME video
  // element (no new asset, nothing swapped out).
  // -------------------------------------------------------------------
  function initHeroParallax() {
    if (reducedMotion) return;
    const frame = document.querySelector(".hero-video-frame");
    const video = frame && frame.querySelector("video");
    if (!video) return;

    let ticking = false;

    function update() {
      const rect = frame.getBoundingClientRect();
      // Only compute while the hero is anywhere near the viewport.
      if (rect.bottom > -200 && rect.top < window.innerHeight + 200) {
        const shift = Math.max(-40, Math.min(40, rect.top * -0.08));
        video.style.transform = `translateY(${shift}px) scale(1.06)`;
      }
      ticking = false;
    }

    window.addEventListener(
      "scroll",
      () => {
        if (!ticking) {
          requestAnimationFrame(update);
          ticking = true;
        }
      },
      { passive: true }
    );
    update();
  }

  // -------------------------------------------------------------------
  // Floating Quick-SOS — forwards to the real SOS button so there is a
  // single source of truth for the SOS flow (main.js's triggerSOS). Fades
  // out while the real SOS card is already visible on screen, so there's
  // never a redundant pair of SOS buttons in view at once.
  // -------------------------------------------------------------------
  // -------------------------------------------------------------------
  // Quick-SOS Floating Action Button — draggable.
  //
  // Behavior:
  //   - A plain tap/click still calls the real #sosBtn immediately (SOS
  //     logic itself lives entirely in main.js and is untouched here).
  //   - Press-and-hold for ~250ms arms drag mode; moving the pointer
  //     before that timer fires cancels the arm and the release is
  //     treated as a normal tap instead.
  //   - Once armed, the button follows the pointer 1:1 (no drag transition,
  //     so it feels immediate), clamped so it can never leave the viewport.
  //   - On release it snaps to whichever screen edge (left/right) it's
  //     closest to, animates there, and the position is saved to
  //     localStorage so it's remembered on the next visit.
  // -------------------------------------------------------------------
  function initQuickSos() {
    const quickBtn = document.getElementById("quickSosBtn");
    const realBtn = document.getElementById("sosBtn");
    if (!quickBtn || !realBtn) return;

    const STORAGE_KEY = "safeher_quick_sos_pos";
    const LONG_PRESS_MS = 250;
    const DRAG_CANCEL_THRESHOLD = 6; // px moved before long-press fires -> treat as scroll/tap, not drag
    const TOP_SAFE_AREA = 64; // keeps the FAB clear of the scroll-progress bar / offline banner up top

    const isCompact = window.matchMedia("(max-width: 600px)").matches;
    const EDGE_MARGIN = isCompact ? 16 : 22;

    let dragState = null;
    let longPressTimer = null;
    let isDragging = false;
    let suppressNextClick = false;

    function size() {
      const rect = quickBtn.getBoundingClientRect();
      return { w: rect.width, h: rect.height };
    }

    function clamp(value, min, max) {
      return Math.min(Math.max(value, min), max);
    }

    function clampPosition(left, top) {
      const { w, h } = size();
      const maxLeft = Math.max(EDGE_MARGIN, window.innerWidth - w - EDGE_MARGIN);
      const maxTop = Math.max(TOP_SAFE_AREA, window.innerHeight - h - EDGE_MARGIN);
      return {
        left: clamp(left, EDGE_MARGIN, maxLeft),
        top: clamp(top, TOP_SAFE_AREA, maxTop),
      };
    }

    function applyPosition(left, top) {
      const clamped = clampPosition(left, top);
      quickBtn.style.right = "auto";
      quickBtn.style.bottom = "auto";
      quickBtn.style.left = clamped.left + "px";
      quickBtn.style.top = clamped.top + "px";
      return clamped;
    }

    function savePosition(left, top) {
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify({ left, top, vw: window.innerWidth, vh: window.innerHeight }));
      } catch (e) { /* localStorage unavailable (private mode, quota) — non-critical */ }
    }

    function loadSavedPosition() {
      try {
        const raw = localStorage.getItem(STORAGE_KEY);
        if (!raw) return null;
        const parsed = JSON.parse(raw);
        if (typeof parsed.left !== "number" || typeof parsed.top !== "number") return null;
        // Re-scale proportionally if the viewport size has changed since it
        // was saved (rotation, resize, different device) so it lands in
        // roughly the same relative spot instead of a fixed pixel spot that
        // might now be off-screen.
        if (parsed.vw && parsed.vh && (parsed.vw !== window.innerWidth || parsed.vh !== window.innerHeight)) {
          return {
            left: (parsed.left / parsed.vw) * window.innerWidth,
            top: (parsed.top / parsed.vh) * window.innerHeight,
          };
        }
        return { left: parsed.left, top: parsed.top };
      } catch (e) {
        return null;
      }
    }

    function snapToNearestEdge(left, top) {
      const { w } = size();
      const center = left + w / 2;
      const snappedLeft = center < window.innerWidth / 2 ? EDGE_MARGIN : window.innerWidth - w - EDGE_MARGIN;

      quickBtn.classList.add("is-snapping");
      const clamped = applyPosition(snappedLeft, top);
      savePosition(clamped.left, clamped.top);

      const clearSnap = () => quickBtn.classList.remove("is-snapping");
      quickBtn.addEventListener("transitionend", clearSnap, { once: true });
      setTimeout(clearSnap, 450); // fallback in case transitionend doesn't fire (e.g. zero-distance snap)
    }

    function initPosition() {
      const saved = loadSavedPosition();
      if (saved) {
        applyPosition(saved.left, saved.top);
        return;
      }
      const { w, h } = size();
      applyPosition(window.innerWidth - w - EDGE_MARGIN, window.innerHeight - h - EDGE_MARGIN);
    }

    // Re-clamp on resize/rotation so the button can never end up stranded
    // off-screen; this only clamps, it doesn't re-snap or change edges.
    window.addEventListener("resize", () => {
      const rect = quickBtn.getBoundingClientRect();
      applyPosition(rect.left, rect.top);
    });

    function onPointerDown(e) {
      if (e.button !== undefined && e.button !== 0) return; // primary touch/left-click only
      const rect = quickBtn.getBoundingClientRect();
      dragState = {
        pointerId: e.pointerId,
        startX: e.clientX,
        startY: e.clientY,
        originLeft: rect.left,
        originTop: rect.top,
      };
      isDragging = false;

      longPressTimer = setTimeout(() => {
        if (!dragState) return;
        isDragging = true;
        quickBtn.classList.add("is-dragging");
        closeFabMenu();
        try { quickBtn.setPointerCapture(dragState.pointerId); } catch (err) { /* no-op */ }
        if (navigator.vibrate) {
          try { navigator.vibrate(15); } catch (err) { /* no-op */ }
        }
      }, LONG_PRESS_MS);

      quickBtn.addEventListener("pointermove", onPointerMove);
      quickBtn.addEventListener("pointerup", onPointerUp);
      quickBtn.addEventListener("pointercancel", onPointerUp);
    }

    function onPointerMove(e) {
      if (!dragState) return;
      const dx = e.clientX - dragState.startX;
      const dy = e.clientY - dragState.startY;

      if (!isDragging) {
        // Real movement before the long-press timer fires means this is a
        // scroll/flick, not a drag attempt — cancel arming so release just
        // falls through to a normal tap/click.
        if (Math.abs(dx) > DRAG_CANCEL_THRESHOLD || Math.abs(dy) > DRAG_CANCEL_THRESHOLD) {
          clearTimeout(longPressTimer);
        }
        return;
      }

      e.preventDefault();
      applyPosition(dragState.originLeft + dx, dragState.originTop + dy);
    }

    function onPointerUp() {
      clearTimeout(longPressTimer);
      quickBtn.removeEventListener("pointermove", onPointerMove);
      quickBtn.removeEventListener("pointerup", onPointerUp);
      quickBtn.removeEventListener("pointercancel", onPointerUp);

      if (isDragging) {
        quickBtn.classList.remove("is-dragging");
        const rect = quickBtn.getBoundingClientRect();
        snapToNearestEdge(rect.left, rect.top);
        try { quickBtn.releasePointerCapture(dragState.pointerId); } catch (err) { /* no-op */ }
        // The browser fires a native "click" right after pointerup even for
        // a drag release — suppress just that one so dragging never also
        // triggers SOS.
        suppressNextClick = true;
        setTimeout(() => { suppressNextClick = false; }, 0);
      }

      isDragging = false;
      dragState = null;
    }

    quickBtn.addEventListener("pointerdown", onPointerDown);

    const fabMenu = document.getElementById("sosFabMenu");

    function positionFabMenu() {
      if (!fabMenu) return;
      const rect = quickBtn.getBoundingClientRect();
      const opensUp = rect.top > window.innerHeight / 2;
      const alignRight = rect.left > window.innerWidth / 2;

      fabMenu.style.left = "auto";
      fabMenu.style.right = "auto";
      fabMenu.style.top = "auto";
      fabMenu.style.bottom = "auto";

      if (alignRight) {
        fabMenu.style.right = (window.innerWidth - rect.right) + "px";
      } else {
        fabMenu.style.left = rect.left + "px";
      }
      if (opensUp) {
        fabMenu.style.bottom = (window.innerHeight - rect.top) + 12 + "px";
        fabMenu.classList.add("opens-up");
        fabMenu.classList.remove("opens-down");
      } else {
        fabMenu.style.top = (rect.bottom + 12) + "px";
        fabMenu.classList.add("opens-down");
        fabMenu.classList.remove("opens-up");
      }
    }

    function closeFabMenu() {
      if (!fabMenu) return;
      fabMenu.classList.remove("open");
      fabMenu.setAttribute("aria-hidden", "true");
      quickBtn.setAttribute("aria-expanded", "false");
    }

    function toggleFabMenu() {
      if (!fabMenu) return;
      const willOpen = !fabMenu.classList.contains("open");
      if (willOpen) {
        positionFabMenu();
        fabMenu.classList.add("open");
        fabMenu.setAttribute("aria-hidden", "false");
        quickBtn.setAttribute("aria-expanded", "true");
      } else {
        closeFabMenu();
      }
    }

    // Tap the FAB -> open/close the speed-dial menu (SOS itself is the
    // first item in that menu, so it's always exactly one more tap away —
    // dragging, above, is unaffected and still works via long-press).
    quickBtn.addEventListener("click", () => {
      if (suppressNextClick) return;
      toggleFabMenu();
    });

    // Tapping any menu item runs that action via window-scoped hooks so
    // this file doesn't need to know the Map/Guardian/Assistant tab
    // internals directly — see safety-map.js and main.js for the hooks.
    fabMenu?.addEventListener("click", (e) => {
      const item = e.target.closest(".sos-fab-item");
      if (!item) return;
      const action = item.dataset.fabAction;
      closeFabMenu();

      if (action === "sos") {
        if (navigator.vibrate) { try { navigator.vibrate([40, 30, 40]); } catch (err) { /* no-op */ } }
        realBtn.click();
      } else if (action === "journey") {
        window.safeherGoToJourneyStart?.();
      } else if (action === "guardian") {
        window.safeherCallGuardian?.();
      } else if (action === "assistant") {
        document.querySelector('.tab-btn[data-tab="assistant"]')?.click();
      } else if (action === "report") {
        window.safeherOpenReportFromFab?.();
      } else if (action === "share") {
        window.safeherShareLiveLocation?.();
      }
    });

    // Close on outside click / Escape / window resize (position would be stale).
    document.addEventListener("click", (e) => {
      if (!fabMenu || !fabMenu.classList.contains("open")) return;
      if (e.target.closest(".sos-fab-menu") || e.target === quickBtn || quickBtn.contains(e.target)) return;
      closeFabMenu();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeFabMenu();
    });
    window.addEventListener("resize", closeFabMenu);

    initPosition();

    if ("IntersectionObserver" in window) {
      const observer = new IntersectionObserver(
        (entries) => {
          entries.forEach((entry) => {
            quickBtn.classList.toggle("is-hidden", entry.isIntersecting);
          });
        },
        { threshold: 0.4 }
      );
      observer.observe(realBtn);
    }
  }

  // -------------------------------------------------------------------
  // Staggered reveal for dynamically-injected list items (contacts,
  // alerts, services, feed). main.js fully replaces each list's innerHTML
  // on every refresh; we just watch for that and add a one-shot CSS
  // animation class per new <li>, staggered by index.
  // -------------------------------------------------------------------
  function initListReveal() {
    const selectors = [
      "#alertsList", "#contactsList", "#servicesList", "#feedList",
      "#incomingInvitesList", "#canTrackMeList",
      "#hubContactsList", "#emergencyHelplinesList",
    ];

    selectors.forEach((sel) => {
      const list = document.querySelector(sel);
      if (!list || !("MutationObserver" in window)) return;

      const observer = new MutationObserver((mutations) => {
        mutations.forEach((m) => {
          let i = 0;
          m.addedNodes.forEach((node) => {
            if (node.nodeType !== 1 || node.tagName !== "LI") return;
            if (node.classList.contains("skeleton-item")) return;
            if (!reducedMotion) {
              node.style.animationDelay = Math.min(i, 6) * 45 + "ms";
              node.classList.add("li-enter");
            }
            i++;
          });
        });
      });

      observer.observe(list, { childList: true });
    });
  }

  // -------------------------------------------------------------------
  // Live trust strip — reads already-rendered DOM state (no extra network
  // calls beyond the one-time contacts load below, which reuses main.js's
  // own loadContacts() so there is exactly one implementation of that
  // fetch+render logic).
  // -------------------------------------------------------------------
  function initTrustStrip() {
    const contactsEl = document.getElementById("trustContactsCount");
    const alertsEl = document.getElementById("trustAlertsCount");
    const twoFaEl = document.getElementById("trust2faStatus");

    function countRealItems(listEl) {
      if (!listEl) return null;
      return Array.from(listEl.children).filter(
        (c) => c.tagName === "LI" && !c.classList.contains("skeleton-item")
      ).length;
    }

    function refreshContacts() {
      const n = countRealItems(document.getElementById("contactsList"));
      if (contactsEl && n !== null) contactsEl.textContent = n;
    }

    function refreshAlerts() {
      const n = countRealItems(document.getElementById("alertsList"));
      if (alertsEl && n !== null) alertsEl.textContent = n;
    }

    function refreshTwoFa() {
      const enabledView = document.getElementById("twoFaEnabledView");
      if (!twoFaEl || !enabledView) return;
      twoFaEl.textContent = enabledView.classList.contains("hidden") ? "Off" : "On";
    }

    if ("MutationObserver" in window) {
      const contactsList = document.getElementById("contactsList");
      const alertsList = document.getElementById("alertsList");
      const twoFaEnabledView = document.getElementById("twoFaEnabledView");

      if (contactsList) new MutationObserver(refreshContacts).observe(contactsList, { childList: true });
      if (alertsList) new MutationObserver(refreshAlerts).observe(alertsList, { childList: true });
      if (twoFaEnabledView) {
        new MutationObserver(refreshTwoFa).observe(twoFaEnabledView, { attributes: true, attributeFilter: ["class"] });
      }
    }

    refreshContacts();
    refreshAlerts();
    refreshTwoFa();

    // main.js defines loadContacts() at top level (classic script, so it's
    // exposed as window.loadContacts) but currently only calls it after an
    // add/delete — the Guardian tab's existing contacts otherwise never load
    // on a fresh page visit. Calling the SAME function here (not a
    // reimplementation) fixes that gap for both the Guardian tab and this
    // trust strip.
    if (typeof window.loadContacts === "function") {
      try { window.loadContacts(); } catch (e) { /* no-op — non-critical */ }
    }
  }

  // -------------------------------------------------------------------
  // Button ripple — purely a click-feedback animation. Delegated on
  // document so it works for every current and future .btn / hub quick-
  // action card without individual listeners; skipped entirely under
  // prefers-reduced-motion.
  // -------------------------------------------------------------------
  function initRipple() {
    if (reducedMotion) return;
    document.addEventListener("click", (e) => {
      const target = e.target.closest(".btn, .hub-quick-action-card, .quick-sos-btn");
      if (!target) return;

      const rect = target.getBoundingClientRect();
      const ripple = document.createElement("span");
      ripple.className = "btn-ripple";
      const size = Math.max(rect.width, rect.height);
      ripple.style.width = ripple.style.height = size + "px";
      ripple.style.left = (e.clientX - rect.left - size / 2) + "px";
      ripple.style.top = (e.clientY - rect.top - size / 2) + "px";

      const prevPosition = getComputedStyle(target).position;
      if (prevPosition === "static") target.style.position = "relative";
      target.style.overflow = target.style.overflow || "hidden";
      target.appendChild(ripple);
      ripple.addEventListener("animationend", () => ripple.remove());
    });
  }

  ready(() => {
    initScrollProgress();
    initHeroParallax();
    initQuickSos();
    initListReveal();
    initTrustStrip();
    initRipple();
  });
})();