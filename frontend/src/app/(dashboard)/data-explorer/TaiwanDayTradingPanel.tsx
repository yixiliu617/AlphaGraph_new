"use client";

/**
 * TaiwanDayTradingPanel — market-wide day-trading (當日沖銷) activity on TWSE.
 *
 * Top: time-series of day-trading share of market (shares % + dollar-buy %
 *      + dollar-sell %). Reveals whether day-trading is rising/falling
 *      relative to real order flow.
 *
 * Bottom: per-ticker table for one selected trading date. Default is the
 *      latest available date. Sortable by shares, buy value, or sell value.
 *
 * Data source: TWSE TWTB4U endpoint. Scraper writes two parquets:
 *   backend/data/taiwan/day_trading/summary.parquet  (1 row/trading-day)
 *   backend/data/taiwan/day_trading/detail.parquet   (1 row/ticker/day)
 *
 * API: GET /api/v1/taiwan/day-trading/{summary,detail,dates}
 */

import { useEffect, useMemo, useState } from "react";
import { Loader2, TrendingUp } from "lucide-react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  taiwanClient,
  type DayTradingSummary,
  type DayTradingDetail,
} from "@/lib/api/taiwanClient";

type SortKey = "buy_value_twd" | "sell_value_twd" | "shares";

const SORT_OPTIONS: { key: SortKey; label: string }[] = [
  { key: "buy_value_twd",  label: "Buy NT$"  },
  { key: "sell_value_twd", label: "Sell NT$" },
  { key: "shares",         label: "Shares"   },
];

// NT$ in billions — day-trading dollar volume is always 1B+ TWD per top ticker.
function fmtNTB(twd: number | null | undefined): string {
  if (twd == null || Number.isNaN(twd)) return "—";
  const b = twd / 1e9;
  if (b >= 100) return b.toFixed(0);
  if (b >= 10)  return b.toFixed(1);
  return b.toFixed(2);
}

// Shares in millions.
function fmtShareM(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  const m = n / 1e6;
  if (m >= 100) return m.toFixed(0);
  if (m >= 10)  return m.toFixed(1);
  return m.toFixed(2);
}

function fmtPct(p: number | null | undefined): string {
  if (p == null || Number.isNaN(p)) return "—";
  return `${p.toFixed(2)}%`;
}

// ---------------------------------------------------------------------------

