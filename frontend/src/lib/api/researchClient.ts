/**
 * researchClient — natural-language Q&A over the earnings-release corpus
 * (and, in the future, earnings-call transcripts + meeting notes).
 *
 * BASE is "/research" — apiRequest prepends http://localhost:8000/api/v1.
 */

import { apiRequest } from "./base";

type AR<T> = { success: boolean; data: T; error?: string };

export type ResearchSourceType =
  | "press_release"
  | "cfo_commentary"
  | "mdna"
  | "transcript_prepared"
  | "transcript_qa"
  | "meeting_note";

export interface ResearchQuote {
  text:     string;
  verified: boolean;
}

export interface ResearchFinding {
  finding_id:        string;
  ticker:            string;
  topic_label:       string;
  topic_slug:        string;
  source_type:       ResearchSourceType;
  source_id:         string;
  filing_date:       string;
  fiscal_period:     string | null;
  title:             string;
  source_url:        string | null;
  key_points:        string[];
  quotes:            ResearchQuote[];
  extracted_at:      string;
  extractor_model:   string;
  extractor_version: string;
}

export interface ResearchQueryResponse {
  ticker:          string;
  question:        string;
  topic_slug:      string;
  lookback_years:  number;
  generated_at:    string;
  findings:        ResearchFinding[];
  docs_considered: number;
  docs_with_hits:  number;
  from_cache:      number;
  newly_extracted: number;
}

export interface ResearchQueryParams {
  ticker:         string;
  question:       string;
  lookback_years?: number;
  source_types?:  ResearchSourceType[];
}

const BASE = "/research";

export const researchClient = {
  query(params: ResearchQueryParams): Promise<AR<ResearchQueryResponse>> {
    return apiRequest<AR<ResearchQueryResponse>>(
      `${BASE}/query`,
      "POST",
      params,
    );
  },
};
