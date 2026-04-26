/**
 * tsmcClient — read-only access to the TSMC ingestion silver layers.
 * Mirrors taiwanClient pattern: typed wrappers over /api/v1/tsmc/*.
 */

import { apiRequest } from "./base";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface TSMCSummary {
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
    transcripts?: {
      rows: number;
      quarters: number;
      earliest_call: string | null;
      latest_call: string | null;
      speakers: number;
      total_chars: number | null;
    };
    guidance?: {
      rows: number;
      periods_covered: number;
      pages: number | null;
      earliest_page: string | null;
      latest_page: string | null;
    };
    pdf_catalog?: { quarters: number; pdfs: number };
  };
}

export interface TSMCMetricRow {
  metric: string;
  unit: string;
  [period: string]: string | number | null;  // dynamic period_label columns
}

export interface TSMCFinancialsWide {
  ticker: string;
  periods: string[];           // chronological
  metrics: TSMCMetricRow[];
}

export interface TSMCSegmentRow {
  dimension: string;
  [period: string]: string | number | null;
}

export interface TSMCSegments {
  metric: string;
  periods: string[];
  rows: TSMCSegmentRow[];
}

export interface TSMCGuidanceRow {
  period_label: string;
  period_end: string;
  metric: string;
  actual: number | null;
  guide_low: number | null;
  guide_high: number | null;
  guide_mid: number | null;
  guide_point: number | null;
  unit: string | null;
  outcome: "BEAT high" | "MISS low" | "in range" | null;
  vs_mid_pct: number | null;
  vs_high_pct: number | null;
  vs_mid_pp: number | null;
  vs_high_pp: number | null;
}

export interface TSMCForwardGuidance {
  issued_at: string;
  for_period: string | null;
  rows: { period_label: string; metric: string; bound: string; value: number; unit: string }[];
}

export interface TSMCTranscriptQuarter {
  period_label: string;
  period_end: string;
  event_date: string;
  turns: number;
  chars: number;
}

export interface TSMCTranscriptTurn {
  ticker: string;
  period_label: string;
  period_end: string;
  event_date: string | null;
  turn_index: number;
  section: "presentation" | "qa";
  speaker_name: string;
  speaker_company: string;
  speaker_role: string;
  text: string;
  char_count: number;
}

export interface TSMCTranscriptMatch {
  period_label: string;
  period_end: string;
  speaker_name: string;
  speaker_role: string;
  section: "presentation" | "qa";
  snippet: string;
}

export interface TSMCPDFEntry {
  label: string;
  type: string;
  url: string;
}
export interface TSMCPDFCatalog {
  ticker: string;
  enumerated_at?: string;
  index_url?: string;
  quarters: Record<string, { title: string; pdfs: TSMCPDFEntry[] }>;
}

// ---------------------------------------------------------------------------
// Client
// ---------------------------------------------------------------------------

export const tsmcClient = {
  async summary() {
    return apiRequest<TSMCSummary>("/tsmc/summary");
  },
  async financialsWide(quarters = 20) {
    return apiRequest<TSMCFinancialsWide>(`/tsmc/financials/wide?quarters=${quarters}`);
  },
  async segments(metric: string, quarters = 20) {
    return apiRequest<TSMCSegments>(
      `/tsmc/segments?metric=${encodeURIComponent(metric)}&quarters=${quarters}`,
    );
  },
  async guidance(quarters = 20) {
    return apiRequest<{ rows: TSMCGuidanceRow[] }>(`/tsmc/guidance?quarters=${quarters}`);
  },
  async forwardGuidance() {
    return apiRequest<TSMCForwardGuidance>("/tsmc/guidance/forward");
  },
  async transcriptQuarters() {
    return apiRequest<{ quarters: TSMCTranscriptQuarter[] }>("/tsmc/transcripts/quarters");
  },
  async transcriptTurns(period_label: string) {
    return apiRequest<{ period_label: string; turns: TSMCTranscriptTurn[] }>(
      `/tsmc/transcripts/turns?period_label=${encodeURIComponent(period_label)}`,
    );
  },
  async transcriptSearch(q: string, limit = 50) {
    return apiRequest<{ query: string; matches: TSMCTranscriptMatch[] }>(
      `/tsmc/transcripts/search?q=${encodeURIComponent(q)}&limit=${limit}`,
    );
  },
  async pdfs() {
    return apiRequest<TSMCPDFCatalog>("/tsmc/pdfs");
  },
};
