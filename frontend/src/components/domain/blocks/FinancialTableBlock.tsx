"use client";

// ---------------------------------------------------------------------------
// FinancialTableBlock — renders financial_table AgentBlocks from the Engine.
// Dumb component: accepts only plain props, no API/store imports.
// ---------------------------------------------------------------------------

interface DataRow {
  ticker: string;
  period_label: string;
  [metric: string]: string | number | null | undefined;
}

export interface FinancialTableData {
  rows:     DataRow[];
  tickers:  string[];
  periods:  string[];
  metrics:  string[];
  source?:  string;
  warnings?: string[];
}

interface FinancialTableBlockProps {
  title: string;
  data:  FinancialTableData;
}

// ---------------------------------------------------------------------------
// Metric display config (labels + formatting hints)
// ---------------------------------------------------------------------------

const METRIC_CONFIG: Record<string, { label: string; unit: string }> = {
  revenue:              { label: "Revenue",            unit: "M"  },
  gross_profit:         { label: "Gross Profit",       unit: "M"  },
  operating_income:     { label: "Operating Income",   unit: "M"  },
  net_income:           { label: "Net Income",         unit: "M"  },
  eps_diluted:          { label: "EPS (Diluted)",      unit: "$"  },
  eps_basic:            { label: "EPS (Basic)",        unit: "$"  },
  rd_expense:           { label: "R&D Expense",        unit: "M"  },
  sga_expense:          { label: "SG&A",               unit: "M"  },
  operating_cf:         { label: "Operating CF",       unit: "M"  },
  capex:                { label: "Capex",              unit: "M"  },
  free_cash_flow:       { label: "Free Cash Flow",     unit: "M"  },
  gross_margin_pct:     { label: "Gross Margin",       unit: "%"  },
  operating_margin_pct: { label: "Operating Margin",   unit: "%"  },
  net_margin_pct:       { label: "Net Margin",         unit: "%"  },
  rd_pct_revenue:       { label: "R&D % Rev",          unit: "%"  },
  cost_of_revenue:      { label: "Cost of Revenue",    unit: "M"  },
};

function metricLabel(metric: string): string {
  return METRIC_CONFIG[metric]?.label ?? metric.replace(/_/g, " ");
}

function formatValue(value: number | null | undefined, metric: string): string {
  if (value == null || (typeof value === "number" && isNaN(value))) return "—";
  const unit = METRIC_CONFIG[metric]?.unit ?? "";
  if (unit === "%")  return `${value.toFixed(1)}%`;
  if (unit === "$")  return `$${value.toFixed(2)}`;
  if (unit === "M") {
    const abs = Math.abs(value);
    if (abs >= 1000) return `${(value / 1000).toFixed(1)}B`;
    return `${value.toFixed(0)}M`;
  }
  if (Number.isInteger(value)) return value.toLocaleString();
  return value.toFixed(2);
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function FinancialTableBlock({ title, data }: FinancialTableBlockProps) {
  const { rows, tickers, periods, metrics, warnings } = data;

  if (!rows.length) {
    return (
      <div className="p-6">
        <p className="text-sm font-semibold text-slate-900 mb-3">{title}</p>
        <p className="text-xs text-slate-400 italic">
          No data available.{" "}
          {warnings && warnings.length > 0 && (
            <span className="text-amber-600">{warnings[0]}</span>
          )}
        </p>
      </div>
    );
  }

  // For multi-ticker: group periods by ticker, show ticker header rows
  const multiTicker = tickers.length > 1;

  return (
    <div className="p-4">
      {/* Title */}
      <p className="text-sm font-semibold text-slate-900 mb-3 truncate">{title}</p>

      {/* Warnings */}
      {warnings && warnings.length > 0 && (
        <div className="mb-3 px-3 py-2 bg-amber-50 border border-amber-200 rounded-lg">
          {warnings.map((w, i) => (
            <p key={i} className="text-[11px] text-amber-700">{w}</p>
          ))}
        </div>
      )}

      {/* Table per ticker */}
      {tickers.map((ticker) => {
        const tickerRows = rows.filter((r) => r.ticker === ticker);
        // Use only the periods that have data for this ticker
        const tickerPeriods = tickerRows.map((r) => r.period_label);

        return (
          <div key={ticker} className={multiTicker ? "mb-5" : undefined}>
            {multiTicker && (
              <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-1">
                {ticker}
              </p>
            )}
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr>
                    <th className="py-1.5 pr-3 text-[10px] font-semibold text-slate-400 uppercase tracking-wider whitespace-nowrap w-36">
                      Metric
                    </th>
                    {tickerPeriods.map((period) => (
                      <th
                        key={period}
                        className="py-1.5 px-2 text-[10px] font-semibold text-slate-400 uppercase tracking-wider text-right whitespace-nowrap"
                      >
                        {period}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {metrics.map((metric) => (
                    <tr
                      key={metric}
                      className="border-t border-slate-100 hover:bg-slate-50"
                    >
                      <td className="py-1.5 pr-3 text-xs text-slate-600 whitespace-nowrap">
                        {metricLabel(metric)}
                      </td>
                      {tickerPeriods.map((period) => {
                        const row = tickerRows.find((r) => r.period_label === period);
                        const val = row?.[metric] as number | null | undefined;
                        return (
                          <td
                            key={period}
                            className="py-1.5 px-2 text-xs text-right font-mono text-slate-800 whitespace-nowrap"
                          >
                            {formatValue(val, metric)}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        );
      })}
    </div>
  );
}
