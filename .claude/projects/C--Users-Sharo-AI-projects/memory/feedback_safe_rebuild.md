---
name: Safe rebuild protocol
description: NEVER run a full-universe rebuild without testing first. Always test the fix on the single affected ticker, verify, then test 3 more tickers, verify, THEN run full universe. Ask user approval before each step.
type: feedback
---

When fixing a data pipeline issue (topline, calculator, heatmap, any parquet-producing code):

1. **Identify** the corner case on ONE ticker
2. **Think** of a solution — write it down, explain the reasoning
3. **Ask user approval** before coding
4. **Apply the fix** and rebuild ONLY that ONE ticker
5. **Verify** the result — show the output, confirm it's correct
6. **Test 3 more tickers** (pick diverse ones: e.g. NVDA, DELL, AMZN) — rebuild + verify each
7. **Show results to user** — get approval
8. **Only then** run the full universe rebuild

**Why:** The user experienced multiple regressions where a fix for LITE broke NVDA, a fix for AMZN broke CDNS, and a cumulative guard broke the entire universe. Every "quick fix" that skipped testing caused more damage than the original bug.

**How to apply:** Before ANY `CalculatedLayerBuilder().build()` or `ToplineBuilder().build()` without a ticker list, STOP and verify you've done steps 1-7 first.
