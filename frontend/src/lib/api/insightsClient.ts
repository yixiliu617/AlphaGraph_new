/**
 * insightsClient -- qualitative insights (Data Explorer Phase B).
 *
 * Endpoints:
 *   GET  /insights/margin/{ticker}          -- cached narrative for ticker
 *   POST /insights/margin/{ticker}/refresh  -- force regeneration
 */

import { apiRequest } from "./base";

// ---------------------------------------------------------------------------
// Types -- mirror backend/app/services/insights/margin_schemas.py
// ---------------------------------------------------------------------------

export type Direction    = "positive" | "negative";
export type CurrentState = "strengthening" | "steady" | "weakening" | "unclear";
export type MarginType   = "gross" | "operating" | "net";
export type DocType      = "8-K" | "10-Q" | "10-K" | "note";

export interface SourceRef {
  index: number;
  title: string;
  doc_type: DocType;
  date: string;
  url: string | null;
}

export interface Factor {
  label: string;
  direction: Direction;
  evidence: string;
  source_ref: number;          // >=0 cites sources[i]; -1 background; -2 user-added
  user_edited?: boolean;
  deleted?: boolean;
}

export interface PeakTroughNarrative {
  period: string;
  value_pct: number;
  factors: Factor[];
}

export interface FactorStatus {
  factor: string;
  current_state: CurrentState;
  evidence: string;
}

export interface CurrentRead {
  summary: string;
  positive_factors_status: FactorStatus[];
  negative_factors_status: FactorStatus[];
  user_edited_summary?: boolean;
}

// Edit request body — mirrors backend MarginEditRequest
export type EditAction  = "edit" | "add" | "delete" | "undo";
export type EditSection =
  | "peak"
  | "trough"
  | "current_pos"
  | "current_neg"
  | "current_summary";

export interface MarginEditRequest {
  action:      EditAction;
  margin_type: MarginType;
  section:     EditSection;
  factor_key:  string;                          // label (existing) or empty for summary
  period_end?: string;                          // optional override
  payload?:    Record<string, unknown>;         // new values
  prev?:       Record<string, unknown>;         // pre-edit values (audit)
}

export interface MarginNarrative {
  margin_type: MarginType;
  peak: PeakTroughNarrative;
  trough: PeakTroughNarrative;
  current_situation: CurrentRead;
}

export interface MarginInsights {
  ticker: string;
  generated_at: string;
  period_end: string;
  margins: MarginNarrative[];
  sources: SourceRef[];
  disclaimer: string;
}

// ---------------------------------------------------------------------------
// Client
// ---------------------------------------------------------------------------

export const insightsClient = {
  /** Fetch cached or freshly-generated margin insights for a ticker. */
  getMarginInsights(ticker: string): Promise<MarginInsights> {
    return apiRequest<MarginInsights>(`/insights/margin/${encodeURIComponent(ticker)}`);
  },

  /** Force regeneration, bypassing the cache. */
  refreshMarginInsights(ticker: string): Promise<MarginInsights> {
    return apiRequest<MarginInsights>(
      `/insights/margin/${encodeURIComponent(ticker)}/refresh`,
      "POST",
    );
  },

  /** Apply a single edit event (edit / add / delete / undo). */
  editMarginInsights(ticker: string, edit: MarginEditRequest): Promise<MarginInsights> {
    return apiRequest<MarginInsights>(
      `/insights/margin/${encodeURIComponent(ticker)}/edit`,
      "POST",
      edit,
    );
  },
};
