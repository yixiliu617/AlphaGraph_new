/**
 * umcClient — read-only access to UMC (2303.TW) ingestion silver layers.
 * Mirrors tsmcClient pattern, omitting transcript + guidance endpoints
 * that UMC doesn't publish.
 */

import { apiRequest } from "./base";

export interface UMCSummary {
  ticker: string;
  layers: {
    quarterly_facts?: {
      rows: number;
      metrics: number;
      periods: number;
      earliest_period_end: string;
      latest_period_end: string;
      source_reports: number;
    };
  };
  notes: {
    transcripts: string;
  };
}

export interface UMCMetricRow {
  metric: string;
  unit: string;
  [period: string]: string | number | null;
}

export interface UMCFinancialsWide {
  ticker: string;
  periods: string[];
  metrics: UMCMetricRow[];
}

export interface UMCSegmentRow {
  dimension: string;
  [period: string]: string | number | null;
}

export interface UMCSegments {
  metric: string;
  periods: string[];
  rows: UMCSegmentRow[];
}

export interface UMCQuarter {
  period_label: string;
  period_end: string;
  fact_count: number;
  metrics: number;
  sources: string[];
}

export interface UMCCapacityRow {
  metric: string;
  unit: string;
  [period: string]: string | number | null;
}

export interface UMCCapacity {
  ticker: string;
  unit: string;
  periods: string[];
  metrics: UMCCapacityRow[];
}

export interface UMCWide {
  ticker: string;
  periods: string[];
  metrics: UMCMetricRow[];
}

export interface UMCGuidanceRow {
  issued_in_period: string;
  for_period: string;
  metric: string;
  verbal: string | null;
  guide_low: number | null;
  guide_mid: number | null;
  guide_high: number | null;
  guide_point: number | null;
  actual: number | null;
  outcome: "BEAT high" | "MISS low" | "in range"
         | "ABOVE guidance" | "BELOW guidance" | "near point" | null;
  vs_mid_pct: number | null;
  vs_mid_pp: number | null;
  unit: string;
}

export const umcClient = {
  async summary() {
    return apiRequest<UMCSummary>("/umc/summary");
  },
  async financialsWide(quarters = 20) {
    return apiRequest<UMCFinancialsWide>(`/umc/financials/wide?quarters=${quarters}`);
  },
  async segments(metric: string, quarters = 20) {
    return apiRequest<UMCSegments>(
      `/umc/segments?metric=${encodeURIComponent(metric)}&quarters=${quarters}`,
    );
  },
  async quarters() {
    return apiRequest<{ ticker: string; quarters: UMCQuarter[] }>("/umc/quarters");
  },
  async capacity(quarters = 28, unit = "kpcs_12in_eq") {
    return apiRequest<UMCCapacity>(
      `/umc/capacity?quarters=${quarters}&unit=${encodeURIComponent(unit)}`,
    );
  },
  async cashflow(quarters = 20) {
    return apiRequest<UMCWide>(`/umc/cashflow?quarters=${quarters}`);
  },
  async balanceSheet(quarters = 20) {
    return apiRequest<UMCWide>(`/umc/balance-sheet?quarters=${quarters}`);
  },
  async annual(years = 10) {
    return apiRequest<UMCWide>(`/umc/annual?years=${years}`);
  },
  async guidance(quarters = 20) {
    return apiRequest<{ ticker: string; rows: UMCGuidanceRow[] }>(
      `/umc/guidance?quarters=${quarters}`,
    );
  },
};
