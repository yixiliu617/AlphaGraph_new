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
