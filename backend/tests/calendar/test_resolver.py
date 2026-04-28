import pandas as pd

from backend.app.services.calendar.storage import _resolve_soft_fields


def test_resolver_first_non_null_in_run_order():
    """A wins over B+C; B wins over C; C wins only when A+B both null."""
    row = pd.Series({
        "webcast_url_a": "https://a.com",
        "webcast_url_b": "https://b.com",
        "webcast_url_c": "https://c.com",
        "dial_in_phone_a": None,
        "dial_in_phone_b": "555-1111",
        "dial_in_phone_c": "555-2222",
        "dial_in_pin_a": None,
        "dial_in_pin_b": None,
        "dial_in_pin_c": "9999",
    })
    resolved = _resolve_soft_fields(row)
    assert resolved["webcast_url"]   == "https://a.com"   # A wins
    assert resolved["dial_in_phone"] == "555-1111"        # B wins (A null)
    assert resolved["dial_in_pin"]   == "9999"            # C wins (A+B null)


def test_resolver_handles_all_null():
    row = pd.Series({"webcast_url_a": None, "webcast_url_b": None, "webcast_url_c": None})
    resolved = _resolve_soft_fields(row)
    assert resolved["webcast_url"] is None


def test_resolver_handles_nan():
    """pd.NaN should be treated as null."""
    import numpy as np
    row = pd.Series({
        "webcast_url_a": np.nan,
        "webcast_url_b": "https://b.com",
        "webcast_url_c": np.nan,
    })
    resolved = _resolve_soft_fields(row)
    assert resolved["webcast_url"] == "https://b.com"


def test_resolver_transcript_url_uses_only_b():
    row = pd.Series({"transcript_url_b": "https://seekingalpha.com/x"})
    assert _resolve_soft_fields(row)["transcript_url"] == "https://seekingalpha.com/x"


def test_resolver_transcript_url_null_when_b_empty():
    row = pd.Series({"transcript_url_b": None})
    assert _resolve_soft_fields(row)["transcript_url"] is None


def test_read_events_preserves_public_value_when_per_source_empty(tmp_path):
    """Regression guard: legacy rows with a public-column value but no
    _a/_b/_c entries must keep their public value after read."""
    import pandas as pd
    from backend.app.services.calendar.storage import read_events, ALL_COLS

    # Build a 2-row frame: row1 = legacy (public webcast_url set, _a/_b/_c null);
    # row2 = new-style (public null, _a set).
    rows = [
        {"ticker": "X", "market": "US", "fiscal_period": "FY2026-Q1",
         "webcast_url": "https://legacy.example.com/x"},
        {"ticker": "Y", "market": "US", "fiscal_period": "FY2026-Q1",
         "webcast_url_a": "https://from-a.example.com/y"},
    ]
    df = pd.DataFrame(rows)
    for c in ALL_COLS:
        if c not in df.columns:
            df[c] = pd.NA
    df = df[ALL_COLS]

    p = tmp_path / "events.parquet"
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(p, index=False)

    out = read_events(data_dir=tmp_path)
    by_ticker = {r["ticker"]: r for _, r in out.iterrows()}
    assert by_ticker["X"]["webcast_url"] == "https://legacy.example.com/x", \
        "legacy public webcast_url must be preserved when _a/_b/_c are null"
    assert by_ticker["Y"]["webcast_url"] == "https://from-a.example.com/y", \
        "new-style _a value must populate the public webcast_url"
