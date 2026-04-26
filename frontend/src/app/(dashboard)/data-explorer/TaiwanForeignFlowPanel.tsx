"use client";

/**
 * TaiwanForeignFlowPanel — 三大法人 daily buy/sell flow on TWSE.
 *
 * Top: time-series of NET BUY (NT$B) per institutional-investor group.
 *      Default lines:
 *        Foreign     (外資及陸資)  — solid, indigo, the headline signal
 *        Trust       (投信)        — solid, amber
 *        Prop hedge  (自營商-避險)  — dashed, slate
 *        Total       (合計)         — dashed, emerald  (= prop+trust+foreign+foreign_prop)
 *      Toggle pills below the chart to add/remove series.
 *
 * Bottom: latest-day breakdown table — buy / sell / net per investor type.
 *
 * Data source: TWSE BFI82U via tools/web_scraper/twse_foreign_flow.py.
 * Schema evolves with time (4 → 5 → 6 rows); lines just go missing pre-split.
 */

import { useEffect, useMemo, useState } from "react";
import { Loader2, TrendingUp } from "lucide-react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  taiwanClient,
  type ForeignFlowInvestor,
  type ForeignFlowRow,
} from "@/lib/api/taiwanClient";

// Chart series config. Two flavors:
//   - "stitched" pseudo-keys coalesce historical schema variants into one
//     visually continuous line (the chart's headline signal). For example
//     `foreign_unified` = foreign ?? foreign_legacy ?? foreign_only, picking
//     whichever key has a value on a given date (each era has only one).
//   - real keys (e.g. foreign_legacy, prop_legacy) are still selectable as
//     drilldowns when the user wants the strictly-defined-by-TWSE figure.
type SeriesKey =
  | "foreign_unified"   // pseudo: foreign | foreign_legacy | foreign_only
  | "prop_unified"      // pseudo: (prop_self + prop_hedge) | prop_legacy
  | ForeignFlowInvestor;

const SERIES: { key: SeriesKey; label: string; color: string; dashed: boolean; defaultOn: boolean }[] = [
  { key: "foreign_unified", label: "Foreign (外資)",         color: "#6366f1", dashed: false, defaultOn: true  },
  { key: "trust",           label: "Trust (投信)",           color: "#f59e0b", dashed: false, defaultOn: true  },
  { key: "prop_unified",    label: "Prop (自營商)",          color: "#0ea5e9", dashed: false, defaultOn: true  },
  { key: "total",           label: "Total (合計)",           color: "#059669", dashed: true,  defaultOn: true  },
  // Drilldowns (off by default) — strict TWSE-label rows
  { key: "foreign_prop",    label: "Foreign prop",          color: "#a855f7", dashed: true,  defaultOn: false },
  { key: "prop_hedge",      label: "Prop hedge (自營-避險)",  color: "#0284c7", dashed: true,  defaultOn: false },
  { key: "prop_self",       label: "Prop self (自營-自行)",   color: "#7dd3fc", dashed: true,  defaultOn: false },
];

function fmtNetB(twd: number | null | undefined): string {
  if (twd == null || Number.isNaN(twd)) return "—";
  const b = twd / 1e9;
  const sign = b > 0 ? "+" : "";
  return `${sign}${b.toFixed(2)}`;
}

// Aggregate market-wide values are large but the trailing decimals matter
// (479.80B reads very differently from 480B when comparing dates side-by-side).
// Always show 2 decimals.
function fmtNTB(twd: number | null | undefined): string {
  if (twd == null || Number.isNaN(twd)) return "—";
  return (twd / 1e9).toFixed(2);
}

// Pretty label for the latest-day table.
const INVESTOR_LABEL: Record<ForeignFlowInvestor, string> = {
  foreign:        "Foreign (外資)",
  foreign_prop:   "Foreign prop (外資自營商)",
  foreign_legacy: "Foreign (legacy)",
  trust:          "Trust (投信)",
  prop_self:      "Prop self (自營-自行)",
  prop_hedge:     "Prop hedge (自營-避險)",
  prop_legacy:    "Prop (legacy)",
  total:          "Total (合計)",
};

