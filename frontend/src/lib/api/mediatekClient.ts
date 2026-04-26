/**
 * mediatekClient — read-only access to MediaTek (2454.TW) silver layers.
 * Mirrors umcClient pattern (no segments, no transcripts in v1).
 */

import { apiRequest } from "./base";

export interface MediaTekSummary {
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
    segments: string;
    transcripts: string;
  };
}

export interface MediaTekMetricRow {
  metric: string;
  unit: string;
  [period: string]: string | number | null;
}

export interface MediaTekFinancialsWide {
  ticker: string;
  periods: string[];
  metrics: MediaTekMetricRow[];
}

export interface MediaTekQuarter {
  period_label: string;
  period_end: string;
  fact_count: number;
  metrics: number;
  sources: string[];
}

export const mediatekClient = {
  async summary() {
    return apiRequest<MediaTekSummary>("/mediatek/summary");
  },
  async financialsWide(quarters = 20) {
    return apiRequest<MediaTekFinancialsWide>(`/mediatek/financials/wide?quarters=${quarters}`);
  },
  async quarters() {
    return apiRequest<{ ticker: string; quarters: MediaTekQuarter[] }>("/mediatek/quarters");
  },
};
