"use client";

/**
 * TSMCPanel — review what the TSMC quarterly-report ingestion has captured.
 * Six sub-tabs:
 *   1. Financials  — wide pivot of headline P&L metrics across quarters
 *   2. Tech mix    — revenue % by node over time + line chart
 *   3. Platform / Geography — revenue % breakdowns
 *   4. Guidance vs Actual  — TSMC's track record beating its own guides
 *   5. Transcripts — list quarters, expand to read speaker turns, search
 *   6. PDFs        — catalog of every PDF cached on disk
 */

import { useEffect, useMemo, useState } from "react";
import { Loader2, Search, ChevronDown, ChevronRight, ExternalLink, Mic, FileText, BarChart2, TrendingUp, Layers, Globe } from "lucide-react";
import {
  CartesianGrid, Legend, Line, LineChart, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from "recharts";
import {
  tsmcClient,
  type TSMCSummary,
  type TSMCFinancialsWide,
  type TSMCMetricRow,
  type TSMCSegments,
  type TSMCGuidanceRow,
  type TSMCForwardGuidance,
  type TSMCTranscriptQuarter,
  type TSMCTranscriptTurn,
  type TSMCTranscriptMatch,
  type TSMCPDFCatalog,
} from "@/lib/api/tsmcClient";

type SubTab = "financials" | "tech" | "platform_geo" | "guidance" | "transcripts" | "pdfs";

const SUBTABS: { key: SubTab; label: string; icon: React.ReactNode }[] = [
  { key: "financials",  label: "Financials",     icon: <BarChart2 size={14} /> },
  { key: "tech",        label: "Tech mix",       icon: <Layers size={14} /> },
  { key: "platform_geo",label: "Platform / Geo", icon: <Globe size={14} /> },
  { key: "guidance",    label: "Guidance",       icon: <TrendingUp size={14} /> },
  { key: "transcripts", label: "Transcripts",    icon: <Mic size={14} /> },
  { key: "pdfs",        label: "PDF catalog",    icon: <FileText size={14} /> },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return "—";
  if (Math.abs(v) >= 1000) return v.toLocaleString(undefined, { maximumFractionDigits: 0 });
  return v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function fmtUnit(unit: string): string {
  switch (unit) {
    case "ntd_b": return "NT$ B";
    case "usd_b": return "US$ B";
    case "pct": return "%";
    case "ntd_per_share": return "NT$";
    case "usd_per_adr": return "US$/ADR";
    case "ntd_per_usd": return "USD/NTD";
    case "kpcs_12in_eq": return "kpcs (12in eq)";
    case "days": return "days";
    case "ratio": return "x";
    default: return unit;
  }
}

// Walk-down structure mirroring DataExplorer's NVDA `Quarterly Financial
// Data` table: spine subtotals are bold, derived ratios (margins) are
// small italic, indented sub-components contribute to the subtotal above.
type FinFmt = "M" | "%" | "$" | "kpcs";

interface TSMCRowDef {
  label: string;
  metric: string;
  fmt: FinFmt;
  bold?: boolean;
  derived?: boolean;
  indent?: 1 | 2;
}
interface TSMCRowGroup { heading: string; rows: TSMCRowDef[] }

// Note: amounts are NTD billions unless noted. We keep one extra "(US$)"
// row each for revenue and capex since TSMC reports both consistently.
const TSMC_ROW_GROUPS: TSMCRowGroup[] = [
  { heading: "Revenue", rows: [
    { label: "Net Revenue",         metric: "net_revenue",        fmt: "M", bold: true },
    { label: "Net Revenue (US$ B)", metric: "net_revenue_usd",    fmt: "$", indent: 1 },
  ]},
  { heading: "Gross Profit", rows: [
    { label: "Cost of Revenue",     metric: "cost_of_revenue",    fmt: "M", indent: 1 },
    { label: "Gross Profit",        metric: "gross_profit",       fmt: "M", bold: true },
    { label: "Gross Margin %",      metric: "gross_margin",       fmt: "%", derived: true },
  ]},
  { heading: "Operating Expenses", rows: [
    { label: "R&D",                 metric: "r_and_d",            fmt: "M", indent: 1 },
    { label: "SG&A",                metric: "sga",                fmt: "M", indent: 1 },
    { label: "Total OpEx",          metric: "operating_expenses", fmt: "M", bold: true },
  ]},
  { heading: "Operating Income", rows: [
    { label: "Operating Income",    metric: "operating_income",   fmt: "M", bold: true },
    { label: "Op Margin %",         metric: "operating_margin",   fmt: "%", derived: true },
  ]},
  { heading: "Net Income", rows: [
    { label: "Net Income",          metric: "net_income",         fmt: "M", bold: true },
    { label: "Net Profit Margin %", metric: "net_profit_margin",  fmt: "%", derived: true },
    { label: "EPS (NT$/share)",     metric: "eps",                fmt: "$" },
  ]},
  { heading: "Productivity", rows: [
    { label: "Wafer Shipment (kpcs 12in eq)", metric: "wafer_shipment", fmt: "kpcs", indent: 1 },
  ]},
  { heading: "Cash Flow", rows: [
    { label: "CapEx",               metric: "capex",              fmt: "M", indent: 1 },
    { label: "CapEx (US$ B)",       metric: "capex_usd",          fmt: "$", indent: 1 },
    { label: "Free Cash Flow",      metric: "free_cash_flow",     fmt: "M", bold: true },
    { label: "Ending Cash Balance", metric: "ending_cash_balance",fmt: "M", indent: 1 },
  ]},
];

// ---------------------------------------------------------------------------
// Main panel
// ---------------------------------------------------------------------------

export default function TSMCPanel() {
  const [tab, setTab] = useState<SubTab>("financials");
  const [summary, setSummary] = useState<TSMCSummary | null>(null);
  const [summaryErr, setSummaryErr] = useState<string | null>(null);

  useEffect(() => {
    tsmcClient.summary()
      .then(setSummary)
      .catch((e) => setSummaryErr(e instanceof Error ? e.message : "load failed"));
  }, []);

  return (
    <div className="space-y-4">
      {/* Header card */}
      <div className="bg-white border border-slate-200 rounded-lg shadow-sm p-5">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h2 className="text-lg font-bold text-slate-900">TSMC (2330.TW) — Ingested Data</h2>
            <p className="text-xs text-slate-500 mt-0.5">
              Long-format silver layers extracted from investor.tsmc.com
            </p>
          </div>
        </div>
        {summaryErr && (
          <div className="text-xs text-rose-600 bg-rose-50 px-3 py-2 rounded">
            Error loading summary: {summaryErr}
          </div>
        )}
        {summary && (
          <div className="grid grid-cols-4 gap-4 text-xs">
            <Stat label="Quarterly facts"
                  value={`${summary.layers.quarterly_facts?.rows.toLocaleString() ?? 0}`}
                  detail={`${summary.layers.quarterly_facts?.metrics ?? 0} metrics × ${summary.layers.quarterly_facts?.periods ?? 0} periods`}
                  range={`${summary.layers.quarterly_facts?.earliest_period_end?.slice(0,7)} → ${summary.layers.quarterly_facts?.latest_period_end?.slice(0,7)}`} />
            <Stat label="Transcript turns"
                  value={`${summary.layers.transcripts?.rows.toLocaleString() ?? 0}`}
                  detail={`${summary.layers.transcripts?.quarters ?? 0} earnings calls · ${summary.layers.transcripts?.speakers ?? 0} unique speakers`}
                  range={`${summary.layers.transcripts?.earliest_call?.slice(0,10) ?? "—"} → ${summary.layers.transcripts?.latest_call?.slice(0,10) ?? "—"}`} />
            <Stat label="Guidance facts"
                  value={`${summary.layers.guidance?.rows.toLocaleString() ?? 0}`}
                  detail={`${summary.layers.guidance?.periods_covered ?? 0} periods covered · ${summary.layers.guidance?.pages ?? 0} source pages`}
                  range={`${summary.layers.guidance?.earliest_page ?? "—"} → ${summary.layers.guidance?.latest_page ?? "—"}`} />
            <Stat label="PDFs cached"
                  value={`${summary.layers.pdf_catalog?.pdfs ?? 0}`}
                  detail={`${summary.layers.pdf_catalog?.quarters ?? 0} quarters × 5 doc types`}
                  range="Mgmt report · Earnings release · Presentation · Transcript · FS" />
          </div>
        )}
      </div>

      {/* Sub-tab switcher */}
      <div className="flex items-center bg-slate-100 rounded-lg p-0.5 gap-0.5 w-fit">
        {SUBTABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`flex items-center gap-1.5 h-8 px-3 rounded-md text-xs font-semibold transition-colors ${
              tab === t.key
                ? "bg-white text-slate-900 shadow-sm"
                : "text-slate-500 hover:text-slate-700"
            }`}
          >
            {t.icon} {t.label}
          </button>
        ))}
      </div>

      {/* Active sub-tab content */}
      <div>
        {tab === "financials"  && <FinancialsTab />}
        {tab === "tech"        && <SegmentTab metric="revenue_share_by_technology" title="Wafer Revenue by Technology" colorByOrder unit="%" />}
        {tab === "platform_geo"&& <PlatformGeoTab />}
        {tab === "guidance"    && <GuidanceTab />}
        {tab === "transcripts" && <TranscriptsTab />}
        {tab === "pdfs"        && <PDFsTab />}
      </div>
    </div>
  );
}

