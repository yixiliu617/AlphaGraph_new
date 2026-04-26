"use client";

/**
 * MediaTekPanel — review what the MediaTek quarterly press-release ingestion
 * has captured. Two sub-tabs:
 *   1. Financials — wide pivot of P&L (NT$ M, 18 metrics × up to 40 quarters)
 *   2. Coverage   — period coverage table
 *
 * No segments tab (MediaTek doesn't publish them in the press release).
 * No transcripts tab (different format from TSMC's LSEG; not yet ingested).
 */

import { useEffect, useState } from "react";
import { Loader2, BarChart2, FileText } from "lucide-react";
import {
  mediatekClient,
  type MediaTekSummary,
  type MediaTekFinancialsWide,
  type MediaTekMetricRow,
  type MediaTekQuarter,
} from "@/lib/api/mediatekClient";

type SubTab = "financials" | "quarters";

const SUBTABS: { key: SubTab; label: string; icon: React.ReactNode }[] = [
  { key: "financials", label: "Financials", icon: <BarChart2 size={14} /> },
  { key: "quarters",   label: "Coverage",   icon: <FileText size={14} /> },
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
        {tab === "financials" && <FinancialsTab />}
        {tab === "quarters"   && <QuartersTab />}
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
