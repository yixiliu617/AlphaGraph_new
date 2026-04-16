import { apiRequest } from "./base";

// ---------------------------------------------------------------------------
// Ingestion engine client
// Owns the /ingest endpoint. Changing ingestion parameters only touches here.
// ---------------------------------------------------------------------------

interface IngestionRequest {
  source_uri: string;
  recipe_id: string;
  raw_text?: string;
}

export const ingestClient = {
  run: (sourceUri: string, recipeId: string, rawText?: string) =>
    apiRequest("/ingest", "POST", {
      source_uri: sourceUri,
      recipe_id: recipeId,
      raw_text: rawText,
    } satisfies IngestionRequest),
};
