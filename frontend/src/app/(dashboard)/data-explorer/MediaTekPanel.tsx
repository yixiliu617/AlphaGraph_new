"use client";

/**
 * MediaTekPanel — review what the MediaTek quarterly press-release ingestion
 * has captured. Sub-tabs:
 *   1. Financials — wide pivot of P&L (NT$ M, 18 metrics × up to 40 quarters)
 *   2. Materials  — full catalog of every IR PDF (Press Release, Presentation,
 *                  Transcript, Financial Statements, upcoming Earnings Call
 *                  Invitation, plus TWSE Consolidated/Unconsolidated reports)
 *   3. Coverage   — period coverage table for the silver layer
 *
 * No segments tab (MediaTek doesn't publish them in the press release).
 * No transcripts-text tab yet (transcript is published as PDF; viewer/search
 * UI not built yet — for now the Materials tab links to the source).
 */

import { useEffect, useState } from "react";
import { Loader2, BarChart2, FileText, ExternalLink, FileBarChart, FileSearch, Mic, Calendar, Files, TrendingUp, Search, ChevronDown, ChevronRight, LineChart as LineChartIcon } from "lucide-react";
import PricesTab from "./PricesTab";
import {
  mediatekClient,
  type MediaTekSummary,
  type MediaTekFinancialsWide,
  type MediaTekMetricRow,
  type MediaTekQuarter,
  type MediaTekPDFCatalog,
  type MediaTekPDFEntry,
  type MediaTekTranscriptQuarter,
  type MediaTekTranscriptTurn,
  type MediaTekTranscriptMatch,
  type MediaTekGuidanceRow,
  type MediaTekSourceIssue,
} from "@/lib/api/mediatekClient";

type SubTab = "prices" | "financials" | "guidance" | "transcripts" | "materials" | "quarters";

const SUBTABS: { key: SubTab; label: string; icon: React.ReactNode }[] = [
  { key: "prices",      label: "Prices",      icon: <LineChartIcon size={14} /> },
  { key: "financials",  label: "Financials",  icon: <BarChart2 size={14} /> },
  { key: "guidance",    label: "Guidance",    icon: <TrendingUp size={14} /> },
  { key: "transcripts", label: "Transcripts", icon: <Mic size={14} /> },
  { key: "materials",   label: "Materials",   icon: <Files size={14} /> },
  { key: "quarters",    label: "Coverage",    icon: <FileText size={14} /> },
];

function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return "—";
  if (Math.abs(v) >= 1000) return v.toLocaleString(undefined, { maximumFractionDigits: 0 });
  return v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

type FinFmt = "M" | "%" | "$";
interface MTKRowDef {
  label: string; metric: string; fmt: FinFmt;
  bold?: boolean; derived?: boolean; indent?: 1 | 2;
}
interface MTKRowGroup { heading: string; rows: MTKRowDef[] }

const MTK_ROW_GROUPS: MTKRowGroup[] = [
  { heading: "Revenue", rows: [
    { label: "Net Revenue",       metric: "net_revenue",        fmt: "M", bold: true },
  ]},
  { heading: "Gross Profit", rows: [
    { label: "Cost of Revenue",   metric: "cost_of_revenue",    fmt: "M", indent: 1 },
    { label: "Gross Profit",      metric: "gross_profit",       fmt: "M", bold: true },
    { label: "Gross Margin %",    metric: "gross_margin",       fmt: "%", derived: true },
  ]},
  { heading: "Operating Expenses", rows: [
    { label: "Selling Expenses",  metric: "selling_expenses",   fmt: "M", indent: 1 },
    { label: "G&A Expenses",      metric: "g_and_a",            fmt: "M", indent: 1 },
    { label: "R&D Expenses",      metric: "r_and_d",            fmt: "M", indent: 1 },
    { label: "Total OpEx",        metric: "operating_expenses", fmt: "M", bold: true },
  ]},
  { heading: "Operating Income", rows: [
    { label: "Operating Income",  metric: "operating_income",   fmt: "M", bold: true },
    { label: "Op Margin %",       metric: "operating_margin",   fmt: "%", derived: true },
  ]},
  { heading: "Net Income", rows: [
    { label: "Non-Op Inc/Exp",    metric: "non_operating_items",fmt: "M", indent: 1 },
    { label: "Pre-Tax Income",    metric: "net_income_before_tax", fmt: "M", indent: 1 },
    { label: "Income Tax",        metric: "income_tax_expense", fmt: "M", indent: 1 },
    { label: "Net Income",        metric: "net_income",         fmt: "M", bold: true },
    { label: "  to Parent",       metric: "net_income_attributable", fmt: "M", indent: 1 },
    { label: "Net Margin %",      metric: "net_profit_margin",  fmt: "%", derived: true },
    { label: "EPS (NT$ / share)", metric: "eps",                fmt: "$" },
  ]},
  { heading: "Cash Flow", rows: [
    { label: "Operating CF",      metric: "operating_cash_flow",fmt: "M", bold: true },
  ]},
];

