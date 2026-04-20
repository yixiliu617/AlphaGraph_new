"use client";

import React, { useState, useEffect, useMemo } from "react";
import { Loader2 } from "lucide-react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";
import {
  pricingClient,
  type PricingRow,
  type PricingCategory,
  type CamelProduct,
  type CamelRow,
  type GpuPriceRow,
  type GpuHistoryRow,
} from "@/lib/api/pricingClient";

const CATEGORY_LABELS: Record<string, string> = {
  cpu: "CPUs",
  memory: "Memory (DRAM)",
  "video-card": "Video Cards (GPUs)",
  storage: "Storage (SSD / HDD)",
  monitor: "Monitors",
  "power-supply": "Power Supplies",
};

const CATEGORY_ORDER = [
  "memory",
  "video-card",
  "cpu",
  "storage",
  "monitor",
  "power-supply",
];

const COLORS = [
  "#6366f1", "#f59e0b", "#10b981", "#ef4444", "#8b5cf6",
  "#06b6d4", "#f97316", "#ec4899", "#14b8a6", "#a855f7",
  "#84cc16", "#e11d48", "#0ea5e9",
];

function formatUSD(v: number) {
  if (v >= 1000) return `$${(v / 1000).toFixed(1)}K`;
  return `$${v}`;
}

interface ChartData {
  label: string;
  [component: string]: number | string;
}

