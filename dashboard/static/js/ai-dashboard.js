/**
 * Fortress AI dashboard — Alpine.js store + SSE + shortcuts + V2 charts/expert.
 */
document.addEventListener("alpine:init", () => {
  Alpine.data("fortressDashboard", () => ({
    loading: true,
    error: null,
    expertMode: false,
    comparisonOpen: false,
    skimSwarmOpen: false,
    skimFallbackUniverse: [],
    skimSortCol: "wins",
    skimSortDir: "desc",
    skimSortedRows: [],
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

    capReview: null,
    capMetricsRows: [],
    capLoading: false,
    capError: null,

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
    skim: { universe: [], dry_run: null, pnl: null },
    skimPollTimer: null,
    infra: { universe: [], dry_run: null, pnl: null, anchor: "SMH" },

    init() {
      this.loadSkimFallbackUniverse();
      this.recomputeSkimSort();
      this.expertMode = localStorage.getItem("fai_expert") === "1";
      this.fetchSkimStatus();
      this.fetchInfraStatus();
      this.refresh();
      this.loadCharts();
      this.fetchComparison();
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
      this.fetchCapabilityReview();
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
        this.fetchSkimStatus();
        this.fetchInfraStatus();
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
      this.skimPollTimer = setInterval(() => {
        this.fetchSkimStatus();
        this.fetchInfraStatus();
      }, 45000);
    },

    stopPolling() {
      if (this.pollTimer) clearInterval(this.pollTimer);
      if (this.chartPollTimer) clearInterval(this.chartPollTimer);
      if (this.mediumPollTimer) clearInterval(this.mediumPollTimer);
      if (this.slowPollTimer) clearInterval(this.slowPollTimer);
      if (this.skimPollTimer) clearInterval(this.skimPollTimer);
      this.pollTimer = null;
      this.chartPollTimer = null;
      this.mediumPollTimer = null;
      this.slowPollTimer = null;
      this.skimPollTimer = null;
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
      if (this.skimSwarmOpen && k === "escape") {
        e.preventDefault();
        this.closeSkimSwarm();
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

    loadSkimFallbackUniverse() {
      try {
        const el = document.getElementById("fai-skim-fallback-universe");
        if (!el?.textContent) return;
        const parsed = JSON.parse(el.textContent);
        if (Array.isArray(parsed)) this.skimFallbackUniverse = parsed;
      } catch (_) {}
    },

    async fetchSkimStatus() {
      try {
        const r = await fetch("/api/skim/status");
        if (!r.ok) throw new Error(await r.text());
        this.skim = await r.json();
        this.recomputeSkimSort();
      } catch (_) {
        this.skim = this.skim || { error: "unavailable" };
        this.recomputeSkimSort();
      }
    },

    async fetchInfraStatus() {
      try {
        const r = await fetch("/api/infra/status");
        if (!r.ok) throw new Error(await r.text());
        this.infra = await r.json();
      } catch (_) {
        this.infra = this.infra || { error: "unavailable" };
      }
    },

    infraRows() {
      const universe = this.infra?.universe || [];
      const states = this.infra?.symbol_states || [];
      const bySym = {};
      for (const s of states) bySym[s.symbol] = s;
      return universe.map((sym) => {
        const st = bySym[sym] || {};
        return {
          symbol: sym,
          layer: st.layer || "—",
          side: st.side || "flat",
          realized_usd: st.realized_usd,
          wins: st.wins,
          losses: st.losses,
        };
      });
    },

    skimPnlFmt(v) {
      if (v == null || Number.isNaN(Number(v))) return "—";
      const n = Number(v);
      const sign = n >= 0 ? "+" : "";
      return sign + "$" + n.toFixed(2);
    },

    skimPnlClass(v) {
      if (v == null || Number.isNaN(Number(v))) return "text-slate-400";
      return Number(v) >= 0 ? "text-emerald-300" : "text-red-300";
    },

    openSkimSwarm() {
      this.skimSwarmOpen = true;
      if (!this.skim?.pnl) this.fetchSkimStatus();
    },

    closeSkimSwarm() {
      this.skimSwarmOpen = false;
    },

    skimUniverse() {
      if (this.skim?.universe?.length) return this.skim.universe;
      return this.skimFallbackUniverse || [];
    },

    skimSwarmRowsRaw() {
      const universe = this.skimUniverse();
      const states = this.skim?.symbol_states || [];
      const quotes = this.skim?.symbol_quotes || {};
      const openMap = {};
      for (const p of this.skim?.pnl?.open_positions_detail || []) {
        if (p?.symbol) openMap[p.symbol] = p;
      }
      const realizedMap = {};
      for (const p of this.skim?.pnl?.per_symbol_realized || []) {
        if (p?.symbol) realizedMap[p.symbol] = p;
      }
      const stateMap = {};
      for (const s of states) {
        if (s?.symbol) stateMap[s.symbol] = s;
      }
      return universe.map((sym) => {
        const row = stateMap[sym] || {};
        const quote = quotes[sym] || {};
        const open = openMap[sym];
        const real = realizedMap[sym];
        const side = quote.side || open?.side || row.side || "flat";
        const isOpen = Boolean(quote.is_open || (side !== "flat" && side !== "—"));
        return {
          symbol: sym,
          side,
          is_open: isOpen,
          last_price: quote.last ?? null,
          change_pct: quote.change_pct ?? null,
          position_pct: quote.position_pct ?? null,
          avg_entry: quote.avg_entry ?? null,
          last_action: row.last_action || "—",
          last_block_reason: row.last_block_reason || "—",
          unrealized_usd: quote.unrealized_usd ?? open?.unrealized_usd,
          realized_usd: real?.realized_usd ?? row.realized_usd,
          exits: real?.exits ?? row.exits ?? row.learned?.stats?.exits,
          wins: real?.wins ?? row.wins ?? row.learned?.stats?.wins,
          losses: real?.losses ?? row.losses ?? row.learned?.stats?.losses,
          learned: row.learned,
          company: row.company,
        };
      });
    },

    skimSwarmRows() {
      return this.skimSortedRows || [];
    },

    recomputeSkimSort() {
      const dir = this.skimSortDir === "asc" ? 1 : -1;
      const col = this.skimSortCol;
      this.skimSortedRows = [...this.skimSwarmRowsRaw()].sort((a, b) => {
        const cmp = this.skimSortCompare(a, b, col);
        if (cmp !== 0) return cmp * dir;
        return String(a.symbol).localeCompare(String(b.symbol));
      });
    },

    toggleSkimSort(col) {
      if (this.skimSortCol === col) {
        this.skimSortDir = this.skimSortDir === "asc" ? "desc" : "asc";
      } else {
        this.skimSortCol = col;
        const textAsc = ["symbol", "company", "last_action", "side"];
        this.skimSortDir = textAsc.includes(col) ? "asc" : "desc";
      }
      this.recomputeSkimSort();
    },

    skimSortIndicator(col) {
      if (this.skimSortCol !== col) return "";
      return this.skimSortDir === "asc" ? " ▲" : " ▼";
    },

    skimSortThClass(col) {
      if (this.skimSortCol !== col) return "text-slate-500 hover:text-emerald-200";
      return "text-emerald-300";
    },

    skimSortValue(row, col) {
      switch (col) {
        case "open":
          return row.is_open ? 1 : 0;
        case "symbol":
          return row.symbol || "";
        case "side": {
          const order = { long: 0, short: 1, flat: 2 };
          return order[row.side] ?? 3;
        }
        case "last_price":
          return row.last_price;
        case "change_pct":
          return row.change_pct;
        case "position_pct":
          return row.position_pct;
        case "avg_entry":
          return row.avg_entry;
        case "unrealized_usd":
          return row.unrealized_usd;
        case "realized_usd":
          return row.realized_usd;
        case "last_action":
          return row.last_action || "";
        case "wins":
          return this.skimLearnedWins(row);
        case "losses":
          return this.skimLearnedLosses(row);
        case "exits":
          return this.skimLearnedExits(row);
        case "company":
          return row.company?.name || "";
        default:
          return row.symbol || "";
      }
    },

    skimSortCompare(a, b, col) {
      const va = this.skimSortValue(a, col);
      const vb = this.skimSortValue(b, col);
      const aNull = va == null || va === "" || (typeof va === "number" && Number.isNaN(va));
      const bNull = vb == null || vb === "" || (typeof vb === "number" && Number.isNaN(vb));
      if (aNull && bNull) return 0;
      if (aNull) return 1;
      if (bNull) return -1;
      if (typeof va === "number" && typeof vb === "number") return va - vb;
      if (typeof va === "boolean" && typeof vb === "boolean") return Number(va) - Number(vb);
      return String(va).localeCompare(String(vb));
    },

    skimLearnedWins(row) {
      const stats = row?.learned?.stats;
      if (stats && stats.wins != null) return Number(stats.wins) || 0;
      if (row?.wins != null) return Number(row.wins) || 0;
      return null;
    },

    skimLearnedLosses(row) {
      const stats = row?.learned?.stats;
      if (stats && stats.losses != null) return Number(stats.losses) || 0;
      if (row?.losses != null) return Number(row.losses) || 0;
      return null;
    },

    skimLearnedExits(row) {
      const stats = row?.learned?.stats;
      if (stats && stats.exits != null) return Number(stats.exits) || 0;
      if (row?.exits != null) return Number(row.exits) || 0;
      return null;
    },

    skimOpenCount() {
      return this.skimSwarmRowsRaw().filter((r) => r.is_open).length;
    },

    skimPriceFmt(v) {
      if (v == null || Number.isNaN(Number(v))) return "—";
      return Number(v).toFixed(2);
    },

    skimPctFmt(v) {
      if (v == null || Number.isNaN(Number(v))) return "—";
      const n = Number(v);
      const sign = n >= 0 ? "+" : "";
      return sign + n.toFixed(2) + "%";
    },

    skimPctClass(v) {
      if (v == null || Number.isNaN(Number(v))) return "text-slate-400";
      return Number(v) >= 0 ? "text-emerald-300" : "text-red-300";
    },

    skimLearnedLabel(row) {
      const stats = row?.learned?.stats;
      if (stats) {
        return `W ${stats.wins ?? 0} / L ${stats.losses ?? 0} · ${stats.exits ?? 0} ex`;
      }
      if (row?.exits != null) {
        return `W ${row.wins ?? 0} / L ${row.losses ?? 0} · ${row.exits} ex`;
      }
      return "—";
    },

    skimBlockReasonPairs() {
      const raw = this.tradingDiagnostics?.skim_swarm?.block_reason_counts || {};
      return Object.entries(raw)
        .sort((a, b) => b[1] - a[1])
        .map(([reason, count]) => ({ reason, count }));
    },

    aiBlockReasonPairs() {
      const raw = this.tradingDiagnostics?.fortress_ai?.block_reason_counts || {};
      return Object.entries(raw)
        .sort((a, b) => b[1] - a[1])
        .map(([reason, count]) => ({ reason, count }));
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

    _buildCapMetricsRows(latest) {
      const m = (latest && latest.metrics) || {};
      const fmt = (v) => (v == null ? "—" : String(v));
      const rows = [];
      const skim = m.skim_swarm || {};
      rows.push({ label: "Skim expectancy (5d)", value: fmt(skim.rolling_expectancy_usd) });
      rows.push({ label: "Skim payoff (5d)", value: fmt(skim.rolling_payoff_ratio) });
      const infra = m.infra_swarm || {};
      rows.push({ label: "Infra expectancy (5d)", value: fmt(infra.rolling_expectancy_usd) });
      const cl = m.classic_fortress || {};
      rows.push({ label: "Classic fills (10d)", value: fmt(cl.rolling_fills) });
      rows.push({ label: "Classic avg candidates", value: fmt(cl.avg_candidates_per_screen) });
      rows.push({ label: "Classic days since fill", value: fmt(cl.days_since_last_fill) });
      rows.push({ label: "Classic regime", value: fmt(cl.latest_regime) });
      return rows;
    },

    async fetchCapabilityReview() {
      this.capLoading = true;
      this.capError = null;
      try {
        const r = await fetch("/api/si/capability-review");
        if (!r.ok) throw new Error(await r.text());
        this.capReview = await r.json();
        this.capMetricsRows = this._buildCapMetricsRows(this.capReview.latest || {});
      } catch (err) {
        this.capError = String(err);
      } finally {
        this.capLoading = false;
      }
    },

    async runCapabilityReview(apply) {
      this.capLoading = true;
      this.capError = null;
      try {
        const r = await fetch("/api/si/capability-review/run", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ apply: !!apply }),
        });
        const j = await r.json();
        if (!r.ok || !j.ok) throw new Error(j.error || r.statusText);
        await this.fetchCapabilityReview();
      } catch (err) {
        this.capError = String(err);
      } finally {
        this.capLoading = false;
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
