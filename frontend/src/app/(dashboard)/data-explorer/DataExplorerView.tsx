"use client";

import React, { useState, useEffect } from "react";
import ReactDOM from "react-dom";
import { BarChart2, Plus, Download, TrendingUp, X, AlertTriangle, Loader2, ExternalLink, FileText, ArrowUpRight, ArrowDownRight, Info, RefreshCw, ChevronRight, ChevronDown, Pencil, Check, Trash2, Undo2, PlusCircle } from "lucide-react";
import type { MarginInsights, MarginNarrative, Factor, FactorStatus, CurrentState, MarginType, MarginEditRequest, EditSection, Direction } from "@/lib/api/insightsClient";
import {
  LineChart, Line, Bar, ComposedChart,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from "recharts";
import {
  dataClient,
  type DataRow,
  type CellSource,
  type SectorHeatmap,
  type SectorHeatmapDefinition,
  type SectorHeatmapMetric,
  type SectorHeatmapRow,
  type SectorHeatmapPoint,
  type CorporateEvent,
} from "@/lib/api/dataClient";
import SemiPricingPanel from "./SemiPricingPanel";
import TaiwanSemiHeatmapPanel from "./TaiwanSemiHeatmapPanel";
import TaiwanDayTradingPanel from "./TaiwanDayTradingPanel";
import TaiwanForeignFlowPanel from "./TaiwanForeignFlowPanel";
import TSMCPanel from "./TSMCPanel";
import UMCPanel from "./UMCPanel";
import MediaTekPanel from "./MediaTekPanel";
import PricesTab from "./PricesTab";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface DataExplorerViewProps {
  loadedTickers: string[];
  activeTicker: string;
  rows: DataRow[];                 // pre-filtered to activeTicker, sorted by end_date
  loading: boolean;
  error: string | null;
  apiWarnings: string[];
  onTickerChange: (symbol: string) => void;
  onAddTicker: (symbol: string) => void;
  onRemoveTicker: (symbol: string) => void;

  // Phase B — qualitative margin narrative (optional; panel falls back to
  // Phase A numeric-only display when not yet loaded)
  marginInsights?: MarginInsights | null;
  marginInsightsLoading?: boolean;
  marginInsightsError?: string | null;
  onRefreshMarginInsights?: () => void;
  onEditMarginInsights?: (edit: MarginEditRequest) => void | Promise<void>;

  // Sector heatmap (bottom full-width section)
  heatmapDefinitions?: SectorHeatmapDefinition[];
  heatmapGroupDef?:    string;
  heatmapMetric?:      SectorHeatmapMetric;
  heatmap?:            SectorHeatmap | null;
  heatmapLoading?:     boolean;
  onHeatmapGroupDefChange?: (key: string) => void;
  onHeatmapMetricChange?:   (metric: SectorHeatmapMetric) => void;
}

// ---------------------------------------------------------------------------
// Table row definition
// ---------------------------------------------------------------------------

type CellFmt = "M" | "%" | "$" | "pp";  // pp = percentage-point delta (e.g. "+2.5 pp")

interface RowDef {
  label: string;
  metric: string;            // unique key, also the data column name
  fmt: CellFmt;
  // Visual tier:
  //  - bold:    spine subtotal (Revenue, Gross Profit, Op Income, Pretax, Net Income)
  //  - derived: ratio / margin / YoY / QoQ — small italic, deeper indent
  //  - indent:  visual nesting under a subtotal (1 = sub-component, 2 = sub-sub)
  bold?:    boolean;
  derived?: boolean;
  indent?:  1 | 2;
  // For frontend-computed cells (Other OpEx, Tax Rate, Income from Cont Ops).
  // When defined, the renderer calls compute(row) instead of looking up the
  // metric column. Use the metric field as a unique key only.
  compute?: (row: DataRow) => number | null;
}

interface RowGroup {
  heading: string;
  rows: RowDef[];
}

// Complete income-statement walk-down. Spine items are bold:
//   Net Revenue → Gross Profit → Operating Income → Pretax Income → Net Income
// Sub-components contribute to the subtotal directly above them and are
// indented one level. Derived ratios / growth metrics are small italic.
const ROW_GROUPS: RowGroup[] = [
  // ── REVENUE ──
  {
    heading: "Revenue",
    rows: [
      { label: "Net Revenue",       metric: "revenue",                fmt: "M", bold: true },
      { label: "YoY %",             metric: "revenue_yoy_pct",        fmt: "%", derived: true },
      { label: "QoQ %",             metric: "revenue_qoq_pct",        fmt: "%", derived: true },
    ],
  },
  // ── GROSS PROFIT ──
  {
    heading: "Gross Profit",
    rows: [
      { label: "Cost of Revenue",   metric: "cost_of_revenue",        fmt: "M", indent: 1 },
      { label: "Gross Profit",      metric: "gross_profit",           fmt: "M", bold: true },
      { label: "YoY %",             metric: "gross_profit_yoy_pct",   fmt: "%", derived: true },
      { label: "Gross Margin %",    metric: "gross_margin_pct",       fmt: "%", derived: true },
      { label: "GM% Δ YoY",         metric: "gross_margin_pct_diff_yoy", fmt: "pp", derived: true },
    ],
  },
  // ── OPERATING EXPENSES (decomposes into R&D + SG&A + Other) ──
  {
    heading: "Operating Expenses",
    rows: [
      { label: "R&D",               metric: "rd_expense",             fmt: "M", indent: 1 },
      { label: "SG&A",              metric: "sga_expense",            fmt: "M", indent: 1 },
      { label: "Other Operating Expense", metric: "_other_opex",      fmt: "M", indent: 1,
        compute: (r) => {
          const opex = getNum(r, "opex");
          if (opex === null) return null;
          const rd  = getNum(r, "rd_expense")  ?? 0;
          const sga = getNum(r, "sga_expense") ?? 0;
          return opex - rd - sga;
        } },
      { label: "Total OpEx",        metric: "opex",                   fmt: "M", bold: true },
      { label: "OpEx % Revenue",    metric: "_opex_pct_rev",          fmt: "%", derived: true,
        compute: (r) => _ratioPct(r, "opex", "revenue") },
    ],
  },
  // ── OPERATING INCOME (= Gross Profit − Total OpEx) ──
  {
    heading: "Operating Income",
    rows: [
      { label: "Operating Income",  metric: "operating_income",       fmt: "M", bold: true },
      { label: "YoY %",             metric: "operating_income_yoy_pct", fmt: "%", derived: true },
      { label: "QoQ %",             metric: "operating_income_qoq_pct", fmt: "%", derived: true },
      { label: "Op Margin %",       metric: "operating_margin_pct",   fmt: "%", derived: true },
      { label: "OPM% Δ YoY",        metric: "operating_margin_pct_diff_yoy", fmt: "pp", derived: true },
    ],
  },
  // ── PRETAX INCOME (= Op Income − Interest Exp + Interest Inc + Other Non-Op) ──
  {
    heading: "Pretax Income",
    rows: [
      { label: "Interest Expense",          metric: "interest_expense",  fmt: "M", indent: 1 },
      { label: "Interest Income",           metric: "interest_income",   fmt: "M", indent: 1 },
      { label: "Other Non-Op Income, net",  metric: "other_income_net",  fmt: "M", indent: 1 },
      { label: "Pretax Income",             metric: "pretax_income",     fmt: "M", bold: true },
    ],
  },
  // ── NET INCOME (= Pretax − Tax − [extraordinary / minority] ) ──
  {
    heading: "Net Income",
    rows: [
      { label: "Income Tax Expense",     metric: "income_tax",          fmt: "M", indent: 1 },
      { label: "Effective Tax Rate",     metric: "_effective_tax_rate", fmt: "%", derived: true,
        compute: (r) => {
          const tax    = getNum(r, "income_tax");
          const pretax = getNum(r, "pretax_income");
          if (tax === null || pretax === null || pretax === 0) return null;
          return Number(((tax / pretax) * 100).toFixed(2));
        } },
      { label: "Income from Cont Ops",   metric: "_income_cont_ops",    fmt: "M", indent: 1,
        compute: (r) => {
          const pretax = getNum(r, "pretax_income");
          const tax    = getNum(r, "income_tax");
          if (pretax === null || tax === null) return null;
          return pretax - tax;
        } },
      { label: "Net Income (GAAP)",      metric: "net_income",          fmt: "M", bold: true },
      { label: "YoY %",                  metric: "net_income_yoy_pct",  fmt: "%", derived: true },
      { label: "QoQ %",                  metric: "net_income_qoq_pct",  fmt: "%", derived: true },
      { label: "Net Margin %",           metric: "net_margin_pct",      fmt: "%", derived: true },
      { label: "NPM% Δ YoY",             metric: "net_margin_pct_diff_yoy", fmt: "pp", derived: true },
    ],
  },
  // ── PER SHARE ──
  {
    heading: "Per Share",
    rows: [
      { label: "EPS Basic",              metric: "eps_basic",           fmt: "$" },
      { label: "EPS Diluted",            metric: "eps_diluted",         fmt: "$" },
      { label: "EPS Diluted YoY %",      metric: "eps_diluted_yoy_pct", fmt: "%", derived: true },
      { label: "Shares Basic (M)",       metric: "shares_basic",        fmt: "M", indent: 1 },
      { label: "Shares Diluted (M)",     metric: "shares_diluted",      fmt: "M", indent: 1 },
    ],
  },
];

// ---------------------------------------------------------------------------
// Quality alerts — sanity checks computed from loaded rows
// Mirrors the backend `_validate` checks so the user sees alerts immediately
// when viewing a ticker, without waiting for a build report. Severity:
//   - hard:     definitely broken data
//   - soft:     suspicious but possibly legitimate
//   - coverage: missing values that should be present
// ---------------------------------------------------------------------------

interface QualityAlert {
  severity: "hard" | "soft" | "coverage";
  category: string;
  period:   string;
  message:  string;
}

function validateRows(ticker: string, rows: DataRow[]): QualityAlert[] {
  if (rows.length === 0) return [];

  // We work on standalone-quarter rows sorted oldest first.
  const sorted = [...rows].sort((a, b) =>
    String(a.end_date).localeCompare(String(b.end_date))
  );

  const alerts: QualityAlert[] = [];
  const num = (r: DataRow, k: string): number | null => {
    const v = r[k];
    return typeof v === "number" ? v : null;
  };
  const period = (r: DataRow): string =>
    String(r.period_label ?? String(r.end_date).slice(0, 10));
  const push = (severity: QualityAlert["severity"], category: string, r: DataRow, message: string) =>
    alerts.push({ severity, category, period: period(r), message });

  // ── HARD #1: revenue must be > 0 ────────────────────────────────
  for (const r of sorted) {
    const v = num(r, "revenue");
    if (v !== null && v <= 0) {
      push("hard", "sign", r, `revenue = ${v.toLocaleString()} (must be > 0)`);
    }
  }

  // ── HARD #2/#3: cost_of_revenue and opex must be > 0 ───────────
  for (const r of sorted) {
    for (const k of ["cost_of_revenue", "opex"]) {
      const v = num(r, k);
      if (v !== null && v <= 0) {
        push("hard", "sign", r, `${k} = ${v.toLocaleString()} (must be > 0)`);
      }
    }
  }

  // ── HARD #5: rd_expense, sga_expense must be ≥ 0 ───────────────
  for (const r of sorted) {
    for (const k of ["rd_expense", "sga_expense"]) {
      const v = num(r, k);
      if (v !== null && v < 0) {
        push("hard", "sign", r, `${k} = ${v.toLocaleString()} (must be ≥ 0)`);
      }
    }
  }

  // ── HARD #6: shares_basic / shares_diluted must be > 0 ─────────
  for (const r of sorted) {
    for (const k of ["shares_basic", "shares_diluted"]) {
      const v = num(r, k);
      if (v !== null && v <= 0) {
        push("hard", "sign", r, `${k} = ${v} (must be > 0)`);
      }
    }
  }

  // ── HARD #7: revenue − cost_of_revenue ≈ gross_profit (1% of revenue) ──
  for (const r of sorted) {
    const rev = num(r, "revenue");
    const cor = num(r, "cost_of_revenue");
    const gp  = num(r, "gross_profit");
    if (rev === null || cor === null || gp === null) continue;
    const expected = rev - cor;
    const tol = Math.abs(rev) * 0.01;
    if (Math.abs(expected - gp) > tol) {
      push("hard", "identity", r,
        `revenue (${rev.toFixed(0)}) − cost_of_revenue (${cor.toFixed(0)}) = ${expected.toFixed(0)}; gross_profit reported = ${gp.toFixed(0)} (Δ ${Math.abs(expected-gp).toFixed(0)}, > 1% of revenue)`,
      );
    }
  }

  // ── HARD #8: gross_profit − opex ≈ operating_income (1% of revenue) ──
  for (const r of sorted) {
    const rev = num(r, "revenue");
    const gp  = num(r, "gross_profit");
    const op  = num(r, "opex");
    const oi  = num(r, "operating_income");
    if (rev === null || gp === null || op === null || oi === null) continue;
    const expected = gp - op;
    const tol = Math.abs(rev) * 0.01;
    if (Math.abs(expected - oi) > tol) {
      push("hard", "identity", r,
        `gross_profit (${gp.toFixed(0)}) − opex (${op.toFixed(0)}) = ${expected.toFixed(0)}; operating_income reported = ${oi.toFixed(0)} (Δ ${Math.abs(expected-oi).toFixed(0)}, > 1% of revenue)`,
      );
    }
  }

  // ── HARD #9: no duplicate (fiscal_year, fiscal_quarter) per ticker ──
  const seen = new Map<string, number>();
  for (const r of sorted) {
    const key = `${r.fiscal_year ?? "?"}-${r.fiscal_quarter ?? "?"}`;
    seen.set(key, (seen.get(key) ?? 0) + 1);
  }
  for (const [key, n] of seen) {
    if (n > 1 && key !== "?-?") {
      alerts.push({ severity: "hard", category: "duplicate", period: key, message: `${key} appears ${n} times (must be 1)` });
    }
  }

  // ── SOFT #4: gross_profit < 0 ──────────────────────────────────
  for (const r of sorted) {
    const v = num(r, "gross_profit");
    if (v !== null && v < 0) {
      push("soft", "sign", r, `gross_profit = ${v.toFixed(0)} (negative — verify)`);
    }
  }

  // ── SOFT #10: op_inc − int_exp + int_inc + other ≈ pretax (5%) ──
  for (const r of sorted) {
    const rev    = num(r, "revenue");
    const oi     = num(r, "operating_income");
    const intExp = num(r, "interest_expense");
    const intInc = num(r, "interest_income");
    const other  = num(r, "other_income_net");
    const pretax = num(r, "pretax_income");
    if (rev === null || oi === null || intExp === null || intInc === null || other === null || pretax === null) continue;
    const expected = oi - intExp + intInc + other;
    const tol = Math.abs(rev) * 0.05;
    if (Math.abs(expected - pretax) > tol) {
      push("soft", "identity", r,
        `op_inc − int_exp + int_inc + other = ${expected.toFixed(0)}; pretax_income reported = ${pretax.toFixed(0)} (Δ ${Math.abs(expected-pretax).toFixed(0)}, > 5% of revenue)`,
      );
    }
  }

  // ── SOFT #11: pretax − tax ≈ net_income (5%) ───────────────────
  for (const r of sorted) {
    const rev    = num(r, "revenue");
    const pretax = num(r, "pretax_income");
    const tax    = num(r, "income_tax");
    const ni     = num(r, "net_income");
    if (rev === null || pretax === null || tax === null || ni === null) continue;
    const expected = pretax - tax;
    const tol = Math.abs(rev) * 0.05;
    if (Math.abs(expected - ni) > tol) {
      push("soft", "identity", r,
        `pretax (${pretax.toFixed(0)}) − tax (${tax.toFixed(0)}) = ${expected.toFixed(0)}; net_income reported = ${ni.toFixed(0)} (Δ ${Math.abs(expected-ni).toFixed(0)}, > 5% of revenue)`,
      );
    }
  }

  // ── SOFT #12: gross_margin_pct outside [0%, 95%] ───────────────
  for (const r of sorted) {
    const v = num(r, "gross_margin_pct");
    if (v !== null && (v < 0 || v > 95)) {
      push("soft", "range", r, `gross_margin_pct = ${v.toFixed(1)}% (outside [0%, 95%])`);
    }
  }

  // ── SOFT #13: |net_margin_pct| > 100% ──────────────────────────
  for (const r of sorted) {
    const v = num(r, "net_margin_pct");
    if (v !== null && Math.abs(v) > 100) {
      push("soft", "range", r, `net_margin_pct = ${v.toFixed(1)}% (|value| > 100%)`);
    }
  }

  // ── SOFT #14: effective tax rate outside [-50%, 50%] ───────────
  for (const r of sorted) {
    const tax    = num(r, "income_tax");
    const pretax = num(r, "pretax_income");
    if (tax === null || pretax === null || pretax === 0) continue;
    const etr = (tax / pretax) * 100;
    if (etr < -50 || etr > 50) {
      push("soft", "range", r, `effective tax rate = ${etr.toFixed(1)}% (outside [-50%, 50%])`);
    }
  }

  // ── SOFT #15: QoQ revenue jump > +200% or < -50% ───────────────
  for (const r of sorted) {
    const v = num(r, "revenue_qoq_pct");
    if (v !== null && (v > 200 || v < -50)) {
      push("soft", "cliff", r, `revenue QoQ = ${v.toFixed(1)}% (cliff — possible YTD-not-converted bug or M&A)`);
    }
  }

  // ── COVERAGE: core metrics must be populated for every quarter ──
  const CORE = ["revenue", "net_income", "gross_profit", "operating_income", "eps_basic", "eps_diluted"];
  for (const r of sorted) {
    for (const k of CORE) {
      if (num(r, k) === null) {
        alerts.push({ severity: "coverage", category: "missing", period: period(r), message: `${k} is missing` });
      }
    }
  }

  // ── COVERAGE: YoY/QoQ should be present beyond the oldest 4 / 1 rows ──
  if (sorted.length > 4) {
    for (let i = 4; i < sorted.length; i++) {
      const r = sorted[i];
      if (num(r, "revenue_yoy_pct") === null && num(r, "revenue") !== null) {
        alerts.push({ severity: "coverage", category: "missing", period: period(r), message: `revenue_yoy_pct is missing` });
      }
    }
  }
  if (sorted.length > 1) {
    for (let i = 1; i < sorted.length; i++) {
      const r = sorted[i];
      if (num(r, "revenue_qoq_pct") === null && num(r, "revenue") !== null) {
        alerts.push({ severity: "coverage", category: "missing", period: period(r), message: `revenue_qoq_pct is missing` });
      }
    }
  }

  return alerts;
}

// ---------------------------------------------------------------------------
// Helpers: formatting and heat-map colouring
// ---------------------------------------------------------------------------

function getNum(row: DataRow, metric: string): number | null {
  const v = row[metric];
  return typeof v === "number" ? v : null;
}

/**
 * Cell color rules (matches the Data Explorer design spec):
 *   - Percent cells: green if positive, red if negative, gray if null/zero.
 *   - Value cells ($/M): black; red only when the value itself is negative (losses).
 *   - Null values: light gray em-dash.
 */
function cellStyle(value: number | null, format: CellFmt): React.CSSProperties {
  const italicFmts = format === "%" || format === "pp";
  if (value === null) return { color: "#cbd5e1", fontStyle: italicFmts ? "italic" : undefined };
  if (italicFmts) {
    const base: React.CSSProperties = { fontStyle: "italic" };
    if (value > 0) return { ...base, color: "#059669" };  // emerald-600
    if (value < 0) return { ...base, color: "#dc2626" };  // red-600
    return { ...base, color: "#64748b" };                  // slate-500
  }
  // $ or M values — black, with red for negatives (losses / outflows)
  return value < 0 ? { color: "#dc2626" } : { color: "#0f172a" };
}

function fmtCell(value: number | null, format: CellFmt): string {
  if (value === null) return "—";
  // Accounting convention: negatives as (1,234), not -1,234.
  const neg = value < 0;
  const abs = Math.abs(value);
  if (format === "%") {
    // Percent cells keep the minus sign — italics + red already signal negative.
    return `${value.toFixed(1)}%`;
  }
  if (format === "pp") {
    // Percentage-point delta: explicit +/- sign to emphasize direction of change.
    const sign = value > 0 ? "+" : value < 0 ? "−" : "";
    return `${sign}${abs.toFixed(1)} pp`;
  }
  if (format === "$") {
    const body = `$${abs.toFixed(2)}`;
    return neg ? `(${body})` : body;
  }
  // Millions: whole numbers with thousands separators, parens for negatives.
  const body = Math.round(abs).toLocaleString("en-US");
  return neg ? `(${body})` : body;
}

function fmtAxis(v: number): string {
  if (Math.abs(v) >= 1000) return `${(v / 1000).toFixed(0)}k`;
  return `${v}`;
}

/** Ratio as a percent (|numerator| / denominator * 100). Returns null when
 * either value is missing or the denominator is zero/negative. */
function _ratioPct(row: DataRow, numKey: string, denKey: string): number | null {
  const num = getNum(row, numKey);
  const den = getNum(row, denKey);
  if (num === null || den === null || den <= 0) return null;
  return Number(((Math.abs(num) / den) * 100).toFixed(2));
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ChartCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-white border border-slate-200 rounded-xl shadow-sm overflow-hidden">
      <div className="flex items-center justify-between px-5 py-3 border-b border-slate-100">
        <div className="flex items-center gap-2">
          <TrendingUp size={14} className="text-slate-400" />
          <span className="text-xs font-semibold text-slate-700">{title}</span>
        </div>
        <button className="p-1 text-slate-300 hover:text-slate-500 transition-colors">
          <Download size={13} />
        </button>
      </div>
      <div className="p-4">{children}</div>
    </div>
  );
}

