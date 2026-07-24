// SafeHer — scroll-triggered 3D reveal for .card elements.
// Purely presentational: toggles a class that CSS (see .card / .card.in-view
// in style.css) uses to animate cards from a tilted/faded state into their
// resting "fanned deck" position as they scroll into view. Does not touch
// app state, IDs, or any SafeHer feature logic — safe to load after main.js.
(function () {
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function revealAll() {
    document.querySelectorAll(".card").forEach((card) => card.classList.add("in-view"));
  }

  function init() {
    const cards = document.querySelectorAll(".card");
    if (!cards.length) return;

    if (reducedMotion || !("IntersectionObserver" in window)) {
      revealAll();
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("in-view");
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.15, rootMargin: "0px 0px -60px 0px" }
    );

    cards.forEach((card, i) => {
      // Small stagger so cards in the same row settle in one after another,
      // echoing the fanned-card animation from the reference design.
      card.style.transitionDelay = Math.min(i % 4, 3) * 70 + "ms";
      observer.observe(card);
    });

    // Cards that are already switched to a hidden tab (display:none) never
    // intersect until the tab becomes visible; re-check whenever a tab is
    // shown so their cards still animate in instead of staying invisible.
    document.querySelectorAll(".tab-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        setTimeout(() => {
          document.querySelectorAll(".card:not(.in-view)").forEach((card) => {
            const rect = card.getBoundingClientRect();
            if (rect.top < window.innerHeight && rect.bottom > 0) {
              card.classList.add("in-view");
            }
          });
        }, 50);
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