function CategoryChart({
  category,
  rows,
  selectedComponents,
}: {
  category: string;
  rows: PricingRow[];
  selectedComponents: string[];
}) {
  const filtered = rows.filter(
    (r) =>
      r.category === category &&
      (selectedComponents.length === 0 ||
        selectedComponents.includes(r.component)),
  );

  const components = [
    ...new Set(filtered.map((r) => r.component)),
  ].sort();

  // Use date as key (works for both monthly and weekly)
  const xKey = filtered[0]?.date ? "date" : "month";
  const byLabel = new Map<string, Record<string, number>>();
  for (const r of filtered) {
    const label = r.date || r.month;
    if (!byLabel.has(label)) byLabel.set(label, {});
    byLabel.get(label)![r.component] = r.avg_price_usd;
  }

  const chartData: ChartData[] = [...byLabel.entries()]
    .map(([label, values]) => ({ label, ...values }))
    .sort((a, b) => {
      // ISO dates ("2024-10-07") sort correctly as strings
      // Month labels ("Nov 2024") need date parsing
      const da = new Date(a.label + (a.label.length <= 8 ? "" : ""));
      const db = new Date(b.label + (b.label.length <= 8 ? "" : ""));
      if (!isNaN(da.getTime()) && !isNaN(db.getTime())) return da.getTime() - db.getTime();
      return a.label.localeCompare(b.label);
    });

  if (chartData.length === 0) return null;

  // Format date labels for display
  const formatLabel = (val: string) => {
    if (!val || val.length < 7) return val;
    // "2024-10-07" -> "Oct 07"
    try {
      const d = new Date(val + "T00:00:00");
      return d.toLocaleDateString("en-US", { month: "short", day: "2-digit" });
    } catch {
      return val;
    }
  };

  return (
    <div className="bg-white rounded-lg border border-slate-200 p-4">
      <h3 className="text-sm font-semibold text-slate-800 mb-3">
        {CATEGORY_LABELS[category] ?? category}
      </h3>
      <ResponsiveContainer width="100%" height={320}>
        <LineChart data={chartData}>
          <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
          <XAxis
            dataKey="label"
            tick={{ fontSize: 9 }}
            tickFormatter={formatLabel}
            interval={Math.max(0, Math.floor(chartData.length / 8) - 1)}
          />
          <YAxis
            tick={{ fontSize: 10 }}
            tickFormatter={formatUSD}
            width={55}
          />
          <Tooltip
            formatter={(v: number, name: string) => [`$${v.toFixed(0)}`, name]}
            labelStyle={{ fontWeight: 600, fontSize: 12 }}
            contentStyle={{ fontSize: 11 }}
          />
          <Legend
            wrapperStyle={{ fontSize: 10, paddingTop: 4 }}
            iconSize={8}
          />
          {components.map((comp, i) => (
            <Line
              key={comp}
              type="monotone"
              dataKey={comp}
              stroke={COLORS[i % COLORS.length]}
              strokeWidth={1.5}
              dot={{ r: 1.5 }}
              activeDot={{ r: 4 }}
              connectNulls
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export default function SemiPricingPanel() {
  const [categories, setCategories] = useState<PricingCategory[]>([]);
  const [rows, setRows] = useState<PricingRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeCategory, setActiveCategory] = useState<string | null>(null);
  const [selectedComponents, setSelectedComponents] = useState<
    Record<string, string[]>
  >({});
  const [granularity, setGranularity] = useState<"monthly" | "weekly">("monthly");
  const [hasWeekly, setHasWeekly] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([
      pricingClient.getCategories(),
      pricingClient.getTrends(undefined, undefined, granularity),
    ])
      .then(([catResult, allRows]) => {
        if (cancelled) return;
        setCategories(catResult.categories);
        setHasWeekly(catResult.has_weekly);
        setRows(allRows);
        if (catResult.categories.length > 0 && !activeCategory) {
          const first =
            CATEGORY_ORDER.find((c) =>
              catResult.categories.some((cat) => cat.category === c),
            ) ?? catResult.categories[0].category;
          setActiveCategory(first);
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [granularity]); // eslint-disable-line react-hooks/exhaustive-deps

  const sortedCategories = useMemo(
    () =>
      [...categories].sort(
        (a, b) =>
          (CATEGORY_ORDER.indexOf(a.category) === -1
            ? 99
            : CATEGORY_ORDER.indexOf(a.category)) -
          (CATEGORY_ORDER.indexOf(b.category) === -1
            ? 99
            : CATEGORY_ORDER.indexOf(b.category)),
      ),
    [categories],
  );

  const toggleComponent = (cat: string, comp: string) => {
    setSelectedComponents((prev) => {
      const current = prev[cat] ?? [];
      if (current.includes(comp)) {
        return { ...prev, [cat]: current.filter((c) => c !== comp) };
      }
      return { ...prev, [cat]: [...current, comp] };
    });
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 size={20} className="animate-spin text-slate-400" />
        <span className="ml-2 text-sm text-slate-500">
          Loading pricing data...
        </span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-center py-12 text-sm text-red-600">{error}</div>
    );
  }

  if (categories.length === 0) {
    return (
      <div className="text-center py-12 text-sm text-slate-500">
        No pricing data available. Run the PCPartPicker scraper first.
      </div>
    );
  }

  const activeCat = sortedCategories.find(
    (c) => c.category === activeCategory,
  );

  return (
    <div className="space-y-4">
      {/* Category tab bar + granularity toggle */}
      <div className="flex items-center gap-1 flex-wrap">
        {hasWeekly && (
          <div className="flex items-center bg-slate-100 rounded-md p-0.5 mr-2">
            <button
              onClick={() => setGranularity("monthly")}
              className={`h-6 px-2 rounded text-[10px] font-semibold transition-colors ${
                granularity === "monthly"
                  ? "bg-white text-slate-800 shadow-sm"
                  : "text-slate-500"
              }`}
            >
              Monthly
            </button>
            <button
              onClick={() => setGranularity("weekly")}
              className={`h-6 px-2 rounded text-[10px] font-semibold transition-colors ${
                granularity === "weekly"
                  ? "bg-white text-slate-800 shadow-sm"
                  : "text-slate-500"
              }`}
            >
              Weekly
            </button>
          </div>
        )}
        {sortedCategories.map((cat) => (
          <button
            key={cat.category}
            onClick={() => setActiveCategory(cat.category)}
            className={`h-7 px-3 rounded-md text-xs font-medium transition-colors ${
              activeCategory === cat.category
                ? "bg-indigo-600 text-white"
                : "text-slate-600 hover:bg-slate-100 border border-slate-200"
            }`}
          >
            {CATEGORY_LABELS[cat.category] ?? cat.category}
            <span className="ml-1 opacity-60">({cat.components.length})</span>
          </button>
        ))}
      </div>

      {/* Component filter chips */}
      {activeCat && (
        <div className="flex items-center gap-1 flex-wrap">
          <span className="text-[10px] font-semibold text-slate-400 uppercase mr-1">
            Filter:
          </span>
          {activeCat.components.map((comp) => {
            const selected =
              selectedComponents[activeCat.category]?.includes(comp) ?? false;
            const noneSelected =
              !selectedComponents[activeCat.category]?.length;
            return (
              <button
                key={comp}
                onClick={() =>
                  toggleComponent(activeCat.category, comp)
                }
                className={`h-6 px-2 rounded text-[10px] font-medium transition-colors border ${
                  selected
                    ? "bg-indigo-50 border-indigo-300 text-indigo-700"
                    : noneSelected
                      ? "bg-white border-slate-200 text-slate-600 hover:bg-slate-50"
                      : "bg-white border-slate-200 text-slate-400 hover:bg-slate-50"
                }`}
              >
                {comp}
              </button>
            );
          })}
          {(selectedComponents[activeCat.category]?.length ?? 0) > 0 && (
            <button
              onClick={() =>
                setSelectedComponents((prev) => ({
                  ...prev,
                  [activeCat.category]: [],
                }))
              }
              className="h-6 px-2 rounded text-[10px] font-medium text-red-500 hover:bg-red-50 border border-red-200"
            >
              Clear
            </button>
          )}
        </div>
      )}

      {/* Chart */}
      {activeCategory && (
        <CategoryChart
          category={activeCategory}
          rows={rows}
          selectedComponents={
            selectedComponents[activeCategory] ?? []
          }
        />
      )}

      {/* Summary table */}
      {activeCategory && activeCat && (
        <div className="bg-white rounded-lg border border-slate-200 overflow-hidden">
          <table className="w-full text-xs">
            <thead>
              <tr className="bg-slate-50 border-b border-slate-200">
                <th className="text-left px-3 py-2 font-semibold text-slate-600">
                  Component
                </th>
                <th className="text-right px-3 py-2 font-semibold text-slate-600">
                  18mo Ago
                </th>
                <th className="text-right px-3 py-2 font-semibold text-slate-600">
                  Latest
                </th>
                <th className="text-right px-3 py-2 font-semibold text-slate-600">
                  Change
                </th>
                <th className="text-right px-3 py-2 font-semibold text-slate-600">
                  Min
                </th>
                <th className="text-right px-3 py-2 font-semibold text-slate-600">
                  Max
                </th>
              </tr>
            </thead>
            <tbody>
              {activeCat.components.map((comp) => {
                const compRows = rows
                  .filter(
                    (r) =>
                      r.category === activeCategory &&
                      r.component === comp,
                  )
                  .sort(
                    (a, b) =>
                      new Date(a.date).getTime() -
                      new Date(b.date).getTime(),
                  );
                if (compRows.length === 0) return null;
                const first = compRows[0].avg_price_usd;
                const latest =
                  compRows[compRows.length - 1].avg_price_usd;
                const min = Math.min(
                  ...compRows.map((r) => r.avg_price_usd),
                );
                const max = Math.max(
                  ...compRows.map((r) => r.avg_price_usd),
                );
                const changePct =
                  first > 0 ? ((latest - first) / first) * 100 : 0;
                return (
                  <tr
                    key={comp}
                    className="border-b border-slate-100 hover:bg-slate-50"
                  >
                    <td className="px-3 py-1.5 font-medium text-slate-800">
                      {comp}
                    </td>
                    <td className="px-3 py-1.5 text-right text-slate-600">
                      ${first}
                    </td>
                    <td className="px-3 py-1.5 text-right font-semibold text-slate-800">
                      ${latest}
                    </td>
                    <td
                      className={`px-3 py-1.5 text-right font-semibold ${
                        changePct > 0
                          ? "text-red-600"
                          : changePct < 0
                            ? "text-green-600"
                            : "text-slate-500"
                      }`}
                    >
                      {changePct > 0 ? "+" : ""}
                      {changePct.toFixed(0)}%
                    </td>
                    <td className="px-3 py-1.5 text-right text-green-600">
                      ${min}
                    </td>
                    <td className="px-3 py-1.5 text-right text-red-600">
                      ${max}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Data source attribution */}
      <p className="text-[10px] text-slate-400 text-right">
        Source: PCPartPicker Price Trends | Average prices (USD) over last 18
        months | Updated daily
      </p>

      {/* ── CamelCamelCamel section ── */}
      <GpuPricingSection />
      <CamelSection />
    </div>
  );
}

// ---------------------------------------------------------------------------
// CamelCamelCamel — individual Amazon product price history
// ---------------------------------------------------------------------------
// Cloud GPU Pricing — live rental prices from Vast.ai, RunPod, Tensordock
// ---------------------------------------------------------------------------

const GPU_TIERS = [
  { label: "Datacenter", gpus: ["H200 NVL", "H100 SXM", "A100 SXM4", "A100 PCIE", "L40S", "L40", "A40"] },
  { label: "Consumer High-End", gpus: ["RTX 5090", "RTX 5080", "RTX 4090", "RTX 4080S", "RTX 4080"] },
  { label: "Consumer Mid", gpus: ["RTX 5070 Ti", "RTX 5070", "RTX 4070 Ti", "RTX 3090", "RTX 3080"] },
  { label: "Professional", gpus: ["RTX A6000", "RTX 6000Ada", "RTX 5880Ada"] },
];

const HISTORY_GPUS = ["H100 SXM", "A100 SXM4", "L40S", "RTX 4090", "RTX 5090"];
const HISTORY_COLORS = ["#e11d48", "#2563eb", "#16a34a", "#f59e0b", "#8b5cf6"];

function GpuPricingSection() {
  const [gpuData, setGpuData] = useState<GpuPriceRow[]>([]);
  const [historyData, setHistoryData] = useState<GpuHistoryRow[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      pricingClient.getGpuLatest(),
      pricingClient.getGpuHistory(),
    ])
      .then(([latest, history]) => {
        setGpuData(latest);
        setHistoryData(history);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="flex items-center gap-2 py-4 justify-center"><Loader2 size={14} className="animate-spin text-slate-400" /><span className="text-xs text-slate-500">Loading GPU prices...</span></div>;
  if (gpuData.length === 0) return null;

  const ts = gpuData[0]?.timestamp?.slice(0, 16) ?? "";

  const getPrice = (gpu: string, market: string) =>
    gpuData.find((r) => r.gpu_name === gpu && r.market_type === market);

  const priceCell = (row: GpuPriceRow | undefined) => {
    if (!row) return <td className="px-2 py-1 text-right text-[10px] text-slate-300">-</td>;
    return (
      <td className="px-2 py-1 text-right text-[11px] font-mono">
        <span className="text-green-700 font-semibold">${row.min_price.toFixed(2)}</span>
        <span className="text-slate-400"> - </span>
        <span className="text-slate-600">${row.max_price.toFixed(2)}</span>
        <span className="text-[9px] text-slate-400 ml-1">({row.num_offers})</span>
      </td>
    );
  };

  return (
    <div className="border-t border-slate-200 pt-4 mt-2">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-bold text-slate-800">Cloud GPU Rental Prices</h2>
        <span className="text-[10px] text-slate-400">Last updated: {ts} | Vast.ai + RunPod</span>
      </div>

      {/* History chart */}
      {historyData.length > 1 && (() => {
        const filteredHistory = historyData.filter((r) => HISTORY_GPUS.includes(r.gpu_name));
        const timestamps = [...new Set(filteredHistory.map((r) => `${r.date} ${r.hour}`))].sort();
        const chartData = timestamps.map((ts) => {
          const point: Record<string, number | string> = { time: ts.slice(5) };
          for (const gpu of HISTORY_GPUS) {
            const row = filteredHistory.find((r) => `${r.date} ${r.hour}` === ts && r.gpu_name === gpu);
            if (row) point[gpu] = row.median_price;
          }
          return point;
        });

        return (
          <div className="bg-white rounded-lg border border-slate-200 p-4 mb-3">
            <h3 className="text-xs font-semibold text-slate-600 mb-2">GPU Rental Price History (On-Demand Median $/hr)</h3>
            <ResponsiveContainer width="100%" height={260}>
              <LineChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                <XAxis dataKey="time" tick={{ fontSize: 9 }} />
                <YAxis tick={{ fontSize: 10 }} tickFormatter={(v: number) => `$${v.toFixed(2)}`} width={55} />
                <Tooltip formatter={(v: number, name: string) => [`$${v.toFixed(3)}/hr`, name]} contentStyle={{ fontSize: 11 }} />
                <Legend wrapperStyle={{ fontSize: 10 }} iconSize={8} />
                {HISTORY_GPUS.map((gpu, i) => (
                  <Line key={gpu} type="monotone" dataKey={gpu} stroke={HISTORY_COLORS[i]} strokeWidth={2}
                    dot={{ r: 3 }} activeDot={{ r: 5 }} connectNulls />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>
        );
      })()}

      <div className="grid grid-cols-2 gap-3">
        {GPU_TIERS.map((tier) => {
          const hasData = tier.gpus.some((g) => gpuData.some((r) => r.gpu_name === g));
          if (!hasData) return null;
          return (
            <div key={tier.label} className="bg-white rounded-lg border border-slate-200 overflow-hidden">
              <div className="px-3 py-1.5 bg-slate-50 border-b border-slate-100">
                <span className="text-[10px] font-bold text-slate-600 uppercase">{tier.label}</span>
              </div>
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-slate-100">
                    <th className="text-left px-2 py-1 text-[10px] font-semibold text-slate-500">GPU</th>
                    <th className="text-right px-2 py-1 text-[10px] font-semibold text-slate-500">On-Demand $/hr</th>
                    <th className="text-right px-2 py-1 text-[10px] font-semibold text-slate-500">Spot $/hr</th>
                  </tr>
                </thead>
                <tbody>
                  {tier.gpus.map((gpu) => {
                    const od = getPrice(gpu, "on_demand");
                    const sp = getPrice(gpu, "spot");
                    if (!od && !sp) return null;
                    return (
                      <tr key={gpu} className="border-b border-slate-50 hover:bg-slate-50">
                        <td className="px-2 py-1 font-medium text-slate-800 text-[11px]">{gpu}</td>
                        {priceCell(od)}
                        {priceCell(sp)}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          );
        })}
      </div>

      <p className="text-[10px] text-slate-400 text-right mt-1">
        Source: Vast.ai + RunPod + Tensordock | Per-GPU hourly rates | Updated every 2 hours
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------

const CAMEL_COLORS = ["#e11d48", "#2563eb", "#16a34a", "#d97706"];

function CamelSection() {
  const [products, setProducts] = useState<CamelProduct[]>([]);
  const [rows, setRows] = useState<CamelRow[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      pricingClient.getCamelProducts(),
      pricingClient.getCamelData(),
    ])
      .then(([prods, allRows]) => {
        if (cancelled) return;
        setProducts(prods);
        setRows(allRows);
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) {
    return (
      <div className="flex items-center gap-2 py-8 justify-center">
        <Loader2 size={16} className="animate-spin text-slate-400" />
        <span className="text-sm text-slate-500">
          Loading Amazon price history...
        </span>
      </div>
    );
  }

  if (products.length === 0) return null;

  // Build short labels from product names
  const shortName = (name: string) => {
    const match = name.match(/^([\w.-]+)\s+([\w]+)\s+.*?(\d+GB)\s+\((\dx\d+GB)\)/i);
    if (match) return `${match[1]} ${match[3]} ${match[4]}`;
    return name.slice(0, 40);
  };

  // Build chart data: quarters on X-axis, each product as a series
  // Sort chronologically: "Q1 2020" → 2020.0, "Q2 2020" → 2020.25, etc.
  const quarterSortKey = (q: string) => {
    const m = q.match(/Q(\d)\s+(\d{4})/);
    if (!m) return 0;
    return parseInt(m[2]) + (parseInt(m[1]) - 1) * 0.25;
  };
  const allQuarters = [...new Set(rows.map((r) => r.quarter))].sort(
    (a, b) => quarterSortKey(a) - quarterSortKey(b),
  );
  const chartData = allQuarters.map((q) => {
    const point: Record<string, number | string> = { quarter: q };
    for (const prod of products) {
      const row = rows.find(
        (r) => r.asin === prod.asin && r.quarter === q,
      );
      if (row) point[prod.asin] = row.approx_price_usd;
    }
    return point;
  });

  return (
    <>
      <div className="border-t border-slate-200 pt-4 mt-2">
        <h2 className="text-sm font-bold text-slate-800 mb-3">
          Amazon Price History (CamelCamelCamel)
        </h2>

        {/* Combined chart */}
        <div className="bg-white rounded-lg border border-slate-200 p-4">
          <h3 className="text-xs font-semibold text-slate-600 mb-2">
            DDR4 Memory — Individual Product Prices (Quarterly)
          </h3>
          <ResponsiveContainer width="100%" height={320}>
            <LineChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
              <XAxis
                dataKey="quarter"
                tick={{ fontSize: 9 }}
                interval={Math.max(0, Math.floor(chartData.length / 8) - 1)}
              />
              <YAxis
                tick={{ fontSize: 10 }}
                tickFormatter={formatUSD}
                width={55}
              />
              <Tooltip
                formatter={(v: number, asin: string) => {
                  const prod = products.find((p) => p.asin === asin);
                  return [`$${v}`, prod ? shortName(prod.product_name) : asin];
                }}
                labelStyle={{ fontWeight: 600, fontSize: 12 }}
                contentStyle={{ fontSize: 11 }}
              />
              <Legend
                formatter={(asin: string) => {
                  const prod = products.find((p) => p.asin === asin);
                  return prod ? shortName(prod.product_name) : asin;
                }}
                wrapperStyle={{ fontSize: 10, paddingTop: 4 }}
                iconSize={8}
              />
              {products.map((prod, i) => (
                <Line
                  key={prod.asin}
                  type="monotone"
                  dataKey={prod.asin}
                  stroke={CAMEL_COLORS[i % CAMEL_COLORS.length]}
                  strokeWidth={1.5}
                  dot={{ r: 2 }}
                  activeDot={{ r: 5 }}
                  connectNulls
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>

        {/* Product summary table */}
        <div className="bg-white rounded-lg border border-slate-200 overflow-hidden mt-3">
          <table className="w-full text-xs">
            <thead>
              <tr className="bg-slate-50 border-b border-slate-200">
                <th className="text-left px-3 py-2 font-semibold text-slate-600">
                  Product
                </th>
                <th className="text-right px-3 py-2 font-semibold text-slate-600">
                  Lowest
                </th>
                <th className="text-right px-3 py-2 font-semibold text-slate-600">
                  Highest
                </th>
                <th className="text-right px-3 py-2 font-semibold text-slate-600">
                  Current
                </th>
                <th className="text-right px-3 py-2 font-semibold text-slate-600">
                  Quarters
                </th>
              </tr>
            </thead>
            <tbody>
              {products.map((prod) => (
                <tr
                  key={prod.asin}
                  className="border-b border-slate-100 hover:bg-slate-50"
                >
                  <td className="px-3 py-1.5 font-medium text-slate-800">
                    {shortName(prod.product_name)}
                  </td>
                  <td className="px-3 py-1.5 text-right text-green-600 font-semibold">
                    {prod.lowest != null ? `$${prod.lowest}` : "-"}
                  </td>
                  <td className="px-3 py-1.5 text-right text-red-600 font-semibold">
                    {prod.highest != null ? `$${prod.highest}` : "-"}
                  </td>
                  <td className="px-3 py-1.5 text-right font-semibold text-slate-800">
                    {prod.current != null ? `$${prod.current}` : "-"}
                  </td>
                  <td className="px-3 py-1.5 text-right text-slate-500">
                    {prod.quarters}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <p className="text-[10px] text-slate-400 text-right mt-1">
          Source: CamelCamelCamel Amazon Price History | Quarterly readings |
          Individual product tracking
        </p>
      </div>
    </>
  );
}
