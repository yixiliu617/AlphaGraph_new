---
name: guidance-tab-pattern
description: Project-wide structure for any company's "Guidance" sub-tab in the AlphaGraph dashboard. Mandates a forward-guidance card at the top showing the latest report's view of the next period(s) for every guided metric, followed by per-metric historical guidance-vs-actual tables. Use whenever building or modifying a guidance tab for any covered company (TSMC, UMC, MediaTek, ASE, ASML, future additions). Pairs with `time-axis-sort-convention` (rows newest-first) and `readable-data-table` (table aesthetics).
version: 1.0
last_validated_at: 2026-04-27
conditions: []
prerequisites: [time-axis-sort-convention, readable-data-table]
tags: [convention, ui, guidance, financial-data]
---

# Guidance Tab Pattern

## The rule (non-negotiable for every guidance tab)

A company's **Guidance** sub-tab MUST have these two sections in this order:

1. **Forward Guidance card** at the top — a single colored card showing the latest report's view of every guided metric for the next period(s). One badge per metric, with: pretty label, target period, numeric value or range, verbal text quote.
2. **Historical guidance vs actual tables** below — one table per guided metric, sorted reverse-chronologically (`for_period` desc). Outcome column shows BEAT / IN RANGE / MISS (or company-specific equivalents like ABOVE / BELOW / NEAR POINT for point-target metrics).

The forward card answers "what does management expect *now*?". The historical tables answer "how good has management been at predicting?". Both questions matter; the forward card matters most for someone landing on the tab cold, which is why it leads.

## Why

A PM landing on UMC's Guidance tab is asking *"what does UMC expect for next quarter?"* far more often than *"how often has UMC missed?"*. The forward-guidance card answers the first question without scrolling. Historical analysis is one screen down. Without the forward card, the user has to scan the top row of every historical table to find the latest forecast — slow and error-prone.

TSMC has had this layout since the original `TSMCPanel.tsx` build (`Forward guidance for {next_period}` card in indigo). That pattern is the canonical template. Every other company's guidance tab matches it.

## Reference implementations

