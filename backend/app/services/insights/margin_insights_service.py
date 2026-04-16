"""
margin_insights_service.py -- orchestrator for qualitative margin insights.

Pipeline per request:

    DataAgent.fetch(margins)            -> time series + latest period_end
    cache.get(ticker, period_end)       -> return if hit (unless refresh=True)
    compute peak/trough stats           -> deterministic, matches frontend
    source_fetcher.fetch_margin_sources -> 8-K + MD&A + news excerpts
    build_prompt                        -> analyst-persona system + user msg
    llm.generate_structured_output      -> MarginInsights dict
    cache.set(insights)                 -> write-through
    return insights

Forward path: when real press-release / transcript / news extractors
land in Pinecone, replace `source_fetcher.fetch_margin_sources` with a
Pinecone query. Nothing else in this module changes.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from backend.app.interfaces.llm_provider import LLMProvider
from backend.app.services.data_agent.data_agent import DataAgent, DataSpec

from .edits_store import EditEvent, EditsStore
from .margin_cache import MarginInsightsCache
from .margin_schemas import MarginInsights, SourceRef
from .source_fetcher import SourceExcerpt, SourceTarget, fetch_margin_sources

log = logging.getLogger(__name__)

_MARGIN_METRICS = ["gross_margin_pct", "operating_margin_pct", "net_margin_pct"]
_LOOKBACK_YEARS = 6.0

_SYSTEM_PROMPT = """You are a senior sell-side equity analyst covering this company.

Your job: explain WHY the company's margins peaked and troughed over the
observable history, and assess how the drivers behind each peak/trough
look TODAY.

Source hierarchy (use in this order):
  PRIMARY   -- the provided 8-K earnings releases and 10-Q/10-K MD&A excerpts.
               Prefer these for every factor whenever the relevant period is
               covered. Cite via source_ref.
  SECONDARY -- your own well-known background knowledge about this company,
               its industry, and major events around the peak/trough dates.
               Use this ONLY to fill gaps when no provided source covers the
               relevant period. Set source_ref = -1 to flag a factor that
               relies on background knowledge rather than a cited document.

