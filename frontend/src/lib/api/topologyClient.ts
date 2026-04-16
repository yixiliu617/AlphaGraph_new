import { apiRequest } from "./base";

// ---------------------------------------------------------------------------
// Topology / Graph client
// Owns all /topology endpoints. Changing Neo4j query shapes only touches here.
// ---------------------------------------------------------------------------

export const topologyClient = {
  getNeighbors: (nodeId: string) =>
    apiRequest(`/topology/neighbors/${nodeId}`, "GET"),

  getFilteredNeighbors: (nodeId: string, filters: unknown) =>
    apiRequest(`/topology/filtered/${nodeId}`, "POST", filters),

  findPath: (startNodeId: string, endNodeId: string) =>
    apiRequest(`/topology/path?start=${encodeURIComponent(startNodeId)}&end=${encodeURIComponent(endNodeId)}`, "GET"),
};
