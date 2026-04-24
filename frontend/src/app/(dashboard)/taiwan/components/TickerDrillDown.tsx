"use client";

import { X } from "lucide-react";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";
import type { MonthlyRevenueRow, TickerDetail } from "@/lib/api/taiwanClient";

interface Props {
  ticker: string;
  detail: TickerDetail | null;
  history: MonthlyRevenueRow[];
  onClose: () => void;
}

export default function TickerDrillDown({ ticker, detail, history, onClose }: Props) {
  const chartData = history.map((r) => ({
    ym: r.fiscal_ym,
    revenue: (r.revenue_twd ?? 0) / 1e9,
    yoy: (r.yoy_pct ?? 0) * 100,
  }));

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-6"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-xl shadow-2xl w-full max-w-4xl max-h-[90vh] flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4 px-6 py-4 border-b border-slate-200 shrink-0">
          <div>
            <h2 className="text-base font-bold text-slate-900">
              <span className="font-mono text-indigo-700 mr-2">{ticker}</span>
              {detail?.name ?? ""}
            </h2>
            <p className="text-[11px] text-slate-500 mt-0.5">
              {detail?.market} · {detail?.subsector}
            </p>
          </div>
          <button
            onClick={onClose}
            className="p-1 text-slate-400 hover:text-slate-700 transition-colors"
            title="Close"
          >
            <X size={18} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-5">
          <section>
            <h3 className="text-xs font-bold uppercase tracking-wider text-slate-500 mb-2">
              Monthly revenue — last {history.length} months (TWD bn)
            </h3>
            <div className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chartData}>
                  <CartesianGrid stroke="#e2e8f0" />
                  <XAxis dataKey="ym" fontSize={10} />
                  <YAxis fontSize={10} />
                  <Tooltip />
                  <Line type="monotone" dataKey="revenue" stroke="#4f46e5" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </section>

          <section>
            <h3 className="text-xs font-bold uppercase tracking-wider text-slate-500 mb-2">
              Material information
            </h3>
            <p className="text-xs text-slate-400 italic">
              Coming in Plan 2 — material info feed + side-by-side bilingual view.
            </p>
          </section>
        </div>
      </div>
    </div>
  );
}
