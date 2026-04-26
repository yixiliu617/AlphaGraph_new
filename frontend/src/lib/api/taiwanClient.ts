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

export interface DayTradingSummary {
  date: string;
  total_shares: number;
  total_shares_pct: number;         // % of total market shares traded
  total_buy_value_twd: number;
  total_buy_value_pct: number;      // % of total market buy value
  total_sell_value_twd: number;
  total_sell_value_pct: number;
  scraped_at: string;
}

export type ForeignFlowInvestor =
  | "foreign"          // 外資及陸資 (excl. foreign-prop) — primary signal
  | "foreign_prop"     // 外資自營商
  | "foreign_legacy"   // 外資及陸資 (single line, pre-2020)
  | "trust"            // 投信 — domestic mutual funds
  | "prop_self"        // 自營商(自行買賣)
  | "prop_hedge"       // 自營商(避險)
  | "prop_legacy"      // 自營商 (single line, pre-2014)
  | "total";           // 三大法人 合計

export interface ForeignFlowRow {
  date:           string;
  investor_type:  ForeignFlowInvestor;
  buy_value_twd:  number;
  sell_value_twd: number;
  net_buy_twd:    number;
  scraped_at:     string;
}

export interface DayTradingDetail {
  date: string;
  ticker: string;
  name: string;
  suspension_flag: string;          // "" unless flagged (new-listing cash-buy suspension)
  shares: number;
  buy_value_twd: number;
  sell_value_twd: number;
  scraped_at: string;
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

  dayTradingSummary: (start?: string, end?: string) => {
    const qs = new URLSearchParams();
    if (start) qs.set("start", start);
    if (end)   qs.set("end",   end);
    const q = qs.toString();
    return apiRequest<AR<DayTradingSummary[]>>(
      `${BASE}/day-trading/summary${q ? `?${q}` : ""}`,
    );
  },

  dayTradingDetail: (date: string, limit = 200, sort: "buy_value_twd" | "sell_value_twd" | "shares" = "buy_value_twd") => {
    const qs = new URLSearchParams({ date, limit: String(limit), sort });
    return apiRequest<AR<DayTradingDetail[]>>(
      `${BASE}/day-trading/detail?${qs}`,
    );
  },

  dayTradingDates: () =>
    apiRequest<AR<string[]>>(`${BASE}/day-trading/dates`),

  foreignFlow: (params?: { start?: string; end?: string; investor_types?: string[] }) => {
    const qs = new URLSearchParams();
    if (params?.start) qs.set("start", params.start);
    if (params?.end)   qs.set("end",   params.end);
    if (params?.investor_types && params.investor_types.length)
      qs.set("investor_types", params.investor_types.join(","));
    const q = qs.toString();
    return apiRequest<AR<ForeignFlowRow[]>>(
      `${BASE}/foreign-flow${q ? `?${q}` : ""}`,
    );
  },

  foreignFlowDates: () =>
    apiRequest<AR<string[]>>(`${BASE}/foreign-flow/dates`),
};
