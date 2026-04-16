import { apiRequest } from "./base";

// ---------------------------------------------------------------------------
// Chat / Unified Data Engine client
// Owns the /chat endpoint and its request/response shapes.
// ---------------------------------------------------------------------------

interface ChatRequest {
  message: string;
  session_id?: string;
  context_filters?: Record<string, unknown>;
}

export const chatClient = {
  query: (message: string, sessionId?: string) =>
    apiRequest("/chat", "POST", {
      message,
      session_id: sessionId,
    } satisfies ChatRequest),
};
