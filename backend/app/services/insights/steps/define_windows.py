"""
Phase 3 — Time Window Definition.

Validates and normalises the time window list resolved in Phase 0.
Returns the final ordered list of horizons that will be used for data
retrieval in Phase 4.

Replacing or skipping this step: remove the call in runner.py.
No other file is affected.
"""

from typing import List
from backend.app.models.domain.insight_models import TimeHorizon


# Canonical display order: short → mid → long.
_HORIZON_ORDER = [TimeHorizon.SHORT_1Y, TimeHorizon.MID_5Y, TimeHorizon.LONG_10Y]
_HORIZON_YEARS = {
    TimeHorizon.SHORT_1Y:  1,
    TimeHorizon.MID_5Y:    5,
    TimeHorizon.LONG_10Y: 10,
}


def define_windows(raw_windows: List[str]) -> List[str]:
    """
    Phase 3: Deduplicate, validate, and sort the time-window list.

    Input:  raw list of strings from Phase 0 (e.g. ["1Y", "10Y", "5Y"])
    Output: sorted, deduplicated list of valid TimeHorizon values
            (e.g. ["1Y", "5Y", "10Y"]).
    Falls back to all three horizons if the input is empty or all invalid.
    """
    valid_values = {h.value for h in TimeHorizon}
    seen: set[str] = set()
    validated: List[TimeHorizon] = []

    for w in raw_windows:
        if w in valid_values and w not in seen:
            seen.add(w)
            validated.append(TimeHorizon(w))

    if not validated:
        validated = list(_HORIZON_ORDER)

    # Sort in canonical short → long order.
    validated.sort(key=lambda h: _HORIZON_YEARS[h])

    return [h.value for h in validated]
