"use client";

/**
 * PostMeetingWizard — replaces the right-side NoteSearchPanel
 * after a recording stops and through the full AI summarization flow.
 *
 * Steps driven by note.summary_status:
 *   awaiting_speakers → Step 0: Label speakers
 *   awaiting_topics   → Step 1: Enter / confirm topics
 *   extracting        → Step 2: Progress indicator (backend running)
 *   awaiting_approval → Step 3: Review delta cards
 *   complete          → Step 4: Summary + note enhancements
 */

import { useState, useEffect } from "react";
import { Users, Tag, Loader2, GitCompare, Sparkles, Check, X, Edit2, Flag } from "lucide-react";
import type { NoteStub, DeltaCard } from "@/lib/api/notesClient";
import { notesClient } from "@/lib/api/notesClient";

const CHANGE_TYPE_COLORS: Record<string, string> = {
  SIGNIFICANT: "border-red-200 bg-red-50",
  NUMBER_CHANGE: "border-blue-200 bg-blue-50",
  TONE_SHIFT: "border-amber-200 bg-amber-50",
  NEW_RISK: "border-orange-200 bg-orange-50",
  RESOLVED: "border-green-200 bg-green-50",
};

const SIGNIFICANCE_BADGES: Record<string, string> = {
  HIGH: "bg-red-100 text-red-700",
  MEDIUM: "bg-amber-100 text-amber-700",
  LOW: "bg-slate-100 text-slate-600",
};

interface Props {
  note: NoteStub;
  onSaveSpeakers: (mappings: { label: string; name: string; role?: string }[]) => Promise<void>;
  onExtractTopics: (topics: string[]) => Promise<void>;
  onDelta: (deltaId: string, action: "approve" | "edit" | "dismiss", editedText?: string) => Promise<void>;
}

// ---------------------------------------------------------------------------
// Step 0: Speaker labeling
// ---------------------------------------------------------------------------