Rules:
  1. NEVER leave a peak.factors or trough.factors list empty. If the provided
     sources don't cover that period, fall back to background knowledge and
     mark those factors with source_ref = -1. Aim for 3-5 factors per peak
     and per trough.
  2. When citing a provided source, Factor.evidence must be a short quote or
     faithful paraphrase from that source's text.
  3. When using background knowledge (source_ref = -1), Factor.evidence
     should still be a specific, verifiable claim (e.g. "Crypto-mining demand
     collapse and channel inventory correction in mid-2022 hit gaming GPU
     ASPs and gross margin"), not a vague hedge.
  4. Factors must be specific (e.g. "AI data-center revenue mix shift",
     "inventory write-down for H100 transition"), not generic ("strong
     demand", "market conditions").
  5. Do NOT forecast or speculate about forward numbers. Stay descriptive.
  6. For current_situation: prefer the latest MD&A and most recent 8-K. If
     those don't cover a historical factor, you may use background knowledge
     to assess the current state, again with source_ref = -1 in the
     evidence. Set current_state to "unclear" only when even background
     knowledge is genuinely insufficient.
"""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class MarginInsightsService:

    def __init__(
        self,
        llm: LLMProvider,
        data_agent: DataAgent | None = None,
        cache: MarginInsightsCache | None = None,
        edits: EditsStore | None = None,
    ) -> None:
        self._llm = llm
        self._data_agent = data_agent or DataAgent()
        self._cache = cache or MarginInsightsCache()
        self._edits = edits or EditsStore()

    # -- public API -----------------------------------------------------

    def get(self, ticker: str, refresh: bool = False) -> MarginInsights:
        ticker = ticker.upper().strip()

        # 1. Fetch margin time series (also tells us the latest period_end)
        series_rows = self._fetch_series(ticker)
        if not series_rows:
            return self._empty_insights(ticker, reason="No margin data available for this ticker.")

        latest_period_end = str(series_rows[-1].get("end_date", ""))[:10]

        # 2. Cache lookup -- baseline is the LLM-generated MarginInsights,
        # user edits are layered on top after retrieval.
        if not refresh:
            hit = self._cache.get(ticker, latest_period_end)
            if hit is not None:
                log.info("Margin insights cache HIT %s %s", ticker, latest_period_end)
                return self._apply_overrides(hit)

        log.info("Margin insights cache MISS %s %s -- generating", ticker, latest_period_end)

        # 3. Deterministic stats (peak/trough per margin type)
        stats = _compute_margin_stats(series_rows)

        # 4. Qualitative sources -- targeted by peak/trough/current anchors
        targets = _build_source_targets(stats, series_rows)
        excerpts = fetch_margin_sources(ticker, targets)

        # 5. Build prompt with recent user-edits injected as style guidance
        recent_edits = self._edits.recent_edits_for_prompt(ticker, limit=12)
        prompt = _build_user_prompt(
            ticker=ticker,
            series=series_rows,
            stats=stats,
            excerpts=excerpts,
            recent_edits=recent_edits,
        )
        schema = MarginInsights.model_json_schema()

        try:
            raw = self._llm.generate_structured_output(
                prompt=prompt,
                output_schema=schema,
            )
        except Exception as exc:
            log.error("LLM call failed for %s: %s", ticker, exc)
            return self._empty_insights(
                ticker,
                reason=f"LLM synthesis failed: {exc}",
                period_end=latest_period_end,
            )

        # 6. Ensure generation metadata is set correctly (LLM may omit)
        raw["ticker"] = ticker
        raw["period_end"] = latest_period_end
        raw["generated_at"] = datetime.now(timezone.utc).isoformat()
        # Merge sources: the LLM should echo sources back, but our list is authoritative
        raw["sources"] = [
            SourceRef(
                index=i,
                title=e.title,
                doc_type=e.doc_type,
                date=e.date,
                url=e.url,
            ).model_dump()
            for i, e in enumerate(excerpts)
        ]

        try:
            insights = MarginInsights.model_validate(raw)
        except Exception as exc:
            log.error("LLM output failed validation for %s: %s", ticker, exc)
            return self._empty_insights(
                ticker,
                reason=f"Synthesis returned invalid shape: {exc}",
                period_end=latest_period_end,
            )

        # 7. Write-through cache (LLM baseline only; overrides apply on read)
        self._cache.set(insights)
        return self._apply_overrides(insights)

    # -- edit API -------------------------------------------------------

    def apply_edit(self, ticker: str, event_data: dict[str, Any]) -> MarginInsights:
        """
        Persist a user edit and return the merged insights. event_data fields
        match the EditEvent shape from the API request.
        """
        ticker = ticker.upper().strip()

        # We need period_end to scope the edit; derive it from the current
        # latest-quarter row. The endpoint can also pass it explicitly.
        period_end = event_data.get("period_end")
        if not period_end:
            series_rows = self._fetch_series(ticker)
            if not series_rows:
                raise ValueError(f"No data for {ticker}")
            period_end = str(series_rows[-1].get("end_date", ""))[:10]

        action = event_data.get("action", "edit")

        if action == "undo":
            self._edits.undo_last(ticker, period_end)
        else:
            event = EditEvent(
                ticker     =ticker,
                period_end =period_end,
                margin_type=event_data["margin_type"],
                section    =event_data["section"],
                action     =action,
                factor_key =event_data.get("factor_key", ""),
                payload    =event_data.get("payload", {}),
                prev       =event_data.get("prev", {}),
            )
            self._edits.append(event)

        return self.get(ticker, refresh=False)

    # -- override merging -----------------------------------------------

    def _apply_overrides(self, insights: MarginInsights) -> MarginInsights:
        """
        Layer user-edit overrides on top of an LLM baseline. Returns a NEW
        MarginInsights -- the cached baseline is left untouched so future
        regenerations work from the original LLM output.
        """
        overrides = self._edits.get_overrides(insights.ticker, insights.period_end)
        if not overrides:
            return insights

        merged = insights.model_dump()
        for narrative in merged.get("margins", []):
            mtype = narrative.get("margin_type")
            mover = overrides.get(mtype)
            if not mover:
                continue
            _merge_factor_section(narrative.get("peak", {}),   mover.get("peak", {}))
            _merge_factor_section(narrative.get("trough", {}), mover.get("trough", {}))
            _merge_current(narrative.get("current_situation", {}), mover)

        try:
            return MarginInsights.model_validate(merged)
        except Exception as exc:
            log.warning("Override merge produced invalid shape for %s: %s", insights.ticker, exc)
            return insights

    # -- helpers --------------------------------------------------------

    def _fetch_series(self, ticker: str) -> list[dict[str, Any]]:
        spec = DataSpec(
            tickers=[ticker],
            metrics=_MARGIN_METRICS,
            period="quarterly",
            lookback_years=_LOOKBACK_YEARS,
        )
        result = self._data_agent.fetch(spec)
        return [r for r in result.rows if r.get("ticker") == ticker]

    def _empty_insights(
        self,
        ticker: str,
        reason: str,
        period_end: str | None = None,
    ) -> MarginInsights:
        return MarginInsights(
            ticker=ticker,
            period_end=period_end or "",
            generated_at=datetime.now(timezone.utc).isoformat(),
            margins=[],
            sources=[],
            disclaimer=f"No narrative available. {reason}",
        )


# ---------------------------------------------------------------------------
# Deterministic peak / trough computation
# Mirrors the frontend computeMarginStats() in DataExplorerView.tsx so the
# prompt and the UI always agree on which period is "peak" and "trough".
# ---------------------------------------------------------------------------

def _compute_margin_stats(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    specs = [
        ("gross",     "gross_margin_pct"),
        ("operating", "operating_margin_pct"),
        ("net",       "net_margin_pct"),
    ]
    out: dict[str, dict[str, Any]] = {}
    for margin_type, key in specs:
        min_v: float | None = None
        max_v: float | None = None
        trough_period: str | None = None
        peak_period:   str | None = None
        trough_end: str | None = None
        peak_end:   str | None = None
        current: float | None = None
        current_period: str | None = None
        current_end:    str | None = None

        for r in rows:
            v = r.get(key)
            if not isinstance(v, (int, float)):
                continue
            end_date = str(r.get("end_date", ""))[:10]
            period   = r.get("period_label") or end_date
            if min_v is None or v < min_v:
                min_v = v
                trough_period = period
                trough_end    = end_date
            if max_v is None or v > max_v:
                max_v = v
                peak_period = period
                peak_end    = end_date

        for r in reversed(rows):
            v = r.get(key)
            if isinstance(v, (int, float)):
                current = v
                current_end = str(r.get("end_date", ""))[:10]
                current_period = r.get("period_label") or current_end
                break

        out[margin_type] = {
            "key": key,
            "min": min_v,
            "max": max_v,
            "peak_period":    peak_period,
            "peak_end":       peak_end,
            "trough_period":  trough_period,
            "trough_end":     trough_end,
            "current":        current,
            "current_period": current_period,
            "current_end":    current_end,
        }
    return out


def _build_source_targets(
    stats: dict[str, dict[str, Any]],
    series_rows: list[dict[str, Any]],
) -> list[SourceTarget]:
    """
    Translate peak/trough/current dates from stats into SourceTargets. The
    fetcher dedupes by period_end, so overlapping anchors (e.g. NVDA net
    and operating margin both peaking in the same quarter) cost one filing,
    not two.
    """
    targets: list[SourceTarget] = []
    for margin_type, s in stats.items():
        if s.get("peak_end"):
            targets.append(SourceTarget(anchor=f"peak-{margin_type}", period_end=s["peak_end"]))
        if s.get("trough_end"):
            targets.append(SourceTarget(anchor=f"trough-{margin_type}", period_end=s["trough_end"]))

    # Current anchor: end_date of the latest row in the series
    if series_rows:
        latest_end = str(series_rows[-1].get("end_date", ""))[:10]
        if latest_end:
            targets.append(SourceTarget(anchor="current", period_end=latest_end))

    return targets


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def _build_user_prompt(
    ticker: str,
    series: list[dict[str, Any]],
    stats: dict[str, dict[str, Any]],
    excerpts: list[SourceExcerpt],
    recent_edits: list[EditEvent] | None = None,
) -> str:
    # Compact series -- only margins + period, not full financials
    compact_series = [
        {
            "period": r.get("period_label") or str(r.get("end_date", ""))[:10],
            "gross":  r.get("gross_margin_pct"),
            "op":     r.get("operating_margin_pct"),
            "net":    r.get("net_margin_pct"),
        }
        for r in series
    ]

    stats_block_lines = []
    for margin_type, s in stats.items():
        stats_block_lines.append(
            f"  {margin_type:<10s} peak={_fmt_pct(s['max'])} @ {s['peak_period']}"
            f"   trough={_fmt_pct(s['min'])} @ {s['trough_period']}"
            f"   current={_fmt_pct(s['current'])} @ {s['current_period']}"
        )
    stats_block = "\n".join(stats_block_lines)

    if excerpts:
        source_lines = []
        for i, e in enumerate(excerpts):
            source_lines.append(
                f"[{i}] {e.doc_type} {e.date} -- {e.title}\n{e.text}\n"
            )
        sources_block = "\n".join(source_lines)
    else:
        sources_block = "(no provided sources -- rely entirely on background knowledge with source_ref = -1 for every factor)"

    edits_block = _format_recent_edits(recent_edits or [])

    return f"""Ticker: {ticker}

Margin time series (quarterly, %):
{json.dumps(compact_series, indent=2)}

Peak / trough / current:
{stats_block}

Sources:
{sources_block}

{edits_block}Produce a MarginInsights object covering all three margin_types (gross,
operating, net). For each:
  - peak.period and peak.value_pct must match the stats block exactly.
  - trough.period and trough.value_pct must match the stats block exactly.
  - peak.factors: 3-5 factors explaining why margin was strongest at that
    period, each citing a source_ref.
  - trough.factors: 3-5 factors explaining why margin was weakest at that
    period, each citing a source_ref.
  - current_situation.positive_factors_status: for each historical peak
    factor, assess how it looks TODAY (strengthening / steady / weakening
    / unclear) with a one-sentence evidence line.
  - current_situation.negative_factors_status: same for trough factors.
  - current_situation.summary: 2-3 sentences tying it together.

If the provided sources don't cover a peak or trough period, fall back to
your background knowledge about this company and the industry context
around that date, and set source_ref = -1 on those factors. Always produce
3-5 factors per peak and per trough -- empty lists are not acceptable.
"""


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "  n/a"
    return f"{v:6.2f}%"


# ---------------------------------------------------------------------------
# Override merging helpers
# ---------------------------------------------------------------------------

def _merge_factor_section(
    section_dict: dict[str, Any],
    overlay: dict[str, dict[str, Any]],
) -> None:
    """
    Merge user overrides into a peak/trough section IN PLACE.

    For each factor in the LLM baseline, if a matching factor_key exists in
    the overlay, replace the factor's content. Soft-deleted factors are
    dropped from the output. User-added factors (not present in the
    baseline) are appended.
    """
    if not section_dict:
        return
    factors: list[dict[str, Any]] = section_dict.get("factors") or []
    out: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    for f in factors:
        key = (f.get("label") or "").strip()
        ov  = overlay.get(key)
        if ov is None:
            out.append(f)
            seen_keys.add(key)
            continue
        if ov.get("deleted"):
            seen_keys.add(key)
            continue  # soft-delete: drop from output
        merged = {**f, **ov}
        merged["user_edited"] = True
        merged.setdefault("deleted", False)
        out.append(merged)
        seen_keys.add(key)

    # Append user-added factors (overlay keys not in baseline)
    for key, ov in overlay.items():
        if key in seen_keys or ov.get("deleted"):
            continue
        added = dict(ov)
        added["user_edited"] = True
        added.setdefault("deleted", False)
        added.setdefault("source_ref", -2)
        added.setdefault("label", key)
        added.setdefault("direction", "positive")
        added.setdefault("evidence", "")
        out.append(added)

    section_dict["factors"] = out


def _merge_current(
    current_dict: dict[str, Any],
    margin_overlay: dict[str, Any],
) -> None:
    """Apply summary + factor-status overrides to current_situation in place."""
    if not current_dict:
        return
    summary_ov = margin_overlay.get("current_summary")
    if summary_ov and "text" in summary_ov:
        current_dict["summary"] = summary_ov["text"]
        current_dict["user_edited_summary"] = True

    # current_pos / current_neg overlays mirror peak/trough but the entries
    # are FactorStatus, not Factor. Schema is similar enough that the same
    # merge works on a "factors-equivalent" list. We attach to the existing
    # positive_factors_status / negative_factors_status lists.
    for section_key, list_key in (
        ("current_pos", "positive_factors_status"),
        ("current_neg", "negative_factors_status"),
    ):
        overlay = margin_overlay.get(section_key)
        if not overlay:
            continue
        items: list[dict[str, Any]] = current_dict.get(list_key) or []
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for it in items:
            key = (it.get("factor") or "").strip()
            ov  = overlay.get(key)
            if ov is None:
                out.append(it)
                seen.add(key)
                continue
            if ov.get("deleted"):
                seen.add(key)
                continue
            merged = {**it, **ov}
            out.append(merged)
            seen.add(key)
        for key, ov in overlay.items():
            if key in seen or ov.get("deleted"):
                continue
            added = dict(ov)
            added.setdefault("factor", key)
            added.setdefault("current_state", "unclear")
            added.setdefault("evidence", "")
            out.append(added)
        current_dict[list_key] = out


# ---------------------------------------------------------------------------
# Inject recent edits into the prompt as style guidance
# ---------------------------------------------------------------------------

def _format_recent_edits(events: list[EditEvent]) -> str:
    if not events:
        return ""

    lines: list[str] = [
        "USER CORRECTIONS (highest weight -- match this style and specificity):",
        "The user has previously rewritten or added the following items. Honor",
        "their phrasing, level of detail, and the type of factor they prefer.",
        "Do not regress to vaguer language than the user has shown.",
        "",
    ]
    for ev in events:
        action_label = {
            "edit":   "rewrote",
            "add":    "added",
            "delete": "removed",
        }.get(ev.action, ev.action)
        section_label = {
            "peak":            "peak driver",
            "trough":          "trough driver",
            "current_pos":     "current tailwind",
            "current_neg":     "current headwind",
            "current_summary": "current-situation summary",
        }.get(ev.section, ev.section)
        payload = ev.payload or {}
        if ev.section == "current_summary":
            text = payload.get("summary", "")
            lines.append(f"  - {action_label} {ev.margin_type} {section_label}: \"{text[:200]}\"")
        else:
            label = payload.get("label") or ev.factor_key
            evid  = payload.get("evidence", "")
            lines.append(
                f"  - {action_label} {ev.margin_type} {section_label} \"{label}\""
                + (f": \"{evid[:200]}\"" if evid else "")
            )
    lines.append("")
    return "\n".join(lines) + "\n"
