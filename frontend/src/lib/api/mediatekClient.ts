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

export interface MediaTekPDFEntry {
  type: string;          // 'press_release' | 'presentation' | 'transcript' | 'financial_statements' | 'earnings_call_invitation' | 'consolidated_financial_report' | 'unconsolidated_financial_report' | …
  url: string;
  label: string;
}

export interface MediaTekPDFQuarter {
  year: number;
  quarter: number;
  pdfs: MediaTekPDFEntry[];
}

export interface MediaTekPDFCatalog {
  ticker: string;
  company: string;
  index_url: string;
  enumerated_at: string;
  quarter_count: number;
  quarters: Record<string, MediaTekPDFQuarter>;
}

export interface MediaTekTranscriptQuarter {
  period_label: string;
  period_end: string;
  event_date: string;
  turns: number;
  chars: number;
  speakers: number;
}

export interface MediaTekSourceIssue {
  period_label: string;
  file_type: string;
  issue: string;
  detected_on: string;
  evidence: Record<string, unknown>;
  mitigation: Record<string, string>;
  user_facing_message: string;
  recovery_options: string[];
}

export interface MediaTekTranscriptTurn {
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

export interface MediaTekTranscriptMatch {
  period_label: string;
  period_end: string;
  speaker_name: string;
  speaker_role: string;
  section: "presentation" | "qa";
  snippet: string;
}

export interface MediaTekGuidanceRow {
  issued_in_period: string;
  for_period: string;
  metric: string;
  verbal: string | null;
  guide_low: number | null;
  guide_mid: number | null;
  guide_high: number | null;
  guide_point: number | null;
  actual: number | null;
  outcome: "BEAT high" | "MISS low" | "in range" | null;
  vs_mid_pct: number | null;
  vs_mid_pp: number | null;
  unit: string;
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
  async pdfs() {
    return apiRequest<MediaTekPDFCatalog>("/mediatek/pdfs");
  },
  async transcriptQuarters() {
    return apiRequest<{ quarters: MediaTekTranscriptQuarter[]; source_issues: MediaTekSourceIssue[] }>(
      "/mediatek/transcripts/quarters",
    );
  },
  async transcriptTurns(period_label: string) {
    return apiRequest<{ period_label: string; turns: MediaTekTranscriptTurn[] }>(
      `/mediatek/transcripts/turns?period_label=${encodeURIComponent(period_label)}`,
    );
  },
  async transcriptSearch(q: string, limit = 50) {
    return apiRequest<{ query: string; matches: MediaTekTranscriptMatch[] }>(
      `/mediatek/transcripts/search?q=${encodeURIComponent(q)}&limit=${limit}`,
    );
  },
  async guidance(quarters = 20) {
    return apiRequest<{ ticker: string; rows: MediaTekGuidanceRow[] }>(
      `/mediatek/guidance?quarters=${quarters}`,
    );
  },
};