function SpeakerStep({ note, onSave }: { note: NoteStub; onSave: Props["onSaveSpeakers"] }) {
  // Detect unique speaker labels from transcript
  const labels = Array.from(
    new Set(note.transcript_lines.map((l) => l.speaker_label))
  ).sort();

  const [mappings, setMappings] = useState<Record<string, { name: string; role: string }>>(
    Object.fromEntries(labels.map((l) => [l, { name: "", role: "" }]))
  );
  const [isSaving, setIsSaving] = useState(false);

  const handleSave = async () => {
    setIsSaving(true);
    const payload = labels.map((l) => ({
      label: l,
      name: mappings[l]?.name || l,
      role: mappings[l]?.role || undefined,
    }));
    await onSave(payload);
    setIsSaving(false);
  };

  return (
    <div className="flex-1 overflow-y-auto p-4 space-y-4">
      <div className="flex items-center gap-2">
        <Users size={16} className="text-slate-600" />
        <h3 className="text-sm font-semibold text-slate-800">Label Speakers</h3>
      </div>
      <p className="text-xs text-slate-500">
        I detected {labels.length} speaker{labels.length !== 1 ? "s" : ""}. Label them to make future searches like
        "what did the CFO say about margins" work.
      </p>

      <div className="space-y-3">
        {labels.map((label) => (
          <div key={label} className="space-y-1">
            <p className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider">{label}</p>
            <div className="flex gap-2">
              <input
                type="text"
                value={mappings[label]?.name ?? ""}
                onChange={(e) =>
                  setMappings((m) => ({ ...m, [label]: { ...m[label], name: e.target.value } }))
                }
                placeholder="Full name (e.g. John Smith)"
                className="flex-1 px-2.5 py-1.5 text-xs border border-slate-200 rounded-lg focus:outline-none focus:ring-1 focus:ring-indigo-500 focus:border-indigo-500"
              />
              <input
                type="text"
                value={mappings[label]?.role ?? ""}
                onChange={(e) =>
                  setMappings((m) => ({ ...m, [label]: { ...m[label], role: e.target.value } }))
                }
                placeholder="Role (e.g. CFO)"
                className="w-24 px-2.5 py-1.5 text-xs border border-slate-200 rounded-lg focus:outline-none focus:ring-1 focus:ring-indigo-500 focus:border-indigo-500"
              />
            </div>
          </div>
        ))}
      </div>

      <div className="flex gap-2 pt-2">
        <button
          onClick={() => onSave(labels.map((l) => ({ label: l, name: l })))}
          className="text-xs text-slate-500 hover:text-slate-700 px-3 py-1.5"
        >
          Skip
        </button>
        <button
          onClick={handleSave}
          disabled={isSaving}
          className="flex-1 py-2 text-xs font-medium bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50 transition-colors"
        >
          {isSaving ? "Saving…" : "Continue →"}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 1: Topic elicitation
// ---------------------------------------------------------------------------

function TopicsStep({ note, onExtract }: { note: NoteStub; onExtract: Props["onExtractTopics"] }) {
  const [topics, setTopics] = useState<string[]>([]);
  const [topicInput, setTopicInput] = useState("");
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [isLoadingSuggestions, setIsLoadingSuggestions] = useState(false);
  const [isExtracting, setIsExtracting] = useState(false);

  useEffect(() => {
    setIsLoadingSuggestions(true);
    notesClient.suggestTopics(note.note_id)
      .then((res) => { if (res.success && res.data) setSuggestions(res.data.suggestions); })
      .finally(() => setIsLoadingSuggestions(false));
  }, [note.note_id]);

  const addTopic = (t: string) => {
    const clean = t.trim().toLowerCase();
    if (!clean || topics.includes(clean)) return;
    setTopics((prev) => [...prev, clean]);
    setTopicInput("");
  };

  const removeTopic = (t: string) => setTopics((prev) => prev.filter((x) => x !== t));

  const handleExtract = async () => {
    if (topics.length === 0) return;
    setIsExtracting(true);
    await onExtract(topics);
    setIsExtracting(false);
  };

  return (
    <div className="flex-1 overflow-y-auto p-4 space-y-4">
      <div className="flex items-center gap-2">
        <Tag size={16} className="text-slate-600" />
        <h3 className="text-sm font-semibold text-slate-800">Key Topics</h3>
      </div>
      <p className="text-xs text-slate-500">
        What were the most important topics from this meeting?
        I'll extract detailed fragments for each one.
      </p>

      {/* Added topics */}
      {topics.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {topics.map((t) => (
            <span key={t} className="flex items-center gap-1 px-2 py-1 text-xs bg-indigo-600 text-white rounded-lg">
              {t}
              <button onClick={() => removeTopic(t)} className="hover:text-slate-300">
                <X size={10} />
              </button>
            </span>
          ))}
        </div>
      )}

      {/* Input */}
      <div className="flex gap-2">
        <input
          type="text"
          value={topicInput}
          onChange={(e) => setTopicInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); addTopic(topicInput); } }}
          placeholder="e.g. gross margin, capex, China demand…"
          className="flex-1 px-3 py-1.5 text-xs border border-slate-200 rounded-lg focus:outline-none focus:ring-1 focus:ring-indigo-500 focus:border-indigo-500"
        />
        <button onClick={() => addTopic(topicInput)} className="px-3 text-xs font-medium border border-indigo-200 text-indigo-600 rounded-lg hover:bg-indigo-50 transition-colors">
          Add
        </button>
      </div>

      {/* AI suggestions */}
      {isLoadingSuggestions ? (
        <div className="flex items-center gap-2 text-xs text-slate-400">
          <Loader2 size={12} className="animate-spin" /> Scanning transcript for topics…
        </div>
      ) : suggestions.length > 0 && (
        <div>
          <p className="text-[10px] text-slate-400 font-medium uppercase tracking-wider mb-2">AI-detected topics</p>
          <div className="flex flex-wrap gap-1.5">
            {suggestions
              .filter((s) => !topics.includes(s.toLowerCase()))
              .map((s) => (
                <button
                  key={s}
                  onClick={() => addTopic(s)}
                  className="px-2 py-1 text-xs border border-dashed border-slate-300 text-slate-600 rounded-lg hover:border-indigo-400 hover:bg-indigo-50 hover:text-indigo-700 transition-colors"
                >
                  + {s}
                </button>
              ))}
          </div>
        </div>
      )}

      <button
        onClick={handleExtract}
        disabled={topics.length === 0 || isExtracting}
        className="w-full py-2 text-xs font-medium bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50 transition-colors flex items-center justify-center gap-2"
      >
        {isExtracting ? (
          <><Loader2 size={13} className="animate-spin" /> Extracting {topics.length} topics…</>
        ) : (
          `Extract ${topics.length} topic${topics.length !== 1 ? "s" : ""} →`
        )}
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 2: Extracting (progress display)
// ---------------------------------------------------------------------------

