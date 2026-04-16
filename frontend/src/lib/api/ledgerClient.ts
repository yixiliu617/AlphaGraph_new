import { apiRequest } from "./base";

// ---------------------------------------------------------------------------
// Thesis Ledger client
// Owns the /ledger endpoints. Adding new ledger operations only touches here.
// ---------------------------------------------------------------------------

export const ledgerClient = {
  get: (tenantId: string) =>
    apiRequest(`/ledger/${tenantId}`, "GET"),
};
