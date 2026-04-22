# Meeting Intelligence — Plan 2: MeetingIntelligencePanel Sidebar

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On Variant B notes whose wizard has finished (`summary_status === "complete"`), replace the sidebar's CompleteStep with a dedicated `MeetingIntelligencePanel` that renders the AI-extraction output (narrative, topic fragments, action items, any legacy delta cards) in a scrollable read-only view. Variant A and every in-progress wizard state stay exactly as today.

**Architecture:** A single new dumb React component (`MeetingIntelligencePanel.tsx`) that takes `note: NoteStub` and reads directly from `note.ai_summary`. It does not call any APIs and does not mutate state — it is purely presentational. `NotesEditorView.tsx` gains one branch in its existing sidebar switch so the new panel renders instead of `PostMeetingWizard`'s CompleteStep only when `ux_variant === "B"` and `summary_status === "complete"`. No backend changes. No new endpoints. No chat — that lands in Plan 3, at which point the chat UI will be added inside `MeetingIntelligencePanel` below the extraction area.

**Tech Stack:** Next.js 15 + React 19 + TypeScript, Tailwind v4, `lucide-react` icons. No tests — this is pure presentational code; verification is `npx tsc --noEmit` plus manual browser smoke.

**Out of scope for Plan 2:**
- Chat input / sticky-bottom layout / chat history — Plan 3.
- `analysis_jobs` column + analysis module runs — Plan 3.
- `chat_messages` column + chat endpoints — Plan 3.
- Cross-note search / semantic retrieval — Plan 4.
- Any change to Variant A behaviour, or to Variant B states other than `complete`.

---

## File Structure

**Frontend — create:**
- `frontend/src/components/domain/notes/MeetingIntelligencePanel.tsx` — the whole feature. Renders a header, a narrative paragraph, a list of expandable topic-fragment cards, an action-item checklist, and (if present on old notes) pending delta cards as read-only badges. No state except per-card expand/collapse.

**Frontend — modify:**
- `frontend/src/app/(dashboard)/notes/[id]/NotesEditorView.tsx` — import `MeetingIntelligencePanel`; update the sidebar branching so `summary_status === "complete"` routes to the new panel for Variant B only, with the existing `PostMeetingWizard` continuing to handle every other state (including `complete` on Variant A).

No other files touched.

---

## Task 1: Create `MeetingIntelligencePanel` component

**Files:**
- Create: `frontend/src/components/domain/notes/MeetingIntelligencePanel.tsx`

- [ ] **Step 1: Write the component**

Create `frontend/src/components/domain/notes/MeetingIntelligencePanel.tsx`:

```tsx
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
```

- [ ] **Step 2: Type-check**

Run (from repo root):

```bash
cd frontend && npx tsc --noEmit 2>&1 | grep -v "\.next/types" | head -40
```

Expected: no errors on `MeetingIntelligencePanel.tsx`. (The pre-existing `.next/types/app/(dashboard)/notes/[id]/page.ts` Next 15 params-Promise error is unrelated and filtered out.)

- [ ] **Step 3: No commit yet**

Task 2 touches the same feature (the new panel becomes wired only in Task 2). Commit at the end of Task 2.

---

## Task 2: Wire `MeetingIntelligencePanel` into the sidebar (B + complete only)

**Files:**
- Modify: `frontend/src/app/(dashboard)/notes/[id]/NotesEditorView.tsx`

- [ ] **Step 1: Import the new panel**

Edit `frontend/src/app/(dashboard)/notes/[id]/NotesEditorView.tsx`. Add the import near the other domain-component imports at the top of the file:

```tsx
import MeetingIntelligencePanel from "@/components/domain/notes/MeetingIntelligencePanel";
```

- [ ] **Step 2: Narrow the wizard-state constant so "complete" can be routed separately**

Find the existing constant:

```tsx
const WIZARD_STATUSES = new Set([
  "awaiting_speakers", "awaiting_topics", "extracting", "awaiting_approval", "complete"
]);
```

Replace it with two narrower sets so we can distinguish "in-progress wizard" from "done":

