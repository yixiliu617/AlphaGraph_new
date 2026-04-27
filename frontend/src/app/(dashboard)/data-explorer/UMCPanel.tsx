"use client";

/**
 * UMCPanel — review what the UMC quarterly-report ingestion has captured.
 * Sub-tabs:
 *   1. Financials  — wide pivot of headline P&L metrics across quarters (NT$ M)
 *   2. Tech mix    — revenue % by node (UMC: 9 buckets, 14nm-and-below to 0.5um)
 *   3. Geography   — revenue % by region
 *   4. Application — revenue % by end-market (Computer/Communication/Consumer/Others)
 *   5. Customer    — Fabless vs IDM split
 *   6. Quarters    — coverage table (which periods, how many sources contributed each)
 *
 * Differences from TSMCPanel:
 *   - No Transcripts tab (UMC publishes a calendar invitation, not a transcript)
 *   - No Guidance tab (UMC's verbal guidance isn't in tabular form)
 *   - Currency unit is `ntd_m` not `ntd_b` — table header reflects this
 *   - 4 segment dimensions (UMC's Customer Type + Application axes don't exist in TSMC)
 */

import { useEffect, useMemo, useState } from "react";
import { Loader2, Layers, Globe, BarChart2, Users, AppWindow, FileText, Factory, Wallet, Scale, Calendar, TrendingUp, LineChart as LineChartIcon } from "lucide-react";
import PricesTab from "./PricesTab";
import {
  Bar, BarChart, CartesianGrid, ComposedChart, Legend, Line, LineChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import {
  umcClient,
  type UMCSummary,
  type UMCFinancialsWide,
  type UMCMetricRow,
  type UMCSegments,
  type UMCQuarter,
  type UMCCapacity,
  type UMCWide,
  type UMCGuidanceRow,
} from "@/lib/api/umcClient";

type SubTab = "prices" | "financials" | "tech" | "geo" | "application" | "customer"
  | "capacity" | "cashflow" | "balanceSheet" | "annual" | "guidance" | "quarters";

const SUBTABS: { key: SubTab; label: string; icon: React.ReactNode }[] = [
  { key: "prices",       label: "Prices",        icon: <LineChartIcon size={14} /> },
  { key: "financials",   label: "Financials",    icon: <BarChart2 size={14} /> },
  { key: "annual",       label: "Annual",        icon: <Calendar size={14} /> },
  { key: "cashflow",     label: "Cash Flow",     icon: <Wallet size={14} /> },
  { key: "balanceSheet", label: "Balance Sheet", icon: <Scale size={14} /> },
  { key: "tech",         label: "Tech mix",      icon: <Layers size={14} /> },
  { key: "geo",          label: "Geography",     icon: <Globe size={14} /> },
  { key: "application",  label: "Application",   icon: <AppWindow size={14} /> },
  { key: "customer",     label: "Customer",      icon: <Users size={14} /> },
  { key: "capacity",     label: "Capacity",      icon: <Factory size={14} /> },
  { key: "guidance",     label: "Guidance",      icon: <TrendingUp size={14} /> },
  { key: "quarters",     label: "Coverage",      icon: <FileText size={14} /> },
];

function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return "—";
  if (Math.abs(v) >= 1000) return v.toLocaleString(undefined, { maximumFractionDigits: 0 });
  return v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

type FinFmt = "M" | "%" | "$";

interface UMCRowDef {
  label: string;
  metric: string;
  fmt: FinFmt;
  bold?: boolean;
  derived?: boolean;
  indent?: 1 | 2;
}
interface UMCRowGroup { heading: string; rows: UMCRowDef[] }

const UMC_ROW_GROUPS: UMCRowGroup[] = [
  { heading: "Revenue", rows: [
    { label: "Net Revenue",                   metric: "net_revenue",            fmt: "M", bold: true },
  ]},
  { heading: "Gross Profit", rows: [
    { label: "Gross Profit",                  metric: "gross_profit",           fmt: "M", bold: true },
  ]},
  { heading: "Operating Income", rows: [
    { label: "Operating Expenses",            metric: "operating_expenses",     fmt: "M", indent: 1 },
    { label: "Net Other Operating Inc / Exp", metric: "other_operating_income", fmt: "M", indent: 1 },
    { label: "Operating Income",              metric: "operating_income",       fmt: "M", bold: true },
  ]},
  { heading: "Net Income", rows: [
    { label: "Net Non-Operating Inc / Exp",   metric: "non_operating_items",    fmt: "M", indent: 1 },
    { label: "Net Income",                    metric: "net_income",             fmt: "M", bold: true },
    { label: "EPS (NT$ / share)",             metric: "eps",                    fmt: "$" },
    { label: "EPS (US$ / ADR)",               metric: "eps_adr",                fmt: "$" },
  ]},
  { heading: "FX", rows: [
    { label: "USD / NTD avg rate",            metric: "usd_ntd_avg_rate",       fmt: "$" },
  ]},
];

export default function UMCPanel() {
  const [tab, setTab] = useState<SubTab>("financials");
  const [summary, setSummary] = useState<UMCSummary | null>(null);
  const [summaryErr, setSummaryErr] = useState<string | null>(null);

  useEffect(() => {
    umcClient.summary()
      .then(setSummary)
      .catch((e) => setSummaryErr(e instanceof Error ? e.message : "load failed"));
  }, []);

  return (
    <div className="space-y-4">
      <div className="bg-white border border-slate-200 rounded-lg shadow-sm p-5">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h2 className="text-lg font-bold text-slate-900">UMC (2303.TW) — Ingested Data</h2>
            <p className="text-xs text-slate-500 mt-0.5">
              Long-format silver layer extracted from www.umc.com IR site
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
                  detail="Each report covers 3 periods (curQ + prevQ + YoY)"
                  range="Quarterly management reports" />
            <Stat label="Transcripts"
                  value="Not published"
                  detail="UMC issues only a calendar invitation"
                  range="Forward guidance is verbal, not tabular" />
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
        {tab === "prices"      && <PricesTab ticker="2303.TW" currency="TWD" />}
        {tab === "financials"  && <FinancialsTab />}
        {tab === "tech"        && <SegmentTab metric="revenue_share_by_technology"   title="Wafer Revenue by Geometry"        unit="%" />}
        {tab === "geo"         && <SegmentTab metric="revenue_share_by_geography"    title="Net Revenue by Region"             unit="%" />}
        {tab === "application" && <SegmentTab metric="revenue_share_by_application"  title="Net Revenue by Application"        unit="%" />}
        {tab === "customer"    && <SegmentTab metric="revenue_share_by_customer_type"title="Net Revenue by Customer Type"      unit="%" />}
        {tab === "capacity"    && <CapacityTab />}
        {tab === "cashflow"    && <WidePivotTab fetcher={(q) => umcClient.cashflow(q)}     title="Cash Flow Statement"          unitNote="NT$ Millions" defaultQ={20} />}
        {tab === "balanceSheet"&& <WidePivotTab fetcher={(q) => umcClient.balanceSheet(q)} title="Balance Sheet Highlights"     unitNote="NT$ Billions, days, %" defaultQ={20} />}
        {tab === "annual"      && <WidePivotTab fetcher={(q) => umcClient.annual(q)}       title="Full-Year P&L"                unitNote="NT$ Millions" defaultQ={10} qLabel="Years" qOptions={[3,5,8,10]} />}
        {tab === "guidance"    && <GuidanceTab />}
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
  const [data, setData] = useState<UMCFinancialsWide | null>(null);
  const [quarters, setQuarters] = useState(20);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    umcClient.financialsWide(quarters)
      .then(setData)
      .finally(() => setLoading(false));
  }, [quarters]);

  if (loading) return <SectionLoading />;
  if (!data || !data.metrics.length) return <Empty>No financials data.</Empty>;

  const byMetric = new Map<string, UMCMetricRow>();
  data.metrics.forEach((r) => byMetric.set(r.metric, r));

  const flatRows = UMC_ROW_GROUPS.flatMap((g) => g.rows);

  const formatCell = (rd: UMCRowDef, v: number | null | undefined): string => {
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
            {[8, 12, 20, 28].map((n) => (
              <button key={n}
                onClick={() => setQuarters(n)}
                className={`h-6 px-2 rounded text-[10px] font-semibold ${
                  quarters === n ? "bg-slate-900 text-white" : "text-slate-500 hover:bg-slate-100"
                }`}
              >{n}Q</button>
            ))}
          </div>
          <span className="text-[10px] font-mono text-slate-400">
            2303.TW · {data.periods.length} quarters
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
              const firstOfGroup = ri > 0 && UMC_ROW_GROUPS.some((g) => g.rows[0]?.metric === rd.metric);
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

function SegmentTab({ metric, title, unit }: { metric: string; title: string; unit: string }) {
  const [data, setData] = useState<UMCSegments | null>(null);
  const [loading, setLoading] = useState(true);
  const [quarters, setQuarters] = useState(20);

  useEffect(() => {
    setLoading(true);
    umcClient.segments(metric, quarters)
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

  const dims = data.rows.map((r) => r.dimension);
  const COLORS = ["#2563eb","#dc2626","#16a34a","#ea580c","#9333ea","#0891b2","#ca8a04","#db2777","#475569","#65a30d","#7c3aed"];

  return (
    <div className="space-y-4">
      <div className="bg-white border border-slate-200 rounded-lg shadow-sm">
        <div className="flex items-center justify-between px-4 py-2 border-b border-slate-200">
          <h3 className="text-sm font-bold text-slate-900">{title}</h3>
          <div className="flex items-center gap-1 text-xs">
            {[8, 16, 24, 28].map((n) => (
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
// Capacity & Utilization tab
// ---------------------------------------------------------------------------

function CapacityTab() {
  const [data, setData] = useState<UMCCapacity | null>(null);
  const [quarters, setQuarters] = useState(28);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    umcClient.capacity(quarters)
      .then(setData)
      .finally(() => setLoading(false));
  }, [quarters]);

  // Build composed chart series: bars for capacity + wafer shipments,
  // lines for utilization (right axis %) and ASP (right axis USD).
  // API returns periods newest-first (table convention); chart x-axis
  // wants oldest-first so the line reads left→right chronologically.
  const chartData = useMemo(() => {
    if (!data) return [];
    const byMetric = new Map(data.metrics.map((m) => [m.metric, m]));
    return [...data.periods].reverse().map((p) => ({
      period: p,
      total_capacity:        byMetric.get("total_capacity")?.[p] ?? null,
      wafer_shipments:       byMetric.get("wafer_shipments")?.[p] ?? null,
      capacity_utilization:  byMetric.get("capacity_utilization")?.[p] ?? null,
      blended_asp:           byMetric.get("blended_asp")?.[p] ?? null,
    }));
  }, [data]);

  if (loading) return <SectionLoading />;
  if (!data || !data.metrics.length) return <Empty>No capacity data.</Empty>;

  const byMetric = new Map(data.metrics.map((m) => [m.metric, m]));
  const cap = byMetric.get("total_capacity");
  const ship = byMetric.get("wafer_shipments");
  const util = byMetric.get("capacity_utilization");
  const asp = byMetric.get("blended_asp");

  return (
    <div className="space-y-4">
      <div className="bg-amber-50 border border-amber-200 rounded-md px-3 py-2 text-[11px] text-amber-800">
        <strong>Unit:</strong> 12&quot; K wafer equivalents. UMC switched its
        wafer reporting from 8&quot; to 12&quot; equivalents in 2024 — the
        2024+ reports restate prior periods on the new basis, so the time
        series is continuous from 1Q23 onward. Utilization rate is unitless
        (already %). <strong>Blended ASP</strong> is read visually from the
        published chart on page 8 of each report (UMC publishes the value
        only graphically, no tabular numbers); precision ±25-50 USD per
        data point.
      </div>

      <div className="bg-white border border-slate-200 rounded-lg shadow-sm">
        <div className="flex items-center justify-between px-4 py-2 border-b border-slate-200">
          <h3 className="text-sm font-bold text-slate-900">
            Wafer Shipments · Total Capacity · Utilization
          </h3>
          <div className="flex items-center gap-1 text-xs">
            {[8, 12, 20, 28].map((n) => (
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
          <ResponsiveContainer width="100%" height={340}>
            <ComposedChart data={chartData} margin={{ left: 0, right: 16, top: 8, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              <XAxis dataKey="period" tick={{ fontSize: 10, fill: "#64748b" }} />
              <YAxis
                yAxisId="left"
                tick={{ fontSize: 10, fill: "#64748b" }}
                label={{ value: 'kpcs (12" eq)', angle: -90, position: "insideLeft", fontSize: 10, fill: "#64748b" }}
              />
              <YAxis
                yAxisId="right"
                orientation="right"
                domain={[0, 100]}
                tick={{ fontSize: 10, fill: "#64748b" }}
                unit="%"
                label={{ value: "Utilization", angle: 90, position: "insideRight", fontSize: 10, fill: "#64748b" }}
              />
              <YAxis
                yAxisId="asp"
                orientation="right"
                domain={[0, 2500]}
                tick={false}
                axisLine={false}
                hide
              />
              <Tooltip contentStyle={{ fontSize: 11 }}
                       formatter={(v: number, name: string) => {
                         if (name === "capacity_utilization") return [`${v?.toFixed?.(1) ?? v}%`, "Utilization"];
                         if (name === "total_capacity")       return [v?.toLocaleString?.() ?? v, "Total Capacity (kpcs)"];
                         if (name === "wafer_shipments")      return [v?.toLocaleString?.() ?? v, "Wafer Shipments (kpcs)"];
                         if (name === "blended_asp")          return [`$${v?.toLocaleString?.() ?? v}`, "Blended ASP (USD/wafer)"];
                         return [v, name];
                       }} />
              <Legend wrapperStyle={{ fontSize: 11 }}
                      formatter={(v: string) => v === "capacity_utilization" ? "Utilization (%)" :
                                               v === "total_capacity"       ? "Total Capacity" :
                                               v === "wafer_shipments"      ? "Wafer Shipments" :
                                               v === "blended_asp"          ? "Blended ASP (chart-est)" : v} />
              <Bar yAxisId="left" dataKey="total_capacity"  fill="#cbd5e1" />
              <Bar yAxisId="left" dataKey="wafer_shipments" fill="#2563eb" />
              <Line yAxisId="right" type="monotone" dataKey="capacity_utilization"
                    stroke="#dc2626" strokeWidth={2.5} dot={{ r: 3 }} connectNulls />
              <Line yAxisId="asp" type="monotone" dataKey="blended_asp"
                    stroke="#16a34a" strokeWidth={2} strokeDasharray="4 2"
                    dot={{ r: 2.5 }} connectNulls />
            </ComposedChart>
          </ResponsiveContainer>
        </div>

        <div className="overflow-x-auto border-t border-slate-200">
          <table className="w-full text-xs border-collapse">
            <thead className="bg-slate-50">
              <tr className="border-b border-slate-200">
                <th className="text-left px-3 py-2 font-semibold text-slate-700 sticky left-0 bg-slate-50">Metric</th>
                {data.periods.map((p) => (
                  <th key={p} className="text-right px-2 py-2 font-semibold text-slate-700 whitespace-nowrap">{p}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {[
                { row: cap,  label: 'Total Capacity (kpcs 12" eq)',  fmt: "n" },
                { row: ship, label: 'Wafer Shipments (kpcs 12" eq)', fmt: "n" },
                { row: util, label: "Utilization Rate",              fmt: "%" },
                { row: asp,  label: "Blended ASP (USD/wafer, est.)", fmt: "$" },
              ].map(({ row, label, fmt }) =>
                row ? (
                  <tr key={row.metric} className="border-b border-slate-100 hover:bg-slate-50">
                    <td className="px-3 py-1.5 font-medium text-slate-700 sticky left-0 bg-white whitespace-nowrap">{label}</td>
                    {data.periods.map((p) => {
                      const v = row[p] as number | null;
                      const cell = v == null ? "—"
                        : fmt === "%" ? `${fmtNum(v, 1)}%`
                        : fmt === "$" ? `$${fmtNum(v, 0)}`
                        : fmtNum(v, 0);
                      return (
                        <td key={p} className="px-2 py-1.5 text-right tabular-nums whitespace-nowrap text-slate-700">
                          {cell}
                        </td>
                      );
                    })}
                  </tr>
                ) : null,
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Generic wide-pivot tab used for Cash Flow, Balance Sheet, Annual P&L
// ---------------------------------------------------------------------------

function WidePivotTab({
  fetcher, title, unitNote, defaultQ, qLabel = "Q", qOptions = [8, 12, 20, 28],
}: {
  fetcher: (n: number) => Promise<UMCWide>;
  title: string;
  unitNote: string;
  defaultQ: number;
  qLabel?: string;
  qOptions?: number[];
}) {
  const [data, setData] = useState<UMCWide | null>(null);
  const [n, setN] = useState(defaultQ);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetcher(n).then(setData).finally(() => setLoading(false));
  }, [n, fetcher]);

  if (loading) return <SectionLoading />;
  if (!data || !data.metrics.length) return <Empty>No data.</Empty>;

  const formatCell = (unit: string, v: number | null | undefined): string => {
    if (v == null || Number.isNaN(v)) return "—";
    if (unit === "pct") return `${fmtNum(v, 1)}%`;
    if (unit === "days") return fmtNum(v, 0);
    if (unit === "ntd_per_share" || unit === "usd_per_adr" || unit === "ntd_per_usd")
      return fmtNum(v, 2);
    if (unit === "ntd_b") return fmtNum(v, 2);
    return fmtNum(v, 0);
  };

  return (
    <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
      <div className="flex items-center justify-between px-6 py-3 border-b border-slate-100 bg-slate-50/80">
        <span className="text-xs font-semibold text-slate-700">
          {title} <span className="text-slate-400 font-normal">({unitNote})</span>
        </span>
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1">
            {qOptions.map((qq) => (
              <button key={qq}
                onClick={() => setN(qq)}
                className={`h-6 px-2 rounded text-[10px] font-semibold ${
                  n === qq ? "bg-slate-900 text-white" : "text-slate-500 hover:bg-slate-100"
                }`}
              >{qq}{qLabel === "Q" ? "Q" : "Y"}</button>
            ))}
          </div>
          <span className="text-[10px] font-mono text-slate-400">
            2303.TW · {data.periods.length} {qLabel === "Q" ? "quarters" : "years"}
          </span>
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="text-xs w-full">
          <thead>
            <tr className="border-b border-slate-200">
              <th className="sticky left-0 z-30 bg-white text-left px-4 py-2 text-[10px] font-bold text-slate-500 uppercase tracking-wider w-56 min-w-[224px] border-r border-slate-200 align-bottom shadow-[4px_0_6px_-4px_rgba(15,23,42,0.08)]">
                Metric
              </th>
              {data.periods.map((p) => (
                <th key={p} className="px-3 py-2 text-right text-[10px] font-bold text-slate-600 whitespace-nowrap min-w-[90px]">
                  {p}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.metrics.map((row, ri) => {
              const stripe  = ri % 2 === 0 ? "bg-white" : "bg-slate-50";
              return (
                <tr key={row.metric}
                    className={`group border-b border-slate-50 ${stripe} hover:!bg-indigo-50/60 transition-colors`}>
                  <td className={`sticky left-0 z-10 ${stripe} group-hover:!bg-indigo-50/60 px-4 py-1.5 border-r border-slate-200 whitespace-nowrap shadow-[4px_0_6px_-4px_rgba(15,23,42,0.08)]`}>
                    <span className="text-[11px] text-slate-700">{row.metric}</span>
                    <span className="text-[10px] text-slate-400 ml-2">{row.unit}</span>
                  </td>
                  {data.periods.map((p) => {
                    const v = row[p] as number | null;
                    return (
                      <td key={p}
                          className="px-3 py-1.5 text-right tabular-nums whitespace-nowrap text-[11px] text-slate-700">
                        {formatCell(row.unit, v)}
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
// Guidance vs Actual tab
// ---------------------------------------------------------------------------

function GuidanceTab() {
  const [rows, setRows] = useState<UMCGuidanceRow[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    umcClient.guidance(28)
      .then((d) => setRows(d.rows))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <SectionLoading />;
  if (!rows.length) return <Empty>No guidance data.</Empty>;

  // Group rows by metric for separate tables
  const byMetric: Record<string, UMCGuidanceRow[]> = {};
  rows.forEach((r) => {
    (byMetric[r.metric] = byMetric[r.metric] || []).push(r);
  });

  // Pretty labels for each guidance metric
  const METRIC_TITLES: Record<string, string> = {
    "guidance_gross_margin":         "Gross Margin (%) — guidance vs actual",
    "guidance_capacity_utilization": "Capacity Utilization (%) — guidance vs actual",
    "guidance_wafer_shipments_qoq":  "Wafer Shipments QoQ % — guidance vs actual",
    "guidance_asp_usd_qoq":          "Blended ASP QoQ % — guidance vs actual (chart-est.)",
    "guidance_annual_capex":         "Annual CAPEX (US$ B) — guidance vs realized (sum of qtrly capex_total ÷ avg FX)",
  };

  // ── Forward guidance card data ──────────────────────────────────────────
  // Project rule: every guidance tab leads with the latest forward guidance,
  // matching TSMC's "Forward guidance for 1Q26" pattern. See
  // .claude/skills/guidance-tab-pattern/SKILL.md.
  //
  // The /umc/guidance endpoint sorts rows newest-first (by for_period date),
  // so the FIRST row is from the most recent issuing report. Filter all rows
  // sharing that issuing period to populate the card.
  const latestIssued = rows[0]?.issued_in_period;
  const forwardRows = latestIssued
    ? rows.filter((r) => r.issued_in_period === latestIssued)
    : [];
  // De-dupe to one row per metric (the for_period is constant per metric in
  // a single report's forward guidance — quarterly metrics → next-Q,
  // annual capex → next-FY)
  const forwardByMetric = new Map<string, UMCGuidanceRow>();
  forwardRows.forEach((r) => {
    if (!forwardByMetric.has(r.metric)) forwardByMetric.set(r.metric, r);
  });

  const FORWARD_CARD_ORDER: Array<[string, string]> = [
    ["guidance_gross_margin",         "Gross Margin"],
    ["guidance_capacity_utilization", "Capacity Util."],
    ["guidance_wafer_shipments_qoq",  "Wafer Ship. QoQ"],
    ["guidance_asp_usd_qoq",          "ASP (USD) QoQ"],
    ["guidance_annual_capex",         "Annual CAPEX"],
  ];

  const fmtForwardValue = (r: UMCGuidanceRow): string => {
    const isCapex = r.metric === "guidance_annual_capex";
    if (r.guide_point != null && isCapex) return `$${r.guide_point.toFixed(2)}B`;
    if (r.guide_point != null)            return r.guide_point.toFixed(2);
    if (r.guide_low != null && r.guide_high != null) {
      return `${r.guide_low.toFixed(0)}–${r.guide_high.toFixed(0)}%`;
    }
    return "—";  // verbal-only, shown via the verbal line below
  };

  const fmtVal = (v: number | null | undefined, digits = 1, suffix = "%", prefix = "") =>
    v == null ? "—" : `${prefix}${v.toFixed(digits)}${suffix}`;

  const outcomeClass = (o: string | null) =>
    o === "BEAT high"      ? "bg-emerald-100 text-emerald-700"
    : o === "ABOVE guidance" ? "bg-emerald-100 text-emerald-700"
    : o === "MISS low"     ? "bg-rose-100 text-rose-700"
    : o === "BELOW guidance" ? "bg-rose-100 text-rose-700"
    : o === "in range"     ? "bg-slate-100 text-slate-600"
    : o === "near point"   ? "bg-slate-100 text-slate-600"
    : "text-slate-300";

  return (
    <div className="space-y-4">
      {/* Forward guidance card — newest report's view of next period(s).
          Project rule: every guidance tab leads with this. */}
      {forwardByMetric.size > 0 && (
        <div className="bg-indigo-50 border border-indigo-200 rounded-lg p-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-bold text-indigo-900">
              Forward guidance{" "}
              <span className="text-[11px] font-normal text-indigo-700">
                issued in {latestIssued} report
              </span>
            </h3>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3 text-xs">
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
                    {fmtForwardValue(r)}
                  </div>
                  {r.verbal && (
                    <div className="text-[10px] text-indigo-700 mt-0.5 italic line-clamp-2" title={r.verbal}>
                      &ldquo;{r.verbal}&rdquo;
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      <div className="bg-amber-50 border border-amber-200 rounded-md px-3 py-2 text-[11px] text-amber-800">
        <strong>Note on UMC&apos;s guidance:</strong> UMC issues qualitative
        QoQ guidance (e.g. &quot;high-20% range&quot;, &quot;mid-70%
        range&quot;) rather than TSMC-style numeric ranges. We map qualifiers
        to implied ranges (low-Xx% → X to X+3%; mid-Xx% → X+3 to X+7%; high-Xx%
        → X+6 to X+9%). The verbal guidance is preserved alongside.
        Annual CAPEX guidance (e.g. &quot;US$1.5 billion&quot;) is shown
        as a point estimate; realized values come from sum of quarterly
        capex_total ÷ avg USD/NTD rate.
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
                    <th className="text-right px-2 py-2 font-semibold text-slate-500">{metric === "guidance_annual_capex" ? "Point" : "Low"}</th>
                    <th className="text-right px-2 py-2 font-semibold text-slate-500">Mid</th>
                    <th className="text-right px-2 py-2 font-semibold text-slate-500">High</th>
                    <th className="text-right px-2 py-2 font-semibold text-slate-700">Actual</th>
                    <th className="text-center px-2 py-2 font-semibold text-slate-700">Outcome</th>
                    <th className="text-right px-2 py-2 font-semibold text-slate-700">{metric === "guidance_annual_capex" ? "vs Point" : "vs Mid (pp)"}</th>
                  </tr>
                </thead>
                <tbody>
                  {subset.map((r) => {
                    const isPctMetric = metric === "guidance_gross_margin" ||
                                        metric === "guidance_capacity_utilization" ||
                                        metric === "guidance_wafer_shipments_qoq" ||
                                        metric === "guidance_asp_usd_qoq";
                    const isCapexMetric = metric === "guidance_annual_capex";
                    const suffix = isPctMetric ? "%" : isCapexMetric ? "B" : "";
                    const prefix = isCapexMetric ? "$" : "";
                    const ppFmt = (v: number | null) => v == null ? "—"
                      : isCapexMetric ? `${v >= 0 ? "+" : ""}${prefix}${v.toFixed(2)}${suffix}`
                      : `${v >= 0 ? "+" : ""}${v.toFixed(2)}${isPctMetric ? "pp" : ""}`;
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
                          {r.verbal ?? "—"}
                        </td>
                        <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{
                          isCapexMetric
                            ? fmtVal(r.guide_point, 2, suffix, prefix)  // capex shows point in low column
                            : fmtVal(r.guide_low,  1, suffix)
                        }</td>
                        <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{
                          isCapexMetric ? "—" : fmtVal(r.guide_mid, 1, suffix)
                        }</td>
                        <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{
                          isCapexMetric ? "—" : fmtVal(r.guide_high, 1, suffix)
                        }</td>
                        <td className="px-2 py-1.5 text-right tabular-nums font-bold text-slate-900">{fmtVal(r.actual, 2, suffix, prefix)}</td>
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

function QuartersTab() {
  const [quarters, setQuarters] = useState<UMCQuarter[]>([]);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    umcClient.quarters()
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
        <p className="text-[11px] text-slate-500 mt-0.5">
          Each quarter is reported by up to 3 source reports (the period appears
          as curQ in its own report, prevQ in the next quarter's report, and
          YoY one year later).
        </p>
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
                  {q.sources.map((s) => s.replace(/^umc_management_report_/, "")).join(", ")}
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