| Company | Panel file | Forward-card source | Latest period (today) |
|---|---|---|---|
| TSMC (2330.TW) | `frontend/src/app/(dashboard)/data-explorer/TSMCPanel.tsx` → `GuidanceTab` | `/api/v1/tsmc/guidance/forward` (dedicated endpoint returning latest issuing report's forward guidance) | 1Q26 / 2Q26 (TSMC issues numeric ranges) |
| UMC (2303.TW) | `frontend/src/app/(dashboard)/data-explorer/UMCPanel.tsx` → `GuidanceTab` | derived client-side from `/api/v1/umc/guidance` rows where `issued_in_period == max(issued_in_period)` | 1Q26 (quarterly metrics) + FY26 (annual CAPEX) — UMC issues qualitative + implied ranges |
| MediaTek (2454.TW) | not built yet | — | MediaTek doesn't issue formal guidance in the same structured way; if added, follow this pattern |
| Future companies | — | — | — |

## How to apply (for a NEW company panel)

When you add a new company's Guidance tab:

1. **Source the data**. The `/api/v1/{ticker}/guidance` endpoint should return rows of shape:
   ```json
   {
     "issued_in_period": "4Q25",       // when management issued this guidance
     "for_period": "1Q26",             // what period the guidance covers (Q or FY)
     "metric": "guidance_gross_margin",
     "verbal": "high-20% range",       // the verbatim text from the filing
     "guide_low": 26, "guide_mid": 27.5, "guide_high": 29,
     "guide_point": null,              // single-number target (e.g. annual capex)
     "actual": null,                   // null for future periods, populated when realized
     "outcome": null,                  // null until comparable, then BEAT/IN/MISS labels
     "vs_mid_pct": null, "vs_mid_pp": null,
     "unit": "pct"
   }
   ```
   The endpoint MUST sort rows newest-first (per `time-axis-sort-convention`). The first row in the response is from the most recent issuing report.

2. **Compute `latestIssued`** in the React component:
   ```tsx
   const latestIssued = rows[0]?.issued_in_period;
   const forwardRows = latestIssued
     ? rows.filter((r) => r.issued_in_period === latestIssued)
     : [];
   const forwardByMetric = new Map<string, GuidanceRow>();
   forwardRows.forEach((r) => {
     if (!forwardByMetric.has(r.metric)) forwardByMetric.set(r.metric, r);
   });
   ```
   The first row of the API response is the most recent forward guidance because the API is sorted newest-first. All rows sharing that `issued_in_period` populate the card; one row per metric (de-duped).

3. **Render the card** as a colored panel (indigo for the project default; pick a company-tinted color only if there's a strong design reason). One small card per metric in a 4-5 column responsive grid:
   ```tsx
   {forwardByMetric.size > 0 && (
     <div className="bg-indigo-50 border border-indigo-200 rounded-lg p-4">
       <h3 className="text-sm font-bold text-indigo-900 mb-3">
         Forward guidance{" "}
         <span className="text-[11px] font-normal text-indigo-700">
           issued in {latestIssued} report
         </span>
       </h3>
       <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3 text-xs">
         {FORWARD_CARD_ORDER.map(([metric, label]) => {
           const r = forwardByMetric.get(metric);
           if (!r) return null;
           return (
             <div key={metric} className="bg-white/50 border border-indigo-200/60 rounded p-2">
               <div className="flex items-baseline justify-between">
                 <div className="text-[10px] uppercase tracking-wide text-indigo-700 font-semibold">{label}</div>
                 <div className="text-[10px] text-indigo-500 font-mono">{r.for_period}</div>
               </div>
               <div className="text-base font-bold text-indigo-900 mt-1">
                 {fmtForwardValue(r)}
               </div>
               {r.verbal && (
                 <div className="text-[10px] text-indigo-700 mt-0.5 italic line-clamp-2" title={r.verbal}>
                   &ldquo;{r.verbal}&rdquo;
                 </div>
               )}
             </div>
           );
         })}
       </div>
     </div>
   )}
   ```

4. **Render the historical tables** below the card. One per metric, with columns: For Period · Issued In · Verbal · Low/Mid/High (or Point) · Actual · Outcome · vs Mid/Point. Use the `outcomeClass()` and `fmtVal()` helpers from the existing UMC/TSMC implementations.

5. **Each row in each historical table is sorted reverse-chronologically by `for_period`** (per `time-axis-sort-convention`). The most recent quarter's actual-vs-guidance is at the TOP. FY items interleave with quarterly items by year boundary.

## What gets shown in the forward card per metric

The card distinguishes three guidance shapes — pick the right format based on what the API returns:

| Shape | Backend has | Card value | Example |
|---|---|---|---|
| Numeric range | `guide_low`, `guide_high` (with optional `guide_mid`) | `"26–29%"` | TSMC gross margin "57.5–59.5%" → `"57.5–59.5%"` |
| Point estimate | `guide_point`, no low/high | `"$1.5B"` (or `"30.50"` for FX) | UMC annual CAPEX "US$1.5 billion" → `"$1.5B"` |
| Verbal only | only `verbal`, no numerics | `"—"` (em-dash) | UMC ASP "Will remain firm" → `"—"` with the quote shown below |

The verbal text is always shown as a quoted italic line below the numeric (or instead of it for verbal-only metrics). Truncate to 2 lines with `line-clamp-2` + a `title` tooltip for the full text on hover.

## What if a company doesn't issue formal guidance?

Some companies (most fabless designers, e.g. MediaTek) don't issue formal forward guidance — they only narrate qualitative direction in the call. For these:

- Hide the forward card entirely (`forwardByMetric.size === 0` → render nothing)
- The Guidance tab itself may be hidden from the panel's sub-tab list
- Document in the company's `project_taiwan_ir_extraction_{ticker}.md` memory note that guidance is not published

Do NOT fabricate guidance ranges from prose ("management is optimistic about Q2"). The forward card is reserved for formal disclosed guidance.

## Where this fits with other rules

- **`time-axis-sort-convention`** — historical tables in the guidance tab follow the same newest-first row order. Forward card always shows the LATEST issuing period, naturally consistent.
- **`readable-data-table`** — table aesthetics (alignment, color, sticky columns) for the historical sections.
- **`tsmc-quarterly-reports`** — TSMC-specific guidance extraction (numeric ranges from the 業績展望 page).

## Verification checklist

Before merging a new company's Guidance tab:

1. Open the tab cold. Is the forward card the first thing visible? If no — fix layout order.
2. Does the card show the LATEST issuing report's view (e.g. 4Q25 report → 1Q26 + FY26 guidance)? If you see an older period — the API isn't sorting newest-first.
3. Each metric card has: label, target period chip, numeric value or em-dash, verbal quote (when present)? Missing pieces — extend the renderer.
4. Historical tables below the card render reverse-chronologically? Top row = most recent realized period.
5. When a company issues NO forward guidance for a given metric, is the card slot omitted (not "—" empty)? Hard rule: if no row exists, no card.
6. Outcome badges colored consistently (emerald=BEAT/ABOVE, rose=MISS/BELOW, slate=in-range/near-point, slate-dim=verbal-only)?

## Anti-patterns (don't do these)

- **Don't compute the forward guidance from the future** — never use `actual is null` as the filter. Use `issued_in_period == max(issued_in_period)`. A company that hasn't reported next quarter yet has `actual is null` for many older guidances if the universe shifted; that's not the same thing as "latest issued".
- **Don't auto-populate the card from prose interpretation** — only use what the structured guidance silver layer extracted. Verbal-only metrics get verbal-only cards.
- **Don't merge the forward card into the historical tables as a "highlighted top row"** — they answer different questions and need different visual emphasis. The card is large + indigo + grid-shaped; the tables are dense + grayscale + tabular.
- **Don't show the forward card if the latest issuing report has no forward guidance entries at all** (e.g. a flash 8-K with no outlook section). `forwardByMetric.size === 0` → skip the card entirely.