```tsx
// The wizard UI is shown for every step before completion.
const WIZARD_IN_PROGRESS_STATUSES = new Set([
  "awaiting_speakers", "awaiting_topics", "extracting", "awaiting_approval",
]);
```

(Remove the old `WIZARD_STATUSES` constant entirely.)

- [ ] **Step 3: Update the `showWizard` derivation and add the B-only MeetingIntelligencePanel branch**

In the component body, find where `showWizard` is computed. Replace the single boolean with two:

```tsx
  const showWizard = WIZARD_IN_PROGRESS_STATUSES.has(note.summary_status);
  const showMeetingIntelligence = note.ux_variant === "B" && note.summary_status === "complete";
```

Then find the existing sidebar branching block. Replace this block:

```tsx
        {/* Right — Recording panel OR Post-meeting wizard OR Search panel (1/3) */}
        <div className="flex-[1] flex flex-col overflow-hidden bg-slate-50 border-l border-slate-200 min-w-0">
          {showRecordingPopup ? (
            <RecordingPanel
              noteId={note.note_id}
              onClose={onCloseRecording}
              onComplete={onRecordingComplete}
            />
          ) : showWizard ? (
            <PostMeetingWizard
              note={note}
              onSaveSpeakers={onSaveSpeakers}
              onExtractTopics={onExtractTopics}
              onDelta={onDelta}
              onMarkComplete={onMarkComplete}
            />
          ) : (
            <NoteSearchPanel
              contextTickers={note.company_tickers}
              contextNoteType={note.note_type}
            />
          )}
        </div>
```

with the four-way branch:

```tsx
        {/* Right — Recording panel → wizard → B-only MeetingIntelligencePanel → search panel */}
        <div className="flex-[1] flex flex-col overflow-hidden bg-slate-50 border-l border-slate-200 min-w-0">
          {showRecordingPopup ? (
            <RecordingPanel
              noteId={note.note_id}
              onClose={onCloseRecording}
              onComplete={onRecordingComplete}
            />
          ) : showWizard ? (
            <PostMeetingWizard
              note={note}
              onSaveSpeakers={onSaveSpeakers}
              onExtractTopics={onExtractTopics}
              onDelta={onDelta}
              onMarkComplete={onMarkComplete}
            />
          ) : showMeetingIntelligence ? (
            <MeetingIntelligencePanel note={note} />
          ) : note.summary_status === "complete" ? (
            // Variant A: show the existing CompleteStep via the wizard so A users see
            // the same "AI Summary & Polished Transcripts Finished" message they had
            // before this plan.
            <PostMeetingWizard
              note={note}
              onSaveSpeakers={onSaveSpeakers}
              onExtractTopics={onExtractTopics}
              onDelta={onDelta}
              onMarkComplete={onMarkComplete}
            />
          ) : (
            <NoteSearchPanel
              contextTickers={note.company_tickers}
              contextNoteType={note.note_type}
            />
          )}
        </div>
```

Why the explicit Variant-A branch for `"complete"`: the old `WIZARD_STATUSES` set treated `complete` as "show the wizard," which routed to `CompleteStep`. Now that we've pulled `complete` out of `WIZARD_IN_PROGRESS_STATUSES`, Variant A needs the explicit branch so its behaviour is preserved. Without it, A notes in the complete state would fall through to `NoteSearchPanel`.

- [ ] **Step 4: Type-check**

Run:

```bash
cd frontend && npx tsc --noEmit 2>&1 | grep -v "\.next/types" | head -40
```

Expected: no errors. If there's an "unused import" warning on `useRef`/`useEffect` in `NotesEditorView.tsx`, leave it — those imports are unrelated to this change.

- [ ] **Step 5: Commit (Tasks 1 + 2 together)**

```bash
git add frontend/src/components/domain/notes/MeetingIntelligencePanel.tsx frontend/src/app/\(dashboard\)/notes/\[id\]/NotesEditorView.tsx
git commit -m "feat(notes): MeetingIntelligencePanel sidebar for variant B complete state"
```

---

## Task 3: Manual smoke test

**Files:** none modified.

- [ ] **Step 1: Restart frontend dev server**

No backend changes in this plan, so the backend doesn't need a restart. In the frontend terminal:

