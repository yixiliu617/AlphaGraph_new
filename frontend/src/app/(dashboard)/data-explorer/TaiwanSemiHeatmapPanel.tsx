"use client";

/**
 * TaiwanSemiHeatmapPanel — dense heatmap of Taiwan-listed semiconductor
 * companies' monthly revenue disclosures (MOPS).
 *
 * Rows: one ticker per row, grouped by subsector (Foundry / IC Design /
 * Memory / OSAT / Wafer / Equipment / PCB-Substrate / Materials /
 * Optical / Server EMS). Columns: trailing N months, most-recent on
 * the left (filing order).
 *
 * Metric toggle: Revenue (NT$B) · YoY % · MoM %
 *  - %: red→green diverging, cap at ±60%
 *  - NT$B: each row shaded relative to its OWN history (TSMC NT$300B
 *    and Ali Corp NT$1B cannot share an absolute scale)
 */

import { Fragment, useEffect, useMemo, useState } from "react";
import { Loader2, TrendingUp, BarChart3 } from "lucide-react";
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  taiwanClient,
  type WatchlistEntry,
  type MonthlyRevenueRow,
} from "@/lib/api/taiwanClient";

type Metric = "revenue" | "yoy_pct" | "mom_pct";

const METRIC_OPTIONS: { key: Metric; label: string }[] = [
  { key: "yoy_pct", label: "YoY %" },
  { key: "mom_pct", label: "MoM %" },
  { key: "revenue", label: "Revenue (NT$B)" },
];

// Subsector display order — same as a Taiwan semi analyst reads value-chain:
// wafer → foundry → IC design → memory → OSAT → PCB → materials → optics → EMS.
const SUBSECTOR_ORDER = [
  "Foundry",
  "IC Design",
  "Memory",
  "DRAM Module",
  "OSAT",
  "Wafer",
  "Equipment",
  "PCB/Substrate",
  "Materials",
  "Optical",
  "Server EMS",
];

// ---------------------------------------------------------------------------
// Formatting + color
// ---------------------------------------------------------------------------

const PCT_CAP = 60;  // cap the color scale at ±60% for readable gradient

function fmtPct(v: number | null): string {
  if (v == null || Number.isNaN(v)) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${(v * 100).toFixed(1)}%`;
}

// Revenue arrives in raw TWD (e.g. TSMC Mar-2026 ≈ 2.8e11). Express in NT$B.
function fmtRevenueB(twd: number | null): string {
  if (twd == null || Number.isNaN(twd)) return "—";
  const b = twd / 1e9;
  if (b >= 100) return b.toFixed(0);
  if (b >= 10)  return b.toFixed(1);
  return b.toFixed(2);
}

function fmtFiscalYm(ym: string): string {
  // "2026-03" → "Mar '26"
  const [y, m] = ym.split("-");
  if (!y || !m) return ym;
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const idx = parseInt(m, 10) - 1;
  const monLabel = months[idx] ?? m;
  return `${monLabel} '${y.slice(2)}`;
}

function yearBoundary(ym: string): boolean {
  return ym.endsWith("-12") || ym.endsWith("-01");
}

// Diverging red→white→green for percent values. `v` in 0–1 fraction.
function pctColor(v: number | null): string {
  if (v == null || Number.isNaN(v)) return "#f8fafc";  // slate-50
  const pct = v * 100;
  const clamped = Math.max(-PCT_CAP, Math.min(PCT_CAP, pct));
  const t = clamped / PCT_CAP;  // -1..1
  if (t >= 0) {
    // 0 → white, 1 → emerald-600
    const alpha = Math.max(0.05, Math.abs(t));
    return `rgba(5, 150, 105, ${alpha.toFixed(3)})`;
  }
  const alpha = Math.max(0.05, Math.abs(t));
  return `rgba(220, 38, 38, ${alpha.toFixed(3)})`;
}

// Per-row relative scale for absolute $ values.
function revenueColor(v: number | null, rowMin: number, rowMax: number): string {
  if (v == null || Number.isNaN(v) || rowMax <= rowMin) return "#f8fafc";
  const t = (v - rowMin) / (rowMax - rowMin);  // 0..1
  // White → indigo-500 gradient
  const alpha = Math.max(0.05, t);
  return `rgba(99, 102, 241, ${alpha.toFixed(3)})`;
}

