"use client";

/**
 * MeetingIntelligencePanel — Variant-B sidebar view shown after the wizard
 * has completed (summary_status === "complete"). Renders the AI extraction
 * output in a scrollable read-only layout.
 *
 * Plan 2 scope: extraction-only. Chat input + history are added in Plan 3;
 * this component intentionally leaves vertical space at the bottom that
 * Plan 3 will fill with the chat area.
 */

import { useState } from "react";
import { Brain, Sparkles, ChevronDown, ChevronRight, TrendingUp, TrendingDown, Minus, Flag, CheckSquare, GitCompare } from "lucide-react";
import type { NoteStub, TopicFragment, DeltaCard } from "@/lib/api/notesClient";

interface Props {
  note: NoteStub;
}

const TONE_STYLE: Record<string, { label: string; className: string; Icon: typeof TrendingUp }> = {
  bullish:  { label: "bullish",  className: "text-green-700 bg-green-50 border-green-200", Icon: TrendingUp },
  bearish:  { label: "bearish",  className: "text-red-700 bg-red-50 border-red-200",     Icon: TrendingDown },
  cautious: { label: "cautious", className: "text-amber-700 bg-amber-50 border-amber-200", Icon: Minus },
  neutral:  { label: "neutral",  className: "text-slate-600 bg-slate-50 border-slate-200", Icon: Minus },
};

function toneFor(tone: string) {
  return TONE_STYLE[tone?.toLowerCase()] ?? TONE_STYLE.neutral;
}

