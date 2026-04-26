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
    audio_duration_sec?: number;
    chunk_count?: number;
    chunk_seconds?: number[];
    gemini_seconds?: number;
    total_seconds?: number;
    /** What language Gemini rendered into segments[*].text_english.
     * "none" means monolingual; otherwise either a preset code or a
     * free-form name (e.g. "French", "Arabic"). */
    translation_language?: "none" | "en" | "zh-hans" | "zh-hant" | "ja" | "ko" | string;
    /** Header-friendly label resolved server-side (e.g. "English",
     * "简体中文", "Arabic"). Used as the third column header in the
     * polished_transcript table. */
    translation_label?: string;
    /** "hans" or "hant" -- only set after a /convert-chinese toggle.
     * Default (unset) means the original Gemini output, which is "hans"
     * for new uploads (per the prompt rule) but legacy notes may be either. */
    chinese_variant?: "hans" | "hant";
    /** Coverage gaps flagged by the backend gap detector. Each gap is a
     * stretch of audio (>5 min) where Gemini produced no transcript --
     * either because it skipped (the "lazy" failure mode) or because
     * downstream errors lost the segments. The UI surfaces these as a
     * banner with one-click "Retranscribe from {start_label}" buttons. */
    coverage_gaps?: Array<{
      kind: "lead" | "middle" | "tail";
      start_sec: number;
      end_sec: number;
      duration_sec: number;
      start_label: string;
      end_label: string;
    }>;
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
      note_type?: string;
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

  /**
   * Upload an audio file and run the same Gemini polish pipeline as a live
   * recording — returns a fresh note_id you can navigate to. Uses the
   * built-in fetch (not apiRequest) so we can send multipart/form-data.
   * Long-running: typical 30-min audio ~ 60-90 sec wait.
   */
  uploadTranscribeAudio: async (
    file: File,
    options?: {
      title?: string;
      language?: "auto" | "zh" | "ja" | "ko" | "en";
      note_type?: string;
      /** Translation target. Either one of the preset codes or any free-form
       * language name (e.g. "French", "Arabic"). "none" skips translation
       * entirely. Backend treats unknown strings as a literal language
       * name in the Gemini prompt. */
      translation_language?: "none" | "en" | "zh-hans" | "zh-hant" | "ja" | "ko" | string;
    },
  ): Promise<AR<{
    note_id:            string;
    language:           string;
    language_source:    "user" | "auto";
    is_bilingual:       boolean;
    segments:           number;
    key_topics:         string[];
    input_tokens:       number;
    output_tokens:      number;
    audio_filename:     string;
    gemini_seconds?:    number;
    total_seconds?:     number;
    audio_duration_sec?: number;
    chunk_count?:       number;
    chunk_seconds?:     number[];
  }>> => {
    const fd = new FormData();
    fd.append("audio", file);
    if (options?.title)     fd.append("title",     options.title);
    if (options?.note_type) fd.append("note_type", options.note_type);
    if (options?.language && options.language !== "auto") fd.append("language", options.language);
    if (options?.translation_language) fd.append("translation_language", options.translation_language);

    const url = `${(process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1")}${BASE}/upload-transcribe`;
    const response = await fetch(url, { method: "POST", body: fd });
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(
        (errorData as { detail?: string }).detail ||
          `Upload failed: ${response.status} ${response.statusText}`,
      );
    }
    return response.json();
  },

  /** Toggle a note's Chinese transcript between Simplified (zh-Hans) and
   * Traditional (zh-Hant) using the local zhconv lib. No LLM cost. */
  convertChineseVariant: (noteId: string, to: "hans" | "hant") =>
    apiRequest<AR<{
      note_id: string;
      to: "hans" | "hant";
      fields_changed: number;
      total_segments: number;
    }>>(`${BASE}/${noteId}/convert-chinese`, "POST", { to }),

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

  /**
   * Cut audio from `startSeconds` and re-transcribe just that portion via
   * Gemini, splicing the new segments back into the note's existing
   * polished transcript. Used to recover from Gemini repetition loops or
   * partial chunk failures without re-paying for already-good earlier
   * minutes.
   */
  retranscribeFrom: (
    noteId: string,
    startSeconds: number,
    language?: "zh" | "ja" | "ko" | "en",
  ) =>
    apiRequest<AR<{
      note_id: string;
      dropped: number;
      added: number;
      total_segments: number;
      start_seconds: number;
      gemini_seconds: number | null;
      chunk_count: number | null;
      language: string;
    }>>(
      `${BASE}/${noteId}/retranscribe-from`,
      "POST",
      { start_seconds: startSeconds, language: language ?? null },
    ),

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
