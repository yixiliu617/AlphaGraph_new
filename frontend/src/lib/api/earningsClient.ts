/**
 * earningsClient — fetches 8-K Item 2.02 earnings press releases and
 * CFO commentaries persisted by backend/scripts/ingest_earnings_releases.py.
 *
 * BASE is "/earnings" — apiRequest prepends http://localhost:8000/api/v1.
 */

import { apiRequest } from "./base";

type AR<T> = { success: boolean; data: T; error?: string };

export interface EarningsReleaseStub {
  id:               string;   // "TICKER:accession_no:exhibit"
  ticker:           string;
  exhibit:          string;   // e.g. "EX-99.1" or "EX-99.01"
  exhibit_norm:     string;   // normalized to "EX-99.1" etc.
  doc_type_label:   string;   // "Press Release" | "CFO Commentary" | "Exhibit EX-..."
  title:            string;   // "[NVDA] Press Release · FY2026-Q4 (2026-02-25)"
  filing_date:      string;   // YYYY-MM-DD
  period_of_report: string;   // YYYY-MM-DD
  fiscal_period:    string | null;  // "FY2026-Q4" or null if no mapping
  text_chars:       number;
  url:              string | null;
}

export interface EarningsReleaseDetail extends EarningsReleaseStub {
  items:        string;
  description:  string;
  document:     string;
  text_raw:     string;
}

const BASE = "/earnings";

export const earningsClient = {
  /** List lightweight stubs (no text_raw). Optionally filter by ticker. */
  list(params?: { ticker?: string; limit?: number }): Promise<AR<EarningsReleaseStub[]>> {
    const qs = new URLSearchParams();
    if (params?.ticker) qs.set("ticker", params.ticker);
    if (params?.limit)  qs.set("limit",  String(params.limit));
    const q = qs.toString();
    return apiRequest<AR<EarningsReleaseStub[]>>(`${BASE}/releases${q ? `?${q}` : ""}`);
  },

  /** Get full text for one release (press release or CFO commentary). */
  get(releaseId: string): Promise<AR<EarningsReleaseDetail>> {
    return apiRequest<AR<EarningsReleaseDetail>>(
      `${BASE}/releases/${encodeURIComponent(releaseId)}`,
    );
  },
};
