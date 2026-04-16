// ---------------------------------------------------------------------------
// Shared HTTP primitives
// Every domain client imports from here — never from each other.
// ---------------------------------------------------------------------------

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

// Bump this whenever the backend's API_VERSION changes (main.py).
// A mismatch is logged as a console warning so it's visible during development.
const EXPECTED_API_VERSION = "1.0.0";

export async function apiRequest<T = unknown>(
  endpoint: string,
  method: "GET" | "POST" | "PUT" | "DELETE" = "GET",
  body?: unknown
): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${endpoint}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  // Surface API version mismatches during development.
  const serverVersion = response.headers.get("X-API-Version");
  if (serverVersion && serverVersion !== EXPECTED_API_VERSION) {
    console.warn(
      `[AlphaGraph] API version mismatch: expected ${EXPECTED_API_VERSION}, got ${serverVersion}. ` +
        "Check mappers and generated types."
    );
  }

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(
      (errorData as { error?: string }).error ||
        `API request failed: ${response.status} ${response.statusText}`
    );
  }

  return response.json() as Promise<T>;
}
