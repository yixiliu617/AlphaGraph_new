/**
 * taiwanClient — HTTP client for /api/v1/taiwan/* endpoints.
 * All methods return the shared { success, data } envelope from apiRequest.
 */

import { apiRequest } from "./base";

type AR<T> = { success: boolean; data: T; error?: string };

export interface WatchlistEntry {
  ticker: string;
  name: string;
  market: "TWSE" | "TPEx" | string;
  sector: string;
  subsector: string;
  notes: string;
}

export interface MonthlyRevenueRow {
  ticker: string;
  market: string;
  fiscal_ym: string;
  revenue_twd: number | null;
  yoy_pct: number | null;
  mom_pct: number | null;
  ytd_pct: number | null;
  cumulative_ytd_twd: number | null;
  prior_year_month_twd: number | null;
  first_seen_at: string;
  last_seen_at: string;
  amended: boolean;
  parse_flags?: string[];
}

export interface TickerDetail extends WatchlistEntry {
  latest_revenue: MonthlyRevenueRow | null;
}

export interface ScraperHealth {
  scraper_name: string;
  last_run_at: string | null;
  last_success_at: string | null;
  last_error_at: string | null;
  last_error_msg: string | null;
  rows_inserted: number;
  rows_updated: number;
  rows_amended: number;
  status: "ok" | "degraded" | "failed";
  lag_seconds: number | null;
}

const BASE = "/taiwan";

export const taiwanClient = {
  watchlist: () =>
    apiRequest<AR<WatchlistEntry[]>>(`${BASE}/watchlist`),

  monthlyRevenue: (tickers: string[], months = 12) => {
    const qs = new URLSearchParams({
      tickers: tickers.join(","),
      months: String(months),
    });
    return apiRequest<AR<MonthlyRevenueRow[]>>(`${BASE}/monthly-revenue?${qs}`);
  },

  ticker: (ticker: string) =>
    apiRequest<AR<TickerDetail>>(`${BASE}/ticker/${ticker}`),

  health: () =>
    apiRequest<AR<{ scrapers: ScraperHealth[] }>>(`${BASE}/health`),
};
