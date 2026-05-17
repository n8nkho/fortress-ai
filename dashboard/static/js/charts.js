/**
 * Chart.js helpers for Fortress AI V2 (expects Chart global from CDN).
 */
(function () {
  "use strict";

  var F = {
    macro: null,
    perf: null,
    comparison: null,

    destroyMacro: function () {
      if (this.macro) {
        try {
          this.macro.destroy();
        } catch (_) {}
        this.macro = null;
      }
    },

    destroyPerf: function () {
      if (this.perf) {
        try {
          this.perf.destroy();
        } catch (_) {}
        this.perf = null;
      }
    },

    destroyComparison: function () {
      if (this.comparison) {
        try {
          this.comparison.destroy();
        } catch (_) {}
        this.comparison = null;
      }
    },

    renderMacro: function (canvas, payload) {
      if (!window.Chart || !canvas || !payload || !payload.prices || !payload.prices.length) {
        return;
      }
      this.destroyMacro();
      var ctx = canvas.getContext("2d");
      this.macro = new Chart(ctx, {
        type: "line",
        data: {
          labels: payload.labels || [],
          datasets: [
            {
              data: payload.prices,
              borderColor: "#00e5ff",
              backgroundColor: "rgba(0, 229, 255, 0.08)",
              borderWidth: 2,
              fill: true,
              tension: 0.25,
              pointRadius: 0,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            x: { display: false },
            y: { display: false },
          },
        },
      });
    },

    renderPerf: function (canvas, bundle) {
      if (!window.Chart || !canvas || !bundle) return;
      var labels = bundle.labels || [];
      var ai = bundle.ai_daily_usd || [];
      if (!labels.length || !ai.length) return;
      this.destroyPerf();
      var ctx = canvas.getContext("2d");
      this.perf = new Chart(ctx, {
        type: "line",
        data: {
          labels: labels,
          datasets: [
            {
              label: "LLM spend (USD)",
              data: ai,
              borderColor: "#00e5ff",
              backgroundColor: "rgba(0, 229, 255, 0.06)",
              borderWidth: 2,
              fill: true,
              tension: 0.3,
              pointRadius: 2,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { labels: { color: "#94a3b8" } },
          },
          scales: {
            x: {
              ticks: { color: "#64748b", maxRotation: 45, font: { size: 9 } },
              grid: { color: "rgba(255,255,255,0.06)" },
            },
            y: {
              ticks: { color: "#64748b" },
              grid: { color: "rgba(255,255,255,0.06)" },
            },
          },
        },
      });
    },

    renderComparison: function (canvas, chart) {
      if (!window.Chart || !canvas || !chart || !chart.labels || !chart.labels.length) {
        return;
      }
      this.destroyComparison();
      var ctx = canvas.getContext("2d");
      this.comparison = new Chart(ctx, {
        type: "bar",
        data: {
          labels: chart.labels,
          datasets: [
            {
              label: "Realized P&L",
              data: chart.realized_usd || [],
              backgroundColor: "rgba(52, 211, 153, 0.75)",
              borderColor: "#34d399",
              borderWidth: 1,
            },
            {
              label: "Unrealized P&L",
              data: chart.unrealized_usd || [],
              backgroundColor: "rgba(99, 102, 241, 0.65)",
              borderColor: "#818cf8",
              borderWidth: 1,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { labels: { color: "#94a3b8", font: { size: 10 } } },
            tooltip: {
              callbacks: {
                label: function (ctx) {
                  var v = ctx.parsed.y;
                  var sign = v >= 0 ? "+" : "";
                  return ctx.dataset.label + ": " + sign + v.toFixed(2);
                },
              },
            },
          },
          scales: {
            x: {
              ticks: { color: "#94a3b8", font: { size: 10 } },
              grid: { color: "rgba(255,255,255,0.06)" },
            },
            y: {
              ticks: {
                color: "#64748b",
                callback: function (v) {
                  return (v >= 0 ? "+" : "") + v;
                },
              },
              grid: { color: "rgba(255,255,255,0.06)" },
            },
          },
        },
      });
    },
  };

  window.FortressCharts = F;
})();