function Stat({ label, value, detail, range }: { label: string; value: string; detail?: string; range?: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide font-semibold text-slate-500">{label}</div>
      <div className="text-xl font-bold text-slate-900 leading-tight mt-0.5">{value}</div>
      {detail && <div className="text-[11px] text-slate-600 mt-1">{detail}</div>}
      {range && <div className="text-[10px] text-slate-400 mt-0.5">{range}</div>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab 1 — Financials wide table
// ---------------------------------------------------------------------------

function FinancialsTab() {
  const [data, setData] = useState<TSMCFinancialsWide | null>(null);
  const [quarters, setQuarters] = useState(20);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    tsmcClient.financialsWide(quarters)
      .then(setData)
      .finally(() => setLoading(false));
  }, [quarters]);

  if (loading) return <SectionLoading />;
  if (!data || !data.metrics.length) return <Empty>No financials data.</Empty>;

  // Index metrics by name for fast lookup as we iterate the row groups.
  const byMetric = new Map<string, TSMCMetricRow>();
  data.metrics.forEach((r) => byMetric.set(r.metric, r));

  const flatRows = TSMC_ROW_GROUPS.flatMap((g) => g.rows);

  // Format one cell's value according to the row's intended fmt.
  const formatCell = (rd: TSMCRowDef, v: number | null | undefined): string => {
    if (v == null || Number.isNaN(v)) return "—";
    switch (rd.fmt) {
      case "%":   return `${fmtNum(v, 1)}%`;
      case "$":   return fmtNum(v, 2);
      case "kpcs":return fmtNum(v, 0);
      case "M":
      default:    return fmtNum(v, 2);
    }
  };

  return (
    <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
      <div className="flex items-center justify-between px-6 py-3 border-b border-slate-100 bg-slate-50/80">
        <span className="text-xs font-semibold text-slate-700">
          Quarterly Financial Data{" "}
          <span className="text-slate-400 font-normal">(NT$ Billions unless noted)</span>
        </span>
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1">
            {[8, 12, 20, 40, 60].map((n) => (
              <button key={n}
                onClick={() => setQuarters(n)}
                className={`h-6 px-2 rounded text-[10px] font-semibold ${
                  quarters === n ? "bg-slate-900 text-white" : "text-slate-500 hover:bg-slate-100"
                }`}
              >{n}Q</button>
            ))}
          </div>
          <span className="text-[10px] font-mono text-slate-400">
            2330.TW · {data.periods.length} quarters
          </span>
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="text-xs w-full">
          <thead>
            <tr className="border-b border-slate-200">
              <th className="sticky left-0 z-30 bg-white text-left px-4 py-2 text-[10px] font-bold text-slate-500 uppercase tracking-wider w-44 min-w-[176px] border-r border-slate-200 align-bottom shadow-[4px_0_6px_-4px_rgba(15,23,42,0.08)]">
                Metric
              </th>
              {data.periods.map((p) => (
                <th key={p} className="px-3 py-2 text-right text-[10px] font-bold text-slate-600 whitespace-nowrap min-w-[88px]">
                  {p}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {flatRows.map((rd, ri) => {
              // Detect first row of each group to draw a thicker top border —
              // matches the NVDA table's group-divider look.
              const firstOfGroup = ri > 0 && TSMC_ROW_GROUPS.some((g) => g.rows[0]?.metric === rd.metric);
              const stripe  = ri % 2 === 0 ? "bg-white" : "bg-slate-50";
              const labelPadding = rd.derived ? "pl-12 pr-4" : rd.indent === 1 ? "pl-8 pr-4" : "px-4";
              const row = byMetric.get(rd.metric);
              return (
                <tr key={rd.metric}
                    className={`group border-b border-slate-50 ${firstOfGroup ? "border-t border-slate-200" : ""} ${stripe} hover:!bg-indigo-50/60 transition-colors`}>
                  <td className={`sticky left-0 z-10 ${stripe} group-hover:!bg-indigo-50/60 ${labelPadding} py-1.5 border-r border-slate-200 whitespace-nowrap shadow-[4px_0_6px_-4px_rgba(15,23,42,0.08)]`}>
                    <span className={`${rd.derived ? "text-[10px] italic text-slate-500" : "text-[11px] text-slate-700"} ${rd.bold ? "font-semibold text-slate-900" : ""}`}>
                      {rd.label}
                    </span>
                  </td>
                  {data.periods.map((p) => {
                    const v = row ? (row[p] as number | null) : null;
                    return (
                      <td key={p}
                          className={`px-3 py-1.5 text-right tabular-nums whitespace-nowrap ${rd.derived ? "text-[10px] italic text-slate-500" : rd.bold ? "text-[11px] font-semibold text-slate-900" : "text-[11px] text-slate-700"}`}>
                        {formatCell(rd, v)}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab 2 — Generic segment view (Tech / Platform / Geography)
// ---------------------------------------------------------------------------

function SegmentTab({ metric, title, unit, colorByOrder }: { metric: string; title: string; unit: string; colorByOrder?: boolean }) {
  const [data, setData] = useState<TSMCSegments | null>(null);
  const [loading, setLoading] = useState(true);
  const [quarters, setQuarters] = useState(20);

  useEffect(() => {
    setLoading(true);
    tsmcClient.segments(metric, quarters)
      .then(setData)
      .finally(() => setLoading(false));
  }, [metric, quarters]);

  // API returns periods newest-first (table convention); chart x-axis
  // wants oldest-first so the line reads left→right chronologically.
  const chartData = useMemo(() => {
    if (!data) return [];
    return [...data.periods].reverse().map((p) => {
      const row: Record<string, string | number | null> = { period: p };
      data.rows.forEach((r) => { row[r.dimension] = r[p] as number | null; });
      return row;
    });
  }, [data]);

  if (loading) return <SectionLoading />;
  if (!data || !data.rows.length) return <Empty>No segment data for {metric}.</Empty>;

  // Order dimensions by latest-period value (already sorted server-side)
  const dims = data.rows.map((r) => r.dimension);
  // Color palette — distinct, indexed
  const COLORS = ["#2563eb","#dc2626","#16a34a","#ea580c","#9333ea","#0891b2","#ca8a04","#db2777","#475569","#65a30d","#7c3aed"];

  return (
    <div className="space-y-4">
      <div className="bg-white border border-slate-200 rounded-lg shadow-sm">
        <div className="flex items-center justify-between px-4 py-2 border-b border-slate-200">
          <h3 className="text-sm font-bold text-slate-900">{title}</h3>
          <div className="flex items-center gap-1 text-xs">
            {[8, 16, 24, 40, 60].map((n) => (
              <button key={n}
                onClick={() => setQuarters(n)}
                className={`h-6 px-2 rounded text-[11px] ${
                  quarters === n ? "bg-slate-900 text-white" : "text-slate-500 hover:bg-slate-100"
                }`}
              >{n}Q</button>
            ))}
          </div>
        </div>
        <div className="p-4">
          <ResponsiveContainer width="100%" height={300}>
            <LineChart data={chartData} margin={{ left: 0, right: 16, top: 8, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              <XAxis dataKey="period" tick={{ fontSize: 10, fill: "#64748b" }} />
              <YAxis tick={{ fontSize: 10, fill: "#64748b" }} unit={unit === "%" ? "%" : ""} />
              <Tooltip contentStyle={{ fontSize: 11 }} />
              <Legend wrapperStyle={{ fontSize: 10 }} />
              {dims.map((d, i) => (
                <Line key={d} type="monotone" dataKey={d} stroke={COLORS[i % COLORS.length]}
                      strokeWidth={2} dot={false} connectNulls />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
        <div className="overflow-x-auto border-t border-slate-200">
          <table className="w-full text-xs border-collapse">
            <thead className="bg-slate-50">
              <tr className="border-b border-slate-200">
                <th className="text-left px-3 py-2 font-semibold text-slate-700 sticky left-0 bg-slate-50">Dimension</th>
                {data.periods.map((p) => (
                  <th key={p} className="text-right px-2 py-2 font-semibold text-slate-700 whitespace-nowrap">{p}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.rows.map((r) => (
                <tr key={r.dimension} className="border-b border-slate-100 hover:bg-slate-50">
                  <td className="px-3 py-1.5 font-medium text-slate-700 sticky left-0 bg-white whitespace-nowrap">{r.dimension}</td>
                  {data.periods.map((p) => {
                    const v = r[p] as number | null;
                    return (
                      <td key={p} className="px-2 py-1.5 text-right tabular-nums whitespace-nowrap text-slate-700">
                        {v == null ? "—" : `${fmtNum(v, 1)}%`}
                      </td>
                    );
                  })}
                </tr>
              ))}
              {/* Total row — sum each column, color-flag if it drifts beyond
                  ±1pp from 100%. Confirms TSMC's published rounding plus
                  catches parser bugs (missed rows show up as <100%). */}
              <tr className="border-t-2 border-slate-300 bg-slate-50">
                <td className="px-3 py-1.5 font-bold text-slate-800 sticky left-0 bg-slate-50 whitespace-nowrap">Total</td>
                {data.periods.map((p) => {
                  let sum = 0;
                  let any = false;
                  for (const r of data.rows) {
                    const v = r[p] as number | null;
                    if (v != null) { sum += v; any = true; }
                  }
                  if (!any) {
                    return <td key={p} className="px-2 py-1.5 text-right text-slate-300">—</td>;
                  }
                  const drift = Math.abs(sum - 100);
                  const color =
                    drift <= 1 ? "text-slate-800"
                    : drift <= 2 ? "text-amber-600"
                    : "text-rose-600 font-semibold";
                  return (
                    <td key={p} className={`px-2 py-1.5 text-right tabular-nums whitespace-nowrap font-semibold ${color}`}
                        title={drift > 1 ? `Sum drifts ${drift.toFixed(1)}pp from 100% — likely missing segment or parser issue` : undefined}>
                      {`${fmtNum(sum, 1)}%`}
                    </td>
                  );
                })}
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab 3 — Platform / Geography (two stacked SegmentTabs)
// ---------------------------------------------------------------------------

function PlatformGeoTab() {
  return (
    <div className="space-y-4">
      <div className="bg-amber-50 border border-amber-200 rounded-md px-3 py-2 text-[11px] text-amber-800">
        <strong>Scope note:</strong> TSMC's quarterly management report only publishes
        revenue <em>percentage</em> by Platform and Geography (and by Technology
        node). It does <em>not</em> disclose dollar amounts, operating income,
        or margins broken down by segment. The 3 share-percentage tables
        below are everything TSMC reports at the segment level.
      </div>
      <SegmentTab metric="revenue_share_by_platform"   title="Net Revenue by Platform"   unit="%" />
      <SegmentTab metric="revenue_share_by_geography"  title="Net Revenue by Geography"  unit="%" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab 4 — Guidance vs Actual
// ---------------------------------------------------------------------------

function GuidanceTab() {
  const [rows, setRows] = useState<TSMCGuidanceRow[]>([]);
  const [forward, setForward] = useState<TSMCForwardGuidance | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    Promise.all([tsmcClient.guidance(60), tsmcClient.forwardGuidance()])
      .then(([g, fwd]) => { setRows(g.rows); setForward(fwd); })
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <SectionLoading />;

  const metricSpec: { key: string; title: string; unit_label: string; digits: number; }[] = [
    { key: "revenue",          title: "Revenue (US$ B)",  unit_label: "US$ B", digits: 2 },
    { key: "gross_margin",     title: "Gross Margin (%)", unit_label: "%",     digits: 1 },
    { key: "operating_margin", title: "Operating Margin (%)", unit_label: "%", digits: 1 },
  ];

  return (
    <div className="space-y-4">
      {/* Forward guidance card */}
      {forward && forward.rows.length > 0 && (
        <div className="bg-indigo-50 border border-indigo-200 rounded-lg p-4">
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-sm font-bold text-indigo-900">
              Forward guidance for {forward.for_period}  <span className="text-[11px] font-normal text-indigo-700">issued {forward.issued_at}</span>
            </h3>
          </div>
          <div className="grid grid-cols-4 gap-3 text-xs">
            {(["revenue", "gross_margin", "operating_margin", "usd_ntd_avg_rate"] as const).map((m) => {
              const lo = forward.rows.find((r) => r.metric === m && r.bound === "low");
              const hi = forward.rows.find((r) => r.metric === m && r.bound === "high");
              const pt = forward.rows.find((r) => r.metric === m && r.bound === "point");
              const unit = (lo ?? hi ?? pt)?.unit ?? "";
              const label = m === "revenue" ? "Revenue" : m === "gross_margin" ? "Gross Margin" : m === "operating_margin" ? "Op Margin" : "USD/NTD";
              return (
                <div key={m}>
                  <div className="text-[10px] uppercase tracking-wide text-indigo-700">{label}</div>
                  <div className="text-base font-bold text-indigo-900 mt-0.5">
                    {pt
                      ? `${fmtNum(pt.value, 2)}`
                      : (lo && hi
                          ? `${fmtNum(lo.value, 1)} – ${fmtNum(hi.value, 1)}${unit === "pct" ? "%" : ""}`
                          : "—")}
                  </div>
                  <div className="text-[10px] text-indigo-600">{fmtUnit(unit)}</div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Per-metric historical table */}
      {metricSpec.map((spec) => {
        const subset = rows.filter((r) => r.metric === spec.key);
        if (!subset.length) return null;
        const tally = subset.reduce(
          (acc, r) => {
            if (r.outcome === "BEAT high") acc.beat += 1;
            else if (r.outcome === "MISS low") acc.miss += 1;
            else if (r.outcome === "in range") acc.in_range += 1;
            return acc;
          },
          { beat: 0, miss: 0, in_range: 0 },
        );
        return (
          <div key={spec.key} className="bg-white border border-slate-200 rounded-lg shadow-sm">
            <div className="flex items-center justify-between px-4 py-2 border-b border-slate-200 bg-slate-50/80">
              <h3 className="text-sm font-bold text-slate-900">
                {spec.title} <span className="text-slate-400 font-normal">· guidance vs actual</span>
              </h3>
              <div className="flex items-center gap-3 text-[11px]">
                <span className="text-emerald-600 font-semibold">Beat: {tally.beat}</span>
                <span className="text-slate-500 font-semibold">In range: {tally.in_range}</span>
                <span className={`font-semibold ${tally.miss > 0 ? "text-rose-600" : "text-slate-400"}`}>Miss: {tally.miss}</span>
                <span className="text-slate-400 font-mono">· {subset.length} qtr</span>
              </div>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="bg-slate-50">
                  <tr className="border-b border-slate-200">
                    <th className="text-left  px-3 py-2 font-semibold text-slate-700 w-20">Period</th>
                    <th className="text-right px-2 py-2 font-semibold text-slate-700">Actual</th>
                    <th className="text-right px-2 py-2 font-semibold text-slate-500">Guide low</th>
                    <th className="text-right px-2 py-2 font-semibold text-slate-500">Mid-point</th>
                    <th className="text-right px-2 py-2 font-semibold text-slate-500">Guide high</th>
                    <th className="text-center px-2 py-2 font-semibold text-slate-700">Outcome</th>
                    <th className="text-right px-2 py-2 font-semibold text-slate-700">vs Mid (%)</th>
                    <th className="text-right px-2 py-2 font-semibold text-slate-700">vs High (%)</th>
                  </tr>
                </thead>
                <tbody>
                  {subset.map((r) => {
                    const pctClass = (v: number | null) =>
                      v == null ? "text-slate-300"
                      : v > 0.5  ? "text-emerald-600 font-semibold"
                      : v < -0.5 ? "text-rose-600 font-semibold"
                      : "text-slate-500";
                    const fmtPct = (v: number | null) =>
                      v == null ? "—" : `${v >= 0 ? "+" : ""}${fmtNum(v, 1)}%`;
                    return (
                      <tr key={r.period_label} className="border-b border-slate-100 hover:bg-slate-50">
                        <td className="px-3 py-1.5 font-semibold text-slate-800">{r.period_label}</td>
                        <td className="px-2 py-1.5 text-right tabular-nums font-bold text-slate-900">{fmtNum(r.actual,     spec.digits)}</td>
                        <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">          {fmtNum(r.guide_low,  spec.digits)}</td>
                        <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">          {fmtNum(r.guide_mid,  spec.digits)}</td>
                        <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">          {fmtNum(r.guide_high, spec.digits)}</td>
                        <td className={`px-2 py-1.5 text-center text-[11px] font-semibold ${
                          r.outcome === "BEAT high" ? "text-emerald-600" :
                          r.outcome === "MISS low"  ? "text-rose-600" :
                          r.outcome === "in range"  ? "text-slate-500" : "text-slate-300"
                        }`}>{r.outcome ?? "—"}</td>
                        <td className={`px-2 py-1.5 text-right tabular-nums ${pctClass(r.vs_mid_pct)}`}>{fmtPct(r.vs_mid_pct)}</td>
                        <td className={`px-2 py-1.5 text-right tabular-nums ${pctClass(r.vs_high_pct)}`}>{fmtPct(r.vs_high_pct)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab 5 — Transcripts (list + expand + search)
// ---------------------------------------------------------------------------

function TranscriptsTab() {
  const [quarters, setQuarters] = useState<TSMCTranscriptQuarter[]>([]);
  const [openPeriod, setOpenPeriod] = useState<string | null>(null);
  const [turns, setTurns] = useState<TSMCTranscriptTurn[]>([]);
  const [loadingQ, setLoadingQ] = useState(true);
  const [loadingT, setLoadingT] = useState(false);
  const [query, setQuery] = useState("");
  const [matches, setMatches] = useState<TSMCTranscriptMatch[]>([]);
  const [searching, setSearching] = useState(false);

  useEffect(() => {
    tsmcClient.transcriptQuarters().then((d) => setQuarters(d.quarters)).finally(() => setLoadingQ(false));
  }, []);

  useEffect(() => {
    if (!openPeriod) { setTurns([]); return; }
    setLoadingT(true);
    tsmcClient.transcriptTurns(openPeriod).then((d) => setTurns(d.turns)).finally(() => setLoadingT(false));
  }, [openPeriod]);

  const runSearch = async () => {
    if (!query.trim() || query.trim().length < 2) { setMatches([]); return; }
    setSearching(true);
    try {
      const r = await tsmcClient.transcriptSearch(query.trim(), 50);
      setMatches(r.matches);
    } finally { setSearching(false); }
  };

  return (
    <div className="space-y-4">
      {/* Search bar */}
      <div className="bg-white border border-slate-200 rounded-lg shadow-sm p-3">
        <form onSubmit={(e) => { e.preventDefault(); runSearch(); }} className="flex items-center gap-2">
          <Search size={14} className="text-slate-400" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search transcripts (e.g. '2-nanometer', 'AI accelerator', 'capex')"
            className="flex-1 h-8 px-2 text-sm border border-slate-200 rounded focus:border-slate-400 outline-none"
          />
          <button type="submit" disabled={searching}
            className="h-8 px-3 text-xs font-semibold bg-slate-900 text-white rounded hover:bg-slate-800 disabled:opacity-50">
            {searching ? "Searching…" : "Search"}
          </button>
        </form>
        {matches.length > 0 && (
          <div className="mt-3 max-h-96 overflow-y-auto border-t border-slate-100 pt-2">
            <div className="text-[11px] text-slate-500 mb-2">{matches.length} matches:</div>
            {matches.map((m, i) => (
              <div key={i} className="px-2 py-2 hover:bg-slate-50 border-b border-slate-100">
                <div className="text-[11px] flex items-center gap-2">
                  <span className="font-semibold text-slate-700">{m.period_label}</span>
                  <span className={`px-1.5 py-0.5 rounded text-[10px] ${m.section === "qa" ? "bg-amber-100 text-amber-700" : "bg-blue-100 text-blue-700"}`}>{m.section}</span>
                  <span className="text-slate-600">{m.speaker_name}</span>
                  <span className="text-slate-400">— {m.speaker_role}</span>
                </div>
                <div className="text-xs text-slate-700 mt-1 leading-relaxed">{m.snippet}</div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Quarter list */}
      <div className="bg-white border border-slate-200 rounded-lg shadow-sm">
        <div className="px-4 py-2 border-b border-slate-200">
          <h3 className="text-sm font-bold text-slate-900">Earnings call transcripts ({quarters.length} quarters)</h3>
        </div>
        {loadingQ ? <SectionLoading /> : (
          <div className="divide-y divide-slate-100">
            {quarters.map((q) => (
              <div key={q.period_label}>
                <button onClick={() => setOpenPeriod(openPeriod === q.period_label ? null : q.period_label)}
                  className="w-full text-left px-4 py-2 flex items-center gap-3 hover:bg-slate-50">
                  {openPeriod === q.period_label ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  <span className="font-semibold text-sm text-slate-800 w-16">{q.period_label}</span>
                  <span className="text-xs text-slate-500 w-32">{q.event_date}</span>
                  <span className="text-xs text-slate-600">{q.turns} turns · {(q.chars / 1000).toFixed(0)}k chars</span>
                </button>
                {openPeriod === q.period_label && (
                  <div className="px-6 pb-4 bg-slate-50 max-h-[600px] overflow-y-auto">
                    {loadingT ? <SectionLoading /> : (
                      <div className="space-y-3 pt-2">
                        {turns.map((t) => (
                          <div key={t.turn_index} className="text-xs">
                            <div className="flex items-center gap-2 mb-1">
                              <span className={`px-1.5 py-0.5 rounded text-[10px] ${t.section === "qa" ? "bg-amber-100 text-amber-700" : "bg-blue-100 text-blue-700"}`}>{t.section}</span>
                              <span className="font-semibold text-slate-800">{t.speaker_name}</span>
                              <span className="text-slate-500 text-[11px]">{t.speaker_role}</span>
                            </div>
                            <div className="text-slate-700 leading-relaxed whitespace-pre-wrap pl-2 border-l-2 border-slate-200">{t.text}</div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab 6 — PDF catalog
// ---------------------------------------------------------------------------

function PDFsTab() {
  const [catalog, setCatalog] = useState<TSMCPDFCatalog | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    tsmcClient.pdfs().then(setCatalog).finally(() => setLoading(false));
  }, []);

  if (loading) return <SectionLoading />;
  if (!catalog || Object.keys(catalog.quarters).length === 0) return <Empty>No PDF catalog.</Empty>;

  // Sort quarters in reverse chronological
  const yqs = Object.keys(catalog.quarters).sort().reverse();
  return (
    <div className="bg-white border border-slate-200 rounded-lg shadow-sm">
      <div className="px-4 py-2 border-b border-slate-200">
        <h3 className="text-sm font-bold text-slate-900">PDF catalog · {Object.keys(catalog.quarters).length} quarters</h3>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="bg-slate-50">
            <tr className="border-b border-slate-200">
              <th className="text-left px-3 py-2">Quarter</th>
              <th className="text-left px-3 py-2">Mgmt Report</th>
              <th className="text-left px-3 py-2">Earnings Release</th>
              <th className="text-left px-3 py-2">Presentation</th>
              <th className="text-left px-3 py-2">Transcript</th>
              <th className="text-left px-3 py-2">Financial Stmts</th>
            </tr>
          </thead>
          <tbody>
            {yqs.map((yq) => {
              const info = catalog.quarters[yq];
              const findT = (t: string) => info.pdfs.find((p) => p.type === t);
              const cells = ["management_report","earnings_release","presentation","transcript","financial_statements"];
              return (
                <tr key={yq} className="border-b border-slate-100 hover:bg-slate-50">
                  <td className="px-3 py-1.5 font-semibold text-slate-700 whitespace-nowrap">{yq}</td>
                  {cells.map((t) => {
                    const p = findT(t);
                    return (
                      <td key={t} className="px-3 py-1.5">
                        {p ? (
                          <a href={p.url} target="_blank" rel="noopener" className="inline-flex items-center gap-1 text-indigo-600 hover:underline">
                            <FileText size={11} /> link <ExternalLink size={9} />
                          </a>
                        ) : <span className="text-slate-300">—</span>}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function SectionLoading() {
  return (
    <div className="bg-white border border-slate-200 rounded-lg shadow-sm p-12 flex items-center justify-center text-slate-400 text-sm">
      <Loader2 size={16} className="animate-spin mr-2" /> Loading…
    </div>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-white border border-slate-200 rounded-lg shadow-sm p-8 text-center text-slate-500 text-sm">
      {children}
    </div>
  );
}