function TopicFragmentCard({ fragment }: { fragment: TopicFragment }) {
  const [open, setOpen] = useState(false);
  const tone = toneFor(fragment.overall_tone);
  const supporting = fragment.supporting_sentences ?? [];

  return (
    <div className="border border-slate-200 rounded-lg bg-white overflow-hidden">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 px-3 py-2 hover:bg-slate-50 transition-colors text-left"
      >
        {open ? <ChevronDown size={13} className="text-slate-400 shrink-0" /> : <ChevronRight size={13} className="text-slate-400 shrink-0" />}
        <span className="flex-1 text-xs font-semibold text-slate-800 capitalize truncate">{fragment.topic}</span>
        <span className={`px-1.5 py-0.5 text-[9px] font-bold rounded border uppercase tracking-wide flex items-center gap-1 ${tone.className}`}>
          <tone.Icon size={9} />
          {tone.label}
        </span>
      </button>

      {open && (
        <div className="px-3 py-2 border-t border-slate-100 space-y-2 bg-slate-50">
          {fragment.topic_summary && (
            <p className="text-xs text-slate-700 leading-relaxed">{fragment.topic_summary}</p>
          )}

          {fragment.key_numbers && fragment.key_numbers.length > 0 && (
            <div>
              <p className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider mb-1">Key Numbers</p>
              <div className="flex flex-wrap gap-1">
                {fragment.key_numbers.map((n, i) => (
                  <span key={i} className="px-1.5 py-0.5 text-[10px] font-mono font-semibold bg-indigo-50 text-indigo-700 rounded border border-indigo-100">
                    {n}
                  </span>
                ))}
              </div>
            </div>
          )}

          {fragment.speakers_involved && fragment.speakers_involved.length > 0 && (
            <div>
              <p className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider mb-1">Speakers</p>
              <p className="text-[11px] text-slate-600">{fragment.speakers_involved.join(", ")}</p>
            </div>
          )}

          {supporting.length > 0 && (
            <div>
              <p className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider mb-1">
                Supporting Quotes ({supporting.length})
              </p>
              <div className="space-y-1.5">
                {supporting.slice(0, 4).map((s) => (
                  <div key={s.sentence_id} className="flex gap-2 text-[11px]">
                    <span className="shrink-0 px-1 py-0.5 text-[9px] font-mono font-semibold bg-slate-200 text-slate-700 rounded">
                      {s.timestamp}
                    </span>
                    <span className="text-slate-700 leading-snug italic">&ldquo;{s.text}&rdquo;</span>
                  </div>
                ))}
                {supporting.length > 4 && (
                  <p className="text-[10px] text-slate-400">+ {supporting.length - 4} more in the polished transcript above</p>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function DeltaCardPreview({ card }: { card: DeltaCard }) {
  return (
    <div className="border border-slate-200 bg-white rounded-lg p-2.5 space-y-1.5">
      <div className="flex items-center gap-2">
        <GitCompare size={11} className="text-slate-400" />
        <span className="text-xs font-semibold text-slate-700 capitalize">{card.topic}</span>
        <span className="ml-auto text-[9px] font-bold uppercase tracking-wide text-slate-500 bg-slate-100 px-1.5 py-0.5 rounded">
          {card.change_type.replace("_", " ")}
        </span>
      </div>
      <p className="text-[11px] text-slate-400 italic">&ldquo;{card.previous_statement}&rdquo;</p>
      <p className="text-[11px] text-slate-800 font-medium">&ldquo;{card.current_statement}&rdquo;</p>
    </div>
  );
}

export default function MeetingIntelligencePanel({ note }: Props) {
  const summary = note.ai_summary;
  const topicFragments: TopicFragment[] = summary?.topic_fragments ?? [];
  const deltaCards: DeltaCard[] = summary?.delta_cards ?? [];
  const actionItems: string[] = summary?.action_items ?? [];
  const narrative = summary?.ai_narrative ?? "";

  return (
    <div className="flex flex-col h-full overflow-hidden bg-slate-50">
      {/* Panel header */}
      <div className="px-4 py-3 border-b border-slate-200 bg-white shrink-0">
        <div className="flex items-center gap-2">
          <Brain size={14} className="text-indigo-600" />
          <span className="text-xs font-semibold text-slate-700">Meeting Intelligence</span>
          <span className="ml-auto text-[10px] text-slate-400 font-mono">Variant B</span>
        </div>
        <p className="mt-1 text-[10px] text-slate-400">
          AI extraction output. Transcripts are in the main editor.
        </p>
      </div>

      {/* Scrollable body */}
      <div className="flex-1 overflow-y-auto p-3 space-y-4 min-h-0">
        {/* Narrative summary */}
        {narrative && (
          <section>
            <div className="flex items-center gap-1.5 mb-1.5">
              <Sparkles size={11} className="text-amber-500" />
              <h4 className="text-[10px] font-bold text-slate-600 uppercase tracking-wider">Summary</h4>
            </div>
            <p className="text-xs text-slate-700 leading-relaxed bg-white border border-slate-200 rounded-lg p-3">
              {narrative}
            </p>
          </section>
        )}

        {/* Topic fragments */}
        {topicFragments.length > 0 && (
          <section>
            <div className="flex items-center gap-1.5 mb-1.5">
              <Flag size={11} className="text-slate-500" />
              <h4 className="text-[10px] font-bold text-slate-600 uppercase tracking-wider">
                Topic Fragments ({topicFragments.length})
              </h4>
            </div>
            <div className="space-y-1.5">
              {topicFragments.map((tf, i) => (
                <TopicFragmentCard key={tf.fragment_id ?? `${tf.topic}-${i}`} fragment={tf} />
              ))}
            </div>
          </section>
        )}

        {/* Action items */}
        {actionItems.length > 0 && (
          <section>
            <div className="flex items-center gap-1.5 mb-1.5">
              <CheckSquare size={11} className="text-slate-500" />
              <h4 className="text-[10px] font-bold text-slate-600 uppercase tracking-wider">
                Action Items ({actionItems.length})
              </h4>
            </div>
            <div className="space-y-1 bg-white border border-slate-200 rounded-lg p-2.5">
              {actionItems.map((item, i) => (
                <div key={i} className="flex items-start gap-2 text-xs text-slate-700">
                  <div className="w-3.5 h-3.5 border border-slate-300 rounded shrink-0 mt-0.5" />
                  <span className="leading-snug">{item}</span>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* Legacy delta cards (only shown on old notes that still carry them) */}
        {deltaCards.length > 0 && (
          <section>
            <div className="flex items-center gap-1.5 mb-1.5">
              <GitCompare size={11} className="text-slate-500" />
              <h4 className="text-[10px] font-bold text-slate-600 uppercase tracking-wider">
                Legacy Delta Cards ({deltaCards.length})
              </h4>
            </div>
            <div className="space-y-1.5">
              {deltaCards.map((card) => (
                <DeltaCardPreview key={card.delta_id} card={card} />
              ))}
            </div>
            <p className="mt-1.5 text-[10px] text-slate-400">
              Delta comparison is retired — rebuilt as a chat-agent tool in Plan 4.
            </p>
          </section>
        )}

        {/* Empty state — wizard ran but ai_summary is empty */}
        {!narrative && topicFragments.length === 0 && actionItems.length === 0 && deltaCards.length === 0 && (
          <div className="flex flex-col items-center justify-center py-12 text-center gap-2">
            <Brain size={24} className="text-slate-300" />
            <p className="text-sm font-medium text-slate-600">No AI output yet.</p>
            <p className="text-[11px] text-slate-400 max-w-[220px]">
              Run the wizard (Topics step) to extract topic fragments from this meeting.
            </p>
          </div>
        )}
      </div>

      {/* Plan 3 will add a sticky chat input + message history below this line. */}
    </div>
  );
}
