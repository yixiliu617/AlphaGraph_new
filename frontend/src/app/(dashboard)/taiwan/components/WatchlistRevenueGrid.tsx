"use client";

import { useMemo, useState } from "react";
import type { WatchlistEntry, MonthlyRevenueRow } from "@/lib/api/taiwanClient";

interface Props {
  watchlist: WatchlistEntry[];
  revenue: Record<string, MonthlyRevenueRow[]>;
  onOpenTicker: (ticker: string) => void;
}

function fmtTWD(n: number | null | undefined): string {
  if (n == null) return "—";
  if (Math.abs(n) >= 1e9) return `${(n / 1e9).toFixed(1)} B`;
  if (Math.abs(n) >= 1e6) return `${(n / 1e6).toFixed(1)} M`;
  return n.toLocaleString();
}

function fmtPct(p: number | null | undefined): string {
  if (p == null) return "—";
  const v = p * 100;
  const s = v >= 0 ? "+" : "";
  return `${s}${v.toFixed(1)}%`;
}

function yoyCellClass(p: number | null | undefined): string {
  if (p == null) return "text-slate-400";
  if (p > 0.15) return "text-green-700 bg-green-50";
  if (p > 0.05) return "text-green-600 bg-green-50/50";
  if (p < -0.15) return "text-red-700 bg-red-50";
  if (p < -0.05) return "text-red-600 bg-red-50/50";
  return "text-slate-600";
}

export default function WatchlistRevenueGrid({ watchlist, revenue, onOpenTicker }: Props) {
  const subsectors = useMemo(
    () => Array.from(new Set(watchlist.map((w) => w.subsector))).sort(),
    [watchlist],
  );
  const [activeSubsector, setActiveSubsector] = useState<string>(subsectors[0] ?? "");
  const rows = watchlist.filter((w) => !activeSubsector || w.subsector === activeSubsector);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        {subsectors.map((s) => (
          <button
            key={s}
            onClick={() => setActiveSubsector(s)}
            className={`px-3 py-1 text-xs font-medium rounded-md border transition-colors ${
              activeSubsector === s
                ? "border-indigo-600 bg-indigo-600 text-white"
                : "border-slate-200 bg-white text-slate-600 hover:border-indigo-300 hover:text-indigo-600"
            }`}
          >
            {s}
          </button>
        ))}
      </div>

      <div className="overflow-x-auto bg-white rounded-xl border border-slate-200">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-slate-50 border-b border-slate-200 text-[11px] font-bold text-slate-500 uppercase tracking-wider">
              <th className="text-left px-4 py-2">Ticker</th>
              <th className="text-left px-4 py-2">Company</th>
              <th className="text-right px-4 py-2">Latest Revenue (TWD)</th>
              <th className="text-right px-4 py-2">YoY%</th>
              <th className="text-right px-4 py-2">MoM%</th>
              <th className="text-right px-4 py-2">YTD%</th>
              <th className="text-right px-4 py-2">Fiscal Ym</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((w) => {
              const hist = revenue[w.ticker] ?? [];
              const latest = hist.length > 0 ? hist[hist.length - 1] : null;
              return (
                <tr
                  key={w.ticker}
                  className="border-b border-slate-100 last:border-0 hover:bg-slate-50 cursor-pointer"
                  onClick={() => onOpenTicker(w.ticker)}
                >
                  <td className="px-4 py-2 font-mono font-semibold text-indigo-700">{w.ticker}</td>
                  <td className="px-4 py-2 text-slate-800">{w.name}</td>
                  <td className="px-4 py-2 text-right text-slate-700 tabular-nums">
                    {fmtTWD(latest?.revenue_twd ?? null)}
                  </td>
                  <td className={`px-4 py-2 text-right tabular-nums ${yoyCellClass(latest?.yoy_pct)}`}>
                    {fmtPct(latest?.yoy_pct)}
                  </td>
                  <td className="px-4 py-2 text-right text-slate-700 tabular-nums">
                    {fmtPct(latest?.mom_pct)}
                  </td>
                  <td className="px-4 py-2 text-right text-slate-700 tabular-nums">
                    {fmtPct(latest?.ytd_pct)}
                  </td>
                  <td className="px-4 py-2 text-right text-slate-500 tabular-nums font-mono text-xs">
                    {latest?.fiscal_ym ?? "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