export default function MediaTekPanel() {
  const [tab, setTab] = useState<SubTab>("financials");
  const [summary, setSummary] = useState<MediaTekSummary | null>(null);
  const [summaryErr, setSummaryErr] = useState<string | null>(null);

  useEffect(() => {
    mediatekClient.summary()
      .then(setSummary)
      .catch((e) => setSummaryErr(e instanceof Error ? e.message : "load failed"));
  }, []);

  return (
    <div className="space-y-4">
      <div className="bg-white border border-slate-200 rounded-lg shadow-sm p-5">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h2 className="text-lg font-bold text-slate-900">MediaTek (2454.TW) — Ingested Data</h2>
            <p className="text-xs text-slate-500 mt-0.5">
              Long-format silver layer extracted from quarterly Press Release PDFs
            </p>
          </div>
        </div>
        {summaryErr && (
          <div className="text-xs text-rose-600 bg-rose-50 px-3 py-2 rounded">
            Error loading summary: {summaryErr}
          </div>
        )}
        {summary && (
          <div className="grid grid-cols-3 gap-4 text-xs">
            <Stat label="Quarterly facts"
                  value={`${summary.layers.quarterly_facts?.rows.toLocaleString() ?? 0}`}
                  detail={`${summary.layers.quarterly_facts?.metrics ?? 0} metrics × ${summary.layers.quarterly_facts?.periods ?? 0} periods`}
                  range={`${summary.layers.quarterly_facts?.earliest_period_end?.slice(0,7)} → ${summary.layers.quarterly_facts?.latest_period_end?.slice(0,7)}`} />
            <Stat label="Source reports"
                  value={`${summary.layers.quarterly_facts?.source_reports ?? 0}`}
                  detail="Each report covers 3 periods"
                  range="Quarterly press releases" />
            <Stat label="Coverage gaps"
                  value="None"
                  detail="No transcripts ingested yet"
                  range="Segments not in press release" />
          </div>
        )}
      </div>

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

      <div>
        {tab === "prices"      && <PricesTab ticker="2454.TW" currency="TWD" />}
        {tab === "financials"  && <FinancialsTab />}
        {tab === "guidance"    && <GuidanceTab />}
        {tab === "transcripts" && <TranscriptsTab />}
        {tab === "materials"   && <MaterialsTab />}
        {tab === "quarters"    && <QuartersTab />}
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

function FinancialsTab() {
  const [data, setData] = useState<MediaTekFinancialsWide | null>(null);
  const [quarters, setQuarters] = useState(20);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    mediatekClient.financialsWide(quarters)
      .then(setData)
      .finally(() => setLoading(false));
  }, [quarters]);

  if (loading) return <SectionLoading />;
  if (!data || !data.metrics.length) return <Empty>No financials data.</Empty>;

  const byMetric = new Map<string, MediaTekMetricRow>();
  data.metrics.forEach((r) => byMetric.set(r.metric, r));
  const flatRows = MTK_ROW_GROUPS.flatMap((g) => g.rows);

  const formatCell = (rd: MTKRowDef, v: number | null | undefined): string => {
    if (v == null || Number.isNaN(v)) return "—";
    switch (rd.fmt) {
      case "%":   return `${fmtNum(v, 1)}%`;
      case "$":   return fmtNum(v, 2);
      case "M":
      default:    return fmtNum(v, 0);
    }
  };

  return (
    <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
      <div className="flex items-center justify-between px-6 py-3 border-b border-slate-100 bg-slate-50/80">
        <span className="text-xs font-semibold text-slate-700">
          Quarterly Financial Data{" "}
          <span className="text-slate-400 font-normal">(NT$ Millions unless noted)</span>
        </span>
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1">
            {[8, 12, 20, 40].map((n) => (
              <button key={n}
                onClick={() => setQuarters(n)}
                className={`h-6 px-2 rounded text-[10px] font-semibold ${
                  quarters === n ? "bg-slate-900 text-white" : "text-slate-500 hover:bg-slate-100"
                }`}
              >{n}Q</button>
            ))}
          </div>
          <span className="text-[10px] font-mono text-slate-400">
            2454.TW · {data.periods.length} quarters
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
              const firstOfGroup = ri > 0 && MTK_ROW_GROUPS.some((g) => g.rows[0]?.metric === rd.metric);
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
// Guidance tab — forward guidance card + per-metric historical tables.
// Same pattern as TSMC/UMC; rule documented in
// .claude/skills/guidance-tab-pattern/SKILL.md.
// ---------------------------------------------------------------------------

function GuidanceTab() {
  const [rows, setRows] = useState<MediaTekGuidanceRow[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    mediatekClient.guidance(20)
      .then((d) => setRows(d.rows))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <SectionLoading />;
  if (!rows.length) return <Empty>No guidance data.</Empty>;

  const byMetric: Record<string, MediaTekGuidanceRow[]> = {};
  rows.forEach((r) => { (byMetric[r.metric] = byMetric[r.metric] || []).push(r); });

  // Forward card: latest issuing report's view
  const latestIssued = rows[0]?.issued_in_period;
  const forwardRows = latestIssued ? rows.filter((r) => r.issued_in_period === latestIssued) : [];
  const forwardByMetric = new Map<string, MediaTekGuidanceRow>();
  forwardRows.forEach((r) => { if (!forwardByMetric.has(r.metric)) forwardByMetric.set(r.metric, r); });

  const FORWARD_CARD_ORDER: Array<[string, string]> = [
    ["guidance_revenue",          "Revenue (NT$ B)"],
    ["guidance_gross_margin",     "Gross Margin"],
    ["guidance_usd_ntd_avg_rate", "USD/NTD"],
  ];

  const fmtForward = (r: MediaTekGuidanceRow): string => {
    if (r.metric === "guidance_revenue" && r.guide_low != null && r.guide_high != null) {
      return `${r.guide_low.toFixed(1)}–${r.guide_high.toFixed(1)}B`;
    }
    if (r.metric === "guidance_gross_margin" && r.guide_low != null && r.guide_high != null) {
      return `${r.guide_low.toFixed(1)}–${r.guide_high.toFixed(1)}%`;
    }
    if (r.guide_point != null) return r.guide_point.toFixed(2);
    return "—";
  };

  const METRIC_TITLES: Record<string, string> = {
    "guidance_revenue":          "Revenue (NT$ B) — guidance vs actual",
    "guidance_gross_margin":     "Gross Margin (%) — guidance vs actual",
    "guidance_usd_ntd_avg_rate": "USD/NTD forecast — verbal only (realized FX not in our silver yet)",
  };

  const fmtVal = (v: number | null | undefined, digits = 1, suffix = "%") =>
    v == null ? "—" : `${v.toFixed(digits)}${suffix}`;

  const outcomeClass = (o: string | null) =>
    o === "BEAT high" ? "bg-emerald-100 text-emerald-700"
    : o === "MISS low" ? "bg-rose-100 text-rose-700"
    : o === "in range" ? "bg-slate-100 text-slate-600"
    : "text-slate-300";

  return (
    <div className="space-y-4">
      {forwardByMetric.size > 0 && (
        <div className="bg-indigo-50 border border-indigo-200 rounded-lg p-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-bold text-indigo-900">
              Forward guidance{" "}
              <span className="text-[11px] font-normal text-indigo-700">
                issued in {latestIssued} earnings call
              </span>
            </h3>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs">
            {FORWARD_CARD_ORDER.map(([metric, label]) => {
              const r = forwardByMetric.get(metric);
              if (!r) return null;
              return (
                <div key={metric} className="bg-white/50 border border-indigo-200/60 rounded p-2">
                  <div className="flex items-baseline justify-between">
                    <div className="text-[10px] uppercase tracking-wide text-indigo-700 font-semibold">{label}</div>
                    <div className="text-[10px] text-indigo-500 font-mono">{r.for_period}</div>
                  </div>
                  <div className="text-base font-bold text-indigo-900 mt-1">
                    {fmtForward(r)}
                  </div>
                  {r.verbal && (
                    <div className="text-[10px] text-indigo-700 mt-0.5 italic line-clamp-2" title={r.verbal}>
                      &ldquo;{r.verbal.replace(/\s+/g, " ").trim()}&rdquo;
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      <div className="bg-amber-50 border border-amber-200 rounded-md px-3 py-2 text-[11px] text-amber-800">
        <strong>Source:</strong> MediaTek issues forward guidance verbally at the end of the
        prepared remarks portion of each earnings call (CFO David Ku reads the
        revenue range, gross margin point ± spread, and forecasted FX rate).
        We extract these structured ranges from the published transcript PDF.
        <strong> Realized actuals</strong>: revenue from the press release table
        (NT$ million → divided by 1000 to compare in NT$ B); gross margin
        derived as gross_profit / net_revenue.
      </div>

      {Object.entries(METRIC_TITLES).map(([metric, title]) => {
        const subset = (byMetric[metric] || []).filter((r) => r.actual != null || r.verbal);
        if (!subset.length) return null;
        const tally = subset.reduce(
          (acc, r) => {
            if (r.outcome === "BEAT high") acc.beat += 1;
            else if (r.outcome === "MISS low") acc.miss += 1;
            else if (r.outcome === "in range") acc.in_range += 1;
            else acc.na += 1;
            return acc;
          },
          { beat: 0, miss: 0, in_range: 0, na: 0 },
        );
        const isPct = metric === "guidance_gross_margin";
        const isB = metric === "guidance_revenue";
        const suffix = isPct ? "%" : isB ? "B" : "";

        return (
          <div key={metric} className="bg-white border border-slate-200 rounded-lg shadow-sm">
            <div className="flex items-center justify-between px-4 py-2 border-b border-slate-200 bg-slate-50/80">
              <h3 className="text-sm font-bold text-slate-900">{title}</h3>
              <div className="flex items-center gap-3 text-[11px]">
                {tally.beat > 0 &&     <span className="text-emerald-600 font-semibold">Beat: {tally.beat}</span>}
                {tally.in_range > 0 && <span className="text-slate-500 font-semibold">In range: {tally.in_range}</span>}
                {tally.miss > 0 &&     <span className="text-rose-600 font-semibold">Miss: {tally.miss}</span>}
                {tally.na > 0 &&       <span className="text-slate-400">Verbal-only: {tally.na}</span>}
                <span className="text-slate-400 font-mono">· {subset.length} qtr</span>
              </div>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="bg-slate-50">
                  <tr className="border-b border-slate-200">
                    <th className="text-left  px-3 py-2 font-semibold text-slate-700 w-20">For period</th>
                    <th className="text-left  px-3 py-2 font-semibold text-slate-500 w-24">Issued in</th>
                    <th className="text-left  px-3 py-2 font-semibold text-slate-700 w-72">Verbal guidance</th>
                    <th className="text-right px-2 py-2 font-semibold text-slate-500">Low</th>
                    <th className="text-right px-2 py-2 font-semibold text-slate-500">Mid</th>
                    <th className="text-right px-2 py-2 font-semibold text-slate-500">High</th>
                    <th className="text-right px-2 py-2 font-semibold text-slate-700">Actual</th>
                    <th className="text-center px-2 py-2 font-semibold text-slate-700">Outcome</th>
                    <th className="text-right px-2 py-2 font-semibold text-slate-700">vs Mid</th>
                  </tr>
                </thead>
                <tbody>
                  {subset.map((r) => {
                    const ppFmt = (v: number | null) => v == null ? "—"
                      : `${v >= 0 ? "+" : ""}${v.toFixed(2)}${isPct ? "pp" : isB ? "B" : ""}`;
                    const ppClass = (v: number | null) => v == null ? "text-slate-300"
                      : v > 0.5  ? "text-emerald-600 font-semibold"
                      : v < -0.5 ? "text-rose-600 font-semibold"
                      : "text-slate-500";
                    return (
                      <tr key={`${r.issued_in_period}-${r.for_period}-${r.metric}`}
                          className="border-b border-slate-100 hover:bg-slate-50">
                        <td className="px-3 py-1.5 font-semibold text-slate-800">{r.for_period}</td>
                        <td className="px-3 py-1.5 text-slate-500">{r.issued_in_period}</td>
                        <td className="px-3 py-1.5 text-[11px] text-slate-700 max-w-[280px] truncate" title={r.verbal ?? ""}>
                          {(r.verbal ?? "").replace(/\s+/g, " ").trim() || "—"}
                        </td>
                        <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{fmtVal(r.guide_low,  1, suffix)}</td>
                        <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{fmtVal(r.guide_mid,  1, suffix)}</td>
                        <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{fmtVal(r.guide_high, 1, suffix)}</td>
                        <td className="px-2 py-1.5 text-right tabular-nums font-bold text-slate-900">{fmtVal(r.actual, 2, suffix)}</td>
                        <td className="px-2 py-1.5 text-center">
                          {r.outcome ? (
                            <span className={`text-[10px] px-1.5 py-0.5 rounded font-semibold ${outcomeClass(r.outcome)}`}>
                              {r.outcome}
                            </span>
                          ) : <span className="text-slate-300 text-[10px]">verbal</span>}
                        </td>
                        <td className={`px-2 py-1.5 text-right tabular-nums ${ppClass(r.vs_mid_pp)}`}>{ppFmt(r.vs_mid_pp)}</td>
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
// Transcripts tab — quarter list + expand-to-read + full-text search.
// Same shape as TSMC's transcripts tab.
// ---------------------------------------------------------------------------

function TranscriptsTab() {
  const [quarters, setQuarters] = useState<MediaTekTranscriptQuarter[]>([]);
  const [sourceIssues, setSourceIssues] = useState<MediaTekSourceIssue[]>([]);
  const [openPeriod, setOpenPeriod] = useState<string | null>(null);
  const [turns, setTurns] = useState<MediaTekTranscriptTurn[]>([]);
  const [loadingQ, setLoadingQ] = useState(true);
  const [loadingT, setLoadingT] = useState(false);
  const [query, setQuery] = useState("");
  const [matches, setMatches] = useState<MediaTekTranscriptMatch[]>([]);
  const [searching, setSearching] = useState(false);

  useEffect(() => {
    mediatekClient.transcriptQuarters().then((d) => {
      setQuarters(d.quarters);
      setSourceIssues(d.source_issues || []);
    }).finally(() => setLoadingQ(false));
  }, []);

  useEffect(() => {
    if (!openPeriod) { setTurns([]); return; }
    setLoadingT(true);
    mediatekClient.transcriptTurns(openPeriod).then((d) => setTurns(d.turns)).finally(() => setLoadingT(false));
  }, [openPeriod]);

  const runSearch = async () => {
    if (!query.trim() || query.trim().length < 2) { setMatches([]); return; }
    setSearching(true);
    try {
      const r = await mediatekClient.transcriptSearch(query.trim(), 50);
      setMatches(r.matches);
    } finally { setSearching(false); }
  };

  return (
    <div className="space-y-4">
      <div className="bg-white border border-slate-200 rounded-lg shadow-sm p-3">
        <form onSubmit={(e) => { e.preventDefault(); runSearch(); }} className="flex items-center gap-2">
          <Search size={14} className="text-slate-400" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search MediaTek transcripts (e.g. 'data center ASIC', 'Wi-Fi 8', 'NVIDIA')"
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

      {/* Source-issue banner — surfaced when MediaTek's IR site has known
          per-quarter problems (e.g. wrong-file-uploaded-at-source). One
          card per affected quarter. */}
      {sourceIssues.length > 0 && (
        <div className="space-y-2">
          {sourceIssues.map((issue) => (
            <div key={`${issue.period_label}-${issue.file_type}`}
                 className="bg-amber-50 border border-amber-300 rounded-lg p-3">
              <div className="flex items-start gap-2">
                <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-amber-200 text-amber-800 mt-0.5 whitespace-nowrap">
                  {issue.period_label} · SOURCE ISSUE
                </span>
                <div className="text-[11px] text-amber-900 leading-relaxed flex-1">
                  <p className="mb-1.5">{issue.user_facing_message}</p>
                  {issue.evidence?.url ? (
                    <p className="text-[10px] text-amber-700 font-mono break-all mb-1">
                      Affected URL:{" "}
                      <a href={String(issue.evidence.url)} target="_blank" rel="noopener" className="underline hover:text-amber-900">
                        {String(issue.evidence.url).slice(0, 120)}…
                      </a>
                    </p>
                  ) : null}
                  {issue.mitigation?.guidance_fallback && (
                    <p className="text-[10px] text-amber-800">
                      <strong>Fallback:</strong> {issue.mitigation.guidance_fallback}
                    </p>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="bg-white border border-slate-200 rounded-lg shadow-sm">
        <div className="px-4 py-2 border-b border-slate-200">
          <h3 className="text-sm font-bold text-slate-900">Earnings call transcripts ({quarters.length} quarters)</h3>
          <p className="text-[11px] text-slate-500 mt-0.5">
            MediaTek publishes its own English transcript starting 2021Q2. Pre-2021Q2 calls have no transcript.
            {sourceIssues.length > 0 && ` ${sourceIssues.length} quarter(s) flagged with source-side issues — see banners above.`}
          </p>
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
                  <span className="text-xs text-slate-600">{q.turns} turns · {(q.chars / 1000).toFixed(0)}k chars · {q.speakers} speakers</span>
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
// Materials tab — full PDF catalog: every Press Release / Presentation /
// Transcript / Financial Statements / Earnings Call Invitation that
// MediaTek has published, plus TWSE Consolidated/Unconsolidated reports.
//
// MediaTek posts the upcoming earnings call invitation ~3 weeks before the
// call. The most recent quarter often has only the invitation until the
// call itself happens, then the other 4-5 PDFs land. We surface this via
// a yellow "Upcoming call" badge.
// ---------------------------------------------------------------------------

const MATERIAL_COLUMNS: Array<{ type: string; label: string; icon: React.ReactNode; tooltip: string }> = [
  { type: "earnings_call_invitation",       label: "Invite",       icon: <Calendar size={11} />,    tooltip: "Earnings Call Invitation (date / dial-in details)" },
  { type: "press_release",                  label: "Press Rel.",   icon: <FileText size={11} />,    tooltip: "Press Release (headline P&L, prose narrative, Consolidated Income Statement table)" },
  { type: "presentation",                   label: "Presentation", icon: <FileBarChart size={11} />,tooltip: "Investor Presentation (slide deck — revenue charts, guidance, segment mix)" },
  { type: "transcript",                     label: "Transcript",   icon: <Mic size={11} />,         tooltip: "Earnings call transcript (PREPARED REMARKS + Q&A; published since 2021Q2)" },
  { type: "financial_statements",           label: "Statements",   icon: <FileSearch size={11} />,  tooltip: "Financial Statements (full TIFRS income statement, balance sheet, cash flow)" },
  { type: "consolidated_financial_report",  label: "10-Q (Cons.)", icon: <FileText size={11} />,    tooltip: "TWSE-mandated Consolidated Financial Report (full audited statements)" },
];

function MaterialsTab() {
  const [catalog, setCatalog] = useState<MediaTekPDFCatalog | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    mediatekClient.pdfs()
      .then(setCatalog)
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <SectionLoading />;
  if (!catalog || catalog.quarter_count === 0) return <Empty>No PDF catalog.</Empty>;

  // Quarters newest-first per project convention
  const yqs = Object.keys(catalog.quarters).sort().reverse();
  const todayYQ = yqs[0];

  const findPdf = (yq: string, type: string): MediaTekPDFEntry | undefined =>
    catalog.quarters[yq]?.pdfs.find((p) => p.type === type);

  const isUpcoming = (yq: string): boolean => {
    // "Upcoming" = the most recent quarter that has ONLY an invitation
    // (no press release / transcript yet — call hasn't happened)
    const q = catalog.quarters[yq];
    if (!q) return false;
    const types = new Set(q.pdfs.map((p) => p.type));
    return types.has("earnings_call_invitation") && !types.has("press_release") && !types.has("transcript");
  };

  return (
    <div className="space-y-4">
      <div className="bg-indigo-50 border border-indigo-200 rounded-md px-3 py-2 text-[11px] text-indigo-800">
        <strong>Source:</strong> direct anchors from{" "}
        <a href={catalog.index_url} target="_blank" rel="noopener" className="underline font-mono text-[10px]">
          mediatek.com/investor-relations/financial-information
        </a>
        {" "}·{" "}<span className="font-mono text-[10px]">{catalog.quarter_count}</span> quarters indexed
        {" "}·{" "}refreshed <span className="font-mono text-[10px]">{catalog.enumerated_at?.slice(0, 10)}</span>.
        The <strong>upcoming earnings call invitation</strong> typically lands ~3 weeks before the call;
        the other 4-5 PDFs land within hours of the call itself.
      </div>

      <div className="bg-white border border-slate-200 rounded-lg shadow-sm overflow-hidden">
        <div className="px-4 py-2 border-b border-slate-200 bg-slate-50/80">
          <h3 className="text-sm font-bold text-slate-900">
            IR materials catalog
            <span className="text-[11px] font-normal text-slate-500 ml-2">
              · click any cell to open the source PDF in a new tab
            </span>
          </h3>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="bg-slate-50">
              <tr className="border-b border-slate-200">
                <th className="text-left px-3 py-2 font-semibold text-slate-700 sticky left-0 bg-slate-50 w-24">Quarter</th>
                {MATERIAL_COLUMNS.map((col) => (
                  <th key={col.type} className="text-center px-2 py-2 font-semibold text-slate-700" title={col.tooltip}>
                    <div className="flex items-center justify-center gap-1">
                      {col.icon}
                      <span>{col.label}</span>
                    </div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {yqs.map((yq) => {
                const upcoming = isUpcoming(yq);
                return (
                  <tr key={yq} className={`border-b border-slate-100 hover:bg-slate-50 ${upcoming ? "bg-amber-50/40" : ""}`}>
                    <td className="px-3 py-1.5 font-semibold text-slate-800 sticky left-0 bg-white whitespace-nowrap">
                      {yq.replace(/(\d{4})Q(\d)/, "$2Q$1").replace(/Q(\d{4})/, (_, y) => `Q${y.slice(2)}`)}
                      {upcoming && (
                        <span className="ml-2 px-1.5 py-0.5 rounded text-[9px] font-bold bg-amber-200 text-amber-800">
                          UPCOMING
                        </span>
                      )}
                    </td>
                    {MATERIAL_COLUMNS.map((col) => {
                      const pdf = findPdf(yq, col.type);
                      return (
                        <td key={col.type} className="px-2 py-1.5 text-center">
                          {pdf ? (
                            <a href={pdf.url} target="_blank" rel="noopener"
                               className="inline-flex items-center gap-1 text-indigo-600 hover:text-indigo-800 hover:underline">
                              <FileText size={11} />
                              <ExternalLink size={9} />
                            </a>
                          ) : (
                            <span className="text-slate-300">—</span>
                          )}
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
    </div>
  );
}


function QuartersTab() {
  const [quarters, setQuarters] = useState<MediaTekQuarter[]>([]);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    mediatekClient.quarters()
      .then((r) => setQuarters(r.quarters))
      .finally(() => setLoading(false));
  }, []);
  if (loading) return <SectionLoading />;
  if (!quarters.length) return <Empty>No coverage data.</Empty>;

  return (
    <div className="bg-white border border-slate-200 rounded-lg shadow-sm">
      <div className="px-4 py-2 border-b border-slate-200">
        <h3 className="text-sm font-bold text-slate-900">
          Period coverage · {quarters.length} quarters
        </h3>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="bg-slate-50">
            <tr className="border-b border-slate-200">
              <th className="text-left px-3 py-2">Period</th>
              <th className="text-left px-3 py-2">Period end</th>
              <th className="text-right px-3 py-2">Facts</th>
              <th className="text-right px-3 py-2">Distinct metrics</th>
              <th className="text-left px-3 py-2">Source reports</th>
            </tr>
          </thead>
          <tbody>
            {quarters.map((q) => (
              <tr key={q.period_label} className="border-b border-slate-100 hover:bg-slate-50">
                <td className="px-3 py-1.5 font-semibold text-slate-800">{q.period_label}</td>
                <td className="px-3 py-1.5 text-slate-600">{q.period_end}</td>
                <td className="px-3 py-1.5 text-right tabular-nums">{q.fact_count}</td>
                <td className="px-3 py-1.5 text-right tabular-nums">{q.metrics}</td>
                <td className="px-3 py-1.5 text-[11px] text-slate-600">
                  {q.sources.map((s) => s.replace(/^mediatek_press_release_/, "")).join(", ")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

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
