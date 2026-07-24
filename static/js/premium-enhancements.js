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
  function initQuickSos() {
    const quickBtn = document.getElementById("quickSosBtn");
    const realBtn = document.getElementById("sosBtn");
    if (!quickBtn || !realBtn) return;

    quickBtn.addEventListener("click", () => {
      if (navigator.vibrate) {
        try { navigator.vibrate([40, 30, 40]); } catch (e) { /* no-op */ }
      }
      realBtn.click();
    });

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

  ready(() => {
    initScrollProgress();
    initHeroParallax();
    initQuickSos();
    initListReveal();
    initTrustStrip();
  });
})();
