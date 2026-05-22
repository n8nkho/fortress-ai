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
    mediumPollTimer: null,
    slowPollTimer: null,
    ageTickTimer: null,
    ageTick: 0,
    beliefPanelLastRefresh: null,
    ingestPanelLastRefresh: null,
    mediumTierLastRefresh: null,
    lastTick: Date.now(),
    loopSeconds: 300,
    tabVisible: typeof document !== "undefined" ? !document.hidden : true,

    siStatus: null,
    siProposals: [],
    siTotalLogLines: 0,
    siLoading: false,
    siError: null,

    peStatus: null,
    peAnalysis: null,
    peRecentEvents: [],
    peLoading: false,
    peError: null,

    govVetoPending: null,
    govTiers: null,
    govError: null,

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
      agent_schedule: { manual_only: false, rth: false, weekend: false, auto_cycles: false },
      screener: { symbols: [], ts: null },
      learning: { decisions_logged: 0, beliefs_keys: 0 },
      domain_intel: {},
      belief_memory: { total_beliefs: 0, top_beliefs: [], recent_beliefs: [] },
      ingest_health: {},
    },

    comparison: null,
    tradingDiagnostics: null,
    skim: { universe: [], dry_run: null },

    spy: {
      symbol: "SPY",
      dry_run: true,
      max_exposure_usd: 10000,
      latest_metric: null,
      ladder: null,
      last_decision: null,
      market_preview: null,
      global_markets_enabled: true,
      loading: false,
      cyclePending: false,
      error: null,
      cycleMsg: null,
      siMsg: null,
      si: null,
      lastRefresh: null,
    },
    spyPollTimer: null,

    init() {
      this.expertMode = localStorage.getItem("fai_expert") === "1";
      this.fetchSkimStatus();
      this.refresh();
      this.loadCharts();
      this.fetchComparison();
      this.fetchSpyStatus();
      this.fetchSkimStatus();
      if (this.expertMode) this.fetchExpertBundle();
      this.startPolling();
      if (this.tabVisible) this.connectSSE();
      this.ageTickTimer = setInterval(() => {
        this.ageTick++;
      }, 5000);
      setTimeout(() => this.refreshMediumTier(), 800);
      setTimeout(() => this.refreshSlowTier(), 1200);
      this.loopSeconds = Number(this.state.loop_interval_seconds || 300);
      window.addEventListener("keydown", (e) => this.onKey(e));
      document.addEventListener("visibilitychange", () => this.onVisibilityChange());
      this.fetchSelfImprovement();
      this.fetchPromptEvolution();
      this.fetchGovernance();

      const self = this;
      window.__faiRedrawCharts = function () {
        self.$nextTick(() => self.renderDashboardCharts());
      };
    },

    onVisibilityChange() {
      this.tabVisible = !document.hidden;
      if (this.tabVisible) {
        this.startPolling();
        this.connectSSE();
        this.refresh();
        this.fetchComparison();
        this.fetchSpyStatus();
        this.fetchSkimStatus();
      } else {
        this.stopPolling();
        this.disconnectSSE();
      }
    },

    startPolling() {
      this.stopPolling();
      if (!this.tabVisible) return;
      // Backup poll while SSE is active (server caches state ~25s).
      this.pollTimer = setInterval(() => this.refresh(), 60000);
      this.chartPollTimer = setInterval(() => this.loadCharts(), 60000);
      this.mediumPollTimer = setInterval(() => this.refreshMediumTier(), 60000);
      this.slowPollTimer = setInterval(() => this.refreshSlowTier(), 300000);
      this.spyPollTimer = setInterval(() => {
        this.fetchSpyStatus();
        this.fetchSkimStatus();
      }, 45000);
    },

    stopPolling() {
      if (this.pollTimer) clearInterval(this.pollTimer);
      if (this.chartPollTimer) clearInterval(this.chartPollTimer);
      if (this.mediumPollTimer) clearInterval(this.mediumPollTimer);
      if (this.slowPollTimer) clearInterval(this.slowPollTimer);
      if (this.spyPollTimer) clearInterval(this.spyPollTimer);
      this.pollTimer = null;
      this.chartPollTimer = null;
      this.mediumPollTimer = null;
      this.slowPollTimer = null;
      this.spyPollTimer = null;
    },

    disconnectSSE() {
      if (this.es) {
        try {
          this.es.close();
        } catch (_) {}
        this.es = null;
      }
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
        const patch = { ...j };
        if (patch.domain_intel == null || typeof patch.domain_intel !== "object") {
          patch.domain_intel = {};
        }
        if (patch.belief_memory == null || typeof patch.belief_memory !== "object") {
          patch.belief_memory = {
            total_beliefs: 0,
            top_beliefs: [],
            recent_beliefs: [],
          };
        }
        if (patch.ingest_health == null || typeof patch.ingest_health !== "object") {
          patch.ingest_health = { _missing: true };
        }
        this.state = { ...this.state, ...patch };
        this.tradingDiagnostics = patch.trading_diagnostics || null;
        this.loopSeconds = Number(patch.loop_interval_seconds || 300);
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
        this.$nextTick(() => this.renderComparisonChart());
      } catch (_) {}
    },

    renderComparisonChart() {
      const canvas = document.getElementById("fai-chart-comparison");
      if (window.FortressCharts && canvas && this.comparison?.chart) {
        window.FortressCharts.renderComparison(canvas, this.comparison.chart);
      }
    },

    async fetchSkimStatus() {
      try {
        const r = await fetch("/api/skim/status");
        if (!r.ok) throw new Error(await r.text());
        this.skim = await r.json();
      } catch (_) {
        this.skim = this.skim || { error: "unavailable" };
      }
    },

    async fetchSpyStatus() {
      this.spy.loading = true;
      this.spy.error = null;
      try {
        const r = await fetch("/api/spy/status");
        if (!r.ok) throw new Error(await r.text());
        const j = await r.json();
        this.spy = {
          ...this.spy,
          ...j,
          loading: false,
          lastRefresh: Date.now(),
        };
      } catch (err) {
        this.spy.loading = false;
        this.spy.error = String(err).slice(0, 200);
      }
    },

    async requestSpyCycle() {
      this.spy.cyclePending = true;
      this.spy.cycleMsg = null;
      this.spy.error = null;
      try {
        const r = await fetch("/api/spy/run-cycle", { method: "POST" });
        if (!r.ok) throw new Error(await r.text());
        this.spy.cycleMsg = "Cycle queued — refresh in ~60s";
        setTimeout(() => this.fetchSpyStatus(), 65000);
        setTimeout(() => this.fetchSpyStatus(), 5000);
      } catch (err) {
        this.spy.error = String(err).slice(0, 200);
      } finally {
        this.spy.cyclePending = false;
      }
    },

    spyModeLabel() {
      return this.spy.dry_run ? "DRY-RUN" : "PAPER LIVE";
    },

    spyLadderLabel() {
      const l = this.spy.ladder;
      if (!l) return "flat · 0/3";
      return `${l.side || "flat"} · ${l.rungs_open ?? 0}/${l.max_rungs ?? 3}`;
    },

    spyLastSummaryLine() {
      const m = this.spy.latest_metric;
      const rth = m?.us_equity_rth ? "RTH" : "off-hours";
      const eod = this.spy.last_decision?.observation_summary?.eod_phase || "";
      return eod ? `${eod} · ${rth}` : rth;
    },

    spyReasoningLine() {
      const d = this.spy.last_decision?.decision;
      if (!d) return "No cycles yet — use Run SPY cycle or wait for Mon RTH auto.";
      return d.reasoning || d.market_assessment || "—";
    },

    spyMetricAgeLabel() {
      void this.ageTick;
      const ts = this.spy.latest_metric?.ts;
      if (!ts) return "";
      const ms = Date.parse(ts);
      if (Number.isNaN(ms)) return "";
      return "Updated " + this.panelAgeLabel(ms);
    },

    spySiSkimRate() {
      const r = this.spy.si?.performance?.skim_rate;
      if (r == null) return "—";
      return (r * 100).toFixed(1) + "%";
    },

    spySiPendingLine() {
      const p = this.spy.si?.pending;
      if (!p) return "No pending SI proposal.";
      if (p.proposal?.parameter) {
        return `Pending: ${p.proposal.parameter} → ${p.proposal.proposed_value}`;
      }
      return "Pending SI review.";
    },

    async spySiPropose() {
      this.spy.siMsg = null;
      this.spy.error = null;
      try {
        const r = await fetch("/api/spy/self_improvement/propose", { method: "POST" });
        const j = await r.json();
        if (!r.ok) throw new Error(j.error || r.statusText);
        this.spy.siMsg = j.skipped
          ? `Skipped: ${j.reason || "—"}`
          : `SI: ${j.decision || j.outcome || "done"}`;
        await this.fetchSpyStatus();
      } catch (err) {
        this.spy.error = String(err).slice(0, 200);
      }
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
      if (!this.tabVisible) return;
      if (this.es) return;
      try {
        this.es = new EventSource("/api/stream/decisions");
        this.es.onmessage = (ev) => {
          try {
            const outer = JSON.parse(ev.data);
            const inner = outer.state || outer;
            if (inner && typeof inner === "object") {
              this.state = { ...this.state, ...inner };
              if (this.state.domain_intel == null || typeof this.state.domain_intel !== "object") {
                this.state.domain_intel = {};
              }
              if (this.state.belief_memory == null || typeof this.state.belief_memory !== "object") {
                this.state.belief_memory = {
                  total_beliefs: 0,
                  top_beliefs: [],
                  recent_beliefs: [],
                };
              }
              if (this.state.ingest_health == null || typeof this.state.ingest_health !== "object") {
                this.state.ingest_health = { _missing: true };
              }
              this.lastTick = Date.now();
              this.$nextTick(() => this.renderDashboardCharts());
            }
          } catch (_) {}
        };
        this.es.onerror = () => {
          this.disconnectSSE();
          if (this.tabVisible) setTimeout(() => this.connectSSE(), 5000);
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

    ingestHealthRows() {
      const labels = {
        sec_edgar: "SEC EDGAR",
        fred: "FRED Macro",
        news_sentiment: "News Sentiment",
        cot_report: "COT Report",
        historical_seed: "Historical belief seed",
      };
      const s = this.state.ingest_health?.sources || {};
      return Object.keys(s)
        .sort()
        .map((k) => {
          const m = s[k] || {};
          return { src: k, label: labels[k] || k, ...m };
        });
    },

    ingestHealthMissing() {
      const h = this.state.ingest_health;
      if (!h) return true;
      if (h._missing) return true;
      if (!h.last_run && !h.sources) return true;
      return false;
    },

    ingestRecordSummaryLine() {
      const s = this.state.ingest_health?.sources || {};
      const sec = s.sec_edgar?.record_count ?? 0;
      const fred = s.fred?.record_count ?? 0;
      const news = s.news_sentiment?.record_count ?? 0;
      const cot = s.cot_report?.record_count ?? 0;
      const hist = s.historical_seed?.record_count ?? 0;
      return `SEC: ${sec} records | FRED: ${fred} records | News: ${news} records | COT: ${cot} records | Hist seed: ${hist}`;
    },

    domainIngestStatusLine() {
      const di = this.state.domain_intel || {};
      if (di.web_ingest_status === "active_readonly") {
        return "🟢 INGEST ACTIVE (read-only)";
      }
      const ih = this.state.ingest_health;
      const wd = di.web_ingest_default;
      const fallback = `web ingest default: ${wd ? "on" : "off"}`;
      if (!ih || ih._missing || !ih.last_run) return fallback;
      try {
        const t = new Date(ih.last_run).getTime();
        if (!Number.isNaN(t) && Date.now() - t < 6 * 3600 * 1000) {
          return "🟢 INGEST ACTIVE (read-only)";
        }
      } catch (_) {}
      return fallback;
    },

    beliefsPluralWord() {
      const n = Number(this.state.belief_memory?.total_beliefs ?? 0);
      return n === 1 ? "belief" : "beliefs";
    },

    beliefConfidenceBarClass(score) {
      const v = Number(score) || 0;
      if (v > 0.7) return "bg-emerald-500";
      if (v >= 0.4) return "bg-amber-400";
      return "bg-red-500";
    },

    beliefOutcomeClass(outcome) {
      const o = String(outcome || "").toLowerCase();
      if (o === "win") return "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30";
      if (o === "loss") return "bg-red-500/20 text-red-300 border border-red-500/30";
      return "bg-slate-600/30 text-slate-300 border border-white/10";
    },

    fmtIsoShort(iso) {
      if (!iso) return "—";
      try {
        const d = new Date(iso);
        return d.toLocaleString(undefined, {
          month: "short",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
        });
      } catch (_) {
        return String(iso).slice(0, 16);
      }
    },

    panelAgeLabel(ts) {
      void this.ageTick;
      if (!ts) return "—";
      const s = Math.floor((Date.now() - ts) / 1000);
      if (s < 0) return "—";
      if (s < 60) return `${s}s ago`;
      const m = Math.floor(s / 60);
      if (m < 60) return `${m}m ago`;
      const h = Math.floor(m / 60);
      return `${h}h ago`;
    },

    beliefPanelAgeLabel() {
      void this.ageTick;
      return this.beliefPanelLastRefresh
        ? `last refreshed: ${this.panelAgeLabel(this.beliefPanelLastRefresh)}`
        : "last refreshed: —";
    },

    ingestPanelAgeLabel() {
      void this.ageTick;
      return this.ingestPanelLastRefresh
        ? `last refreshed: ${this.panelAgeLabel(this.ingestPanelLastRefresh)}`
        : "last refreshed: —";
    },

    async refreshMediumTier() {
      try {
        const r = await fetch("/api/ai/current_state");
        if (!r.ok) return;
        const j = await r.json();
        let di = j.domain_intel;
        if (di == null || typeof di !== "object") di = {};
        let bm = j.belief_memory;
        if (bm == null || typeof bm !== "object") {
          bm = { total_beliefs: 0, top_beliefs: [], recent_beliefs: [] };
        }
        this.state = {
          ...this.state,
          belief_memory: bm,
          domain_intel: di,
          beliefs: j.beliefs || this.state.beliefs,
          learning: j.learning || this.state.learning,
          screener: j.screener || this.state.screener,
          weekly_llm_spend_usd: j.weekly_llm_spend_usd,
          today_llm_spend_usd: j.today_llm_spend_usd,
          llm_calls_today: j.llm_calls_today,
          weekly_cost_cap_usd: j.weekly_cost_cap_usd,
        };
        this.mediumTierLastRefresh = Date.now();
        this.beliefPanelLastRefresh = Date.now();
        this.$nextTick(() => this.renderDashboardCharts());
      } catch (_) {}
    },

    async refreshSlowTier() {
      try {
        const r = await fetch("/api/ai/ingest_health");
        if (r.ok) {
          const j = await r.json();
          if (j._missing) {
            this.state = { ...this.state, ingest_health: { _missing: true } };
          } else {
            this.state = { ...this.state, ingest_health: j };
          }
          this.ingestPanelLastRefresh = Date.now();
        }
      } catch (_) {}
      try {
        await this.fetchSelfImprovement();
        await this.fetchPromptEvolution();
        await Promise.all([this.fetchGovernance(), this.fetchGovernanceTiers()]);
      } catch (_) {}
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

    scheduleStatusLabel() {
      const s = this.state.agent_schedule || {};
      if (s.manual_only) return "Manual-only: no auto cadence";
      if (s.weekend) return "Weekend: manual / on-demand only";
      if (this.state.us_equity_rth) return "RTH: cadence from FORTRESS_AI_LOOP_SECONDS (.env)";
      return "Off-hours: on-demand only";
    },

    nextDecisionEta() {
      if (this.state.agent_schedule?.manual_only || this.state.agent_schedule?.weekend) {
        return "On demand only";
      }
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

    screenerCountLabel() {
      const n = this.screenerSymbols().length;
      return n === 1 ? "1 symbol" : `${n} symbols`;
    },

    screenerSourceLabel() {
      const sc = this.state.screener || {};
      if (!(sc.symbols || []).length) return "";
      const labels = {
        ai_screen_market: "AI screen_market",
        classic_daily_signals: "Classic daily_signals",
        fortress_watchlist: "Fortress watchlist",
        classic_config_watchlist: "Classic config watchlist",
      };
      const src = labels[sc.source] || sc.source || "watchlist";
      let line = `Source: ${src}`;
      if (sc.ts) line += ` · ${sc.ts}`;
      return line;
    },

    comparisonSide(side) {
      return (this.comparison && this.comparison[side]) || {};
    },

    comparisonEquity(side) {
      const block = this.comparisonSide(side);
      const eq = block.equity ?? block.portfolio?.equity;
      if (eq == null && block.portfolio && !block.portfolio.connected) {
        return block.portfolio.reason ? "offline" : "—";
      }
      return this.fmtMoney(eq);
    },

    comparisonPositions(side) {
      const block = this.comparisonSide(side);
      const n = block.position_count ?? block.portfolio?.position_count;
      return n != null ? String(n) : "—";
    },

    comparisonUnrealized(side) {
      const block = this.comparisonSide(side);
      const u = block.unrealized_pl ?? block.portfolio?.unrealized_pl;
      if (u == null) return "—";
      return this.fmtMoney(u);
    },

    comparisonRealized(side) {
      const block = this.comparisonSide(side);
      const r = block.realized_pnl;
      if (r == null) return "—";
      const n = block.realized_trade_count;
      const src = block.realized_source;
      const money = this.fmtMoney(r);
      if (n > 0) return `${money} (${n} closes)`;
      if (src === "equity_proxy") return `${money} (est.)`;
      return money;
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

    async fetchSelfImprovement() {
      this.siLoading = true;
      this.siError = null;
      try {
        const [rs, rp] = await Promise.all([
          fetch("/api/self_improvement/status"),
          fetch("/api/self_improvement/proposals"),
        ]);
        if (!rs.ok) throw new Error(await rs.text());
        if (!rp.ok) throw new Error(await rp.text());
        this.siStatus = await rs.json();
        const pj = await rp.json();
        this.siProposals = pj.proposals || [];
        this.siTotalLogLines = Number(pj.total_lines || 0);
      } catch (err) {
        this.siError = String(err);
      } finally {
        this.siLoading = false;
      }
    },

    async proposeSelfImprovement() {
      this.siLoading = true;
      this.siError = null;
      try {
        const r = await fetch("/api/self_improvement/propose", { method: "POST" });
        const j = await r.json();
        if (!r.ok || !j.ok) throw new Error(j.error || r.statusText);
        await this.fetchSelfImprovement();
      } catch (err) {
        this.siError = String(err);
      } finally {
        this.siLoading = false;
      }
    },

    async approveSelfImprovement(proposalId) {
      this.siLoading = true;
      this.siError = null;
      try {
        const r = await fetch("/api/self_improvement/approve", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ proposal_id: proposalId || null }),
        });
        const j = await r.json();
        if (!r.ok || !j.ok) throw new Error(j.error || r.statusText);
        await this.fetchSelfImprovement();
      } catch (err) {
        this.siError = String(err);
      } finally {
        this.siLoading = false;
      }
    },

    async rejectSelfImprovement() {
      this.siLoading = true;
      this.siError = null;
      try {
        const r = await fetch("/api/self_improvement/reject", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reason: "dashboard_reject" }),
        });
        const j = await r.json();
        if (!r.ok || !j.ok) throw new Error(j.error || r.statusText);
        await this.fetchSelfImprovement();
      } catch (err) {
        this.siError = String(err);
      } finally {
        this.siLoading = false;
      }
    },

    async revertSelfImprovement() {
      this.siLoading = true;
      this.siError = null;
      try {
        const r = await fetch("/api/self_improvement/revert", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reason: "dashboard_revert" }),
        });
        const j = await r.json();
        if (!r.ok || !j.ok) throw new Error(j.error || r.statusText);
        await this.fetchSelfImprovement();
      } catch (err) {
        this.siError = String(err);
      } finally {
        this.siLoading = false;
      }
    },

    async monitorSelfImprovement() {
      this.siLoading = true;
      this.siError = null;
      try {
        const r = await fetch("/api/self_improvement/monitor", { method: "POST" });
        const j = await r.json();
        if (!r.ok) throw new Error(j.error || r.statusText);
        await this.fetchSelfImprovement();
      } catch (err) {
        this.siError = String(err);
      } finally {
        this.siLoading = false;
      }
    },

    async fetchPromptEvolution() {
      this.peLoading = true;
      this.peError = null;
      try {
        const r = await fetch("/api/prompt_evolution/status");
        if (!r.ok) throw new Error(await r.text());
        this.peStatus = await r.json();
        this.peRecentEvents = this.peStatus.recent_events || [];
      } catch (err) {
        this.peError = String(err);
      } finally {
        this.peLoading = false;
      }
    },

    async analyzePromptEvolution() {
      this.peLoading = true;
      this.peError = null;
      try {
        const r = await fetch("/api/prompt_evolution/analyze", { method: "POST" });
        const j = await r.json();
        if (!r.ok || !j.ok) throw new Error(j.error || r.statusText);
        this.peAnalysis = j.analysis;
      } catch (err) {
        this.peError = String(err);
      } finally {
        this.peLoading = false;
      }
    },

    async proposePromptEvolution() {
      this.peLoading = true;
      this.peError = null;
      try {
        const r = await fetch("/api/prompt_evolution/propose", { method: "POST" });
        const j = await r.json();
        if (!r.ok || !j.ok) throw new Error(j.error || r.statusText);
        await this.fetchPromptEvolution();
      } catch (err) {
        this.peError = String(err);
      } finally {
        this.peLoading = false;
      }
    },

    async approvePromptEvolution(proposalId) {
      this.peLoading = true;
      this.peError = null;
      try {
        const r = await fetch("/api/prompt_evolution/approve", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ proposal_id: proposalId || null }),
        });
        const j = await r.json();
        if (!r.ok || !j.ok) throw new Error(j.error || r.statusText);
        await this.fetchPromptEvolution();
      } catch (err) {
        this.peError = String(err);
      } finally {
        this.peLoading = false;
      }
    },

    async rejectPromptEvolution() {
      this.peLoading = true;
      this.peError = null;
      try {
        const r = await fetch("/api/prompt_evolution/reject", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reason: "dashboard_reject" }),
        });
        const j = await r.json();
        if (!r.ok || !j.ok) throw new Error(j.error || r.statusText);
        await this.fetchPromptEvolution();
      } catch (err) {
        this.peError = String(err);
      } finally {
        this.peLoading = false;
      }
    },

    async revertPromptEvolution() {
      this.peLoading = true;
      this.peError = null;
      try {
        const r = await fetch("/api/prompt_evolution/revert", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reason: "dashboard_revert" }),
        });
        const j = await r.json();
        if (!r.ok || !j.ok) throw new Error(j.error || r.statusText);
        await this.fetchPromptEvolution();
      } catch (err) {
        this.peError = String(err);
      } finally {
        this.peLoading = false;
      }
    },

    async startAbPromptEvolution(days) {
      this.peLoading = true;
      this.peError = null;
      try {
        const r = await fetch("/api/prompt_evolution/ab/start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ duration_days: days || 7 }),
        });
        const j = await r.json();
        if (!r.ok || !j.ok) throw new Error(j.error || r.statusText);
        await this.fetchPromptEvolution();
      } catch (err) {
        this.peError = String(err);
      } finally {
        this.peLoading = false;
      }
    },

    async endAbPromptEvolution(winner) {
      this.peLoading = true;
      this.peError = null;
      try {
        const r = await fetch("/api/prompt_evolution/ab/end", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ winner, reason: "dashboard_end_ab" }),
        });
        const j = await r.json();
        if (!r.ok || !j.ok) throw new Error(j.error || r.statusText);
        await this.fetchPromptEvolution();
      } catch (err) {
        this.peError = String(err);
      } finally {
        this.peLoading = false;
      }
    },

    async fetchGovernance() {
      this.govError = null;
      try {
        const r = await fetch("/api/governance/pending");
        if (!r.ok) throw new Error(await r.text());
        const j = await r.json();
        this.govVetoPending = j.veto_pending || null;
      } catch (err) {
        this.govError = String(err);
      }
    },

    async fetchGovernanceTiers() {
      this.govError = null;
      try {
        const r = await fetch("/api/governance/tiers");
        if (!r.ok) throw new Error(await r.text());
        this.govTiers = await r.json();
      } catch (err) {
        this.govError = String(err);
      }
    },

    async processGovernanceVetoWindows() {
      this.govError = null;
      try {
        const r = await fetch("/api/governance/process-veto-windows", { method: "POST" });
        const j = await r.json();
        if (!r.ok || !j.ok) throw new Error(j.error || r.statusText);
        await this.fetchGovernance();
      } catch (err) {
        this.govError = String(err);
      }
    },

    async vetoGovernanceProposal(id) {
      this.govError = null;
      try {
        const r = await fetch("/api/governance/veto/" + encodeURIComponent(id), { method: "POST" });
        const j = await r.json();
        if (!r.ok || !j.ok) throw new Error(j.error || r.statusText);
        await this.fetchGovernance();
      } catch (err) {
        this.govError = String(err);
      }
    },
  }));
});
