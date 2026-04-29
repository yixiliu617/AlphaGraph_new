/**
 * meNotesClient — read the signed-in user's synced notes (OneNote, ...)
 * via /api/v1/me/notes/*.
 */
import { apiRequest } from "./base";

export interface NoteSummary {
  id:            string;
  provider:      "google" | "microsoft";
  title:         string | null;
  notebook:      string | null;
  section:       string | null;
  page_link:     string | null;
  last_modified: string | null;
  preview:       string | null;
  truncated:     boolean;
}

export interface NoteFull extends NoteSummary {
  content_html: string | null;
  content_text: string | null;
}

export interface SyncResultRow {
  service:  string;
  ok:       boolean;
  inserted: number;
  updated:  number;
  error:    string | null;
}


export const meNotesClient = {
  async list(notebook?: string, limit = 50) {
    const params = new URLSearchParams({ limit: String(limit) });
    if (notebook) params.set("notebook", notebook);
    return apiRequest<{ notes: NoteSummary[]; notebooks: string[] }>(
      `/me/notes/list?${params}`,
    );
  },
  async search(q: string, limit = 20) {
    const params = new URLSearchParams({ q, limit: String(limit) });
    return apiRequest<{ notes: NoteSummary[] }>(`/me/notes/search?${params}`);
  },
  async get(id: string) {
    return apiRequest<NoteFull>(`/me/notes/${id}`);
  },
  async syncNow() {
    return apiRequest<{ results: SyncResultRow[] }>(`/me/notes/sync`, "POST");
  },
};
