/**
 * notesClient — all HTTP calls for the Notes tab.
 * WebSocket connections are opened directly in RecordingPanel.tsx.
 *
 * BASE is "/notes" — apiRequest prepends http://localhost:8000/api/v1 automatically.
 */

import { apiRequest } from "./base";

// ---------------------------------------------------------------------------
// Shared response wrapper — backend always returns { success, data }
// ---------------------------------------------------------------------------
type AR<T> = { success: boolean; data: T; error?: string };

// ---------------------------------------------------------------------------
// Domain types
// ---------------------------------------------------------------------------

export interface NoteStub {
  note_id: string;
  tenant_id: string;
  title: string;
  note_type: string;
  company_tickers: string[];
  meeting_date: string | null;
  created_at: string;
  updated_at: string;
  editor_content: Record<string, unknown>;
  editor_plain_text: string;
  ux_variant: "A" | "B";
  recording_path: string | null;
  recording_mode: string | null;
  duration_seconds: number | null;
  source_url: string | null;
  transcript_lines: TranscriptLine[];
  polished_transcript: string | null;
  polished_transcript_language: string | null;
  polished_transcript_meta: {
    input_tokens?: number;
    output_tokens?: number;
    model?: string;
    ran_at?: string;
    is_bilingual?: boolean;
    key_topics?: string[];
    segments?: PolishedSegment[];
    summary?: MeetingSummary;
  } | null;
  summary_status: string;
  ai_summary: AISummary | null;
  fragment_ids: string[];
}

export interface TranscriptLine {
  line_id: number;
  timestamp: string;
  speaker_label: string;
  speaker_name: string | null;
  text: string;
  is_flagged: boolean;
  is_interim: boolean;
}

export interface PolishedSegment {
  timestamp: string;      // "MM:SS"
  speaker: string;
  text_original: string;
  text_english: string;
}

export interface SubPoint {
  text: string;
  supporting: string;
}

export interface KeyPoint {
  title: string;
  sub_points: SubPoint[];
}

export interface FinancialMetrics {
  revenue: string[];
  profit: string[];
  orders: string[];
}

/** A number mentioned in the meeting with its context. Produced by
 * `gemini_generate_summary`. The `quote` is a verbatim sentence from the
 * transcript so the analyst can see where the number came from. */
export interface NumberMention {
  label: string;
  value: string;
  quote: string;
}

export interface MeetingSummary {
  storyline: string;
  key_points: KeyPoint[];
  /** Either the new structured form (preferred) or legacy plain strings from
   * pre-refactor notes. The builder / renderer handles both. */
  all_numbers: NumberMention[] | string[];
  recent_updates: string[];
  financial_metrics: FinancialMetrics;
}

export interface SpeakerMapping {
  label: string;
  name: string;
  role?: string;
}

export interface SupportingSentence {
  sentence_id: number;
  timestamp: string;
  speaker: string;
  text: string;
  relevance_reason: string;
  has_number: boolean;
  numbers: string[];
}

export interface TopicFragment {
  topic: string;
  topic_summary: string;
  supporting_sentences: SupportingSentence[];
  overall_tone: string;
  direction: string;
  key_numbers: string[];
  speakers_involved: string[];
  fragment_id: string | null;
}

export interface DeltaCard {
  delta_id: string;
  topic: string;
  previous_statement: string;
  previous_source: string;
  current_statement: string;
  change_type: string;
  significance: string;
  status: string;
  edited_text: string | null;
  approved_fragment_id: string | null;
}

export interface AISummary {
  speaker_mappings: SpeakerMapping[];
  user_topics: string[];
  topic_fragments: TopicFragment[];
  delta_cards: DeltaCard[];
  action_items: string[];
  note_enhancements: string[];
  ai_narrative: string;
}

const BASE = "/notes";

