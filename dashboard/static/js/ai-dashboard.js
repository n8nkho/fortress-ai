/**
 * Fortress AI dashboard — Alpine.js store + SSE + shortcuts + V2 charts/expert.
 */
document.addEventListener("alpine:init", () => {
  Alpine.data("fortressDashboard", () => ({
    loading: true,
    error: null,
    expertMode: false,
    comparisonOpen: false,
    detailModal: null,
    shortcutsOpen: false,
    expertTab: "prompt",
    expertBundle: null,
    chartData: null,
    es: null,
    pollTimer: null,
    chartPollTimer: null,
    lastTick: Date.now(),
    loopSeconds: 300,

    state: {
      ui_status: "WAITING",
      reasoning: "",
      market_assessment: "",
      action: "wait",
      confidence: 0,
      dry_run: true,
      instance: "Fortress-AI",
      portfolio: { connected: false },
      macro: {},
      recent_decisions: [],
      latest_metric: null,
      weekly_llm_spend_usd: 0,
      weekly_cost_cap_usd: 1,
      today_llm_spend_usd: 0,
      llm_calls_today: 0,
      beliefs: {},
      halt: { effective_halted: false },
      loop_interval_seconds: 300,
      last_decision_ts: null,
      agent_runtime: { run_off_hours_auto: false },
      agent_schedule: { manual_only: false, rth: false },
      screener: { symbols: [], ts: null },
      learning: { decisions_logged: 0, beliefs_keys: 0 },
    },

    comparison: null,

    init() {
      this.expertMode = localStorage.getItem("fai_expert") === "1";
      this.refresh();
      this.loadCharts();
      this.fetchComparison();
      if (this.expertMode) this.fetchExpertBundle();
      this.connectSSE();
      this.pollTimer = setInterval(() => this.refresh(), 12000);
      this.chartPollTimer = setInterval(() => this.loadCharts(), 60000);
      this.loopSeconds = Number(this.state.loop_interval_seconds || 300);
      window.addEventListener("keydown", (e) => this.onKey(e));

      const self = this;
      window.__faiRedrawCharts = function () {
        self.$nextTick(() => self.renderDashboardCharts());
      };
    },

    onKey(e) {
      if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
      const k = e.key.toLowerCase();
      if (k === "?" || (e.shiftKey && k === "/")) {
        e.preventDefault();
        this.shortcutsOpen = !this.shortcutsOpen;
        return;
      }
      if (this.shortcutsOpen && (k === "escape" || k === "?")) {
        e.preventDefault();
        this.shortcutsOpen = false;
        return;
      }
      if (k === "e") {
        e.preventDefault();
        this.toggleExpert();
      }
      if (k === "c") {
        e.preventDefault();
        this.comparisonOpen = !this.comparisonOpen;
        if (this.comparisonOpen && !this.comparison) this.fetchComparison();
      }
      if (k === "h") {
        e.preventDefault();
        this.postHalt(true);
      }
      if (k === "r") {
        e.preventDefault();
        this.requestCycleNow();
      }
    },

    toggleExpert() {
      this.expertMode = !this.expertMode;
      localStorage.setItem("fai_expert", this.expertMode ? "1" : "0");
      if (this.expertMode) this.fetchExpertBundle();
    },

    async refresh() {
      try {
        const r = await fetch("/api/ai/current_state");
        if (!r.ok) throw new Error(await r.text());
        const j = await r.json();
        this.state = { ...this.state, ...j };
        this.loopSeconds = Number(j.loop_interval_seconds || 300);
        this.loading = false;
        this.error = null;
        this.lastTick = Date.now();
        this.$nextTick(() => this.renderDashboardCharts());
      } catch (err) {
        this.error = String(err);
        this.loading = false;
      }
    },

    async loadCharts() {
      try {
        const r = await fetch("/api/charts/dashboard");
        if (!r.ok) return;
        this.chartData = await r.json();
        this.$nextTick(() => this.renderDashboardCharts());
      } catch (_) {}
    },

    async fetchComparison() {
      try {
        const r = await fetch("/api/comparison");
        if (!r.ok) return;
        this.comparison = await r.json();
      } catch (_) {}
    },

    async fetchExpertBundle() {
      try {
        const r = await fetch("/api/expert/bundle");
        if (!r.ok) return;
        this.expertBundle = await r.json();
      } catch (_) {}
    },

    renderDashboardCharts() {
      const spy = document.getElementById("fai-chart-spy");
      const perf = document.getElementById("fai-chart-perf");
      if (window.FortressCharts && this.chartData?.spy && spy) {
        window.FortressCharts.renderMacro(spy, this.chartData.spy);
      }
      if (window.FortressCharts && this.chartData?.llm_cost && perf) {
        window.FortressCharts.renderPerf(perf, this.chartData.llm_cost);
      }
    },

    connectSSE() {
      try {
        this.es = new EventSource("/api/stream/decisions");
        this.es.onmessage = (ev) => {
          try {
            const outer = JSON.parse(ev.data);
            const inner = outer.state || outer;
            if (inner && typeof inner === "object") {
              this.state = { ...this.state, ...inner };
              this.lastTick = Date.now();
              this.$nextTick(() => this.renderDashboardCharts());
            }
          } catch (_) {}
        };
        this.es.onerror = () => {
          this.es.close();
          setTimeout(() => this.connectSSE(), 5000);
        };
      } catch (_) {
        /* SSE unsupported — polling only */
      }
    },

    fmtMoney(n) {
      if (n == null || Number.isNaN(Number(n))) return "—";
      return new Intl.NumberFormat(undefined, {
        style: "currency",
        currency: "USD",
        maximumFractionDigits: 0,
      }).format(Number(n));
    },

    fmtPct(n) {
      if (n == null || Number.isNaN(Number(n))) return "—";
      return `${(Number(n) * 100).toFixed(1)}%`;
    },

    fmtTime(iso) {
      if (!iso) return "—";
      try {
        const d = new Date(iso);
        return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
      } catch {
        return iso;
      }
    },

    statusClass() {
      const s = this.state.ui_status || "WAITING";
      return `fai-status-${s}`;
    },

    statusBadgeClass() {
      const s = this.state.ui_status || "WAITING";
      return `fai-status-badge fai-status-badge--${s}`;
    },

    equityDisplay() {
      const eq = this.state.portfolio?.equity;
      return this.fmtMoney(eq);
    },

    confidenceWidth() {
      return `${Math.min(100, Math.max(0, (this.state.confidence || 0) * 100))}%`;
    },

    confidenceZoneLabel() {
      const p = Math.min(100, Math.max(0, (this.state.confidence || 0) * 100));
      if (p < 50) return "Low";
      if (p < 75) return "Medium";
      return "High";
    },

    confidenceHigh() {
      const p = (this.state.confidence || 0) * 100;
      return p >= 75;
    },

    spyChangeDisplay() {
      const ch = this.chartData?.spy?.change_pct;
      if (ch == null || Number.isNaN(Number(ch))) return "—";
      const sign = ch >= 0 ? "+" : "";
      return `${sign}${Number(ch).toFixed(2)}%`;
    },

    spyChangeClass() {
      const ch = this.chartData?.spy?.change_pct;
      if (ch == null) return "text-slate-400";
      return ch >= 0 ? "text-emerald-400" : "text-red-400";
    },

    rsiGaugeCoords(rsi) {
      const v = Math.min(100, Math.max(0, Number(rsi) || 0));
      const cx = 50;
      const cy = 50;
      const r = 38;
      const theta = Math.PI * (1 - v / 100);
      const x = cx + r * Math.cos(theta);
      const y = cy - r * Math.sin(theta);
      const large = v > 50 ? 1 : 0;
      return { x, y, large };
    },

    /** SVG arc path for semicircle RSI gauge (viewBox 0 0 100 60). */
    rsiArcPath(rsi) {
      const v = Math.min(100, Math.max(0, Number(rsi) || 0));
      const cx = 50;
      const cy = 50;
      const r = 38;
      const theta = Math.PI * (1 - v / 100);
      const x = cx + r * Math.cos(theta);
      const y = cy - r * Math.sin(theta);
      const large = v > 50 ? 1 : 0;
      return `M 12 50 A ${r} ${r} 0 ${large} 1 ${x.toFixed(2)} ${y.toFixed(2)}`;
    },

    nextDecisionEta() {
      const interval = Number(this.state.loop_interval_seconds || 300) * 1000;
      const base = this.state.last_decision_ts ? new Date(this.state.last_decision_ts).getTime() : this.lastTick;
      const next = base + interval;
      const left = Math.max(0, next - Date.now());
      const m = Math.floor(left / 60000);
      const s = Math.floor((left % 60000) / 1000);
      return `${m}m ${s}s`;
    },

    decisionSummary(row) {
      const d = row.decision;
      if (row.error) return row.error;
      if (!d || typeof d !== "object") return JSON.stringify(row).slice(0, 120);
      const act = d.action || "?";
      const conf = d.confidence != null ? ` (${Math.round(Number(d.confidence) * 100)}%)` : "";
      const rs = (d.reasoning || "").slice(0, 80);
      return `${act}${conf} — ${rs}`;
    },

    decisionPreview(row) {
      const rs = row?.decision?.reasoning || row?.error || "";
      if (!rs) return "—";
      return rs.length > 100 ? rs.slice(0, 100) + "…" : rs;
    },

    detailMarketContext() {
      const d = this.detailModal?.decision;
      if (!d || typeof d !== "object") return null;
      return d.market_context ?? d.market_assessment ?? this.detailModal?.market_snapshot ?? null;
    },

    detailReasoning() {
      const d = this.detailModal?.decision;
      if (!d || typeof d !== "object") return this.detailModal?.error || "";
      return d.reasoning || "";
    },

    recentFive() {
      const rows = [...(this.state.recent_decisions || [])].reverse().slice(0, 5);
      return rows;
    },

    rowTime(row) {
      return row?.ts || row?.timestamp || null;
    },

    beliefsList() {
      const b = this.state.beliefs || {};
      return Object.entries(b).slice(0, 8);
    },

    beliefsPatterns() {
      return this.beliefsList().map(([k, v]) => {
        let confidence = null;
        let usageCount = null;
        if (v && typeof v === "object") {
          if (v.confidence != null) confidence = Math.round(Number(v.confidence) * 100);
          if (v.usage_count != null) usageCount = v.usage_count;
          if (v.count != null) usageCount = v.count;
        }
        return { key: k, description: k, confidence, usageCount, raw: v };
      });
    },

    learningPct() {
      const n = Number(this.state.learning?.decisions_logged || 0);
      return Math.min(100, Math.round((n / 120) * 100));
    },

    patternsLearned() {
      return Number(this.state.learning?.beliefs_keys ?? 0);
    },

    totalDecisions() {
      return Number(this.state.learning?.decisions_logged ?? 0);
    },

    screenerSymbols() {
      return this.state.screener?.symbols || [];
    },

    screenerTotal() {
      const n = this.screenerSymbols().length;
      return Math.max(n, 1);
    },

    weekCostPct() {
      const cap = Number(this.state.weekly_cost_cap_usd || 1);
      const sp = Number(this.state.weekly_llm_spend_usd || 0);
      if (!cap || cap <= 0) return 0;
      return Math.min(100, (sp / cap) * 100);
    },

    async requestCycleNow() {
      try {
        await fetch("/api/agent/run-cycle", { method: "POST" });
      } catch (_) {}
    },

    async postHalt(active) {
      await fetch("/api/operator/halt", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          active,
          reason: active ? "operator_dashboard_halt" : "operator_resume",
          actor: "fortress_ai_ui",
        }),
      });
      await this.refresh();
    },

    exportBundle() {
      window.open("/api/export/bundle", "_blank");
    },

    openDetail(row) {
      this.detailModal = row;
    },

    closeDetail() {
      this.detailModal = null;
    },

    copyDetailJson() {
      if (!this.detailModal) return;
      const t = JSON.stringify(this.detailModal, null, 2);
      navigator.clipboard?.writeText(t).catch(() => {});
    },

    copyExpertJson() {
      const t = JSON.stringify(this.expertBundle?.last_decision || {}, null, 2);
      navigator.clipboard?.writeText(t).catch(() => {});
    },

    dryRunLabel() {
      return this.state.dry_run ? "WEEK 1 DRY-RUN" : "LIVE PAPER PATH";
    },
  }));
});
