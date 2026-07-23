// SafeHer — lightweight 3D tilt for .card elements.
// Purely presentational; does not touch app state or any SafeHer feature logic.
(function () {
  const MAX_TILT = 6; // degrees
  const supportsHover = window.matchMedia("(hover: hover)").matches;
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  if (!supportsHover || reducedMotion) return;

  function attachTilt(card) {
    card.addEventListener("mousemove", (e) => {
      const rect = card.getBoundingClientRect();
      const px = (e.clientX - rect.left) / rect.width; // 0..1
      const py = (e.clientY - rect.top) / rect.height; // 0..1
      const tiltY = (px - 0.5) * 2 * MAX_TILT;
      const tiltX = (0.5 - py) * 2 * MAX_TILT;
      card.style.setProperty("--tiltX", tiltX.toFixed(2) + "deg");
      card.style.setProperty("--tiltY", tiltY.toFixed(2) + "deg");
    });
    card.addEventListener("mouseleave", () => {
      card.style.setProperty("--tiltX", "0deg");
      card.style.setProperty("--tiltY", "0deg");
    });
  }

  function init() {
    document.querySelectorAll(".card").forEach(attachTilt);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();