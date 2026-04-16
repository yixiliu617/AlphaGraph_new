/**
 * dataClient — financial metrics and topline management.
 *
 * Endpoints:
 *   POST /data/fetch              — fetch metrics for given tickers / period
 *   GET  /data/metrics            — list all available metric names
 *   GET  /data/topline/status     — filing state, build report, stale warnings
 *   POST /data/topline/refresh    — incremental EDGAR refresh (background)
 *   POST /data/topline/add-ticker — add ticker to universe + full first-time build
 */

import { apiRequest } from "./base";

// ---------------------------------------------------------------------------
// Fetch types
// ---------------------------------------------------------------------------

export interface DataSpec {
  tickers: string[];
  metrics: string[];
  period?: "quarterly" | "annual";
  lookback_years?: number;
}

export interface DataRow {
  ticker: string;
  period_label: string;   // e.g. "FY2025-Q3" or "2024-Q4" (calendar fallback)
  end_date: string;       // ISO date string, e.g. "2024-10-27"
  [metric: string]: number | string | null;
}

export interface CellSource {
  ticker: string;
  metric: string;
  metric_label: string;
  value: number | null;
  unit: string;
  period_end: string | null;
  period_start: string | null;
  fiscal_period: string | null;
  fiscal_quarter: string | null;
  fiscal_year: number | null;
  is_ytd: boolean;
  source_layer: string;
  source_file: string;
  xbrl_concepts: string[];
  derivation: {
    formula_description: string;
    inputs: Record<string, number | string | null>;
  } | null;
  filing: {
    form: string;
    accession: string;
    filed_date: string | null;
    edgar_url: string;
  } | null;
}

export interface DataResult {
  rows: DataRow[];
  tickers: string[];
  periods: string[];
  metrics_returned: string[];
  source: "calculated_layer" | "topline" | "mixed" | "none" | string;
  sql_executed: string;
  warnings: string[];
}

// ---------------------------------------------------------------------------
// Topline status types
// ---------------------------------------------------------------------------

export interface FilingInfo {
  accession: string;
  filed: string;
  period: string;
}

export interface TickerFilingState {
  "10k_accession": string | null;
  "10q_accession": string | null;
  "10ka_accession": string | null;
  "10qa_accession": string | null;
  last_built_at: string | null;
  last_period_end: string | null;
  is_amendment_update: boolean;
  stale_warning: string | null;
}

export interface ToplineStatus {
  built: boolean;
  built_at?: string;
  filing_state: Record<string, TickerFilingState>;
  tickers?: Record<string, {
    rows_income?: number;
    rows_balance?: number;
    rows_cashflow?: number;
    error?: string;
  }>;
}

// ---------------------------------------------------------------------------
// Client
// ---------------------------------------------------------------------------

export const dataClient = {
  // ── Fetch ──────────────────────────────────────────────────────────────

  /** Fetch financial metrics for given tickers and period. */
  fetch(spec: DataSpec): Promise<DataResult> {
    return apiRequest<DataResult>("/data/fetch", "POST", spec);
  },

  /** Drill-down: underlying source info for a single table cell. */
  getCellSource(ticker: string, metric: string, end_date: string): Promise<CellSource> {
    const params = new URLSearchParams({ ticker, metric, end_date });
    return apiRequest<CellSource>(`/data/cell-source?${params.toString()}`);
  },

  /** List all available metric names, grouped by type. */
  async listMetrics(): Promise<string[]> {
    const res = await apiRequest<{ all: string[] }>("/data/metrics");
    return res.all;
  },

  // ── Topline management ─────────────────────────────────────────────────

  /** Return filing state, last build report, and stale warnings per ticker. */
  getToplineStatus(): Promise<ToplineStatus> {
    return apiRequest<ToplineStatus>("/data/topline/status");
  },

  /** Trigger incremental EDGAR refresh in background. */
  triggerRefresh(tickers?: string[], force = false): Promise<{ status: string; message: string }> {
    return apiRequest("/data/topline/refresh", "POST", { tickers: tickers ?? null, force });
  },

  /**
   * Add a ticker to the universe and trigger a full first-time build.
   * The build runs in the background; poll getToplineStatus() to check completion.
   */
  addTicker(ticker: string): Promise<{ status: string; ticker: string; message: string }> {
    return apiRequest("/data/topline/add-ticker", "POST", { ticker });
  },

  // ── Sector heatmap ─────────────────────────────────────────────────────

  /** List available group_definition keys (GICS_industry, supplychain, etc.). */
  getSectorHeatmapDefinitions(): Promise<{ definitions: SectorHeatmapDefinition[] }> {
    return apiRequest("/data/sector-heatmap/definitions");
  },

  /** Sector heatmap for the given group_definition + metric, by stepped-back fiscal period. */
  getSectorHeatmap(params: {
    group_definition: string;
    quarters?: number;
    metric?:   SectorHeatmapMetric;
  }): Promise<SectorHeatmap> {
    const qs = new URLSearchParams({
      group_definition: params.group_definition,
      quarters:         String(params.quarters ?? 20),
      metric:           params.metric ?? "revenue_yoy_pct",
    });
    return apiRequest(`/data/sector-heatmap?${qs.toString()}`);
  },
};

// ---------------------------------------------------------------------------
// Sector heatmap types
// ---------------------------------------------------------------------------

export interface SectorHeatmapDefinition {
  key:         string;
  label:       string;
  group_names: string[];
}

export type SectorHeatmapMetric =
  | "revenue_yoy_pct"
  | "revenue_qoq_pct"
  | "revenue"
  | "net_income"
  | "net_income_yoy_pct"
  | "net_income_qoq_pct";

export interface SectorHeatmapPoint {
  label:       string | null;   // stepped-back label: "FY2026-Q4", "FY2026-Q3", ...
  end_date:    string;          // YYYY-MM-DD
  value:       number | null;   // current selected metric value
  yoy:         number | null;   // alias for value (back-compat)
  edgar_label: string | null;   // what edgartools labeled this row
  matches:     boolean;          // true if edgar_label == label
}

export interface SectorHeatmapMismatch {
  position: number;
  end_date: string;
  expected: string;
  edgar:    string;
}

export interface SectorHeatmapRow {
  ticker:           string;
  latest_label:     string | null;
  latest_end_date:  string | null;
  points:           SectorHeatmapPoint[];
  mismatches:       SectorHeatmapMismatch[];
}

export interface SectorHeatmapGroup {
  name: string;
  rows: SectorHeatmapRow[];
}

export interface SectorHeatmap {
  group_definition: string;
  label:            string;
  quarters_count:   number;
  metric:           SectorHeatmapMetric;
  metric_label:     string;
  metric_fmt:       "%" | "$M";
  groups:           SectorHeatmapGroup[];
}
