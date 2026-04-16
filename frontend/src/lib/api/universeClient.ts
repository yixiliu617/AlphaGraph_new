// ---------------------------------------------------------------------------
// Universe API client
// Manages the user's coverage universe (tickers + sectors) on the backend.
//
// The backend endpoint does not exist yet — the store handles persistence
// locally via localStorage. This client is wired up and ready for when
// GET /universe and PUT /universe are implemented server-side.
// ---------------------------------------------------------------------------

import { apiRequest } from "./base";
import type { Ticker } from "@/store/useUniverseStore";

interface UniversePayload {
  tickers: Ticker[];
  sectors: string[];
}

export const universeClient = {
  /**
   * Fetch the current user's coverage universe from the backend.
   * Falls back gracefully if the endpoint is not yet available.
   */
  get: (tenantId: string): Promise<UniversePayload> =>
    apiRequest<UniversePayload>(`/universe/${tenantId}`, "GET"),

  /**
   * Persist the full coverage universe to the backend.
   * Called after any add/remove action once the endpoint exists.
   */
  save: (tenantId: string, payload: UniversePayload): Promise<UniversePayload> =>
    apiRequest<UniversePayload>(`/universe/${tenantId}`, "PUT", payload),
};