function textColorForBg(metric: Metric, v: number | null, intensity: number): string {
  if (v == null) return "#94a3b8";
  if (metric === "revenue") {
    return intensity > 0.55 ? "#ffffff" : "#1e293b";
  }
  const pct = v * 100;
  if (Math.abs(pct) < 5) return "#475569";  // slate-600
  return Math.abs(pct) > 35 ? "#ffffff" : (pct >= 0 ? "#065f46" : "#7f1d1d");
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function TaiwanSemiHeatmapPanel() {
  const [watchlist, setWatchlist] = useState<WatchlistEntry[]>([]);
  const [revenueRows, setRevenueRows] = useState<MonthlyRevenueRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [metric, setMetric] = useState<Metric>("yoy_pct");
  const [months, setMonths] = useState<12 | 24 | 36>(24);

  // Drill-down: ticker whose 10-year history is rendered in the chart below.
  // Defaults to TSMC (2330) on first load; user clicks any row to switch.
  const [selectedTicker, setSelectedTicker] = useState<string>("2330");

  // Fetch watchlist once, then revenue for all its tickers.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    taiwanClient.watchlist()
      .then((wl) => {
        if (cancelled) return wl.data;
        setWatchlist(wl.data);
        return wl.data;
      })
      .then((entries) => {
        if (!entries) return null;
        const tickers = entries.map((e) => e.ticker);
        return taiwanClient.monthlyRevenue(tickers, months);
      })
      .then((rev) => {
        if (!cancelled && rev) setRevenueRows(rev.data);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => { cancelled = true; };
  }, [months]);

  // Union of all fiscal_ym values across the dataset → descending.
  const monthCols = useMemo(() => {
    const s = new Set<string>();
    for (const r of revenueRows) if (r.fiscal_ym) s.add(r.fiscal_ym);
    return [...s].sort().reverse();
  }, [revenueRows]);

  // Index: ticker → (fiscal_ym → row)
  const byTickerMonth = useMemo(() => {
    const m = new Map<string, Map<string, MonthlyRevenueRow>>();
    for (const r of revenueRows) {
      if (!m.has(r.ticker)) m.set(r.ticker, new Map());
      m.get(r.ticker)!.set(r.fiscal_ym, r);
    }
    return m;
  }, [revenueRows]);

  // Group watchlist by subsector for the row headers.
  const grouped = useMemo(() => {
    const g = new Map<string, WatchlistEntry[]>();
    for (const w of watchlist) {
      const key = w.subsector || "Other";
      if (!g.has(key)) g.set(key, []);
      g.get(key)!.push(w);
    }
    // Sort within group: alphabetical by name for stable layout.
    for (const arr of g.values()) arr.sort((a, b) => a.name.localeCompare(b.name));

    // Produce [subsector, entries[]] ordered by SUBSECTOR_ORDER, unknowns at end.
    const out: [string, WatchlistEntry[]][] = [];
    for (const sub of SUBSECTOR_ORDER) {
      if (g.has(sub)) out.push([sub, g.get(sub)!]);
    }
    for (const [sub, arr] of g.entries()) {
      if (!SUBSECTOR_ORDER.includes(sub)) out.push([sub, arr]);
    }
    return out;
  }, [watchlist]);

  // Per-row min/max of revenue across the displayed months — used for
  // relative $ shading (TSMC vs Ali Corp can't share an absolute scale).
  const revenueRowRange = useMemo(() => {
    const m = new Map<string, { min: number; max: number }>();
    for (const [ticker, monthMap] of byTickerMonth.entries()) {
      let mn = Infinity, mx = -Infinity;
      for (const ym of monthCols) {
        const row = monthMap.get(ym);
        const v = row?.revenue_twd;
        if (v != null && !Number.isNaN(v)) {
          if (v < mn) mn = v;
          if (v > mx) mx = v;
        }
      }
      if (mn !== Infinity) m.set(ticker, { min: mn, max: mx });
    }
    return m;
  }, [byTickerMonth, monthCols]);

  function cellValue(row: MonthlyRevenueRow | undefined): number | null {
    if (!row) return null;
    if (metric === "revenue") return row.revenue_twd;
    if (metric === "yoy_pct") return row.yoy_pct;
    return row.mom_pct;
  }

  function cellBg(ticker: string, v: number | null): string {
    if (metric === "revenue") {
      const r = revenueRowRange.get(ticker);
      if (!r) return "#f8fafc";
      return revenueColor(v, r.min, r.max);
    }
    return pctColor(v);
  }

  function cellIntensity(ticker: string, v: number | null): number {
    if (v == null) return 0;
    if (metric === "revenue") {
      const r = revenueRowRange.get(ticker);
      if (!r || r.max <= r.min) return 0;
      return (v - r.min) / (r.max - r.min);
    }
    return Math.min(1, Math.abs(v * 100) / PCT_CAP);
  }

  const formatCell = metric === "revenue" ? fmtRevenueB : fmtPct;

  const selectedEntry = watchlist.find((w) => w.ticker === selectedTicker);

  return (
    <div className="space-y-4">
    <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
      {/* ── Header ── */}
      <div className="flex items-center justify-between gap-3 px-5 py-3 border-b border-slate-100">
        <div className="flex items-center gap-2">
          <TrendingUp size={14} className="text-slate-400" />
          <span className="text-xs font-semibold text-slate-700">
            Taiwan Semi Monthly Revenue · {METRIC_OPTIONS.find((o) => o.key === metric)?.label}
          </span>
          {loading && <Loader2 size={11} className="text-slate-300 animate-spin" />}
        </div>

        <div className="flex items-center gap-3">
          {/* Months window */}
          <div className="flex items-center gap-1 border border-slate-200 rounded-md bg-slate-50 p-0.5">
            {([12, 24, 36] as const).map((n) => (
              <button
                key={n}
                onClick={() => setMonths(n)}
                className={`text-[10px] font-semibold px-2 py-0.5 rounded ${
                  months === n
                    ? "bg-white text-indigo-700 shadow-sm"
                    : "text-slate-500 hover:text-slate-700"
                }`}
              >
                {n}m
              </button>
            ))}
          </div>

          {/* Metric selector */}
          <div className="flex items-center gap-2">
            <label className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider">
              Metric
            </label>
            <select
              value={metric}
              onChange={(e) => setMetric(e.target.value as Metric)}
              className="h-7 px-2 rounded-md border border-slate-200 bg-slate-50 text-xs outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 text-slate-700"
            >
              {METRIC_OPTIONS.map((o) => (
                <option key={o.key} value={o.key}>{o.label}</option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {/* ── Body ── */}
      {error ? (
        <div className="px-5 py-10 text-center text-xs text-red-600">{error}</div>
      ) : monthCols.length === 0 ? (
        <div className="px-5 py-10 text-center text-xs text-slate-400">
          {loading ? "Loading Taiwan monthly revenue…" : "No Taiwan revenue data yet — run the MOPS scraper."}
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="text-[11px] border-collapse">
            <thead>
              <tr className="border-b border-slate-200">
                <th className="sticky left-0 bg-white z-20 text-left font-semibold text-slate-500 px-3 py-2 min-w-[180px] shadow-[4px_0_6px_-4px_rgba(15,23,42,0.08)]">
                  Ticker · Company
                </th>
                {monthCols.map((ym) => (
                  <th
                    key={ym}
                    className={`text-right font-semibold text-slate-500 px-2 py-2 whitespace-nowrap min-w-[68px] tabular-nums ${
                      yearBoundary(ym) ? "border-l border-slate-300" : ""
                    }`}
                  >
                    {fmtFiscalYm(ym)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {grouped.map(([subsector, entries]) => (
                <Fragment key={subsector}>
                  {/* Subsector band */}
                  <tr className="bg-slate-50 border-t border-slate-200">
                    <td
                      className="sticky left-0 bg-slate-50 z-10 px-3 py-1 text-[10px] font-bold uppercase tracking-wider text-slate-500 shadow-[4px_0_6px_-4px_rgba(15,23,42,0.08)]"
                      colSpan={1}
                    >
                      {subsector}
                      <span className="text-slate-400 font-normal normal-case tracking-normal ml-1.5">
                        ({entries.length})
                      </span>
                    </td>
                    <td className="bg-slate-50" colSpan={monthCols.length} />
                  </tr>

                  {entries.map((w) => {
                    const monthMap = byTickerMonth.get(w.ticker);
                    const selected = w.ticker === selectedTicker;
                    const rowBg = selected ? "bg-indigo-50" : "bg-white";
                    const hoverBg = selected ? "hover:bg-indigo-100" : "hover:bg-indigo-50/40";
                    return (
                      <tr
                        key={w.ticker}
                        onClick={() => setSelectedTicker(w.ticker)}
                        className={`border-b border-slate-100 cursor-pointer ${hoverBg} group`}
                      >
                        {/* Sticky ticker/name cell */}
                        <td className={`sticky left-0 ${rowBg} ${selected ? "group-hover:bg-indigo-100" : "group-hover:bg-indigo-50/40"} z-10 px-3 py-1.5 border-r border-slate-100 shadow-[4px_0_6px_-4px_rgba(15,23,42,0.08)] ${selected ? "border-l-2 border-l-indigo-600" : ""}`}>
                          <div className="flex items-baseline gap-2">
                            <span className={`font-mono font-semibold text-[11px] ${selected ? "text-indigo-700" : "text-slate-800"}`}>{w.ticker}</span>
                            <span className="text-[10px] text-slate-500 truncate max-w-[120px]">{w.name}</span>
                          </div>
                        </td>

                        {/* Month cells */}
                        {monthCols.map((ym) => {
                          const row = monthMap?.get(ym);
                          const v = cellValue(row);
                          const bg = cellBg(w.ticker, v);
                          const intensity = cellIntensity(w.ticker, v);
                          const color = textColorForBg(metric, v, intensity);
                          const italic = metric !== "revenue";
                          return (
                            <td
                              key={ym}
                              className={`text-right px-2 py-1.5 tabular-nums whitespace-nowrap ${
                                yearBoundary(ym) ? "border-l border-slate-300" : ""
                              }`}
                              style={{ backgroundColor: bg, color, fontStyle: italic ? "italic" : undefined }}
                              title={row ? `${w.ticker} ${fmtFiscalYm(ym)} · NT$${fmtRevenueB(row.revenue_twd)}B · YoY ${fmtPct(row.yoy_pct)} · MoM ${fmtPct(row.mom_pct)}` : ""}
                            >
                              {formatCell(v)}
                            </td>
                          );
                        })}
                      </tr>
                    );
                  })}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Footer attribution */}
      <div className="px-5 py-2 border-t border-slate-100 text-[10px] text-slate-400 flex justify-between">
        <span>{watchlist.length} tickers · {monthCols.length} months · click a row to drill down</span>
        <span>Source: MOPS (twse.com.tw) monthly revenue filings</span>
      </div>
    </div>

    {/* ── 10-year revenue chart for the selected ticker ── */}
    <TickerRevenueChart ticker={selectedTicker} entry={selectedEntry} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// 10-year monthly revenue chart (bars = revenue NT$B, lines = YoY% + MoM%)
// ---------------------------------------------------------------------------

interface ChartPoint {
  fiscal_ym: string;
  label: string;          // "Mar '26"
  revenue_b: number;      // revenue in NT$B (1e9 TWD)
  yoy_pct: number | null; // in percent (pre-multiplied by 100 for the chart)
  mom_pct: number | null;
}

function TickerRevenueChart({
  ticker,
  entry,
}: {
  ticker: string;
  entry: WatchlistEntry | undefined;
}) {
  const [rows, setRows] = useState<MonthlyRevenueRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!ticker) return;
    let cancelled = false;
    setLoading(true);
    setError(null);

    taiwanClient.monthlyRevenue([ticker], 120)   // 10 years of monthly data
      .then((res) => {
        if (!cancelled) setRows(res.data);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => { cancelled = true; };
  }, [ticker]);

  // Chart expects points in ascending chronological order (left = oldest,
  // right = newest) — the natural reading direction for a time series.
  const chartData: ChartPoint[] = useMemo(() => {
    return rows
      .slice()
      .sort((a, b) => a.fiscal_ym.localeCompare(b.fiscal_ym))
      .map((r) => ({
        fiscal_ym: r.fiscal_ym,
        label:     fmtFiscalYm(r.fiscal_ym),
        revenue_b: r.revenue_twd != null ? r.revenue_twd / 1e9 : 0,
        yoy_pct:   r.yoy_pct != null && !Number.isNaN(r.yoy_pct) ? r.yoy_pct * 100 : null,
        mom_pct:   r.mom_pct != null && !Number.isNaN(r.mom_pct) ? r.mom_pct * 100 : null,
      }));
  }, [rows]);

  // Show ~every 12th month label so a 10-year chart stays readable.
  const tickInterval = Math.max(0, Math.floor(chartData.length / 10) - 1);

  const headerName = entry ? `${entry.ticker} · ${entry.name}` : ticker;
  const headerSub = entry ? `${entry.subsector} · ${entry.market}` : "";

  return (
    <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
      <div className="flex items-center justify-between gap-3 px-5 py-3 border-b border-slate-100">
        <div className="flex items-center gap-2">
          <BarChart3 size={14} className="text-slate-400" />
          <span className="text-xs font-semibold text-slate-700">
            10-Year Monthly Revenue · {headerName}
          </span>
          {headerSub && (
            <span className="text-[10px] text-slate-400">{headerSub}</span>
          )}
          {loading && <Loader2 size={11} className="text-slate-300 animate-spin" />}
        </div>
        <div className="text-[10px] text-slate-400">
          {chartData.length} months · bars = NT$B · lines = YoY % / MoM %
        </div>
      </div>

      <div className="px-4 pt-3 pb-4">
        {error ? (
          <div className="py-10 text-center text-xs text-red-600">{error}</div>
        ) : chartData.length === 0 ? (
          <div className="py-10 text-center text-xs text-slate-400">
            {loading ? "Loading revenue history…" : "No revenue history for this ticker."}
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={340}>
            <ComposedChart data={chartData} margin={{ top: 8, right: 12, bottom: 0, left: -8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
              <XAxis
                dataKey="label"
                tick={{ fontSize: 10, fill: "#64748b" }}
                interval={tickInterval}
              />
              {/* Left axis — revenue NT$B */}
              <YAxis
                yAxisId="revenue"
                orientation="left"
                tick={{ fontSize: 10, fill: "#64748b" }}
                tickFormatter={(v: number) => v >= 100 ? v.toFixed(0) : v.toFixed(1)}
                width={55}
                label={{ value: "NT$B", position: "insideTopLeft", fontSize: 10, fill: "#94a3b8", offset: 0 }}
              />
              {/* Right axis — YoY / MoM percent */}
              <YAxis
                yAxisId="pct"
                orientation="right"
                tick={{ fontSize: 10, fill: "#64748b" }}
                tickFormatter={(v: number) => `${v.toFixed(0)}%`}
                width={45}
                label={{ value: "%", position: "insideTopRight", fontSize: 10, fill: "#94a3b8", offset: 0 }}
              />
              <Tooltip
                contentStyle={{ fontSize: 11, borderRadius: 6, border: "1px solid #e2e8f0" }}
                labelStyle={{ fontWeight: 600, color: "#0f172a" }}
                formatter={(value, name) => {
                  const v = typeof value === "number" ? value : null;
                  const nm = typeof name === "string" ? name : String(name);
                  if (v == null || Number.isNaN(v)) return ["—", nm];
                  if (nm === "Revenue") return [`NT$${v.toFixed(2)}B`, nm];
                  return [`${v.toFixed(1)}%`, nm];
                }}
              />
              <Legend wrapperStyle={{ fontSize: 10, paddingTop: 4 }} iconSize={10} />
              {/* Zero line on the % axis — visual anchor for YoY/MoM direction */}
              <ReferenceLine yAxisId="pct" y={0} stroke="#cbd5e1" strokeDasharray="2 2" />
              <Bar
                yAxisId="revenue"
                dataKey="revenue_b"
                name="Revenue"
                fill="#6366f1"
                fillOpacity={0.75}
                maxBarSize={8}
              />
              <Line
                yAxisId="pct"
                type="monotone"
                dataKey="yoy_pct"
                name="YoY %"
                stroke="#059669"
                strokeWidth={1.8}
                dot={false}
                activeDot={{ r: 4 }}
                connectNulls
              />
              <Line
                yAxisId="pct"
                type="monotone"
                dataKey="mom_pct"
                name="MoM %"
                stroke="#f59e0b"
                strokeWidth={1.2}
                strokeDasharray="3 3"
                dot={false}
                activeDot={{ r: 3 }}
                connectNulls
              />
            </ComposedChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}