```bash
# From frontend/ directory, this should already be running; if not:
npm run dev
```

- [ ] **Step 2: Verify Variant A is unchanged**

Open any Variant A note that's already in the `complete` state (softbank, after you've clicked Finish from Plan 1's fix). Confirm:

  - [ ] Sidebar shows the existing CompleteStep from `PostMeetingWizard` — "AI Summary & Polished Transcripts Finished" header, ai_narrative paragraph, topic fragment pills, action items if any.
  - [ ] No `[Variant B]` label anywhere. No new `MeetingIntelligencePanel` UI.
  - [ ] Library view shows `[A]` on the note row.

- [ ] **Step 3: Create a Variant B note and exercise the new panel**

From the notes library, click **New Note**. Fill in title/type/ticker, pick **B — New** in the Layout block, create. Record ~10 s of audio, **Stop & AI Polish**, **Save Both**. Let the wizard step through Speakers → Topics (blank → auto-derive) → Extracting → **complete**.

Confirm:

  - [ ] Sidebar now shows `MeetingIntelligencePanel`, not the wizard's CompleteStep.
  - [ ] Header reads "Meeting Intelligence" with a brain icon and a "Variant B" label on the right.
  - [ ] "Summary" section shows the ai_narrative text ("Extracted N topic fragments: …").
  - [ ] "Topic Fragments (N)" section shows one collapsible row per topic.
  - [ ] Clicking a row expands it to show topic_summary, key_numbers (if any), speakers, and supporting quotes with timestamps.
  - [ ] "Action Items" section renders a checklist with empty checkboxes.
  - [ ] If the note has no deltas (expected for new notes), the "Legacy Delta Cards" section does NOT appear.

- [ ] **Step 4: Verify in-progress wizard states still route to the wizard on both variants**

Create a second Variant B note. Record ~10 s, Save Both. While the wizard is in the Speakers or Topics step, confirm:

  - [ ] Sidebar shows `PostMeetingWizard`, not `MeetingIntelligencePanel`.

Do the same on a Variant A note — wizard in intermediate state should still show the wizard.

- [ ] **Step 5: Verify `NoteSearchPanel` still shows for B notes that haven't been recorded yet**

Create a third Variant B note but don't record anything. Confirm:

  - [ ] Sidebar shows `NoteSearchPanel` (the "All Sources" search chat) — same as Variant A does in the no-recording state.

- [ ] **Step 6: Reload check**

Reload the browser on the Variant B note from Step 3. Confirm:

  - [ ] Editor still shows the three inserted sections (Plan 1 verification — no regression).
  - [ ] Sidebar still shows `MeetingIntelligencePanel` with all the extracted output.

- [ ] **Step 7: No commit for this task**

Verification only.

---

## Self-Review Checklist

**Spec coverage** (from `docs/superpowers/specs/2026-04-22-meeting-intelligence-ab-design.md` §10 Plan 2):

- "New sidebar branch for variant B when `summary_status === 'complete'`." → Task 2 Step 3.
- "Render AI extraction group (narrative, topic fragments, delta cards, action items) in read-only form." → Task 1 component includes all four sections with appropriate empty states.
- "Verification: on a B note that's completed the wizard, the sidebar shows the extraction panel with all wizard output; A notes unaffected." → Task 3 Steps 2, 3, 6.

**Placeholder scan:** No TBD / TODO / "add appropriate X" / "similar to Task N" patterns. All JSX and command steps have the exact content or invocation the engineer needs.

**Type / name consistency:**

- `MeetingIntelligencePanel` component name consistent between Task 1 export, Task 2 Step 1 import, and Task 2 Step 3 usage.
- `showMeetingIntelligence`, `showWizard`, `showRecordingPopup` used consistently in Task 2 Step 3.
- `WIZARD_IN_PROGRESS_STATUSES` defined once in Task 2 Step 2, referenced once in Task 2 Step 3.
- `TopicFragment` and `DeltaCard` types come from `@/lib/api/notesClient` (already exported there — verified via Plan 1's work).
- Props type `{ note: NoteStub }` on the component matches what Task 2 Step 3 passes.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-22-meeting-intelligence-plan-2-sidebar-panel.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints for review.

Which approach?