export default function TaiwanDayTradingPanel() {
  const [summary,    setSummary]    = useState<DayTradingSummary[]>([]);
  const [dates,      setDates]      = useState<string[]>([]);
  const [detail,     setDetail]     = useState<DayTradingDetail[]>([]);
  const [pickedDate, setPickedDate] = useState<string>("");
  const [sortKey,    setSortKey]    = useState<SortKey>("buy_value_twd");

  const [loadingSummary, setLoadingSummary] = useState(true);
  const [loadingDetail,  setLoadingDetail]  = useState(false);
  const [error,          setError]          = useState<string | null>(null);

  // Initial load: fetch summary time-series + available dates.
  useEffect(() => {
    let cancelled = false;
    setLoadingSummary(true);
    setError(null);
    Promise.all([
      taiwanClient.dayTradingSummary(),
      taiwanClient.dayTradingDates(),
    ])
      .then(([s, d]) => {
        if (cancelled) return;
        setSummary(s.data ?? []);
        const ds = d.data ?? [];
        setDates(ds);
        if (ds.length && !pickedDate) setPickedDate(ds[0]);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoadingSummary(false);
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Fetch per-ticker detail whenever the picked date or sort changes.
  useEffect(() => {
    if (!pickedDate) return;
    let cancelled = false;
    setLoadingDetail(true);
    taiwanClient.dayTradingDetail(pickedDate, 100, sortKey)
      .then((r) => { if (!cancelled) setDetail(r.data ?? []); })
      .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : String(err)); })
      .finally(() => { if (!cancelled) setLoadingDetail(false); });
    return () => { cancelled = true; };
  }, [pickedDate, sortKey]);

  // Chart data — sort ascending by date so left→right = oldest→newest.
  const chartData = useMemo(() => {
    return summary.slice().sort((a, b) => a.date.localeCompare(b.date)).map((r) => ({
      date:       r.date,
      shares_pct: r.total_shares_pct,
      buy_pct:    r.total_buy_value_pct,
      sell_pct:   r.total_sell_value_pct,
    }));
  }, [summary]);

  const tickInterval = Math.max(0, Math.floor(chartData.length / 10) - 1);

  const latestSummary = summary.length > 0 ? summary[summary.length - 1] : null;

  return (
    <div className="space-y-4">

      {/* ── Summary chart ── */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
        <div className="flex items-center justify-between gap-3 px-5 py-3 border-b border-slate-100">
          <div className="flex items-center gap-2">
            <TrendingUp size={14} className="text-slate-400" />
            <span className="text-xs font-semibold text-slate-700">
              TWSE Day-Trading Share of Market (當日沖銷相關統計)
            </span>
            {loadingSummary && <Loader2 size={11} className="text-slate-300 animate-spin" />}
          </div>
          {latestSummary && (
            <span className="text-[10px] text-slate-400 tabular-nums">
              Latest {latestSummary.date}: shares {fmtPct(latestSummary.total_shares_pct)} ·
              buy {fmtPct(latestSummary.total_buy_value_pct)} ·
              sell {fmtPct(latestSummary.total_sell_value_pct)}
            </span>
          )}
        </div>
        <div className="px-4 pt-3 pb-4">
          {error ? (
            <div className="py-10 text-center text-xs text-red-600">{error}</div>
          ) : chartData.length === 0 ? (
            <div className="py-10 text-center text-xs text-slate-400">
              {loadingSummary ? "Loading day-trading history…"
                : "No day-trading data yet. Run twse_day_trading.py scrape / backfill."}
            </div>
          ) : chartData.length === 1 ? (
            <div className="py-8 text-center text-xs text-slate-500">
              Only one trading day in storage so far ({chartData[0].date}).
              Run <code className="px-1 py-0.5 bg-slate-100 rounded">python tools/web_scraper/twse_day_trading.py backfill</code>{" "}
              to load history (2014-01-06 onward, ~100 min).
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={chartData} margin={{ top: 8, right: 12, bottom: 0, left: -8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 10, fill: "#64748b" }}
                  interval={tickInterval}
                />
                <YAxis
                  tick={{ fontSize: 10, fill: "#64748b" }}
                  tickFormatter={(v: number) => `${v.toFixed(0)}%`}
                  width={45}
                  label={{ value: "% of market", position: "insideTopLeft", fontSize: 10, fill: "#94a3b8", offset: 0 }}
                />
                <Tooltip
                  contentStyle={{ fontSize: 11, borderRadius: 6, border: "1px solid #e2e8f0" }}
                  labelStyle={{ fontWeight: 600, color: "#0f172a" }}
                  formatter={(value, name) => {
                    const v = typeof value === "number" ? value : null;
                    if (v == null) return ["—", String(name)];
                    return [`${v.toFixed(2)}%`, String(name)];
                  }}
                />
                <Legend wrapperStyle={{ fontSize: 10, paddingTop: 4 }} iconSize={10} />
                <Line yAxisId={0} type="monotone" dataKey="shares_pct" name="Shares %"    stroke="#6366f1" strokeWidth={1.8} dot={false} activeDot={{ r: 4 }} />
                <Line yAxisId={0} type="monotone" dataKey="buy_pct"    name="Buy value %" stroke="#059669" strokeWidth={1.5} dot={false} activeDot={{ r: 3 }} />
                <Line yAxisId={0} type="monotone" dataKey="sell_pct"   name="Sell value %" stroke="#dc2626" strokeWidth={1.5} strokeDasharray="3 3" dot={false} activeDot={{ r: 3 }} />
              </LineChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>

      {/* ── Per-ticker detail table ── */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
        <div className="flex items-center justify-between gap-3 px-5 py-3 border-b border-slate-100">
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-slate-700">
              Day-Trading by Ticker · {pickedDate || "(no date)"}
            </span>
            {loadingDetail && <Loader2 size={11} className="text-slate-300 animate-spin" />}
          </div>
          <div className="flex items-center gap-3">
            {/* Date selector */}
            <div className="flex items-center gap-2">
              <label className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider">Date</label>
              <select
                value={pickedDate}
                onChange={(e) => setPickedDate(e.target.value)}
                className="h-7 px-2 rounded-md border border-slate-200 bg-slate-50 text-xs outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 text-slate-700"
              >
                {dates.length === 0 ? (
                  <option value="">(no dates)</option>
                ) : (
                  dates.map((d) => <option key={d} value={d}>{d}</option>)
                )}
              </select>
            </div>
            {/* Sort selector */}
            <div className="flex items-center gap-1 border border-slate-200 rounded-md bg-slate-50 p-0.5">
              {SORT_OPTIONS.map((o) => (
                <button
                  key={o.key}
                  onClick={() => setSortKey(o.key)}
                  className={`text-[10px] font-semibold px-2 py-0.5 rounded ${
                    sortKey === o.key
                      ? "bg-white text-indigo-700 shadow-sm"
                      : "text-slate-500 hover:text-slate-700"
                  }`}
                >
                  {o.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="overflow-x-auto max-h-[600px] overflow-y-auto">
          <table className="w-full text-[11px] border-collapse">
            <thead className="sticky top-0 bg-white z-10">
              <tr className="border-b border-slate-200">
                <th className="text-left font-semibold text-slate-500 px-3 py-2 w-[48px]">#</th>
                <th className="text-left font-semibold text-slate-500 px-2 py-2 w-[60px]">Ticker</th>
                <th className="text-left font-semibold text-slate-500 px-2 py-2">Name</th>
                <th className="text-right font-semibold text-slate-500 px-2 py-2 tabular-nums">Shares (M)</th>
                <th className="text-right font-semibold text-slate-500 px-2 py-2 tabular-nums">Buy (NT$B)</th>
                <th className="text-right font-semibold text-slate-500 px-2 py-2 tabular-nums">Sell (NT$B)</th>
                <th className="text-right font-semibold text-slate-500 px-2 py-2 tabular-nums">Buy−Sell (NT$M)</th>
                <th className="text-left font-semibold text-slate-500 px-2 py-2">Flag</th>
              </tr>
            </thead>
            <tbody>
              {detail.length === 0 ? (
                <tr>
                  <td colSpan={8} className="text-center text-xs text-slate-400 py-10">
                    {loadingDetail ? "Loading…" : "No detail rows for this date."}
                  </td>
                </tr>
              ) : (
                detail.map((r, i) => {
                  const spread = (r.buy_value_twd ?? 0) - (r.sell_value_twd ?? 0);
                  const spreadColor = spread > 0 ? "text-emerald-600" : spread < 0 ? "text-red-600" : "text-slate-500";
                  return (
                    <tr key={r.ticker} className="border-b border-slate-100 hover:bg-indigo-50/40">
                      <td className="px-3 py-1.5 text-slate-400 tabular-nums">{i + 1}</td>
                      <td className="px-2 py-1.5 font-mono font-semibold text-slate-800">{r.ticker}</td>
                      <td className="px-2 py-1.5 text-slate-700 truncate max-w-[160px]">{r.name}</td>
                      <td className="px-2 py-1.5 text-right tabular-nums text-slate-700">{fmtShareM(r.shares)}</td>
                      <td className="px-2 py-1.5 text-right tabular-nums font-semibold text-slate-800">{fmtNTB(r.buy_value_twd)}</td>
                      <td className="px-2 py-1.5 text-right tabular-nums font-semibold text-slate-800">{fmtNTB(r.sell_value_twd)}</td>
                      <td className={`px-2 py-1.5 text-right tabular-nums font-semibold ${spreadColor}`}>
                        {spread > 0 ? "+" : ""}{(spread / 1e6).toFixed(0)}
                      </td>
                      <td className="px-2 py-1.5 text-[10px] text-amber-600">{r.suspension_flag}</td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        <div className="px-5 py-2 border-t border-slate-100 text-[10px] text-slate-400 flex justify-between">
          <span>{detail.length} tickers · top-100 by {SORT_OPTIONS.find((o) => o.key === sortKey)?.label}</span>
          <span>Source: TWSE TWTB4U (當日沖銷交易標的及成交量值)</span>
        </div>
      </div>
    </div>
  );
}