function LoadingOverlay() {
  return (
    <div className="flex flex-col items-center justify-center h-64 gap-3 text-slate-400">
      <Loader2 size={28} className="animate-spin text-indigo-500" />
      <span className="text-sm">Loading financial data…</span>
    </div>
  );
}

function EmptyState({ ticker }: { ticker: string }) {
  return (
    <div className="flex flex-col items-center justify-center h-64 gap-2 text-slate-400">
      <BarChart2 size={28} className="text-slate-300" />
      <p className="text-sm font-medium text-slate-500">No data for {ticker}</p>
      <p className="text-xs">
        Data may still be building.{" "}
        <span className="text-indigo-500">Refresh in a moment.</span>
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Add-ticker inline prompt row
// ---------------------------------------------------------------------------

function AddTickerButton({ onAdd }: { onAdd: (symbol: string) => void }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue]     = useState("");

  const commit = () => {
    const sym = value.trim().toUpperCase();
    if (sym) onAdd(sym);
    setValue("");
    setEditing(false);
  };

  if (editing) {
    return (
      <div className="flex items-center gap-1">
        <input
          autoFocus
          value={value}
          onChange={(e) => setValue(e.target.value.toUpperCase())}
          onKeyDown={(e) => {
            if (e.key === "Enter") commit();
            if (e.key === "Escape") { setValue(""); setEditing(false); }
          }}
          placeholder="TICKER"
          className="w-20 h-7 px-2 text-xs font-mono border border-indigo-400 rounded-md outline-none focus:ring-1 focus:ring-indigo-500"
        />
        <button
          onClick={commit}
          className="h-7 px-2 text-xs font-semibold bg-indigo-600 text-white rounded-md hover:bg-indigo-700"
        >
          Add
        </button>
        <button
          onClick={() => { setValue(""); setEditing(false); }}
          className="h-7 px-1.5 text-xs text-slate-400 hover:text-slate-600"
        >
          <X size={12} />
        </button>
      </div>
    );
  }

  return (
    <button
      onClick={() => setEditing(true)}
      className="flex items-center gap-1 h-7 px-3 rounded-md text-xs text-slate-400 hover:text-slate-600 border border-dashed border-slate-200 hover:border-slate-300 transition-colors"
    >
      <Plus size={11} /> Add ticker
    </button>
  );
}

// ---------------------------------------------------------------------------
// Cell drill-down modal
// ---------------------------------------------------------------------------

interface CellRequest {
  ticker: string;
  metric: string;
  end_date: string;
}

function CellSourceModal({
  request,
  onClose,
}: {
  request: CellRequest | null;
  onClose: () => void;
}) {
  const [source, setSource] = useState<CellSource | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!request) return;
    setLoading(true);
    setError(null);
    setSource(null);
    dataClient
      .getCellSource(request.ticker, request.metric, request.end_date)
      .then((res) => setSource(res))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, [request]);

  // Close on Escape
  useEffect(() => {
    if (!request) return;
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [request, onClose]);

  if (!request) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="bg-white rounded-xl shadow-2xl border border-slate-200 w-full max-w-xl max-h-[85vh] overflow-hidden flex flex-col"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200 bg-slate-50">
          <div className="flex items-center gap-2">
            <FileText size={16} className="text-indigo-500" />
            <h2 className="text-sm font-bold text-slate-800">
              Source · {request.ticker} · {request.metric}
            </h2>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-slate-200 text-slate-500 hover:text-slate-700"
          >
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-6 text-xs text-slate-700">
          {loading && (
            <div className="flex items-center gap-2 text-slate-400">
              <Loader2 size={14} className="animate-spin" /> Loading source…
            </div>
          )}
          {error && (
            <div className="flex items-start gap-2 p-3 bg-red-50 border border-red-200 rounded text-red-700">
              <AlertTriangle size={14} className="mt-0.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}
          {source && <SourceDetails source={source} />}
        </div>
      </div>
    </div>
  );
}

function SourceDetails({ source }: { source: CellSource }) {
  const rowBase = "grid grid-cols-[120px_1fr] gap-3 py-1.5 border-b border-slate-100";
  const label   = "text-[10px] font-semibold text-slate-500 uppercase tracking-wider pt-0.5";
  const value   = "text-[12px] text-slate-800 font-mono break-all";

  const fmtValue = (v: number | null, unit: string) => {
    if (v === null) return "—";
    if (unit === "%") return `${v.toFixed(2)}%`;
    if (unit === "$") return `$${v.toFixed(2)}`;
    return `${Math.round(v).toLocaleString("en-US")} M`;
  };

  return (
    <div className="space-y-4">
      {/* Summary */}
      <div className="bg-indigo-50/50 border border-indigo-100 rounded-lg px-4 py-3">
        <div className="text-[10px] font-semibold uppercase tracking-wider text-indigo-500 mb-1">
          {source.metric_label}
        </div>
        <div className="text-2xl font-bold text-slate-900 tabular-nums">
          {fmtValue(source.value, source.unit)}
        </div>
        <div className="text-[11px] text-slate-500 mt-1">
          {source.fiscal_period || source.period_end}
          {source.is_ytd && <span className="ml-2 px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 text-[9px] font-semibold">YTD</span>}
        </div>
      </div>

      {/* Period */}
      <div>
        <div className={rowBase}>
          <span className={label}>Period End</span>
          <span className={value}>{source.period_end ?? "—"}</span>
        </div>
        <div className={rowBase}>
          <span className={label}>Period Start</span>
          <span className={value}>{source.period_start ?? "—"}</span>
        </div>
        <div className={rowBase}>
          <span className={label}>Fiscal Year</span>
          <span className={value}>{source.fiscal_year ?? "—"}</span>
        </div>
        <div className={rowBase}>
          <span className={label}>Fiscal Quarter</span>
          <span className={value}>{source.fiscal_quarter ?? "—"}</span>
        </div>
      </div>

      {/* Derivation (for computed metrics) */}
      {source.derivation && (
        <div>
          <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500 mb-1">
            Derivation
          </div>
          <div className="p-3 bg-slate-50 border border-slate-200 rounded font-mono text-[11px] text-slate-700">
            {source.derivation.formula_description}
          </div>
          {Object.keys(source.derivation.inputs).length > 0 && (
            <div className="mt-2 p-3 bg-slate-50 border border-slate-200 rounded">
              <div className="text-[10px] font-semibold text-slate-500 mb-1">Inputs</div>
              {Object.entries(source.derivation.inputs).map(([k, v]) => (
                <div key={k} className="flex justify-between text-[11px] font-mono">
                  <span className="text-slate-500">{k}</span>
                  <span className="text-slate-800">{v === null ? "—" : String(v)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* XBRL concepts (for base metrics) */}
      {source.xbrl_concepts.length > 0 && (
        <div>
          <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500 mb-1">
            XBRL Concepts (priority order)
          </div>
          <div className="p-3 bg-slate-50 border border-slate-200 rounded space-y-1">
            {source.xbrl_concepts.map((c) => (
              <div key={c} className="font-mono text-[11px] text-slate-700 break-all">
                {c}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Source file */}
      <div>
        <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500 mb-1">
          Source Layer
        </div>
        <div className="flex items-center gap-2 text-[11px] font-mono text-slate-600">
          <span className="px-2 py-0.5 rounded bg-emerald-50 text-emerald-700 font-semibold uppercase text-[9px] tracking-wider">
            {source.source_layer}
          </span>
          <span className="break-all">{source.source_file}</span>
        </div>
      </div>

      {/* Filing link */}
      {source.filing ? (
        <div>
          <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500 mb-1">
            SEC Filing
          </div>
          <div className="p-3 bg-slate-50 border border-slate-200 rounded">
            <div className="flex justify-between items-center mb-1">
              <span className="text-[11px] font-semibold text-slate-700">{source.filing.form}</span>
              <a
                href={source.filing.edgar_url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-1 text-[11px] text-indigo-600 hover:text-indigo-800 font-medium"
              >
                Open on EDGAR <ExternalLink size={11} />
              </a>
            </div>
            <div className="text-[10px] font-mono text-slate-500">
              Accession: {source.filing.accession}
            </div>
          </div>
        </div>
      ) : (
        <div className="text-[11px] text-slate-400 italic">
          Filing state not available — run topline refresh to populate accession numbers.
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Margin insights (data-driven — Phase A, no LLM)
// ---------------------------------------------------------------------------

interface MarginStat {
  label:    string;
  color:    string;
  current:  number | null;
  currentPeriod: string | null;
  min:      number | null;
  max:      number | null;
  trough:   { value: number; period: string } | null;
  peak:     { value: number; period: string } | null;
}

function computeMarginStats(rows: DataRow[]): MarginStat[] {
  const specs = [
    { key: "gross_margin_pct",     label: "Gross Margin",     color: "#10b981" },
    { key: "operating_margin_pct", label: "Operating Margin", color: "#f59e0b" },
    { key: "net_margin_pct",       label: "Net Margin",       color: "#8b5cf6" },
  ];

  return specs.map(({ key, label, color }) => {
    let min: number | null = null;
    let max: number | null = null;
    let trough: { value: number; period: string } | null = null;
    let peak:   { value: number; period: string } | null = null;
    let current: number | null = null;
    let currentPeriod: string | null = null;

    rows.forEach((r) => {
      const v = getNum(r, key);
      if (v === null) return;
      const period = r.period_label ?? (r.end_date ?? "").slice(0, 10);
      if (min === null || v < min) { min = v; trough = { value: v, period }; }
      if (max === null || v > max) { max = v; peak   = { value: v, period }; }
    });

    // Most recent non-null reading
    for (let i = rows.length - 1; i >= 0; i--) {
      const v = getNum(rows[i], key);
      if (v !== null) {
        current = v;
        currentPeriod = rows[i].period_label ?? (rows[i].end_date ?? "").slice(0, 10);
        break;
      }
    }

    return { label, color, current, currentPeriod, min, max, trough, peak };
  });
}

interface MarginInsightsPanelProps {
  rows: DataRow[];
  ticker: string;
  insights?: MarginInsights | null;
  insightsLoading?: boolean;
  insightsError?: string | null;
  onRefreshInsights?: () => void;
  onEditInsights?: (edit: MarginEditRequest) => void | Promise<void>;
}

function MarginInsightsPanel({
  rows,
  ticker,
  insights,
  insightsLoading,
  insightsError,
  onRefreshInsights,
  onEditInsights,
}: MarginInsightsPanelProps) {
  const stats = computeMarginStats(rows);
  const [sourcesOpen, setSourcesOpen] = useState(false);

  const fmtPct = (v: number | null) => (v === null ? "—" : `${v.toFixed(1)}%`);

  const narrativeByType: Record<MarginType, MarginNarrative | undefined> = {
    gross:     insights?.margins.find((m) => m.margin_type === "gross"),
    operating: insights?.margins.find((m) => m.margin_type === "operating"),
    net:       insights?.margins.find((m) => m.margin_type === "net"),
  };

  // Dedup: collect factor labels already shown for prior margins so
  // operating and net can render "same as above" instead of repeating details.
  const grossLabels = new Set<string>(
    (narrativeByType.gross?.peak.factors ?? [])
      .concat(narrativeByType.gross?.trough.factors ?? [])
      .map((f) => f.label.toLowerCase().trim()),
  );
  const grossOpLabels = new Set<string>([
    ...grossLabels,
    ...(narrativeByType.operating?.peak.factors ?? [])
      .concat(narrativeByType.operating?.trough.factors ?? [])
      .map((f) => f.label.toLowerCase().trim()),
  ]);
  const seenByType: Record<MarginType, Set<string>> = {
    gross:     new Set(),     // first -- nothing to dedup against
    operating: grossLabels,
    net:       grossOpLabels,
  };

  // Map stat-panel label -> margin_type so we can pair them up below.
  const typeByLabel: Record<string, MarginType> = {
    "Gross Margin":     "gross",
    "Operating Margin": "operating",
    "Net Margin":       "net",
  };

  return (
    <div className="bg-white border border-slate-200 rounded-xl shadow-sm overflow-hidden flex flex-col">
      <div className="flex items-center justify-between px-5 py-3 border-b border-slate-100">
        <div className="flex items-center gap-2">
          <TrendingUp size={14} className="text-slate-400" />
          <span className="text-xs font-semibold text-slate-700">
            Margin Insights <span className="text-slate-400 font-normal">· {ticker}</span>
          </span>
        </div>
        <div className="flex items-center gap-2">
          {insightsLoading && (
            <span className="flex items-center gap-1 text-[9px] font-mono uppercase tracking-wider text-indigo-500">
              <Loader2 size={10} className="animate-spin" /> Generating
            </span>
          )}
          {!insightsLoading && insights && (
            <span className="text-[9px] font-mono uppercase tracking-wider text-emerald-600">
              AI narrative
            </span>
          )}
          {!insightsLoading && !insights && (
            <span className="text-[9px] font-mono uppercase tracking-wider text-slate-400">
              Data-driven
            </span>
          )}
          {onEditInsights && insights && (
            <button
              onClick={() =>
                onEditInsights({
                  action: "undo",
                  margin_type: "gross",
                  section: "peak",
                  factor_key: "",
                })
              }
              title="Undo last edit"
              className="p-1 text-slate-300 hover:text-slate-600 transition-colors"
            >
              <Undo2 size={12} />
            </button>
          )}
          {onRefreshInsights && (
            <button
              onClick={onRefreshInsights}
              disabled={insightsLoading}
              title="Regenerate narrative"
              className="p-1 text-slate-300 hover:text-slate-600 disabled:opacity-30 transition-colors"
            >
              <RefreshCw size={12} className={insightsLoading ? "animate-spin" : ""} />
            </button>
          )}
        </div>
      </div>

      {insightsError && (
        <div className="flex items-start gap-1.5 px-4 py-2 bg-amber-50 border-b border-amber-200 text-[10px] text-amber-700">
          <AlertTriangle size={11} className="mt-0.5 shrink-0" />
          <span>Narrative unavailable: {insightsError}. Numeric stats below still accurate.</span>
        </div>
      )}

      <div className="p-4 space-y-4 overflow-y-auto" style={{ maxHeight: 520 }}>
        {stats.map((s) => {
          const deltaFromPeak   = s.current !== null && s.peak   ? s.current - s.peak.value   : null;
          const deltaFromTrough = s.current !== null && s.trough ? s.current - s.trough.value : null;
          const mtype = typeByLabel[s.label];
          const narrative = mtype ? narrativeByType[mtype] : undefined;
          return (
            <div key={s.label} className="border-b border-slate-100 last:border-0 pb-3 last:pb-0">
              {/* Header row: label + current value */}
              <div className="flex items-center justify-between mb-1.5">
                <div className="flex items-center gap-2">
                  <span
                    className="inline-block w-2 h-2 rounded-full"
                    style={{ backgroundColor: s.color }}
                  />
                  <span className="text-[11px] font-semibold text-slate-700">{s.label}</span>
                </div>
                <div className="flex items-baseline gap-1.5">
                  <span className="text-sm font-bold tabular-nums text-slate-900">
                    {fmtPct(s.current)}
                  </span>
                  <span className="text-[9px] font-mono text-slate-400">
                    {s.currentPeriod ?? ""}
                  </span>
                </div>
              </div>

              {/* Range bar: min ──●── max, with current position marker */}
              <MarginRangeBar stat={s} />

              {/* Stats grid: range, peak, trough, Δ */}
              <div className="grid grid-cols-4 gap-2 mt-2 text-[10px]">
                <div>
                  <div className="text-slate-400 uppercase tracking-wider">Range</div>
                  <div className="font-mono text-slate-700 tabular-nums">
                    {fmtPct(s.min)} – {fmtPct(s.max)}
                  </div>
                </div>
                <div>
                  <div className="text-slate-400 uppercase tracking-wider flex items-center gap-0.5">
                    <ArrowUpRight size={9} className="text-emerald-500" /> Peak
                  </div>
                  <div className="font-mono text-slate-700 tabular-nums">
                    {s.peak ? `${fmtPct(s.peak.value)}` : "—"}
                  </div>
                  <div className="text-[9px] text-slate-400 font-mono">
                    {s.peak?.period ?? ""}
                  </div>
                </div>
                <div>
                  <div className="text-slate-400 uppercase tracking-wider flex items-center gap-0.5">
                    <ArrowDownRight size={9} className="text-rose-500" /> Trough
                  </div>
                  <div className="font-mono text-slate-700 tabular-nums">
                    {s.trough ? `${fmtPct(s.trough.value)}` : "—"}
                  </div>
                  <div className="text-[9px] text-slate-400 font-mono">
                    {s.trough?.period ?? ""}
                  </div>
                </div>
                <div>
                  <div className="text-slate-400 uppercase tracking-wider">vs Peak / Trough</div>
                  <div className="font-mono tabular-nums">
                    <span className={deltaFromPeak !== null && deltaFromPeak < 0 ? "text-rose-600" : "text-slate-700"}>
                      {deltaFromPeak === null ? "—" : `${deltaFromPeak >= 0 ? "+" : ""}${deltaFromPeak.toFixed(1)}`}
                    </span>
                    {" / "}
                    <span className={deltaFromTrough !== null && deltaFromTrough > 0 ? "text-emerald-600" : "text-slate-700"}>
                      {deltaFromTrough === null ? "—" : `${deltaFromTrough >= 0 ? "+" : ""}${deltaFromTrough.toFixed(1)}`}
                    </span>
                    <span className="text-slate-400"> pp</span>
                  </div>
                </div>
              </div>

              {/* Narrative block (peak/trough factors + current situation) —
                  only shown for net margin. Gross and operating margins move
                  in lockstep with net, so we skip the repetition and let net
                  margin carry the qualitative story for all three. */}
              {mtype === "net" && narrative && (
                <MarginNarrativeBlock
                  narrative={narrative}
                  color={s.color}
                  marginType={mtype}
                  seenLabels={seenByType[mtype]}
                  onEdit={onEditInsights}
                />
              )}
              {mtype === "net" && !narrative && insightsLoading && (
                <NarrativeSkeleton />
              )}
            </div>
          );
        })}

        {/* Sources list (collapsible) — only when we have insights */}
        {insights && insights.sources.length > 0 && (
          <div className="pt-2 border-t border-slate-100">
            <button
              onClick={() => setSourcesOpen((v) => !v)}
              className="flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wider text-slate-500 hover:text-slate-700"
            >
              {sourcesOpen ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
              Sources ({insights.sources.length})
            </button>
            {sourcesOpen && (
              <ul className="mt-1.5 space-y-1">
                {insights.sources.map((src) => (
                  <li key={src.index} className="text-[10px] flex items-baseline gap-1.5">
                    <span className="font-mono text-slate-400">[{src.index}]</span>
                    <span className="px-1 py-px rounded bg-slate-100 text-slate-600 text-[9px] font-semibold uppercase">
                      {src.doc_type}
                    </span>
                    <span className="text-slate-400 font-mono">{src.date}</span>
                    {src.url ? (
                      <a
                        href={src.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-indigo-600 hover:text-indigo-800 truncate"
                      >
                        {src.title}
                      </a>
                    ) : (
                      <span className="text-slate-600 truncate">{src.title}</span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>

      <div className="px-4 py-2 border-t border-slate-100 bg-slate-50/60 flex items-start gap-1.5">
        <Info size={11} className="text-slate-400 mt-0.5 shrink-0" />
        <span className="text-[10px] text-slate-500 leading-snug">
          {insights
            ? insights.disclaimer
            : "Qualitative drivers will populate once the narrative is generated. — Phase B"}
        </span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Narrative sub-components
// ---------------------------------------------------------------------------

function MarginNarrativeBlock({
  narrative,
  color,
  marginType,
  seenLabels,
  onEdit,
}: {
  narrative: MarginNarrative;
  color: string;
  marginType: MarginType;
  seenLabels: Set<string>;
  onEdit?: (edit: MarginEditRequest) => void | Promise<void>;
}) {
  const hasHistorical =
    narrative.peak.factors.length > 0 || narrative.trough.factors.length > 0;

  return (
    <div className="mt-3 space-y-2.5">
      {/* Historical peaks & troughs */}
      {hasHistorical && (
        <div className="rounded-md border border-slate-200 bg-white p-2.5">
          <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500 mb-2 border-b border-slate-100 pb-1">
            Historical peaks & troughs
          </div>
          <div className="space-y-2.5">
            {narrative.peak.factors.length > 0 && (
              <FactorList
                title={`Peak — ${narrative.peak.value_pct.toFixed(1)}% (${narrative.peak.period})`}
                icon={<ArrowUpRight size={10} className="text-emerald-600" />}
                factors={narrative.peak.factors}
                accent="#10b981"
                seenLabels={seenLabels}
                marginType={marginType}
                section="peak"
                onEdit={onEdit}
              />
            )}
            {narrative.trough.factors.length > 0 && (
              <FactorList
                title={`Trough — ${narrative.trough.value_pct.toFixed(1)}% (${narrative.trough.period})`}
                icon={<ArrowDownRight size={10} className="text-rose-600" />}
                factors={narrative.trough.factors}
                accent="#e11d48"
                seenLabels={seenLabels}
                marginType={marginType}
                section="trough"
                onEdit={onEdit}
              />
            )}
          </div>
        </div>
      )}

      {/* Current situation */}
      {narrative.current_situation && (
        <div className="rounded-md border border-slate-200 bg-slate-50/60 p-2.5">
          <div className="flex items-center justify-between mb-1">
            <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">
              Current situation
            </div>
            {narrative.current_situation.user_edited_summary && (
              <span
                className="text-[8px] font-semibold uppercase px-1 py-px rounded"
                style={{ color: "#3730a3", backgroundColor: "#e0e7ff" }}
                title="Summary edited by user"
              >
                edited
              </span>
            )}
          </div>
          <CurrentSummaryEditable
            summary={narrative.current_situation.summary}
            marginType={marginType}
            onEdit={onEdit}
          />
          <div className="grid grid-cols-2 gap-2 mt-2">
            <FactorStatusColumn
              title="Tailwinds today"
              items={narrative.current_situation.positive_factors_status}
              marginType={marginType}
              section="current_pos"
              onEdit={onEdit}
            />
            <FactorStatusColumn
              title="Headwinds today"
              items={narrative.current_situation.negative_factors_status}
              marginType={marginType}
              section="current_neg"
              onEdit={onEdit}
            />
          </div>
        </div>
      )}

      {/* Fallback when narrative exists but all lists are empty */}
      {narrative.peak.factors.length === 0 &&
        narrative.trough.factors.length === 0 &&
        !narrative.current_situation?.summary && (
          <div className="text-[10px] italic text-slate-400">
            Insufficient qualitative evidence for this margin in available sources.
          </div>
        )}

      {/* color ref to avoid unused-var lint */}
      <span className="sr-only" style={{ color }}>{color}</span>
    </div>
  );
}

function FactorList({
  title,
  icon,
  factors,
  accent,
  seenLabels,
  marginType,
  section,
  onEdit,
}: {
  title: string;
  icon: React.ReactNode;
  factors: Factor[];
  accent: string;
  seenLabels: Set<string>;
  marginType: MarginType;
  section: "peak" | "trough";
  onEdit?: (edit: MarginEditRequest) => void | Promise<void>;
}) {
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [adding,     setAdding]     = useState(false);

  const canEdit = !!onEdit;

  const submitEdit = (prev: Factor, patch: Partial<Factor>) => {
    if (!onEdit) return;
    onEdit({
      action:      "edit",
      margin_type: marginType,
      section,
      factor_key:  prev.label,
      payload: {
        label:      patch.label ?? prev.label,
        evidence:   patch.evidence ?? prev.evidence,
        direction:  patch.direction ?? prev.direction,
        source_ref: prev.source_ref,
      },
      prev: { ...prev },
    });
    setEditingKey(null);
  };

  const submitDelete = (prev: Factor) => {
    if (!onEdit) return;
    if (!confirm(`Delete "${prev.label}" from ${marginType} ${section}?`)) return;
    onEdit({
      action:      "delete",
      margin_type: marginType,
      section,
      factor_key:  prev.label,
      payload:     {},
      prev:        { ...prev },
    });
  };

  const submitAdd = (newFactor: Factor) => {
    if (!onEdit) return;
    onEdit({
      action:      "add",
      margin_type: marginType,
      section,
      factor_key:  newFactor.label,
      payload: {
        label:      newFactor.label,
        evidence:   newFactor.evidence,
        direction:  newFactor.direction,
        source_ref: -2,
      },
    });
    setAdding(false);
  };

  return (
    <div>
      <div className="flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wider text-slate-500 mb-1">
        {icon}
        <span>{title}</span>
      </div>
      <ul className="space-y-1 pl-3.5">
        {factors
          .filter((f) => !f.deleted)
          .map((f) => {
            const key = f.label;
            const isEditing = editingKey === key;
            const isRepeat  = seenLabels.has(f.label.toLowerCase().trim());
            if (isEditing) {
              return (
                <FactorEditRow
                  key={key}
                  initial={f}
                  accent={accent}
                  onCancel={() => setEditingKey(null)}
                  onSave={(patch) => submitEdit(f, patch)}
                />
              );
            }
            if (isRepeat) {
              return (
                <FactorRowCompact
                  key={key}
                  factor={f}
                  accent={accent}
                  onEdit={canEdit ? () => setEditingKey(key) : undefined}
                  onDelete={canEdit ? () => submitDelete(f) : undefined}
                />
              );
            }
            return (
              <FactorRowFull
                key={key}
                factor={f}
                accent={accent}
                onEdit={canEdit ? () => setEditingKey(key) : undefined}
                onDelete={canEdit ? () => submitDelete(f) : undefined}
              />
            );
          })}

        {/* Add-new form row */}
        {adding && (
          <FactorEditRow
            initial={{ label: "", direction: "positive", evidence: "", source_ref: -2 }}
            accent={accent}
            onCancel={() => setAdding(false)}
            onSave={(patch) =>
              submitAdd({
                label:      (patch.label ?? "").trim(),
                direction:  patch.direction ?? "positive",
                evidence:   patch.evidence ?? "",
                source_ref: -2,
              })
            }
          />
        )}
      </ul>

      {canEdit && !adding && (
        <button
          onClick={() => setAdding(true)}
          className="mt-1 ml-3.5 flex items-center gap-1 text-[10px] text-slate-400 hover:text-indigo-600"
        >
          <PlusCircle size={10} /> Add factor
        </button>
      )}
    </div>
  );
}

function FactorRowFull({
  factor,
  accent,
  onEdit,
  onDelete,
}: {
  factor: Factor;
  accent: string;
  onEdit?: () => void;
  onDelete?: () => void;
}) {
  const isBackground = factor.source_ref === -1;
  const isUserAdded  = factor.source_ref === -2;
  return (
    <li className="group text-[11px] text-slate-700 leading-snug">
      <span className="flex items-baseline gap-1.5">
        <span
          className="inline-block w-1 h-1 rounded-full translate-y-[-1px]"
          style={{ backgroundColor: accent }}
        />
        <span className="font-semibold text-slate-800">{factor.label}</span>
        {isBackground && (
          <span
            className="text-[8px] font-semibold uppercase px-1 py-px rounded"
            style={{ color: "#92400e", backgroundColor: "#fef3c7" }}
            title="No filing covers this period — sourced from model background knowledge"
          >
            background
          </span>
        )}
        {isUserAdded && (
          <span
            className="text-[8px] font-semibold uppercase px-1 py-px rounded"
            style={{ color: "#3730a3", backgroundColor: "#e0e7ff" }}
            title="Factor added by user"
          >
            user-added
          </span>
        )}
        {factor.user_edited && !isUserAdded && (
          <span
            className="text-[8px] font-semibold uppercase px-1 py-px rounded"
            style={{ color: "#3730a3", backgroundColor: "#e0e7ff" }}
            title="Edited by user — preserved across regenerations"
          >
            edited
          </span>
        )}
        {!isBackground && !isUserAdded && (
          <span className="text-[9px] font-mono text-slate-400">
            [{factor.source_ref}]
          </span>
        )}
        {(onEdit || onDelete) && (
          <span className="ml-auto flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
            {onEdit && (
              <button onClick={onEdit} title="Edit factor" className="text-slate-300 hover:text-indigo-600">
                <Pencil size={10} />
              </button>
            )}
            {onDelete && (
              <button onClick={onDelete} title="Delete factor" className="text-slate-300 hover:text-rose-600">
                <Trash2 size={10} />
              </button>
            )}
          </span>
        )}
      </span>
      <span className="block pl-2.5 text-[10px] text-slate-500">{factor.evidence}</span>
    </li>
  );
}

function FactorRowCompact({
  factor,
  accent,
  onEdit,
  onDelete,
}: {
  factor: Factor;
  accent: string;
  onEdit?: () => void;
  onDelete?: () => void;
}) {
  return (
    <li className="group text-[10px] text-slate-500 leading-snug">
      <span className="flex items-baseline gap-1.5">
        <span
          className="inline-block w-1 h-1 rounded-full translate-y-[-1px]"
          style={{ backgroundColor: accent }}
        />
        <span className="font-semibold text-slate-700">{factor.label}</span>
        <span className="italic text-slate-400">— same as above</span>
        {(onEdit || onDelete) && (
          <span className="ml-auto flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
            {onEdit && (
              <button onClick={onEdit} className="text-slate-300 hover:text-indigo-600">
                <Pencil size={10} />
              </button>
            )}
            {onDelete && (
              <button onClick={onDelete} className="text-slate-300 hover:text-rose-600">
                <Trash2 size={10} />
              </button>
            )}
          </span>
        )}
      </span>
    </li>
  );
}

function FactorEditRow({
  initial,
  accent,
  onSave,
  onCancel,
}: {
  initial: Factor;
  accent: string;
  onSave: (patch: Partial<Factor>) => void;
  onCancel: () => void;
}) {
  const [label,     setLabel]     = useState(initial.label);
  const [evidence,  setEvidence]  = useState(initial.evidence);
  const [direction, setDirection] = useState<Direction>(initial.direction);

  return (
    <li className="text-[11px] pl-3.5 py-1 border-l-2" style={{ borderLeftColor: accent }}>
      <div className="space-y-1">
        <input
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="Factor label (e.g. 'AI data-center mix shift')"
          className="w-full px-1.5 py-0.5 border border-slate-200 rounded text-[11px] font-semibold outline-none focus:border-indigo-400"
        />
        <textarea
          value={evidence}
          onChange={(e) => setEvidence(e.target.value)}
          placeholder="Evidence (one sentence, quote or paraphrase a source)"
          rows={2}
          className="w-full px-1.5 py-0.5 border border-slate-200 rounded text-[10px] text-slate-600 outline-none focus:border-indigo-400 resize-y"
        />
        <div className="flex items-center gap-1.5">
          <select
            value={direction}
            onChange={(e) => setDirection(e.target.value as Direction)}
            className="px-1 py-0.5 border border-slate-200 rounded text-[10px]"
          >
            <option value="positive">positive</option>
            <option value="negative">negative</option>
          </select>
          <button
            onClick={() => {
              const trimmed = label.trim();
              if (!trimmed) return;
              onSave({ label: trimmed, evidence: evidence.trim(), direction });
            }}
            className="flex items-center gap-0.5 px-1.5 py-0.5 rounded bg-indigo-600 text-white text-[10px] font-semibold hover:bg-indigo-700"
          >
            <Check size={10} /> Save
          </button>
          <button
            onClick={onCancel}
            className="flex items-center gap-0.5 px-1.5 py-0.5 rounded border border-slate-200 text-slate-500 text-[10px] hover:bg-slate-50"
          >
            <X size={10} /> Cancel
          </button>
        </div>
      </div>
    </li>
  );
}

const STATE_LABEL: Record<CurrentState, { text: string; color: string; bg: string }> = {
  strengthening: { text: "↑ strengthening", color: "#047857", bg: "#d1fae5" },
  steady:        { text: "→ steady",        color: "#475569", bg: "#e2e8f0" },
  weakening:     { text: "↓ weakening",     color: "#be123c", bg: "#fee2e2" },
  unclear:       { text: "? unclear",       color: "#92400e", bg: "#fef3c7" },
};

function FactorStatusColumn({
  title,
  items,
  marginType,
  section,
  onEdit,
}: {
  title: string;
  items: FactorStatus[];
  marginType: MarginType;
  section: "current_pos" | "current_neg";
  onEdit?: (edit: MarginEditRequest) => void | Promise<void>;
}) {
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [adding, setAdding]         = useState(false);
  const canEdit = !!onEdit;

  const submitEdit = (prev: FactorStatus, patch: Partial<FactorStatus>) => {
    if (!onEdit) return;
    onEdit({
      action: "edit",
      margin_type: marginType,
      section,
      factor_key: prev.factor,
      payload: {
        factor:        patch.factor ?? prev.factor,
        current_state: patch.current_state ?? prev.current_state,
        evidence:      patch.evidence ?? prev.evidence,
      },
      prev: { ...prev },
    });
    setEditingKey(null);
  };

  const submitDelete = (prev: FactorStatus) => {
    if (!onEdit) return;
    if (!confirm(`Delete "${prev.factor}"?`)) return;
    onEdit({
      action: "delete",
      margin_type: marginType,
      section,
      factor_key: prev.factor,
      payload: {},
      prev: { ...prev },
    });
  };

  const submitAdd = (next: FactorStatus) => {
    if (!onEdit) return;
    onEdit({
      action: "add",
      margin_type: marginType,
      section,
      factor_key: next.factor,
      payload: {
        factor:        next.factor,
        current_state: next.current_state,
        evidence:      next.evidence,
      },
    });
    setAdding(false);
  };

  return (
    <div>
      <div className="text-[9px] font-semibold uppercase tracking-wider text-slate-400 mb-1">
        {title}
      </div>
      {items.length === 0 && !adding ? (
        <div className="text-[10px] italic text-slate-400">—</div>
      ) : (
        <ul className="space-y-1">
          {items.map((s) => {
            const key = s.factor;
            if (editingKey === key) {
              return (
                <StatusEditRow
                  key={key}
                  initial={s}
                  onCancel={() => setEditingKey(null)}
                  onSave={(patch) => submitEdit(s, patch)}
                />
              );
            }
            const style = STATE_LABEL[s.current_state] ?? STATE_LABEL.unclear;
            return (
              <li key={key} className="group text-[10px] leading-snug">
                <div className="flex items-center gap-1">
                  <span className="font-semibold text-slate-700 truncate">{s.factor}</span>
                  <span
                    className="shrink-0 px-1 py-px rounded text-[8px] font-semibold uppercase"
                    style={{ color: style.color, backgroundColor: style.bg }}
                  >
                    {style.text}
                  </span>
                  {canEdit && (
                    <span className="ml-auto flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                      <button
                        onClick={() => setEditingKey(key)}
                        className="text-slate-300 hover:text-indigo-600"
                        title="Edit status"
                      >
                        <Pencil size={9} />
                      </button>
                      <button
                        onClick={() => submitDelete(s)}
                        className="text-slate-300 hover:text-rose-600"
                        title="Delete status"
                      >
                        <Trash2 size={9} />
                      </button>
                    </span>
                  )}
                </div>
                <div className="text-slate-500">{s.evidence}</div>
              </li>
            );
          })}
          {adding && (
            <StatusEditRow
              initial={{ factor: "", current_state: "unclear", evidence: "" }}
              onCancel={() => setAdding(false)}
              onSave={(patch) =>
                submitAdd({
                  factor:        (patch.factor ?? "").trim(),
                  current_state: patch.current_state ?? "unclear",
                  evidence:      patch.evidence ?? "",
                })
              }
            />
          )}
        </ul>
      )}

      {canEdit && !adding && (
        <button
          onClick={() => setAdding(true)}
          className="mt-1 flex items-center gap-1 text-[10px] text-slate-400 hover:text-indigo-600"
        >
          <PlusCircle size={10} /> Add
        </button>
      )}
    </div>
  );
}

function StatusEditRow({
  initial,
  onSave,
  onCancel,
}: {
  initial: FactorStatus;
  onSave: (patch: Partial<FactorStatus>) => void;
  onCancel: () => void;
}) {
  const [factor,   setFactor]   = useState(initial.factor);
  const [state,    setState]    = useState<CurrentState>(initial.current_state);
  const [evidence, setEvidence] = useState(initial.evidence);

  return (
    <li className="py-1 border-l-2 border-indigo-300 pl-1.5">
      <div className="space-y-1">
        <input
          value={factor}
          onChange={(e) => setFactor(e.target.value)}
          placeholder="Factor"
          className="w-full px-1 py-0.5 border border-slate-200 rounded text-[10px] font-semibold outline-none focus:border-indigo-400"
        />
        <select
          value={state}
          onChange={(e) => setState(e.target.value as CurrentState)}
          className="w-full px-1 py-0.5 border border-slate-200 rounded text-[10px]"
        >
          <option value="strengthening">strengthening</option>
          <option value="steady">steady</option>
          <option value="weakening">weakening</option>
          <option value="unclear">unclear</option>
        </select>
        <textarea
          value={evidence}
          onChange={(e) => setEvidence(e.target.value)}
          placeholder="Evidence"
          rows={2}
          className="w-full px-1 py-0.5 border border-slate-200 rounded text-[10px] text-slate-600 outline-none focus:border-indigo-400 resize-y"
        />
        <div className="flex items-center gap-1">
          <button
            onClick={() => {
              if (!factor.trim()) return;
              onSave({ factor: factor.trim(), current_state: state, evidence: evidence.trim() });
            }}
            className="flex items-center gap-0.5 px-1.5 py-0.5 rounded bg-indigo-600 text-white text-[10px] font-semibold hover:bg-indigo-700"
          >
            <Check size={10} /> Save
          </button>
          <button
            onClick={onCancel}
            className="flex items-center gap-0.5 px-1.5 py-0.5 rounded border border-slate-200 text-slate-500 text-[10px] hover:bg-slate-50"
          >
            <X size={10} /> Cancel
          </button>
        </div>
      </div>
    </li>
  );
}

function CurrentSummaryEditable({
  summary,
  marginType,
  onEdit,
}: {
  summary: string;
  marginType: MarginType;
  onEdit?: (edit: MarginEditRequest) => void | Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft,   setDraft]   = useState(summary);

  // Keep draft in sync when the upstream summary changes (e.g. after regen)
  useEffect(() => {
    if (!editing) setDraft(summary);
  }, [summary, editing]);

  if (!summary && !editing) return null;

  if (editing) {
    return (
      <div className="space-y-1">
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          rows={3}
          className="w-full px-1.5 py-1 border border-indigo-300 rounded text-[11px] text-slate-700 outline-none focus:border-indigo-500 resize-y"
        />
        <div className="flex items-center gap-1">
          <button
            onClick={() => {
              if (!onEdit) return;
              const trimmed = draft.trim();
              if (!trimmed) return;
              onEdit({
                action: "edit",
                margin_type: marginType,
                section: "current_summary",
                factor_key: "",
                payload: { summary: trimmed },
                prev: { summary },
              });
              setEditing(false);
            }}
            className="flex items-center gap-0.5 px-1.5 py-0.5 rounded bg-indigo-600 text-white text-[10px] font-semibold hover:bg-indigo-700"
          >
            <Check size={10} /> Save
          </button>
          <button
            onClick={() => { setDraft(summary); setEditing(false); }}
            className="flex items-center gap-0.5 px-1.5 py-0.5 rounded border border-slate-200 text-slate-500 text-[10px] hover:bg-slate-50"
          >
            <X size={10} /> Cancel
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="group flex items-start gap-1">
      <p className="text-[11px] text-slate-700 leading-snug flex-1">{summary}</p>
      {onEdit && (
        <button
          onClick={() => setEditing(true)}
          title="Edit summary"
          className="shrink-0 opacity-0 group-hover:opacity-100 transition-opacity text-slate-300 hover:text-indigo-600"
        >
          <Pencil size={10} />
        </button>
      )}
    </div>
  );
}

function NarrativeSkeleton() {
  return (
    <div className="mt-3 space-y-1.5">
      <div className="h-2 w-2/3 bg-slate-100 rounded animate-pulse" />
      <div className="h-2 w-11/12 bg-slate-100 rounded animate-pulse" />
      <div className="h-2 w-5/6 bg-slate-100 rounded animate-pulse" />
    </div>
  );
}

function MarginRangeBar({ stat }: { stat: MarginStat }) {
  if (stat.min === null || stat.max === null || stat.current === null || stat.max === stat.min) {
    return <div className="h-1.5 rounded-full bg-slate-100" />;
  }
  const pct = ((stat.current - stat.min) / (stat.max - stat.min)) * 100;
  return (
    <div className="relative h-1.5 rounded-full bg-slate-100">
      <div
        className="absolute top-0 h-1.5 rounded-full opacity-30"
        style={{ left: 0, width: "100%", backgroundColor: stat.color }}
      />
      <div
        className="absolute top-1/2 -translate-y-1/2 w-2.5 h-2.5 rounded-full border-2 border-white shadow"
        style={{ left: `calc(${pct}% - 5px)`, backgroundColor: stat.color }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sector heatmap panel — companies grouped by user-selectable definition,
// showing revenue YoY% per calendar quarter with color-coded cells.
// ---------------------------------------------------------------------------

// 5-stop diverging color scale for % metrics, soft/professional palette.
// Tuned so dark text is legible across the entire range — the eye lands on
// the numbers, not the background. Comparable to Bloomberg / FactSet
// quintile shading. Saturates at ±maxAbs.
const _HEATMAP_PCT_STOPS: { v: number; rgb: [number, number, number] }[] = [
  { v: -60, rgb: [248, 113, 113] }, // rose-400   — soft red
  { v: -30, rgb: [253, 186, 116] }, // orange-300 — soft peach
  { v:   0, rgb: [254, 240, 138] }, // yellow-200 — pale cream
  { v:  30, rgb: [134, 239, 172] }, // green-300  — soft mint
  { v:  60, rgb: [ 74, 222, 128] }, // green-400  — soft sage
];

function _heatmapColorPct(value: number | null, maxAbs: number = 60): string {
  if (value == null || Number.isNaN(value)) return "#f8fafc"; // slate-50
  const v = Math.max(-maxAbs, Math.min(maxAbs, value));
  // Find the two stops bracketing v
  let lo = _HEATMAP_PCT_STOPS[0];
  let hi = _HEATMAP_PCT_STOPS[_HEATMAP_PCT_STOPS.length - 1];
  for (let i = 0; i < _HEATMAP_PCT_STOPS.length - 1; i++) {
    if (v >= _HEATMAP_PCT_STOPS[i].v && v <= _HEATMAP_PCT_STOPS[i + 1].v) {
      lo = _HEATMAP_PCT_STOPS[i];
      hi = _HEATMAP_PCT_STOPS[i + 1];
      break;
    }
  }
  const span = hi.v - lo.v;
  const t = span === 0 ? 0 : (v - lo.v) / span;
  const r = Math.round(lo.rgb[0] + (hi.rgb[0] - lo.rgb[0]) * t);
  const g = Math.round(lo.rgb[1] + (hi.rgb[1] - lo.rgb[1]) * t);
  const b = Math.round(lo.rgb[2] + (hi.rgb[2] - lo.rgb[2]) * t);
  return `rgb(${r},${g},${b})`;
}

// For absolute $ metrics (Revenue, Net Income), color by RELATIVE position
// within the row's own min..max range. Lighter blue = lower in own history,
// darker blue = higher. This makes each ticker comparable to itself, and
// avoids the trap of comparing $68B NVDA to $665M LITE on the same scale.
function _heatmapColorRelative(
  value: number | null,
  rowMin: number,
  rowMax: number,
): string {
  if (value == null || Number.isNaN(value)) return "#f8fafc";
  if (rowMax === rowMin) return "#dbeafe";  // single-value row → flat light blue
  const t = (value - rowMin) / (rowMax - rowMin);
  // White (low) → indigo-500 (#6366f1) (high)
  const r = Math.round(255 + (99  - 255) * t);
  const g = Math.round(255 + (102 - 255) * t);
  const b = Math.round(255 + (241 - 255) * t);
  return `rgb(${r},${g},${b})`;
}

function _textColorForHeatmap(value: number | null, isPct: boolean, intensity?: number): string {
  if (value == null || Number.isNaN(value)) return "#94a3b8";  // slate-400
  if (isPct) {
    // Soft palette with dynamic range: dark text works everywhere
    return "#0f172a";  // slate-900
  }
  // For $ metrics, intensity is the row-relative t in [0,1]
  return (intensity ?? 0) > 0.6 ? "#ffffff" : "#0f172a";
}

function _formatCellValue(value: number | null, isPct: boolean): string {
  if (value == null) return "—";
  if (isPct) {
    return `${value >= 0 ? "+" : ""}${value.toFixed(1)}`;
  }
  // $M with thousands separators, parentheses for negative
  const neg = value < 0;
  const abs = Math.abs(value);
  const body =
    abs >= 1000
      ? `${(abs / 1000).toFixed(1)}B`
      : `${Math.round(abs).toLocaleString("en-US")}`;
  return neg ? `(${body})` : body;
}

// M&A event indicator + hover popup for the heatmap ticker cell.
// Uses ReactDOM.createPortal to render the popup at document.body level
// so it escapes the table's overflow:hidden / sticky-column z-index stack.
function CorporateEventBadge({ events }: { events: CorporateEvent[] }) {
  const [open, setOpen] = useState(false);
  const triggerRef = React.useRef<HTMLSpanElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);

  if (events.length === 0) return null;

  const handleEnter = () => {
    if (triggerRef.current) {
      const rect = triggerRef.current.getBoundingClientRect();
      setPos({ top: rect.bottom + 4, left: rect.left });
    }
    setOpen(true);
  };
  const handleLeave = () => setOpen(false);

  return (
    <>
      <span
        ref={triggerRef}
        className="text-[9px] text-amber-600 cursor-pointer hover:text-amber-800 font-bold"
        onMouseEnter={handleEnter}
        onMouseLeave={handleLeave}
      >
        {" "}...
      </span>
      {open && pos && ReactDOM.createPortal(
        <div
          className="fixed bg-white border border-slate-200 rounded-xl shadow-2xl p-4 w-[340px] text-left"
          style={{ top: pos.top, left: pos.left, zIndex: 9999 }}
          onMouseEnter={() => setOpen(true)}
          onMouseLeave={handleLeave}
        >
          <div className="text-[10px] font-bold text-slate-700 uppercase tracking-wider mb-2.5 pb-1.5 border-b border-slate-100">
            Corporate Events
          </div>
          <div className="space-y-3 max-h-72 overflow-y-auto">
            {events.map((ev, i) => (
              <div key={i} className="border-b border-slate-100 pb-2.5 last:border-0 last:pb-0">
                <div className="flex items-center gap-2 mb-1.5">
                  <span className={`px-1.5 py-0.5 text-[8px] font-bold uppercase rounded border ${
                    ev.event_type === "acquisition" ? "text-blue-700 bg-blue-50 border-blue-200" :
                    ev.event_type === "spinoff"     ? "text-purple-700 bg-purple-50 border-purple-200" :
                                                     "text-amber-700 bg-amber-50 border-amber-200"
                  }`}>
                    {ev.event_type}
                  </span>
                  <span className="text-[11px] font-semibold text-slate-800">{ev.deal_name}</span>
                </div>
                <div className="text-[10px] text-slate-600 space-y-1 pl-0.5">
                  <div className="flex gap-2">
                    <span className="text-slate-400 shrink-0 w-16">Date</span>
                    <span className="font-mono">{ev.event_date}</span>
                  </div>
                  {ev.related_companies.length > 0 && (
                    <div className="flex gap-2">
                      <span className="text-slate-400 shrink-0 w-16">Related</span>
                      <span className="font-mono">{ev.related_companies.join(", ")}</span>
                    </div>
                  )}
                  <div className="flex gap-2">
                    <span className="text-slate-400 shrink-0 w-16">Impact</span>
                    <span>{ev.impact_type}</span>
                  </div>
                  <div className="flex gap-2">
                    <span className="text-slate-400 shrink-0 w-16">Affected</span>
                    <span className="font-mono text-[9px]">{ev.impacted_quarters.join(", ")}</span>
                  </div>
                  <div className="text-[9px] text-slate-500 mt-1.5 leading-relaxed bg-slate-50 rounded p-2">
                    {ev.details}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>,
        document.body,
      )}
    </>
  );
}

const HEATMAP_METRIC_OPTIONS: { key: SectorHeatmapMetric; label: string; group?: string }[] = [
  // Revenue
  { key: "revenue_yoy_pct",    label: "Revenue YoY %",        group: "Revenue" },
  { key: "revenue_qoq_pct",    label: "Revenue QoQ %",        group: "Revenue" },
  { key: "revenue",            label: "Revenue ($M)",          group: "Revenue" },
  // Net Income
  { key: "net_income",         label: "Net Income ($M)",       group: "Net Income" },
  { key: "net_income_yoy_pct", label: "Net Income YoY %",     group: "Net Income" },
  { key: "net_income_qoq_pct", label: "Net Income QoQ %",     group: "Net Income" },
  // Margins
  { key: "gross_margin_pct",              label: "Gross Margin %",           group: "Margins" },
  { key: "operating_margin_pct",          label: "Op Margin %",              group: "Margins" },
  { key: "net_margin_pct",                label: "Net Margin %",             group: "Margins" },
  // Margin deltas
  { key: "gross_margin_pct_diff_yoy",     label: "Gross Margin Δ YoY (pp)",  group: "Margin Δ" },
  { key: "operating_margin_pct_diff_yoy", label: "Op Margin Δ YoY (pp)",     group: "Margin Δ" },
  { key: "net_margin_pct_diff_yoy",       label: "Net Margin Δ YoY (pp)",    group: "Margin Δ" },
];

function SectorHeatmapPanel({
  definitions, groupDef, metric, heatmap, loading, onGroupDefChange, onMetricChange,
}: {
  definitions: SectorHeatmapDefinition[];
  groupDef:    string;
  metric:      SectorHeatmapMetric;
  heatmap:     SectorHeatmap | null;
  loading:     boolean;
  onGroupDefChange: (key: string) => void;
  onMetricChange:   (m: SectorHeatmapMetric) => void;
}) {
  // EXPERIMENTAL: view mode toggle. Stepped = the existing per-ticker
  // stepped-back columns ("LATEST", "−1Q", ...). Calendar = unified
  // calendar-quarter columns where each ticker's reported period gets
  // placed in the bucket whose date range contains its end_date.
  const [viewMode, setViewMode] = useState<"stepped" | "calendar">("calendar");
  // Calendar mode: toggle to show/hide the per-ticker fiscal period
  // label inside each cell (e.g. "2026 Q4" below the value).
  const [showCellPeriod, setShowCellPeriod] = useState(true);

  const isPct = heatmap?.metric_fmt === "%";

  // For $ metrics, compute per-row min/max so each ticker is colored relative
  // to its OWN history (NVDA $68B vs LITE $665M can't share an absolute scale).
  const rowMinMax = new Map<string, { min: number; max: number }>();
  if (heatmap && !isPct) {
    for (const g of heatmap.groups) {
      for (const r of g.rows) {
        const vals = r.points.map((p) => p.value).filter((v): v is number => v != null);
        if (vals.length > 0) {
          rowMinMax.set(r.ticker, {
            min: Math.min(...vals),
            max: Math.max(...vals),
          });
        }
      }
    }
  }

  // ── Stepped view (default) ────────────────────────────────────────
  // Dynamic color range for % metrics: use p99 of |values| so the scale
  // adapts to the data while ignoring extreme outliers (corrupt rows or
  // one-off 3000% spikes that would flatten the entire color range).
  // Floor at 100 so NVDA's 262% is visible; cap at 500 for sanity.
  const pctColorMax: number = (() => {
    if (!heatmap || !isPct) return 60;
    const allVals: number[] = [];
    for (const g of heatmap.groups) {
      for (const r of g.rows) {
        for (const p of r.points) {
          const v = p.value ?? p.yoy;
          if (v != null && !Number.isNaN(v)) allVals.push(Math.abs(v));
        }
      }
    }
    if (allVals.length === 0) return 100;
    allVals.sort((a, b) => a - b);
    const p99Idx = Math.min(allVals.length - 1, Math.floor(allVals.length * 0.99));
    const p99 = allVals[p99Idx];
    // Floor at 100 so small datasets still show NVDA-scale growth clearly.
    // Cap at 500 so a single corrupt 3000% outlier doesn't flatten everything.
    const clamped = Math.max(100, Math.min(500, p99));
    // Round up to a clean bucket for readable legend labels.
    const buckets = [100, 120, 150, 200, 250, 300, 400, 500];
    for (const b of buckets) {
      if (clamped <= b) return b;
    }
    return 500;
  })();

  // Number of columns is the max `points.length` across all rows so the
  // grid aligns visually (the column count is the same for every row; each
  // row fills its own stepped-back labels from its own latest quarter).
  const numCols = heatmap
    ? Math.max(0, ...heatmap.groups.flatMap((g) => g.rows.map((r) => r.points.length)))
    : 0;

  // Relative column headers: LATEST, −1Q, −2Q, −3Q, −4Q, −1Y, −1Y−1Q, ...
  // First 5 columns are quarterly offsets (0 through 4Q). After that,
  // switch to year+quarter notation so the user can quickly scan annual
  // comparisons (−1Y = same quarter last year, −1Y−1Q = one quarter
  // before that, etc.).
  const columnHeaders = Array.from({ length: numCols }, (_, i) => {
    if (i === 0) return "LATEST";
    if (i <= 4) return `−${i}Q`;
    const years = Math.floor(i / 4);
    const rem   = i % 4;
    if (rem === 0) return `−${years}Y`;
    return `−${years}Y−${rem}Q`;
  });

  // ── Calendar view ────────────────────────────────────────────────
  // Bucket every point's end_date into a calendar quarter by NEAREST
  // quarter-end date (Mar 31, Jun 30, Sep 30, Dec 31). This handles
  // filers whose quarter end_date lands on April 1-2 or July 1-2 (e.g.
  // INTC ends on Saturdays) — strict month-based bucketing would put
  // April 2 into Q2 instead of Q1, creating blanks.
  function _nearestCalendarQ(d: Date): { year: number; q: number } {
    const quarterEnds = [
      { year: d.getFullYear() - 1, q: 4, ref: new Date(d.getFullYear() - 1, 11, 31) },
      { year: d.getFullYear(),     q: 1, ref: new Date(d.getFullYear(), 2, 31) },
      { year: d.getFullYear(),     q: 2, ref: new Date(d.getFullYear(), 5, 30) },
      { year: d.getFullYear(),     q: 3, ref: new Date(d.getFullYear(), 8, 30) },
      { year: d.getFullYear(),     q: 4, ref: new Date(d.getFullYear(), 11, 31) },
      { year: d.getFullYear() + 1, q: 1, ref: new Date(d.getFullYear() + 1, 2, 31) },
    ];
    let best = quarterEnds[0];
    let bestDist = Math.abs(d.getTime() - best.ref.getTime());
    for (const qe of quarterEnds) {
      const dist = Math.abs(d.getTime() - qe.ref.getTime());
      if (dist < bestDist) { best = qe; bestDist = dist; }
    }
    return { year: best.year, q: best.q };
  }

  type CalCol = { key: string; label: string; range: string; sortDate: number };
  const calendarCols: CalCol[] = (() => {
    if (!heatmap) return [];
    const seen = new Map<string, CalCol>();
    for (const g of heatmap.groups) {
      for (const r of g.rows) {
        for (const p of r.points) {
          if (!p.end_date) continue;
          const d = new Date(p.end_date);
          if (Number.isNaN(d.getTime())) continue;
          const { year: yr, q } = _nearestCalendarQ(d);
          const key = `${yr}-Q${q}`;
          if (seen.has(key)) continue;
          const startMonth = (q - 1) * 3 + 1;
          const endMonth   = q * 3;
          const endDay     = [31, 30, 30, 31][q - 1];
          const pad = (n: number) => String(n).padStart(2, "0");
          seen.set(key, {
            key,
            label:    `${yr} Q${q}`,
            range:    `${pad(startMonth)}/01/${yr}–${pad(endMonth)}/${endDay}/${yr}`,
            sortDate: new Date(yr, startMonth - 1, 1).getTime(),
          });
        }
      }
    }
    return [...seen.values()].sort((a, b) => b.sortDate - a.sortDate);
  })();

  // For calendar view: per-ticker map from calendar key → point
  function _calendarPointsForRow(row: SectorHeatmapRow): Map<string, SectorHeatmapPoint> {
    const m = new Map<string, SectorHeatmapPoint>();
    for (const p of row.points) {
      if (!p.end_date) continue;
      const d = new Date(p.end_date);
      if (Number.isNaN(d.getTime())) continue;
      const { year: yr, q } = _nearestCalendarQ(d);
      m.set(`${yr}-Q${q}`, p);
    }
    return m;
  }

  // Collect all mismatches across all rows for the footer
  const allMismatches: { ticker: string; position: number; end_date: string; expected: string; edgar: string }[] = [];
  if (heatmap) {
    for (const g of heatmap.groups) {
      for (const r of g.rows) {
        for (const m of r.mismatches) {
          allMismatches.push({ ticker: r.ticker, ...m });
        }
      }
    }
  }

  return (
    <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between gap-3 px-5 py-3 border-b border-slate-100">
        <div className="flex items-center gap-2">
          <TrendingUp size={14} className="text-slate-400" />
          <span className="text-xs font-semibold text-slate-700">
            Sector Heatmap · {heatmap?.metric_label ?? "Revenue YoY %"}
          </span>
          {loading && (
            <Loader2 size={11} className="text-slate-300 animate-spin" />
          )}
        </div>

        {/* Top-right controls: view mode toggle + metric selector + group definition selector */}
        <div className="flex items-center gap-3">
          {/* View mode toggle (Stepped vs Calendar) */}
          <div className="flex items-center gap-1 border border-slate-200 rounded-md bg-slate-50 p-0.5">
            {(["stepped", "calendar"] as const).map((m) => (
              <button
                key={m}
                onClick={() => setViewMode(m)}
                className={`text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded ${
                  viewMode === m
                    ? "bg-white text-indigo-700 shadow-sm"
                    : "text-slate-500 hover:text-slate-700"
                }`}
              >
                {m === "stepped" ? "Stepped" : "Calendar"}
              </button>
            ))}
          </div>

          {/* Show/hide period labels in cells (calendar mode only) */}
          {viewMode === "calendar" && (
            <button
              onClick={() => setShowCellPeriod((v) => !v)}
              className={`text-[10px] font-semibold px-2 py-0.5 rounded border transition-colors ${
                showCellPeriod
                  ? "bg-indigo-50 text-indigo-700 border-indigo-200"
                  : "bg-slate-50 text-slate-500 border-slate-200"
              }`}
              title="Show or hide the fiscal period label inside each cell"
            >
              {showCellPeriod ? "Periods ✓" : "Periods"}
            </button>
          )}

          <div className="flex items-center gap-2">
            <label className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider">
              Metric
            </label>
            <select
              value={metric}
              onChange={(e) => onMetricChange(e.target.value as SectorHeatmapMetric)}
              className="h-7 px-2 rounded-md border border-slate-200 bg-slate-50 text-xs outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 text-slate-700"
            >
              {(() => {
                const groups = new Map<string, typeof HEATMAP_METRIC_OPTIONS>();
                for (const opt of HEATMAP_METRIC_OPTIONS) {
                  const g = opt.group ?? "";
                  if (!groups.has(g)) groups.set(g, []);
                  groups.get(g)!.push(opt);
                }
                return [...groups.entries()].map(([groupLabel, opts]) =>
                  groupLabel ? (
                    <optgroup key={groupLabel} label={groupLabel}>
                      {opts.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
                    </optgroup>
                  ) : (
                    opts.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)
                  )
                );
              })()}
            </select>
          </div>

          <div className="flex items-center gap-2">
            <label className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider">
              Group by
            </label>
            <select
              value={groupDef}
              onChange={(e) => onGroupDefChange(e.target.value)}
              className="h-7 px-2 rounded-md border border-slate-200 bg-slate-50 text-xs outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 text-slate-700"
            >
              {definitions.length === 0 ? (
                <option value={groupDef}>{groupDef}</option>
              ) : (
                definitions.map((d) => (
                  <option key={d.key} value={d.key}>{d.label}</option>
                ))
              )}
            </select>
          </div>
        </div>
      </div>

      {/* Body */}
      <div className="overflow-x-auto">
        {!heatmap || heatmap.groups.length === 0 ? (
          <div className="px-5 py-10 text-center text-xs text-slate-400">
            {loading ? "Loading sector data…" : "No data available for this group definition."}
          </div>
        ) : viewMode === "stepped" ? (
          // ── STEPPED VIEW (default) ───────────────────────────────
          <table className="w-full text-[11px] border-collapse">
            <thead>
              <tr className="border-b border-slate-200">
                <th className="sticky left-0 bg-white z-10 text-left font-semibold text-slate-500 px-4 py-2 min-w-[130px]">
                  Ticker · Latest
                </th>
                {columnHeaders.map((h, i) => (
                  <th
                    key={i}
                    className="text-center font-mono font-semibold text-slate-500 px-2 py-2 min-w-[60px]"
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {heatmap.groups.map((group) => (
                <React.Fragment key={group.name}>
                  <tr className="bg-slate-50 border-t border-b border-slate-200">
                    <td
                      colSpan={numCols + 1}
                      className="sticky left-0 bg-slate-50 px-4 py-1.5 text-[10px] font-bold text-slate-600 uppercase tracking-widest"
                    >
                      {group.name}
                      <span className="ml-2 text-slate-400 font-normal normal-case">
                        ({group.rows.length} ticker{group.rows.length === 1 ? "" : "s"})
                      </span>
                    </td>
                  </tr>
                  {group.rows.map((row) => (
                    <tr key={`${group.name}-${row.ticker}`} className="border-b border-slate-100 last:border-0">
                      <td className="sticky left-0 bg-white z-10 px-4 py-1.5 font-mono text-slate-700 min-w-[130px]">
                        <div className="font-bold">
                          {row.ticker}
                          <CorporateEventBadge events={row.corporate_events ?? []} />
                        </div>
                        <div className="text-[9px] text-slate-400">
                          {row.latest_label ?? "—"}
                          {row.latest_end_date && ` · ${row.latest_end_date}`}
                        </div>
                      </td>
                      {Array.from({ length: numCols }, (_, i) => {
                        const p = row.points[i];
                        const v = p?.value ?? p?.yoy ?? null;
                        const cellLabel = p?.label ?? "";
                        const cellDate  = p?.end_date ?? "";

                        let bg: string;
                        let intensityT: number | undefined;
                        if (isPct) {
                          bg = _heatmapColorPct(v, pctColorMax);
                        } else {
                          const mm = rowMinMax.get(row.ticker);
                          bg = mm
                            ? _heatmapColorRelative(v, mm.min, mm.max)
                            : "#f8fafc";
                          if (v != null && mm && mm.max !== mm.min) {
                            intensityT = (v - mm.min) / (mm.max - mm.min);
                          }
                        }

                        const tooltipFmt = isPct
                          ? (v == null ? "—" : v.toFixed(1) + "%")
                          : _formatCellValue(v, false);

                        return (
                          <td
                            key={i}
                            className="text-center font-mono px-2 py-1.5 tabular-nums"
                            style={{
                              backgroundColor: bg,
                              color:           _textColorForHeatmap(v, isPct, intensityT),
                            }}
                            title={
                              p
                                ? `${row.ticker}  ${cellLabel}  (${cellDate})  ${tooltipFmt}${!p.matches && p.edgar_label ? `  [edgar: ${p.edgar_label}]` : ""}`
                                : "—"
                            }
                          >
                            <div>{_formatCellValue(v, isPct)}</div>
                            {cellLabel && (
                              <div
                                className="text-[8px] font-normal opacity-70 mt-0.5"
                                style={{ color: _textColorForHeatmap(v, isPct, intensityT) }}
                              >
                                {cellLabel.replace("FY", "").replace("-", " ")}
                              </div>
                            )}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </React.Fragment>
              ))}
            </tbody>
          </table>
        ) : (
          // ── CALENDAR VIEW (experimental) ─────────────────────────
          // Columns are calendar quarters; each ticker's reported period
          // gets placed in the bucket whose range contains its end_date.
          // Tickers that haven't reported yet for a given quarter show blank.
          // Latest period name is shown in its own dedicated column.
          <table className="w-full text-[11px] border-collapse">
            <thead>
              <tr className="border-b border-slate-200">
                <th className="sticky left-0 bg-white z-10 text-left font-semibold text-slate-500 px-4 py-2 min-w-[80px]">
                  Ticker
                </th>
                <th className="sticky bg-white z-10 text-left font-semibold text-slate-500 px-3 py-2 min-w-[110px] border-r border-slate-200" style={{ left: "80px" }}>
                  Latest Period
                </th>
                {calendarCols.map((c) => (
                  <th
                    key={c.key}
                    className="text-center font-mono font-semibold text-slate-500 px-2 py-2 min-w-[68px]"
                    title={`${c.label} (${c.range})`}
                  >
                    {c.range}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {heatmap.groups.map((group) => (
                <React.Fragment key={group.name}>
                  <tr className="bg-slate-50 border-t border-b border-slate-200">
                    <td
                      colSpan={calendarCols.length + 2}
                      className="sticky left-0 bg-slate-50 px-4 py-1.5 text-[10px] font-bold text-slate-600 uppercase tracking-widest"
                    >
                      {group.name}
                      <span className="ml-2 text-slate-400 font-normal normal-case">
                        ({group.rows.length} ticker{group.rows.length === 1 ? "" : "s"})
                      </span>
                    </td>
                  </tr>
                  {group.rows.map((row) => {
                    const calMap = _calendarPointsForRow(row);
                    return (
                      <tr key={`${group.name}-${row.ticker}`} className="border-b border-slate-100 last:border-0">
                        <td className="sticky left-0 bg-white z-10 px-4 py-1.5 font-mono font-bold text-slate-800 min-w-[80px]">
                          {row.ticker}
                          <CorporateEventBadge events={row.corporate_events ?? []} />
                        </td>
                        <td
                          className="sticky bg-white z-10 px-3 py-1.5 font-mono text-slate-600 min-w-[110px] border-r border-slate-200"
                          style={{ left: "80px" }}
                        >
                          <div>{row.latest_label ?? "—"}</div>
                          <div className="text-[9px] text-slate-400">{row.latest_end_date ?? ""}</div>
                        </td>
                        {calendarCols.map((c) => {
                          const p = calMap.get(c.key);
                          const v = p?.value ?? p?.yoy ?? null;

                          let bg: string;
                          let intensityT: number | undefined;
                          if (isPct) {
                            bg = _heatmapColorPct(v, pctColorMax);
                          } else {
                            const mm = rowMinMax.get(row.ticker);
                            bg = mm
                              ? _heatmapColorRelative(v, mm.min, mm.max)
                              : "#f8fafc";
                            if (v != null && mm && mm.max !== mm.min) {
                              intensityT = (v - mm.min) / (mm.max - mm.min);
                            }
                          }

                          const tooltipFmt = isPct
                            ? (v == null ? "—" : v.toFixed(1) + "%")
                            : _formatCellValue(v, false);

                          return (
                            <td
                              key={c.key}
                              className="text-center font-mono px-2 py-1.5 tabular-nums"
                              style={{
                                backgroundColor: bg,
                                color:           _textColorForHeatmap(v, isPct, intensityT),
                              }}
                              title={
                                p
                                  ? `${row.ticker}  ${p.label ?? ""}  (${p.end_date})  ${tooltipFmt}`
                                  : `${row.ticker}  ${c.label}  (no report)`
                              }
                            >
                              <div>{_formatCellValue(v, isPct)}</div>
                              {showCellPeriod && p?.label && (
                                <div
                                  className="text-[8px] font-normal opacity-70 mt-0.5"
                                  style={{ color: _textColorForHeatmap(v, isPct, intensityT) }}
                                >
                                  {p.label.replace("FY", "").replace("-", " ")}
                                </div>
                              )}
                            </td>
                          );
                        })}
                      </tr>
                    );
                  })}
                </React.Fragment>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Footer: legend + mismatch notes */}
      <div className="px-5 py-2 border-t border-slate-100 bg-slate-50/50">
        <div className="flex items-center justify-between text-[10px] text-slate-400">
          <span>
            {viewMode === "stepped"
              ? "Columns step backward from each ticker's most recent reported quarter."
              : "Columns are calendar quarters (Jan-Mar, Apr-Jun, etc.). Tickers that haven't reported yet for a given quarter show blank."}
            {!isPct && " For $ metrics, color shows each ticker's value relative to its own min..max history."}
          </span>
          <div className="flex items-center gap-2 shrink-0">
            {isPct ? (
              <>
                <span>-{pctColorMax}%</span>
                <div
                  className="h-3 w-32 rounded"
                  style={{
                    background:
                      "linear-gradient(to right, rgb(248,113,113) 0%, rgb(253,186,116) 25%, rgb(254,240,138) 50%, rgb(134,239,172) 75%, rgb(74,222,128) 100%)",
                  }}
                />
                <span>+{pctColorMax}%</span>
              </>
            ) : (
              <>
                <span>row min</span>
                <div
                  className="h-3 w-32 rounded"
                  style={{
                    background:
                      "linear-gradient(to right, rgb(255,255,255), rgb(99,102,241))",
                  }}
                />
                <span>row max</span>
              </>
            )}
          </div>
        </div>
        {allMismatches.length > 0 && (
          <div className="mt-2 text-[9px] text-amber-700 border-t border-amber-100 pt-2">
            <span className="font-semibold uppercase tracking-wider">
              EDGAR label mismatches ({allMismatches.length}):
            </span>{" "}
            <span className="font-mono">
              {allMismatches.slice(0, 20).map((m, i) => (
                <span key={i} className="inline-block mr-3">
                  {m.ticker} {m.end_date}: shown as {m.expected}, EDGAR says {m.edgar}
                </span>
              ))}
              {allMismatches.length > 20 && (
                <span className="text-amber-600">… and {allMismatches.length - 20} more</span>
              )}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main view
// ---------------------------------------------------------------------------

export default function DataExplorerView({
  loadedTickers,
  activeTicker,
  rows,
  loading,
  error,
  apiWarnings,
  onTickerChange,
  onAddTicker,
  onRemoveTicker,
  marginInsights,
  marginInsightsLoading,
  marginInsightsError,
  onRefreshMarginInsights,
  onEditMarginInsights,
  heatmapDefinitions = [],
  heatmapGroupDef = "GICS_industry",
  heatmapMetric = "revenue_yoy_pct",
  heatmap = null,
  heatmapLoading = false,
  onHeatmapGroupDefChange = () => {},
  onHeatmapMetricChange = () => {},
}: DataExplorerViewProps) {
  const [cellRequest, setCellRequest]     = useState<CellRequest | null>(null);

  // Table: most recent period on the LEFT (screenshot spec).
  // Charts: keep chronological (oldest on left) so lines/bars flow left-to-right.
  // Margin deltas (gross/op/net margin pp YoY) are served directly by the
  // calculated layer — no client-side derivation needed.
  const tableRows = [...rows].reverse();
  // Sanity check alerts — computed from the loaded rows, mirrors the
  // backend `_validate` checks. Surfaced below the quarterly table.
  const qualityAlerts = validateRows(activeTicker, rows);
  // end_date may come back as "2024-07-28T00:00:00" or "2024-07-28" — strip the time portion.
  const periods       = tableRows.map((r) => (r.end_date ?? "").slice(0, 10));
  // Compact fiscal period label: "FY2025-Q3" -> "2025Q3", "2024-Q4" -> "2024Q4".
  const periodLabels  = tableRows.map((r) => (r.period_label ?? "").replace(/^FY/, "").replace(/-/g, ""));
  const chartData = rows.map((r) => ({
    q:                    r.period_label,
    "Gross Margin %":     getNum(r, "gross_margin_pct"),
    "Operating Margin %": getNum(r, "operating_margin_pct"),
    "Net Margin %":       getNum(r, "net_margin_pct"),
    "Net Revenue":        getNum(r, "revenue"),
    "Gross Profit":       getNum(r, "gross_profit"),
    "Operating Profit":   getNum(r, "operating_income"),
    "Net Income":         getNum(r, "net_income"),
    "Revenue YoY %":      getNum(r, "revenue_yoy_pct"),
    "Op Profit YoY %":    getNum(r, "operating_income_yoy_pct"),
    "Net Profit YoY %":   getNum(r, "net_income_yoy_pct"),
    "Free Cash Flow":     getNum(r, "free_cash_flow"),
    "Operating CF":       getNum(r, "operating_cf"),
    "Investing CF":       getNum(r, "investing_cf"),
    "Financing CF":       getNum(r, "financing_cf"),
    "Capex":              getNum(r, "capex"),
    // Capex ratios (capex in SEC filings is a negative outflow — we take abs
    // so the % values read as "capex intensity" rather than a negative drag)
    "Capex / Gross Profit %": _ratioPct(r, "capex", "gross_profit"),
    "Capex / Revenue %":      _ratioPct(r, "capex", "revenue"),
    "Capex / EBITDA %":       _ratioPct(r, "capex", "ebitda"),
    "Capex / OCF %":          _ratioPct(r, "capex", "operating_cf"),
  }));

  // Interval for X-axis: show one label per 4 quarters (less clutter)
  const xInterval = Math.max(0, Math.floor(periods.length / 6) - 1);

  // Capex-intensity Y-axis: flexible max based on the data, capped at 250%.
  // Finds the max value across all capex ratio series, multiplies by 1.10
  // for headroom, then takes min(that, 250). This keeps the chart readable
  // for most tickers while not squishing everything to the bottom when the
  // data is all below 30%.
  const capexKeys = [
    "Capex / Revenue %",
    "Capex / Gross Profit %",
    "Capex / EBITDA %",
  ];
  const CAPEX_Y_CAP = (() => {
    let dataMax = 0;
    for (const row of chartData) {
      for (const k of capexKeys) {
        const v = (row as Record<string, unknown>)[k];
        if (typeof v === "number" && isFinite(v) && v > dataMax) dataMax = v;
      }
    }
    if (dataMax === 0) return 50; // fallback for empty data
    const flexible = dataMax * 1.10;
    // Round up to a clean number for readable Y-axis ticks
    const buckets = [10, 15, 20, 25, 30, 40, 50, 60, 80, 100, 120, 150, 200, 250];
    for (const b of buckets) {
      if (flexible <= b) return b;
    }
    return 250; // hard cap
  })();

  const hasData = rows.length > 0;

  const [viewMode, setViewMode] = useState<"financials" | "semi-pricing" | "taiwan-semi" | "taiwan-day-trading" | "taiwan-foreign-flow">("financials");

  // Default ticker sub-tab toggle: Prices vs Financials. Applies to any
  // ticker that doesn't have a specialised panel (TSMC/UMC/MediaTek).
  // Lands on Prices so NVDA, AAPL, AMD, etc. show the chart immediately.
  const [usEquityTab, setUsEquityTab] = useState<"prices" | "financials">("prices");

  return (
    <div className="flex flex-col h-full overflow-hidden">

      {/* ── Page header ── */}
      <div className="flex items-center justify-between px-8 py-4 bg-white border-b border-slate-200 shrink-0">
        <div className="flex items-center gap-3">
          <BarChart2 size={22} className="text-indigo-600 shrink-0" />
          <div>
            <h1 className="text-xl font-bold text-slate-900 leading-tight">Data Explorer</h1>
            <p className="text-xs text-slate-500 mt-0.5">Quarterly fundamental data across your universe</p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {/* ── Top-level view tabs ── */}
          <div className="flex items-center bg-slate-100 rounded-lg p-0.5 mr-2">
            <button
              onClick={() => setViewMode("financials")}
              className={`h-7 px-3 rounded-md text-xs font-semibold transition-colors ${
                viewMode === "financials"
                  ? "bg-white text-slate-900 shadow-sm"
                  : "text-slate-500 hover:text-slate-700"
              }`}
            >
              Financials
            </button>
            <button
              onClick={() => setViewMode("semi-pricing")}
              className={`h-7 px-3 rounded-md text-xs font-semibold transition-colors ${
                viewMode === "semi-pricing"
                  ? "bg-white text-slate-900 shadow-sm"
                  : "text-slate-500 hover:text-slate-700"
              }`}
            >
              Semi Pricing
            </button>
            <button
              onClick={() => setViewMode("taiwan-semi")}
              className={`h-7 px-3 rounded-md text-xs font-semibold transition-colors ${
                viewMode === "taiwan-semi"
                  ? "bg-white text-slate-900 shadow-sm"
                  : "text-slate-500 hover:text-slate-700"
              }`}
            >
              Taiwan Semi
            </button>
            <button
              onClick={() => setViewMode("taiwan-day-trading")}
              className={`h-7 px-3 rounded-md text-xs font-semibold transition-colors ${
                viewMode === "taiwan-day-trading"
                  ? "bg-white text-slate-900 shadow-sm"
                  : "text-slate-500 hover:text-slate-700"
              }`}
            >
              TW Day-Trading
            </button>
            <button
              onClick={() => setViewMode("taiwan-foreign-flow")}
              className={`h-7 px-3 rounded-md text-xs font-semibold transition-colors ${
                viewMode === "taiwan-foreign-flow"
                  ? "bg-white text-slate-900 shadow-sm"
                  : "text-slate-500 hover:text-slate-700"
              }`}
            >
              TW Foreign Flow
            </button>
          </div>
          <button className="flex items-center gap-1.5 h-8 px-3 text-xs font-medium border border-slate-200 rounded-md text-slate-600 hover:bg-slate-50 transition-colors">
            <Download size={13} /> One Sheet
          </button>
          <button className="flex items-center gap-1.5 h-8 px-3 text-xs font-medium border border-slate-200 rounded-md text-slate-600 hover:bg-slate-50 transition-colors">
            <TrendingUp size={13} /> Deep Dive
          </button>
        </div>
      </div>

      {/* ── Semi Pricing view ── */}
      {viewMode === "semi-pricing" && (
        <div className="flex-1 overflow-y-auto px-8 py-6 bg-slate-50">
          <SemiPricingPanel />
        </div>
      )}

      {/* ── Taiwan Semi monthly-revenue heatmap ── */}
      {viewMode === "taiwan-semi" && (
        <div className="flex-1 overflow-y-auto px-8 py-6 bg-slate-50">
          <TaiwanSemiHeatmapPanel />
        </div>
      )}

      {/* ── TWSE day-trading (當日沖銷) ── */}
      {viewMode === "taiwan-day-trading" && (
        <div className="flex-1 overflow-y-auto px-8 py-6 bg-slate-50">
          <TaiwanDayTradingPanel />
        </div>
      )}

      {/* ── TWSE 三大法人 (BFI82U) ── */}
      {viewMode === "taiwan-foreign-flow" && (
        <div className="flex-1 overflow-y-auto px-8 py-6 bg-slate-50">
          <TaiwanForeignFlowPanel />
        </div>
      )}


      {/* ── Financials: Ticker tabs ── */}
      {viewMode === "financials" && (<>
      <div className="flex items-center gap-1 px-8 py-2 bg-white border-b border-slate-100 shrink-0 flex-wrap">
        {loadedTickers.map((ticker) => (
          <div key={ticker} className="flex items-center">
            <button
              onClick={() => onTickerChange(ticker)}
              className={`flex items-center gap-1.5 h-7 px-3 rounded-l-md text-xs font-semibold transition-colors ${
                activeTicker === ticker
                  ? "bg-slate-900 text-white"
                  : "text-slate-500 hover:bg-slate-100"
              }`}
            >
              {ticker}
            </button>
            {loadedTickers.length > 1 && (
              <button
                onClick={() => onRemoveTicker(ticker)}
                className={`flex items-center h-7 px-1.5 rounded-r-md text-xs transition-colors ${
                  activeTicker === ticker
                    ? "bg-slate-800 text-slate-300 hover:text-white"
                    : "text-slate-300 hover:text-slate-500 hover:bg-slate-100"
                }`}
              >
                <X size={10} />
              </button>
            )}
          </div>
        ))}

        <AddTickerButton onAdd={onAddTicker} />

        {loading && (
          <span className="ml-auto flex items-center gap-1.5 text-[10px] font-mono text-slate-400 bg-slate-50 border border-slate-200 px-2 py-0.5 rounded">
            <Loader2 size={10} className="animate-spin" />
            LOADING
          </span>
        )}
        {!loading && hasData && (
          <span className="ml-auto text-[10px] font-mono text-slate-400 bg-slate-50 border border-slate-200 px-2 py-0.5 rounded">
            {activeTicker} · {periods.length} quarters
          </span>
        )}
      </div>

      {/* ── API warnings banner ── */}
      {apiWarnings.length > 0 && (
        <div className="flex items-start gap-2 px-8 py-2 bg-amber-50 border-b border-amber-200 text-[11px] text-amber-700 shrink-0">
          <AlertTriangle size={13} className="shrink-0 mt-0.5" />
          <span>{apiWarnings[0]}{apiWarnings.length > 1 ? ` (+${apiWarnings.length - 1} more)` : ""}</span>
        </div>
      )}

      {/* ── Error banner ── */}
      {error && (
        <div className="flex items-center gap-2 px-8 py-2 bg-red-50 border-b border-red-200 text-[11px] text-red-700 shrink-0">
          <AlertTriangle size={13} />
          {error}
        </div>
      )}

      {/* ── Scrollable content ── */}
      <div className="flex-1 overflow-y-auto px-8 py-6 space-y-6 bg-slate-50">

        {activeTicker === "2330.TW" ? (
          <TSMCPanel />
        ) : activeTicker === "2303.TW" ? (
          <UMCPanel />
        ) : activeTicker === "2454.TW" ? (
          <MediaTekPanel />
        ) : loading && !hasData ? (
          <LoadingOverlay />
        ) : !hasData && usEquityTab !== "prices" ? (
          <EmptyState ticker={activeTicker} />
        ) : (
          <>
            {/* ── Sub-tab toggle: Prices | Financials (US equities) ── */}
            <div className="flex items-center gap-2">
              <div className="flex bg-white border border-slate-200 rounded-md overflow-hidden text-xs shadow-sm">
                <button
                  type="button"
                  onClick={() => setUsEquityTab("prices")}
                  className={`px-4 py-1.5 ${
                    usEquityTab === "prices"
                      ? "bg-indigo-600 text-white"
                      : "bg-white text-slate-700 hover:bg-slate-50"
                  }`}
                >
                  Prices
                </button>
                <button
                  type="button"
                  onClick={() => setUsEquityTab("financials")}
                  className={`px-4 py-1.5 border-l border-slate-200 ${
                    usEquityTab === "financials"
                      ? "bg-indigo-600 text-white"
                      : "bg-white text-slate-700 hover:bg-slate-50"
                  }`}
                >
                  Financials
                </button>
              </div>
              <span className="text-[11px] text-slate-500 font-mono">{activeTicker}</span>
            </div>

            {usEquityTab === "prices" && (
              <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-4">
                <PricesTab ticker={activeTicker} />
              </div>
            )}

            {usEquityTab === "financials" && !hasData && (
              <EmptyState ticker={activeTicker} />
            )}

            {usEquityTab === "financials" && hasData && (
              <>
            {/* ── Financial table ── */}
            <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
              <div className="flex items-center justify-between px-6 py-3 border-b border-slate-100 bg-slate-50/80">
                <span className="text-xs font-semibold text-slate-700">
                  Quarterly Financial Data{" "}
                  <span className="text-slate-400 font-normal">(USD Millions)</span>
                </span>
                <span className="text-[10px] font-mono text-slate-400">
                  {activeTicker} · {periods.length} quarters
                </span>
              </div>

              <div className="overflow-x-auto">
                <table className="text-xs w-full">
                  <thead>
                    {/* Row 1: period end date (YYYY-MM-DD) */}
                    <tr className="border-b border-slate-100">
                      <th
                        rowSpan={2}
                        className="sticky left-0 z-30 bg-white text-left px-4 py-2 text-[10px] font-bold text-slate-500 uppercase tracking-wider w-44 min-w-[176px] border-r border-slate-200 align-bottom shadow-[4px_0_6px_-4px_rgba(15,23,42,0.08)]"
                      >
                        Metric
                      </th>
                      {periods.map((p, i) => (
                        <th
                          key={`d-${i}`}
                          className="px-3 pt-2 pb-0.5 text-right text-[10px] font-bold text-slate-600 whitespace-nowrap min-w-[88px]"
                        >
                          {p}
                        </th>
                      ))}
                    </tr>
                    {/* Row 2: fiscal period label (e.g. 2025Q2) */}
                    <tr className="border-b border-slate-200">
                      {periodLabels.map((p, i) => (
                        <th
                          key={`p-${i}`}
                          className="px-3 pb-2 pt-0 text-right text-[10px] font-mono font-medium text-slate-400 whitespace-nowrap"
                        >
                          {p || "—"}
                        </th>
                      ))}
                    </tr>
                  </thead>

                  <tbody>
                    {ROW_GROUPS.flatMap((group) => group.rows).map((rowDef, ri) => {
                      // Thin divider between groups so the walk-down still has
                      // visible structure without the loud header bands.
                      const firstOfGroup =
                        ri > 0 &&
                        ROW_GROUPS.some(
                          (g) => g.rows[0]?.metric === rowDef.metric,
                        );
                      const stripe  = ri % 2 === 0 ? "bg-white" : "bg-slate-50";
                      const derived = rowDef.derived;
                      const isComputed = !!rowDef.compute;

                      // Indent: derived = pl-12, indent=1 = pl-8, default = px-4.
                      // bold spine items intentionally have no indent — they're
                      // the visual hero of each section.
                      const labelPadding = derived
                        ? "pl-12 pr-4"
                        : rowDef.indent === 1
                          ? "pl-8 pr-4"
                          : "px-4";

                      return (
                        <tr
                          key={rowDef.metric}
                          className={`group border-b border-slate-50 ${
                            firstOfGroup ? "border-t border-slate-200" : ""
                          } ${stripe} hover:!bg-indigo-50/60 transition-colors`}
                        >
                          {/* Metric label — sticky, fully opaque, follows row stripe + hover */}
                          <td
                            className={`sticky left-0 z-10 ${stripe} group-hover:!bg-indigo-50/60 ${labelPadding} py-1.5 border-r border-slate-200 whitespace-nowrap shadow-[4px_0_6px_-4px_rgba(15,23,42,0.08)]`}
                          >
                            <span
                              className={`${derived ? "text-[10px] italic text-slate-500" : "text-[11px] text-slate-700"} ${
                                rowDef.bold ? "font-semibold text-slate-900" : ""
                              }`}
                            >
                              {rowDef.label}
                            </span>
                          </td>

                          {/* Data cells — right-aligned, most recent period on left.
                              Click opens a drill-down modal showing the source
                              (XBRL concepts, derivation formula, SEC filing link).
                              Frontend-computed cells (Other OpEx, Tax Rate,
                              Income from Cont Ops) are not clickable since
                              there's no single backing concept to drill into. */}
                          {tableRows.map((r, qi) => {
                            const v = isComputed
                              ? rowDef.compute!(r)
                              : getNum(r, rowDef.metric);
                            const clickable = v !== null && !isComputed;
                            return (
                              <td
                                key={qi}
                                onClick={
                                  clickable
                                    ? () => setCellRequest({
                                        ticker:   activeTicker,
                                        metric:   rowDef.metric,
                                        end_date: r.end_date.slice(0, 10),
                                      })
                                    : undefined
                                }
                                className={`px-3 py-1.5 pr-4 text-right tabular-nums ${
                                  derived ? "text-[10px] italic" : ""
                                } ${rowDef.bold ? "font-semibold" : ""} ${
                                  clickable ? "cursor-pointer hover:underline decoration-dotted decoration-slate-400 underline-offset-2" : ""
                                }`}
                                style={cellStyle(v, rowDef.fmt)}
                              >
                                {fmtCell(v, rowDef.fmt)}
                              </td>
                            );
                          })}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              {/* ── Alerts strip — only visible when checks fire ── */}
              {qualityAlerts.length > 0 && (
                <div className="border-t border-amber-200 bg-amber-50/50 px-5 py-3 text-[11px]">
                  <div className="flex items-center gap-2 mb-2">
                    <AlertTriangle size={13} className="text-amber-600 shrink-0" />
                    <span className="font-bold text-amber-900 uppercase tracking-wider text-[10px]">
                      Alert · {qualityAlerts.length} {qualityAlerts.length === 1 ? "issue" : "issues"} for {activeTicker}
                    </span>
                    <span className="text-[10px] text-amber-700">
                      ({qualityAlerts.filter(a => a.severity === "hard").length} hard ·{" "}
                      {qualityAlerts.filter(a => a.severity === "soft").length} soft ·{" "}
                      {qualityAlerts.filter(a => a.severity === "coverage").length} coverage)
                    </span>
                  </div>
                  <div className="space-y-1 max-h-48 overflow-y-auto">
                    {qualityAlerts.slice(0, 30).map((a, i) => {
                      const sevColor =
                        a.severity === "hard"
                          ? "text-rose-700 bg-rose-100 border-rose-300"
                          : a.severity === "soft"
                          ? "text-amber-700 bg-amber-100 border-amber-300"
                          : "text-slate-700 bg-slate-100 border-slate-300";
                      return (
                        <div key={i} className="flex items-start gap-2">
                          <span className={`inline-block px-1.5 py-0 text-[9px] font-bold uppercase rounded border ${sevColor} shrink-0 mt-0.5`}>
                            {a.severity}
                          </span>
                          <span className="font-mono text-[10px] text-slate-500 shrink-0 mt-0.5 min-w-[80px]">
                            {a.category}
                          </span>
                          <span className="font-mono text-[10px] text-slate-600 shrink-0 mt-0.5 min-w-[90px]">
                            {a.period}
                          </span>
                          <span className="text-[11px] text-slate-700 leading-snug">
                            mismatch: {a.message}
                          </span>
                        </div>
                      );
                    })}
                    {qualityAlerts.length > 30 && (
                      <div className="text-[10px] text-amber-600 italic mt-1">
                        … {qualityAlerts.length - 30} more alerts hidden
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>

            {/* ── Charts row ── */}
            <div className="grid grid-cols-2 gap-6">

              {/* Margins line chart */}
              <ChartCard title="Gross, Operating & Net Margin (%)">
                <ResponsiveContainer width="100%" height={260}>
                  <LineChart data={chartData} margin={{ top: 4, right: 8, bottom: 0, left: -12 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                    <XAxis
                      dataKey="q"
                      tick={{ fontSize: 9, fill: "#94a3b8" }}
                      interval={xInterval}
                      tickLine={false}
                      axisLine={false}
                    />
                    <YAxis
                      tick={{ fontSize: 9, fill: "#94a3b8" }}
                      tickLine={false}
                      axisLine={false}
                      tickFormatter={(v) => `${v}%`}
                    />
                    <Tooltip
                      contentStyle={{ fontSize: 11, borderRadius: 8, border: "1px solid #e2e8f0" }}
                      labelStyle={{ fontSize: 10, fontWeight: 600, color: "#334155" }}
                      formatter={(v: number, name: string) => [`${v?.toFixed(2)}%`, name]}
                      cursor={{ stroke: "#cbd5e1", strokeDasharray: "3 3" }}
                    />
                    <Legend wrapperStyle={{ fontSize: 10, paddingTop: 8 }} />
                    <Line type="monotone" dataKey="Gross Margin %"     stroke="#10b981" strokeWidth={2} dot={{ r: 2.5, strokeWidth: 0, fill: "#10b981" }} activeDot={{ r: 5, strokeWidth: 2, stroke: "#fff" }} connectNulls />
                    <Line type="monotone" dataKey="Operating Margin %"  stroke="#f59e0b" strokeWidth={2} dot={{ r: 2.5, strokeWidth: 0, fill: "#f59e0b" }} activeDot={{ r: 5, strokeWidth: 2, stroke: "#fff" }} connectNulls />
                    <Line type="monotone" dataKey="Net Margin %"        stroke="#8b5cf6" strokeWidth={2} dot={{ r: 2.5, strokeWidth: 0, fill: "#8b5cf6" }} activeDot={{ r: 5, strokeWidth: 2, stroke: "#fff" }} connectNulls />
                  </LineChart>
                </ResponsiveContainer>
              </ChartCard>

              {/* Margin insights — Phase A numeric + Phase B narrative */}
              <MarginInsightsPanel
                rows={rows}
                ticker={activeTicker}
                insights={marginInsights}
                insightsLoading={marginInsightsLoading}
                insightsError={marginInsightsError}
                onRefreshInsights={onRefreshMarginInsights}
                onEditInsights={onEditMarginInsights}
              />
            </div>

            {/* ── Financials chart (full width) ── */}
            <div>
              <ChartCard title={`Financials — ${activeTicker} (USD M / YoY %)`}>
                <ResponsiveContainer width="100%" height={260}>
                  <ComposedChart data={chartData} margin={{ top: 4, right: 8, bottom: 0, left: -12 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" vertical={false} />
                    <XAxis
                      dataKey="q"
                      tick={{ fontSize: 9, fill: "#94a3b8" }}
                      interval={xInterval}
                      tickLine={false}
                      axisLine={false}
                    />
                    <YAxis
                      yAxisId="usd"
                      tick={{ fontSize: 9, fill: "#94a3b8" }}
                      tickLine={false}
                      axisLine={false}
                      tickFormatter={fmtAxis}
                    />
                    <YAxis
                      yAxisId="pct"
                      orientation="right"
                      tick={{ fontSize: 9, fill: "#94a3b8" }}
                      tickLine={false}
                      axisLine={false}
                      tickFormatter={(v: number) => `${v}%`}
                    />
                    <Tooltip
                      contentStyle={{ fontSize: 11, borderRadius: 8, border: "1px solid #e2e8f0" }}
                      formatter={(v: number, name: string) => {
                        if (v == null) return ["—", name];
                        if (name.endsWith("%")) return [`${v.toFixed(1)}%`, name];
                        return [`$${v.toLocaleString()}M`, name];
                      }}
                      cursor={{ fill: "#f1f5f9" }}
                    />
                    <Legend wrapperStyle={{ fontSize: 10, paddingTop: 8 }} />

                    {/* USD bars */}
                    <Bar yAxisId="usd" dataKey="Net Revenue"     fill="#6366f1" radius={[2, 2, 0, 0]} />
                    <Bar yAxisId="usd" dataKey="Operating Profit" fill="#f59e0b" radius={[2, 2, 0, 0]} />
                    <Bar yAxisId="usd" dataKey="Net Income"       fill="#10b981" radius={[2, 2, 0, 0]} />

                    {/* YoY % lines */}
                    <Line
                      yAxisId="pct"
                      type="monotone"
                      dataKey="Revenue YoY %"
                      stroke="#4338ca"
                      strokeWidth={1.5}
                      dot={{ r: 2, fill: "#4338ca", strokeWidth: 0 }}
                      activeDot={{ r: 4, strokeWidth: 2, stroke: "#fff" }}
                      connectNulls
                    />
                    <Line
                      yAxisId="pct"
                      type="monotone"
                      dataKey="Op Profit YoY %"
                      stroke="#b45309"
                      strokeWidth={1.5}
                      dot={{ r: 2, fill: "#b45309", strokeWidth: 0 }}
                      activeDot={{ r: 4, strokeWidth: 2, stroke: "#fff" }}
                      connectNulls
                    />
                    <Line
                      yAxisId="pct"
                      type="monotone"
                      dataKey="Net Profit YoY %"
                      stroke="#047857"
                      strokeWidth={1.5}
                      dot={{ r: 2, fill: "#047857", strokeWidth: 0 }}
                      activeDot={{ r: 4, strokeWidth: 2, stroke: "#fff" }}
                      connectNulls
                    />
                  </ComposedChart>
                </ResponsiveContainer>
              </ChartCard>
            </div>

            {/* ── Cash flow + Capex intensity ── */}
            <div className="grid grid-cols-2 gap-6">
              <ChartCard title="Cash Flow Trend (USD M)">
                <ResponsiveContainer width="100%" height={260}>
                  <LineChart data={chartData} margin={{ top: 4, right: 16, bottom: 0, left: -4 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                    <XAxis
                      dataKey="q"
                      tick={{ fontSize: 9, fill: "#94a3b8" }}
                      interval={xInterval}
                      tickLine={false}
                      axisLine={false}
                    />
                    <YAxis
                      tick={{ fontSize: 9, fill: "#94a3b8" }}
                      tickLine={false}
                      axisLine={false}
                      tickFormatter={fmtAxis}
                    />
                    <Tooltip
                      contentStyle={{ fontSize: 11, borderRadius: 8, border: "1px solid #e2e8f0" }}
                      labelStyle={{ fontSize: 10, fontWeight: 600, color: "#334155" }}
                      formatter={(v: number, name: string) => [
                        v == null ? "—" : `$${v.toLocaleString()}M`,
                        name,
                      ]}
                      cursor={{ stroke: "#cbd5e1", strokeDasharray: "3 3" }}
                    />
                    <Legend wrapperStyle={{ fontSize: 10, paddingTop: 8 }} />
                    <Line type="monotone" dataKey="Operating CF"   stroke="#f59e0b" strokeWidth={2}   dot={{ r: 2.5, fill: "#f59e0b", strokeWidth: 0 }} activeDot={{ r: 5, stroke: "#fff", strokeWidth: 2 }} connectNulls />
                    <Line type="monotone" dataKey="Free Cash Flow" stroke="#10b981" strokeWidth={2}   dot={{ r: 2.5, fill: "#10b981", strokeWidth: 0 }} activeDot={{ r: 5, stroke: "#fff", strokeWidth: 2 }} connectNulls />
                    <Line type="monotone" dataKey="Investing CF"   stroke="#ef4444" strokeWidth={1.5} dot={{ r: 2, fill: "#ef4444", strokeWidth: 0 }}   activeDot={{ r: 4, stroke: "#fff", strokeWidth: 2 }} strokeDasharray="4 2" connectNulls />
                    <Line type="monotone" dataKey="Financing CF"   stroke="#6366f1" strokeWidth={1.5} dot={{ r: 2, fill: "#6366f1", strokeWidth: 0 }}   activeDot={{ r: 4, stroke: "#fff", strokeWidth: 2 }} strokeDasharray="4 2" connectNulls />
                    <Line type="monotone" dataKey="Capex"          stroke="#8b5cf6" strokeWidth={1.5} dot={{ r: 2, fill: "#8b5cf6", strokeWidth: 0 }}   activeDot={{ r: 4, stroke: "#fff", strokeWidth: 2 }} strokeDasharray="4 2" connectNulls />
                  </LineChart>
                </ResponsiveContainer>
              </ChartCard>

              <ChartCard title={`Net Income & FCF — ${activeTicker} (USD M)`}>
                <ResponsiveContainer width="100%" height={260}>
                  <LineChart data={chartData} margin={{ top: 4, right: 16, bottom: 0, left: -4 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                    <XAxis
                      dataKey="q"
                      tick={{ fontSize: 9, fill: "#94a3b8" }}
                      interval={xInterval}
                      tickLine={false}
                      axisLine={false}
                    />
                    <YAxis
                      tick={{ fontSize: 9, fill: "#94a3b8" }}
                      tickLine={false}
                      axisLine={false}
                      tickFormatter={fmtAxis}
                    />
                    <Tooltip
                      contentStyle={{ fontSize: 11, borderRadius: 8, border: "1px solid #e2e8f0" }}
                      labelStyle={{ fontSize: 10, fontWeight: 600, color: "#334155" }}
                      formatter={(v: number, name: string) => [
                        v == null ? "—" : `$${v.toLocaleString()}M`,
                        name,
                      ]}
                      cursor={{ stroke: "#cbd5e1", strokeDasharray: "3 3" }}
                    />
                    <Legend wrapperStyle={{ fontSize: 10, paddingTop: 8 }} />
                    <Line type="monotone" dataKey="Net Income"     stroke="#6366f1" strokeWidth={2} dot={{ r: 2.5, fill: "#6366f1", strokeWidth: 0 }} activeDot={{ r: 5, stroke: "#fff", strokeWidth: 2 }} connectNulls />
                    <Line type="monotone" dataKey="Free Cash Flow" stroke="#10b981" strokeWidth={2} dot={{ r: 2.5, fill: "#10b981", strokeWidth: 0 }} activeDot={{ r: 5, stroke: "#fff", strokeWidth: 2 }} connectNulls />
                  </LineChart>
                </ResponsiveContainer>
              </ChartCard>
            </div>

            {/* ── Capex Intensity — full width on its own row ── */}
            <div>
              <ChartCard title={`Capex Intensity — ${activeTicker} (%)`}>
                <ResponsiveContainer width="100%" height={260}>
                  <LineChart data={chartData} margin={{ top: 4, right: 16, bottom: 0, left: -4 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                    <XAxis
                      dataKey="q"
                      tick={{ fontSize: 9, fill: "#94a3b8" }}
                      interval={xInterval}
                      tickLine={false}
                      axisLine={false}
                    />
                    <YAxis
                      tick={{ fontSize: 9, fill: "#94a3b8" }}
                      tickLine={false}
                      axisLine={false}
                      tickFormatter={(v: number) => `${v}%`}
                      domain={[0, CAPEX_Y_CAP]}
                      allowDataOverflow
                    />
                    <Tooltip
                      contentStyle={{ fontSize: 11, borderRadius: 8, border: "1px solid #e2e8f0" }}
                      labelStyle={{ fontSize: 10, fontWeight: 600, color: "#334155" }}
                      formatter={(v: number, name: string) => [
                        v == null ? "—" : `${v.toFixed(2)}%`,
                        name,
                      ]}
                      cursor={{ stroke: "#cbd5e1", strokeDasharray: "3 3" }}
                    />
                    <Legend wrapperStyle={{ fontSize: 10, paddingTop: 8 }} />
                    <Line type="monotone" dataKey="Capex / Revenue %"      stroke="#6366f1" strokeWidth={2}   dot={{ r: 2.5, fill: "#6366f1", strokeWidth: 0 }} activeDot={{ r: 5, stroke: "#fff", strokeWidth: 2 }} connectNulls />
                    <Line type="monotone" dataKey="Capex / Gross Profit %" stroke="#10b981" strokeWidth={2}   dot={{ r: 2.5, fill: "#10b981", strokeWidth: 0 }} activeDot={{ r: 5, stroke: "#fff", strokeWidth: 2 }} connectNulls />
                    <Line type="monotone" dataKey="Capex / EBITDA %"       stroke="#f59e0b" strokeWidth={2}   dot={{ r: 2.5, fill: "#f59e0b", strokeWidth: 0 }} activeDot={{ r: 5, stroke: "#fff", strokeWidth: 2 }} connectNulls />
                  </LineChart>
                </ResponsiveContainer>
              </ChartCard>
            </div>

            {/* ── Sector heatmap — full-width bottom section ── */}
            <SectorHeatmapPanel
              definitions={heatmapDefinitions}
              groupDef={heatmapGroupDef}
              metric={heatmapMetric}
              heatmap={heatmap}
              loading={heatmapLoading}
              onGroupDefChange={onHeatmapGroupDefChange}
              onMetricChange={onHeatmapMetricChange}
            />
              </>
            )}
          </>
        )}
      </div>

      {/* Drill-down modal — renders only when a cell has been clicked */}
      <CellSourceModal request={cellRequest} onClose={() => setCellRequest(null)} />
      </>
      )}
    </div>
  );
}
