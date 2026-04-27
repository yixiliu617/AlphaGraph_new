// ---------------------------------------------------------------------------
// Central re-export — import domain clients from here or directly from
// their own file. Never cross-import between client files.
// ---------------------------------------------------------------------------

export { chatClient }     from "./chatClient";
export { ingestClient }   from "./ingestClient";
export { ledgerClient }   from "./ledgerClient";
export { topologyClient } from "./topologyClient";
export { universeClient } from "./universeClient";
export { dataClient } from "./dataClient";
export { pricesClient } from "./pricesClient";
export type { PriceBar, PriceSeries, PriceStats } from "./pricesClient";
export { apiRequest, API_BASE_URL } from "./base";