export const notesClient = {
  // ------------------------------------------------------------------
  // CRUD
  // ------------------------------------------------------------------

  list: (params?: { ticker?: string; note_type?: string; limit?: number }) => {
    const qs = new URLSearchParams();
    if (params?.ticker) qs.set("ticker", params.ticker);
    if (params?.note_type) qs.set("note_type", params.note_type);
    if (params?.limit) qs.set("limit", String(params.limit));
    const suffix = qs.toString() ? `?${qs}` : "";
    return apiRequest<AR<NoteStub[]>>(`${BASE}${suffix}`);
  },

  create: (payload: {
    title: string;
    note_type: string;
    company_tickers: string[];
    meeting_date?: string;
    ux_variant?: "A" | "B";
  }) => apiRequest<AR<NoteStub>>(BASE, "POST", payload),

  get: (noteId: string) =>
    apiRequest<AR<NoteStub>>(`${BASE}/${noteId}`),

  update: (
    noteId: string,
    payload: {
      title?: string;
      editor_content?: Record<string, unknown>;
      editor_plain_text?: string;
      company_tickers?: string[];
      meeting_date?: string;
    }
  ) => apiRequest<AR<NoteStub>>(`${BASE}/${noteId}`, "PUT", payload),

  delete: (noteId: string) =>
    apiRequest<AR<{ deleted: string }>>(`${BASE}/${noteId}`, "DELETE"),

  flagLine: (noteId: string, lineId: number, flagged: boolean) =>
    apiRequest<AR<{ line_id: number; flagged: boolean }>>(
      `${BASE}/${noteId}/transcript/flag`,
      "POST",
      { line_id: lineId, flagged }
    ),

  saveTranscript: (noteId: string, lines: TranscriptLine[], durationSeconds: number) =>
    apiRequest<AR<NoteStub>>(
      `${BASE}/${noteId}/transcript`,
      "POST",
      { transcript_lines: lines, duration_seconds: durationSeconds }
    ),

  // ------------------------------------------------------------------
  // Post-meeting wizard
  // ------------------------------------------------------------------

  suggestTopics: (noteId: string) =>
    apiRequest<AR<{ suggestions: string[] }>>(`${BASE}/${noteId}/summary/topics-suggest`),

  saveSpeakers: (noteId: string, mappings: SpeakerMapping[]) =>
    apiRequest<AR<NoteStub>>(`${BASE}/${noteId}/summary/speakers`, "POST", { mappings }),

  extractTopics: (noteId: string, topics: string[]) =>
    apiRequest<AR<NoteStub>>(`${BASE}/${noteId}/summary/extract`, "POST", { topics }),

  processDelta: (
    noteId: string,
    deltaId: string,
    action: "approve" | "edit" | "dismiss",
    editedText?: string
  ) =>
    apiRequest<AR<NoteStub>>(
      `${BASE}/${noteId}/summary/delta/${deltaId}`,
      "POST",
      { action, edited_text: editedText ?? null }
    ),

  markSummaryComplete: (noteId: string) =>
    apiRequest<AR<NoteStub>>(`${BASE}/${noteId}/summary/complete`, "POST", {}),

  regenerateSummary: (noteId: string) =>
    apiRequest<AR<NoteStub>>(`${BASE}/${noteId}/summary/regenerate`, "POST", {}),

  // ------------------------------------------------------------------
  // WebSocket helper — returns the WS URL (connection opened in component)
  // ------------------------------------------------------------------

  recordingWsUrl: (noteId: string, mode: "wasapi" | "browser" | "live_v2", language = "en-US") => {
    const base = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000";
    return `${base}/api/v1/notes/ws/recording/${noteId}?mode=${mode}&language=${language}`;
  },

  ingestUrlWsUrl: (noteId: string, sourceUrl: string, language = "auto") => {
    const base = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000";
    const qs = new URLSearchParams({ url: sourceUrl, language });
    return `${base}/api/v1/notes/ws/ingest-url/${noteId}?${qs}`;
  },
};
