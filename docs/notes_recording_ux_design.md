# Notes Recording UX — Sidebar Design

**Date:** 2026-04-22
**Status:** Design spec — ready to implement

## Current Layout
```
┌─────────────────────────────────────┬──────────────────┐
│ Rich Text Editor (2/3)              │ Right Panel (1/3)│
│                                     │ - Search panel   │
│ [Note content]                      │   OR             │
│                                     │ - Post-meeting   │
│                                     │   wizard         │
└─────────────────────────────────────┴──────────────────┘
+ Floating RecordingPopup (bottom-right)
```

## New Layout: During Recording
```
┌─────────────────────────────────────┬──────────────────┐
│ Rich Text Editor (2/3)              │ Recording (1/3)  │
│                                     │ ┌──────────────┐ │
│ [Note content]                      │ │ 🔴 00:05:32  │ │
│                                     │ │ System Audio  │ │
│                                     │ │ Lang: JA      │ │
│                                     │ ├──────────────┤ │
│                                     │ │ Live Draft    │ │
│                                     │ │ [00:15] JA:   │ │
│                                     │ │ 売上高は...   │ │
│                                     │ │ EN: Revenue   │ │
│                                     │ │ was...        │ │
│                                     │ │              ▼│ │
│                                     │ ├──────────────┤ │
│                                     │ │[Stop & Polish]│ │
│                                     │ │[Stop Only    ]│ │
│                                     │ └──────────────┘ │
└─────────────────────────────────────┴──────────────────┘
```

## New Layout: After Recording (AI Processing)
```
┌─────────────────────────────────────┬──────────────────┐
│ Rich Text Editor (2/3)              │ Meeting Intel    │
│                                     │                  │
│ [Polished transcript appears here   │ ▸ Raw Live Draft │
│  with clickable timestamps,         │   (collapsed)    │
│  speakers, bold key points]         │                  │
│                                     │ ▾ AI Summary     │
│ [AI Analysis sections also appear   │   Key topics...  │
│  here in the main editor]           │   Speakers...    │
│                                     │                  │
│                                     │ ▸ Bull/Bear      │
│                                     │   Debates        │
│                                     │                  │
│                                     │ ▸ Extract Facts  │
│                                     │                  │
│                                     │ ▸ Compare vs     │
│                                     │   Previous       │
│                                     │                  │
│                                     │ ▸ Key Numbers    │
│                                     │                  │
│                                     │ ▸ Action Items   │
│                                     │                  │
└─────────────────────────────────────┴──────────────────┘
```

## Right Sidebar Sections (After Recording)

### 1. Raw Live Draft (collapsible)
- Shows the SenseVoice draft lines with translations
- Collapsed by default after polished version is ready
- User can expand to compare raw vs polished

### 2. AI Summary (expandable)
- Auto-generated when "Stop & AI Polish" is clicked
- Key topics, speakers, duration, language detected
- Shows as a card with expand/collapse

### 3. Pre-set AI Analysis Jobs (expandable cards)
Each is a collapsible card that runs on-demand when expanded:

- **Compare vs Previous Meetings**
  - Compares this meeting's themes with previous meetings of same company/sector
  - Shows what's new, what changed, what was repeated

- **Bull/Bear Debate Points**
  - Extracts bullish and bearish arguments for each company mentioned
  - Two-column format: Bull | Bear

- **Extract Factual Updates**
  - Company-specific factual data points (revenue, guidance, launches)
  - Structured per company

- **Key Numbers & Context**
  - All numbers mentioned with their context
  - Format: "3.8兆円 — hand liquidity maintained"

- **Catalysts (Past & Upcoming)**
  - Timeline of events mentioned: past events + future catalysts
  - Per company

- **Company Pecking Order**
  - Analyst's ranking/preference of companies discussed
  - With rationale

- **Action Items**
  - Any follow-up items, deadlines, or tasks mentioned

### 4. Main Editor (Left Side)
When polished transcript is ready, it appears in the main editor with:
- Side-by-side table (JA/EN or ZH/EN)
- Clickable timestamps
- Bold key data points
- Key topics list at top

AI analysis results also appear in the main editor below the transcript,
as expandable sections.

## Implementation Plan

### Phase 1: Move Recording to Sidebar
1. Remove RecordingPopup floating component
2. Add recording state to the right panel (replace search panel during recording)
3. Show recording controls + live transcript in the right panel
4. Keep same WebSocket logic, just change the UI container

### Phase 2: Post-Recording Intelligence Panel
1. After recording stops, right panel switches to "Meeting Intelligence" mode
2. Show collapsible raw draft
3. Show AI summary when ready
4. Show pre-set AI job cards (initially collapsed, run on click)

### Phase 3: AI Analysis Jobs
1. Each job is a Gemini API call on the transcript text
2. Jobs run independently and can be triggered individually
3. Results cached — don't re-run if already completed
4. Results also inserted into the main editor

## AI Job Prompts (to be built)
- Summary: "Summarize this meeting in 5-7 bullet points..."
- Bull/Bear: "Extract bullish and bearish arguments for each company..."
- Facts: "Extract all factual updates per company..."
- Numbers: "List every number mentioned with its context..."
- Catalysts: "Extract all past events and upcoming catalysts..."
- Pecking Order: "What is the analyst's ranking of companies and why..."
- Compare: "Given this meeting and [previous meeting], what changed..."
