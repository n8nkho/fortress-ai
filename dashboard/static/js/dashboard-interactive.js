/**
 * Fortress AI V2 — non-Alpine helpers (resize → chart redraw, future hooks).
 */
(function () {
  "use strict";

  window.addEventListener(
    "resize",
    function () {
      if (typeof window.__faiRedrawCharts === "function") {
        try {
          window.__faiRedrawCharts();
        } catch (_) {}
      }
    },
    { passive: true }
  );
})();