export default function TaiwanForeignFlowPanel() {
  const [rows,   setRows]   = useState<ForeignFlowRow[]>([]);
  const [dates,  setDates]  = useState<string[]>([]);
  const [active, setActive] = useState<Set<SeriesKey>>(
    () => new Set(SERIES.filter((s) => s.defaultOn).map((s) => s.key)),
  );
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([
      taiwanClient.foreignFlow(),
      taiwanClient.foreignFlowDates(),
    ])
      .then(([ff, dts]) => {
        if (cancelled) return;
        setRows(ff.data ?? []);
        setDates(dts.data ?? []);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, []);

  // Pivot rows -> {date, foreign: ..., foreign_unified: ..., ...} (NT$B units).
  // Compute the two "unified" pseudo-series so a 22-year line stays continuous
  // across the 2008/2014/2020 schema breaks.
  const chartData = useMemo(() => {
    const byDate = new Map<string, Record<string, number | string>>();
    for (const r of rows) {
      if (!byDate.has(r.date)) byDate.set(r.date, { date: r.date });
      byDate.get(r.date)![r.investor_type] = (r.net_buy_twd ?? 0) / 1e9;
    }
    // Add the unified pseudo-keys.
    for (const point of byDate.values()) {
      const f =
        (point["foreign"]        as number | undefined) ??
        (point["foreign_legacy"] as number | undefined) ??
        (point["foreign_only"]   as number | undefined);
      if (f !== undefined) point["foreign_unified"] = f;

      const ps = point["prop_self"]   as number | undefined;
      const ph = point["prop_hedge"]  as number | undefined;
      const pl = point["prop_legacy"] as number | undefined;
      if (ps !== undefined || ph !== undefined) {
        point["prop_unified"] = (ps ?? 0) + (ph ?? 0);
      } else if (pl !== undefined) {
        point["prop_unified"] = pl;
      }
    }
    return Array.from(byDate.values()).sort((a, b) =>
      String(a.date).localeCompare(String(b.date)),
    );
  }, [rows]);

  const tickInterval = Math.max(0, Math.floor(chartData.length / 10) - 1);

  const latestDate = dates[0] ?? "";
  const latestRows = useMemo(
    () => rows.filter((r) => r.date === latestDate),
    [rows, latestDate],
  );

  function toggleSeries(k: SeriesKey) {
    setActive((prev) => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });
  }

  return (
    <div className="space-y-4">

      {/* ── Net-buy time-series chart ── */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
        <div className="flex items-center justify-between gap-3 px-5 py-3 border-b border-slate-100">
          <div className="flex items-center gap-2">
            <TrendingUp size={14} className="text-slate-400" />
            <span className="text-xs font-semibold text-slate-700">
              三大法人 Daily Net Buy (NT$B) · TWSE
            </span>
            {loading && <Loader2 size={11} className="text-slate-300 animate-spin" />}
          </div>
          {latestDate && (
            <span className="text-[10px] text-slate-400">
              Latest {latestDate} ·
              {" "}foreign {fmtNetB(latestRows.find((r) => r.investor_type === "foreign")?.net_buy_twd)}B ·
              {" "}total {fmtNetB(latestRows.find((r) => r.investor_type === "total")?.net_buy_twd)}B
            </span>
          )}
        </div>

        <div className="px-4 pt-3 pb-2">
          {error ? (
            <div className="py-10 text-center text-xs text-red-600">{error}</div>
          ) : chartData.length === 0 ? (
            <div className="py-10 text-center text-xs text-slate-400">
              {loading ? "Loading…" : "No data yet. Run twse_foreign_flow.py scrape / backfill."}
            </div>
          ) : chartData.length === 1 ? (
            <div className="py-8 text-center text-xs text-slate-500">
              Only one trading day in storage so far ({String(chartData[0].date)}).
              Run <code className="px-1 py-0.5 bg-slate-100 rounded">python tools/web_scraper/twse_foreign_flow.py backfill</code>{" "}
              for the 22-year history.
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={320}>
              <LineChart data={chartData} margin={{ top: 8, right: 12, bottom: 0, left: -8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                <XAxis dataKey="date" tick={{ fontSize: 10, fill: "#64748b" }} interval={tickInterval} />
                <YAxis
                  tick={{ fontSize: 10, fill: "#64748b" }}
                  tickFormatter={(v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(0)}B`}
                  width={55}
                />
                <Tooltip
                  contentStyle={{ fontSize: 11, borderRadius: 6, border: "1px solid #e2e8f0" }}
                  labelStyle={{ fontWeight: 600, color: "#0f172a" }}
                  formatter={(value, name) => {
                    const v = typeof value === "number" ? value : null;
                    if (v == null) return ["—", String(name)];
                    return [`${v >= 0 ? "+" : ""}${v.toFixed(2)}B`, String(name)];
                  }}
                />
                <Legend wrapperStyle={{ fontSize: 10, paddingTop: 4 }} iconSize={10} />
                <ReferenceLine y={0} stroke="#cbd5e1" strokeDasharray="2 2" />
                {SERIES.filter((s) => active.has(s.key)).map((s) => (
                  <Line
                    key={s.key}
                    type="monotone"
                    dataKey={s.key}
                    name={s.label}
                    stroke={s.color}
                    strokeWidth={s.key === "foreign" ? 2 : 1.4}
                    strokeDasharray={s.dashed ? "3 3" : undefined}
                    dot={false}
                    activeDot={{ r: 4 }}
                    connectNulls
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
          )}
        </div>

        {/* Series toggle pills */}
        <div className="flex flex-wrap items-center gap-1 px-5 py-2 border-t border-slate-100">
          <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider mr-2">
            Series
          </span>
          {SERIES.map((s) => {
            const on = active.has(s.key);
            return (
              <button
                key={s.key}
                onClick={() => toggleSeries(s.key)}
                className={`text-[10px] font-semibold px-2 py-0.5 rounded border transition-colors ${
                  on
                    ? "border-transparent text-white"
                    : "bg-white text-slate-400 border-slate-200 hover:bg-slate-50"
                }`}
                style={on ? { backgroundColor: s.color } : undefined}
              >
                {s.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* ── Latest-day breakdown table ── */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
        <div className="flex items-center justify-between gap-3 px-5 py-3 border-b border-slate-100">
          <span className="text-xs font-semibold text-slate-700">
            Latest Day Breakdown · {latestDate || "(no date)"}
          </span>
          {loading && <Loader2 size={11} className="text-slate-300 animate-spin" />}
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-[11px] border-collapse">
            <thead>
              <tr className="border-b border-slate-200 bg-slate-50">
                <th className="text-left font-semibold text-slate-500 px-3 py-2">Investor type</th>
                <th className="text-right font-semibold text-slate-500 px-3 py-2 tabular-nums">Buy (NT$B)</th>
                <th className="text-right font-semibold text-slate-500 px-3 py-2 tabular-nums">Sell (NT$B)</th>
                <th className="text-right font-semibold text-slate-500 px-3 py-2 tabular-nums">Net (NT$B)</th>
              </tr>
            </thead>
            <tbody>
              {latestRows.length === 0 ? (
                <tr>
                  <td colSpan={4} className="text-center text-xs text-slate-400 py-6">
                    {loading ? "Loading…" : "No latest-day rows."}
                  </td>
                </tr>
              ) : (
                latestRows
                  .slice()
                  .sort((a, b) => Math.abs(b.net_buy_twd) - Math.abs(a.net_buy_twd))
                  .map((r) => {
                    const net = r.net_buy_twd ?? 0;
                    const netColor = net > 0 ? "text-emerald-600" : net < 0 ? "text-red-600" : "text-slate-500";
                    const isTotal = r.investor_type === "total";
                    return (
                      <tr
                        key={r.investor_type}
                        className={`border-b border-slate-100 ${isTotal ? "font-semibold bg-slate-50/60" : "hover:bg-indigo-50/40"}`}
                      >
                        <td className="px-3 py-1.5 text-slate-800">{INVESTOR_LABEL[r.investor_type]}</td>
                        <td className="px-3 py-1.5 text-right tabular-nums text-slate-700">{fmtNTB(r.buy_value_twd)}</td>
                        <td className="px-3 py-1.5 text-right tabular-nums text-slate-700">{fmtNTB(r.sell_value_twd)}</td>
                        <td className={`px-3 py-1.5 text-right tabular-nums font-semibold ${netColor}`}>
                          {fmtNetB(r.net_buy_twd)}
                        </td>
                      </tr>
                    );
                  })
              )}
            </tbody>
          </table>
        </div>

        <div className="px-5 py-2 border-t border-slate-100 text-[10px] text-slate-400 flex justify-between">
          <span>{rows.length} rows · {dates.length} trading day(s)</span>
          <span>Source: TWSE BFI82U (三大法人買賣金額統計表)</span>
        </div>
      </div>
    </div>
  );
}
