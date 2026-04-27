/**
 * pricesClient — read-only access to the equity-prices silver layer.
 * Daily OHLCV + intraday 15m bars + key-stats card.
 *
 * Endpoints are ticker-parameterised so this client works for any ticker
 * in the universe without per-company duplication.
 */

import { apiRequest } from "./base";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface PriceBar {
  /** ISO timestamp -- daily bars come at midnight UTC, intraday at the bar's close time. */
  t: string;
  o: number | null;
  h: number | null;
  l: number | null;
  c: number | null;
  /** adjusted close, daily only */
  ac?: number | null;
  /** volume (shares, not dollars) */
  v: number;
}

export interface PriceSeries {
  ticker: string;
  interval: string;
  rows: PriceBar[];
}

export interface PriceStats {
  ticker: string;
  as_of: string;
  last_close: number | null;
  prev_close: number | null;
  change_pct: number | null;
  high_52w: number;
  low_52w: number;
  one_year_return_pct: number | null;
  avg_dollar_volume_20d: number | null;
  history_days: number;
}

// ---------------------------------------------------------------------------
// Client
// ---------------------------------------------------------------------------

export const pricesClient = {
  async daily(ticker: string, days = 365) {
    return apiRequest<PriceSeries>(
      `/prices/${encodeURIComponent(ticker)}/daily?days=${days}`,
    );
  },
  async intraday(ticker: string, bars = 200, interval: "15m" | "30m" | "60m" = "15m") {
    return apiRequest<PriceSeries>(
      `/prices/${encodeURIComponent(ticker)}/intraday?bars=${bars}&interval=${interval}`,
    );
  },
  async stats(ticker: string) {
    return apiRequest<PriceStats>(`/prices/${encodeURIComponent(ticker)}/stats`);
  },
};
