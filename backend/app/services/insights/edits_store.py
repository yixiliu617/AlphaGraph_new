"""
edits_store.py -- persistent user edits for margin insights.

Two files on disk:

    margin_insights_edits.jsonl
        Append-only log of every edit event. One JSON line per event.
        This is the source of truth -- nothing else is destructive.

    margin_insights_overrides.json
        Materialized "current state" overlay derived from the log.
        Read on every MarginInsightsService.get() and merged on top of the
        LLM baseline. Rebuilt from the jsonl whenever the log changes.

Why two files instead of one
----------------------------
The jsonl is the audit trail (and undo source). The overrides json is the
hot path -- merging from a flat dict is much faster than replaying the log
on every request. The overrides json is fully derived from the jsonl, so
if it goes corrupt you can rebuild it with `rebuild_overrides()`.

Event shape
-----------
Each line in the jsonl is:

    {
      "ts":          "2026-04-13T18:22:01.123Z",
      "ticker":      "NVDA",
      "period_end":  "2026-01-25",
      "margin_type": "gross",
      "section":     "peak" | "trough" | "current_pos" | "current_neg" | "current_summary",
      "action":      "edit" | "add" | "delete" | "undo",
      "factor_key":  "AI data-center mix shift",   # original label = stable id
      "payload":     { ...new field values... },
      "prev":        { ...old field values... }     # for undo
    }

`factor_key` identifies WHICH factor an edit applies to. For "add" events
the key equals the new factor's label. For section == "current_summary"
the key is the empty string (only one summary per margin_type).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

log = logging.getLogger(__name__)

_THIS_FILE  = Path(__file__).resolve()
_REPO_ROOT  = _THIS_FILE.parents[4]
_DATA_DIR   = _REPO_ROOT / "backend" / "data" / "insights"
_LOG_FILE       = _DATA_DIR / "margin_insights_edits.jsonl"
_OVERRIDES_FILE = _DATA_DIR / "margin_insights_overrides.json"

Section = Literal["peak", "trough", "current_pos", "current_neg", "current_summary"]
Action  = Literal["edit", "add", "delete", "undo"]


# ---------------------------------------------------------------------------
# Event dataclass (in-memory shape)
# ---------------------------------------------------------------------------

@dataclass
class EditEvent:
    ticker:      str
    period_end:  str
    margin_type: str        # "gross" | "operating" | "net"
    section:     Section
    action:      Action
    factor_key:  str
    payload:     dict[str, Any] = field(default_factory=dict)
    prev:        dict[str, Any] = field(default_factory=dict)
    ts:          str = ""

    def to_jsonl(self) -> str:
        d = {
            "ts":          self.ts or _now_iso(),
            "ticker":      self.ticker.upper(),
            "period_end":  self.period_end,
            "margin_type": self.margin_type,
            "section":     self.section,
            "action":      self.action,
            "factor_key":  self.factor_key,
            "payload":     self.payload,
            "prev":        self.prev,
        }
        return json.dumps(d, ensure_ascii=False)

    @classmethod
    def from_jsonl(cls, raw: str) -> "EditEvent":
        d = json.loads(raw)
        return cls(
            ticker     =d["ticker"],
            period_end =d["period_end"],
            margin_type=d["margin_type"],
            section    =d["section"],
            action     =d["action"],
            factor_key =d["factor_key"],
            payload    =d.get("payload", {}),
            prev       =d.get("prev", {}),
            ts         =d.get("ts", ""),
        )


# ---------------------------------------------------------------------------
# EditsStore
# ---------------------------------------------------------------------------

class EditsStore:

    def __init__(self) -> None:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._overrides: dict[str, Any] = self._load_overrides()

    # -- read API --------------------------------------------------------

    def get_overrides(self, ticker: str, period_end: str) -> dict[str, Any]:
        """
        Returns the override block for a (ticker, period_end). Shape:

            {
              "gross": {
                "peak": {
                  "<factor_key>": {label, direction, evidence, source_ref,
                                   user_edited, deleted}
                },
                "trough":   {...},
                "current_pos": {...},
                "current_neg": {...},
                "current_summary": {"text": "..."}
              },
              "operating": {...},
              "net":       {...}
            }
        """
        return self._overrides.get(ticker.upper(), {}).get(period_end, {})

    def recent_edits_for_prompt(self, ticker: str, limit: int = 12) -> list[EditEvent]:
        """
        Return the last `limit` edit events for this ticker (newest first),
        filtered to non-undo events that still represent the current state.
        Used by the service to inject style guidance into the prompt.
        """
        events: list[EditEvent] = []
        if not _LOG_FILE.exists():
            return events
        try:
            for line in _LOG_FILE.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = EditEvent.from_jsonl(line)
                except Exception:
                    continue
                if ev.ticker.upper() != ticker.upper():
                    continue
                if ev.action == "undo":
                    continue
                events.append(ev)
        except Exception as exc:
            log.warning("Could not read edits log: %s", exc)
            return []
        return list(reversed(events))[:limit]

    # -- write API -------------------------------------------------------

    def append(self, event: EditEvent) -> None:
        """Append one event to the jsonl log AND update the in-memory overlay."""
        event.ts = event.ts or _now_iso()
        try:
            with _LOG_FILE.open("a", encoding="utf-8") as fh:
                fh.write(event.to_jsonl() + "\n")
        except Exception as exc:
            log.error("Failed to append edit event: %s", exc)
            return
        # Replay this single event onto the in-memory overlay
        self._apply_event_to_overlay(event)
        self._save_overrides()

    def undo_last(self, ticker: str, period_end: str) -> EditEvent | None:
        """
        Append an "undo" event for the most recent non-undo event matching
        (ticker, period_end). Rebuild the overlay so the previous state is
        reflected. Returns the event that was undone, or None if nothing to
        undo.
        """
        if not _LOG_FILE.exists():
            return None
        events = self._all_events()
        target: EditEvent | None = None
        for ev in reversed(events):
            if (
                ev.ticker.upper() == ticker.upper()
                and ev.period_end  == period_end
                and ev.action != "undo"
                and not _is_already_undone(ev, events)
            ):
                target = ev
                break
        if target is None:
            return None

        undo_event = EditEvent(
            ticker=target.ticker,
            period_end=target.period_end,
            margin_type=target.margin_type,
            section=target.section,
            action="undo",
            factor_key=target.factor_key,
            payload={"undoes_ts": target.ts},
            prev=target.payload,
        )
        try:
            with _LOG_FILE.open("a", encoding="utf-8") as fh:
                fh.write(undo_event.to_jsonl() + "\n")
        except Exception as exc:
            log.error("Failed to append undo event: %s", exc)
            return None

        # Cheapest correctness path: rebuild overlay from scratch
        self.rebuild_overrides()
        return target

    def rebuild_overrides(self) -> None:
        """Replay the entire jsonl log to rebuild margin_insights_overrides.json."""
        self._overrides = {}
        for ev in self._all_events():
            self._apply_event_to_overlay(ev)
        self._save_overrides()

    # -- internal: replay logic ------------------------------------------

    def _apply_event_to_overlay(self, ev: EditEvent) -> None:
        """
        Mutate self._overrides to reflect a single event. This is the only
        place the overlay shape is defined.
        """
        ticker = ev.ticker.upper()
        bucket = self._overrides.setdefault(ticker, {}).setdefault(ev.period_end, {})
        margin = bucket.setdefault(ev.margin_type, {})

        if ev.section == "current_summary":
            if ev.action == "edit":
                margin["current_summary"] = {
                    "text": ev.payload.get("summary", ""),
                    "user_edited": True,
                }
            elif ev.action == "undo":
                margin.pop("current_summary", None)
            return

        section = margin.setdefault(ev.section, {})

        if ev.action in ("edit", "add"):
            entry = dict(ev.payload)
            entry["user_edited"] = True
            entry["deleted"] = False
            if ev.action == "add" and "source_ref" not in entry:
                entry["source_ref"] = -2  # marker for user-added
            section[ev.factor_key] = entry

        elif ev.action == "delete":
            entry = section.get(ev.factor_key, {}).copy()
            entry["deleted"] = True
            entry["user_edited"] = True
            section[ev.factor_key] = entry

        elif ev.action == "undo":
            # Removing the factor_key from the overlay reverts to LLM baseline.
            section.pop(ev.factor_key, None)
            if not section:
                margin.pop(ev.section, None)
            if not margin:
                bucket.pop(ev.margin_type, None)

    # -- internal: persistence -------------------------------------------

    def _all_events(self) -> list[EditEvent]:
        if not _LOG_FILE.exists():
            return []
        out: list[EditEvent] = []
        for line in _LOG_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(EditEvent.from_jsonl(line))
            except Exception:
                continue
        return out

    def _load_overrides(self) -> dict[str, Any]:
        if not _OVERRIDES_FILE.exists():
            return {}
        try:
            return json.loads(_OVERRIDES_FILE.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("Overrides file unreadable: %s -- starting empty", exc)
            return {}

    def _save_overrides(self) -> None:
        try:
            _OVERRIDES_FILE.write_text(
                json.dumps(self._overrides, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except Exception as exc:
            log.error("Failed to write overrides file: %s", exc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _is_already_undone(target: "EditEvent", all_events: list["EditEvent"]) -> bool:
    """
    Look forward from `target` in the chronological log. If a later "undo"
    event references this exact ts, it's already been undone -- skip it.
    """
    for ev in all_events:
        if ev.action != "undo":
            continue
        if ev.payload.get("undoes_ts") == target.ts:
            return True
    return False