function ExtractingStep({ note }: { note: NoteStub }) {
  const topics = note.ai_summary?.user_topics ?? [];
  return (
    <div className="flex-1 flex flex-col items-center justify-center p-6 gap-4">
      <Loader2 size={24} className="text-slate-400 animate-spin" />
      <div className="text-center space-y-1">
        <p className="text-sm font-medium text-slate-700">Extracting topics…</p>
        <p className="text-xs text-slate-400">
          Running LLM extraction across {topics.length} topic{topics.length !== 1 ? "s" : ""} and comparing with previous meetings.
        </p>
      </div>
      {topics.length > 0 && (
        <div className="w-full space-y-1.5 pt-2">
          {topics.map((t, i) => (
            <div key={t} className="flex items-center gap-2">
              <div className="w-3 h-3 rounded-full border-2 border-slate-300 animate-pulse" style={{ animationDelay: `${i * 0.2}s` }} />
              <span className="text-xs text-slate-600">{t}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 3: Delta cards for approval
// ---------------------------------------------------------------------------

function DeltaStep({
  note, onDelta,
}: {
  note: NoteStub;
  onDelta: Props["onDelta"];
}) {
  const deltaCards: DeltaCard[] = note.ai_summary?.delta_cards ?? [];
  const pending = deltaCards.filter((d) => d.status === "PENDING");
  const resolved = deltaCards.filter((d) => d.status !== "PENDING");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [processing, setProcessing] = useState<string | null>(null);

  const handle = async (deltaId: string, action: "approve" | "edit" | "dismiss", text?: string) => {
    setProcessing(deltaId);
    await onDelta(deltaId, action, text);
    setEditingId(null);
    setProcessing(null);
  };

  if (deltaCards.length === 0) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center p-6 gap-3 text-center">
        <Check size={24} className="text-green-500" />
        <p className="text-sm font-medium text-slate-700">No significant changes found.</p>
        <p className="text-xs text-slate-400">
          Management's messaging on these topics appears consistent with previous meetings.
        </p>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto p-3 space-y-3">
      <div className="flex items-center gap-2">
        <GitCompare size={16} className="text-slate-600" />
        <h3 className="text-sm font-semibold text-slate-800">
          {pending.length > 0 ? `${pending.length} change${pending.length !== 1 ? "s" : ""} to review` : "All changes reviewed"}
        </h3>
      </div>

      {pending.map((card) => (
        <div
          key={card.delta_id}
          className={`border rounded-xl overflow-hidden ${CHANGE_TYPE_COLORS[card.change_type] ?? "border-slate-200 bg-slate-50"}`}
        >
          {/* Card header */}
          <div className="px-3 py-2 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold text-slate-700 capitalize">{card.topic}</span>
              <span className={`px-1.5 py-0.5 text-[9px] font-bold rounded uppercase tracking-wide ${SIGNIFICANCE_BADGES[card.significance] ?? ""}`}>
                {card.significance}
              </span>
              <span className="text-[9px] text-slate-500">{card.change_type.replace("_", " ")}</span>
            </div>
          </div>

          {/* Before / After */}
          <div className="px-3 pb-2 space-y-2">
            <div>
              <p className="text-[9px] text-slate-500 font-medium uppercase tracking-wider mb-0.5">
                {card.previous_source}
              </p>
              <p className="text-xs text-slate-600 italic">"{card.previous_statement}"</p>
            </div>
            <div className="h-px bg-current opacity-20" />
            <div>
              <p className="text-[9px] text-slate-500 font-medium uppercase tracking-wider mb-0.5">Today</p>
              {editingId === card.delta_id ? (
                <textarea
                  value={editText}
                  onChange={(e) => setEditText(e.target.value)}
                  className="w-full text-xs border border-slate-300 rounded-lg p-2 focus:outline-none focus:ring-1 focus:ring-indigo-500 focus:border-indigo-500 resize-none"
                  rows={3}
                />
              ) : (
                <p className="text-xs text-slate-800 font-medium">"{card.current_statement}"</p>
              )}
            </div>
          </div>

          {/* Actions */}
          <div className="px-3 pb-3 flex gap-2">
            {editingId === card.delta_id ? (
              <>
                <button
                  onClick={() => handle(card.delta_id, "edit", editText)}
                  disabled={processing === card.delta_id}
                  className="flex-1 py-1.5 text-xs font-medium bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50 flex items-center justify-center gap-1"
                >
                  <Check size={12} /> Save
                </button>
                <button onClick={() => setEditingId(null)} className="px-3 py-1.5 text-xs text-slate-600 border border-slate-200 rounded-lg hover:bg-white">
                  Cancel
                </button>
              </>
            ) : (
              <>
                <button
                  onClick={() => handle(card.delta_id, "approve")}
                  disabled={processing === card.delta_id}
                  className="flex-1 py-1.5 text-xs font-medium bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50 flex items-center justify-center gap-1 transition-colors"
                >
                  {processing === card.delta_id ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
                  Save
                </button>
                <button
                  onClick={() => { setEditingId(card.delta_id); setEditText(card.current_statement); }}
                  className="px-3 py-1.5 text-xs text-slate-600 border border-slate-200 rounded-lg hover:bg-white flex items-center gap-1 transition-colors"
                >
                  <Edit2 size={12} /> Edit
                </button>
                <button
                  onClick={() => handle(card.delta_id, "dismiss")}
                  className="px-3 py-1.5 text-xs text-slate-400 hover:text-red-500 border border-slate-200 rounded-lg hover:border-red-200 hover:bg-red-50 transition-colors"
                  title="Dismiss"
                >
                  <X size={12} />
                </button>
              </>
            )}
          </div>
        </div>
      ))}

      {/* Resolved section */}
      {resolved.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-[10px] text-slate-400 font-medium uppercase tracking-wider">Resolved</p>
          {resolved.map((card) => (
            <div key={card.delta_id} className="flex items-center gap-2 px-3 py-2 bg-white border border-slate-100 rounded-lg">
              <div className={`w-2 h-2 rounded-full ${
                card.status === "APPROVED" || card.status === "EDITED" ? "bg-green-500" : "bg-slate-300"
              }`} />
              <span className="text-xs text-slate-500 capitalize">{card.topic}</span>
              <span className="ml-auto text-[9px] text-slate-400">{card.status}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 4: Complete — summary + note enhancements
// ---------------------------------------------------------------------------

function CompleteStep({ note }: { note: NoteStub }) {
  const summary = note.ai_summary;
  return (
    <div className="flex-1 overflow-y-auto p-4 space-y-4">
      <div className="flex items-center gap-2">
        <Sparkles size={16} className="text-amber-500" />
        <h3 className="text-sm font-semibold text-slate-800">AI Summary Complete</h3>
      </div>

      {summary?.ai_narrative && (
        <p className="text-xs text-slate-600 leading-relaxed bg-white border border-slate-200 rounded-xl p-3">
          {summary.ai_narrative}
        </p>
      )}

      {summary?.action_items && summary.action_items.length > 0 && (
        <div>
          <p className="text-[10px] text-slate-500 font-medium uppercase tracking-wider mb-2">Action Items</p>
          <div className="space-y-1.5">
            {summary.action_items.map((item, i) => (
              <div key={i} className="flex items-start gap-2 text-xs text-slate-700">
                <div className="w-4 h-4 border border-slate-300 rounded shrink-0 mt-0.5" />
                {item}
              </div>
            ))}
          </div>
        </div>
      )}

      {summary?.note_enhancements && summary.note_enhancements.length > 0 && (
        <div>
          <p className="text-[10px] text-slate-500 font-medium uppercase tracking-wider mb-2">
            Suggested additions to your notes
          </p>
          <div className="space-y-2">
            {summary.note_enhancements.map((item, i) => (
              <div key={i} className="flex items-start gap-2 px-3 py-2 bg-amber-50 border border-amber-200 rounded-lg text-xs text-amber-800">
                <Flag size={11} className="text-amber-500 shrink-0 mt-0.5" />
                {item}
              </div>
            ))}
          </div>
        </div>
      )}

      {summary?.topic_fragments && summary.topic_fragments.length > 0 && (
        <div>
          <p className="text-[10px] text-slate-500 font-medium uppercase tracking-wider mb-2">
            {summary.topic_fragments.length} topic fragments saved
          </p>
          <div className="flex flex-wrap gap-1.5">
            {summary.topic_fragments.map((tf) => (
              <span key={tf.topic} className={`px-2 py-1 text-xs rounded-lg ${
                tf.overall_tone === "bullish" ? "bg-green-50 text-green-700" :
                tf.overall_tone === "bearish" ? "bg-red-50 text-red-700" :
                tf.overall_tone === "cautious" ? "bg-amber-50 text-amber-700" :
                "bg-slate-100 text-slate-600"
              }`}>
                {tf.topic}
                <span className="ml-1 text-[9px] opacity-60">({tf.overall_tone})</span>
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main wizard router
// ---------------------------------------------------------------------------

export default function PostMeetingWizard({ note, onSaveSpeakers, onExtractTopics, onDelta }: Props) {
  const STEP_LABELS: Record<string, string> = {
    awaiting_speakers: "1 / 4 — Label Speakers",
    awaiting_topics: "2 / 4 — Choose Topics",
    extracting: "3 / 4 — Extracting",
    awaiting_approval: "4 / 4 — Review Changes",
    complete: "Done",
  };

  return (
    <div className="flex flex-col h-full">
      {/* Wizard progress header */}
      <div className="px-4 py-3 border-b border-slate-200 bg-white shrink-0">
        <div className="flex items-center gap-2">
          <Sparkles size={14} className="text-amber-500" />
          <span className="text-xs font-semibold text-slate-700">Post-Meeting AI</span>
          <span className="ml-auto text-[10px] text-slate-400 font-mono">
            {STEP_LABELS[note.summary_status] ?? ""}
          </span>
        </div>
        {/* Progress bar */}
        <div className="mt-2 h-1 bg-slate-100 rounded-full overflow-hidden">
          <div
            className="h-full bg-amber-400 rounded-full transition-all duration-500"
            style={{
              width: {
                awaiting_speakers: "20%",
                awaiting_topics: "40%",
                extracting: "60%",
                awaiting_approval: "80%",
                complete: "100%",
              }[note.summary_status] ?? "0%",
            }}
          />
        </div>
      </div>

      {/* Step content */}
      {note.summary_status === "awaiting_speakers" && (
        <SpeakerStep note={note} onSave={onSaveSpeakers} />
      )}
      {note.summary_status === "awaiting_topics" && (
        <TopicsStep note={note} onExtract={onExtractTopics} />
      )}
      {note.summary_status === "extracting" && (
        <ExtractingStep note={note} />
      )}
      {note.summary_status === "awaiting_approval" && (
        <DeltaStep note={note} onDelta={onDelta} />
      )}
      {note.summary_status === "complete" && (
        <CompleteStep note={note} />
      )}
    </div>
  );
}
