// ---------------------------------------------------------------------------
// Backward-compatibility shim.
// New code should import directly from "@/lib/api/<client>" or "@/lib/api".
// ---------------------------------------------------------------------------

export { chatClient, ingestClient, ledgerClient, topologyClient, apiRequest } from "./api/index";

// Legacy object kept so existing imports of AlphaGraphAPI don't break during
// the gradual migration to the domain-split clients above.
import { chatClient } from "./api/chatClient";
import { ingestClient } from "./api/ingestClient";
import { ledgerClient } from "./api/ledgerClient";
import { topologyClient } from "./api/topologyClient";

export const AlphaGraphAPI = {
  chat:        (message: string, sessionId?: string) => chatClient.query(message, sessionId),
  ingest:      (sourceUri: string, recipeId: string, rawText?: string) => ingestClient.run(sourceUri, recipeId, rawText),
  getLedger:   (tenantId: string) => ledgerClient.get(tenantId),
  getTopology: (nodeId: string) => topologyClient.getNeighbors(nodeId),
};
