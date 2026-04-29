"""URL reachability validator.

HEAD-first with browser User-Agent; GET-with-Range fallback for CDNs that
reject HEAD. The "validity" decision is broader than literal 2xx because
many real IR-vendor CDNs (Cloudflare, Akamai) refuse HEAD/GET-Range from
non-browser User-Agents but the URL itself is genuine and a real human
can open it. We accept those (state="cdn_block") and reject only
genuine "doesn't exist" signals (404/410, DNS / connection / SSL
failures).

Every check is appended to the validation log
(backend/data/_raw/calendar_enrichment/url_validation_log.jsonl) so the
operator can audit which URLs were rejected and why.

ASCII-only print/log per CLAUDE.md.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Status-code buckets -- see check_url() docstring for rationale.
_NOT_FOUND_CODES   = {404, 410}                          # genuine "doesn't exist"
_CDN_BLOCK_CODES   = {401, 403, 406, 429, 451, 501}      # bot-block / auth gate / region block
_OK_CODES          = {200, 206}                          # straightforward valid

_PROJECT_ROOT = Path(__file__).resolve().parents[5]
_VALIDATION_LOG = (
    _PROJECT_ROOT / "backend" / "data" / "_raw" / "calendar_enrichment"
    / "url_validation_log.jsonl"
)


@dataclass
class ValidationResult:
    valid: bool                  # accept the URL?
    status_code: int | None      # HTTP status from the final attempt; None on connection failure
    state: str                   # human label: ok | cdn_block | not_found | server_error
                                 # | dns_failure | connection_failure | ssl_failure
                                 # | other_4xx | request_error
    method: str                  # HEAD | GET_RANGE | none

    def __bool__(self) -> bool:
        return self.valid


def check_url(url: str, *, timeout: float = 5.0) -> ValidationResult:
    """Determine whether `url` is plausibly reachable from a real browser.

    Two-phase strategy:
      Phase 1: HEAD with browser User-Agent. Decisive on most outcomes.
      Phase 2 (only when HEAD's outcome is inconclusive -- non-decisive
                4xx like 405, or HEAD raised an exception): GET with
                Range: bytes=0-0. Decisive after this regardless.

    Acceptance rules (apply identically to HEAD and GET responses):
      2xx, 206                       -> valid (state=ok)
      401, 403, 406, 429, 451, 501   -> valid (state=cdn_block)
                                        Bot-block / auth gate / region
                                        block -- URL exists, real browser
                                        can probably open it.
      5xx (other than 501)           -> valid (state=server_error)
                                        Server flaky; real users get
                                        retries. Better to ship the URL
                                        than discard.
      404, 410                       -> invalid (state=not_found)
      Other 4xx (e.g. 400)           -> invalid (state=other_4xx)
      DNS/connection failure         -> invalid (state=connection_failure)
      SSL/cert failure               -> invalid (state=ssl_failure)
      Timeout / other RequestException -> invalid (state=request_error)
    """
    headers = {"User-Agent": _BROWSER_UA, "Accept": "*/*"}

    # Phase 1: HEAD
    try:
        r = requests.head(url, headers=headers, allow_redirects=True, timeout=timeout)
        decisive = _decisive_result(r.status_code, method="HEAD")
        if decisive is not None:
            return decisive
        # Non-decisive (e.g. 405 Method Not Allowed): fall through to GET.
    except requests.exceptions.SSLError:
        return ValidationResult(False, None, "ssl_failure", "HEAD")
    except requests.exceptions.ConnectionError:
        return ValidationResult(False, None, "connection_failure", "HEAD")
    except requests.exceptions.Timeout:
        # Timeouts on HEAD: try GET-with-Range; the server may simply not
        # support HEAD efficiently.
        pass
    except requests.RequestException as exc:
        logger.debug("HEAD raised non-standard exception for %s: %s", url, exc)

    # Phase 2: GET with Range
    try:
        r = requests.get(
            url,
            headers={**headers, "Range": "bytes=0-0"},
            allow_redirects=True,
            timeout=timeout,
            stream=True,
        )
        try:
            decisive = _decisive_result(r.status_code, method="GET_RANGE")
        finally:
            r.close()
        # If still non-decisive, treat as other_4xx and reject.
        if decisive is None:
            return ValidationResult(False, r.status_code, "other_4xx", "GET_RANGE")
        return decisive
    except requests.exceptions.SSLError:
        return ValidationResult(False, None, "ssl_failure", "GET_RANGE")
    except requests.exceptions.ConnectionError:
        return ValidationResult(False, None, "connection_failure", "GET_RANGE")
    except requests.exceptions.Timeout:
        return ValidationResult(False, None, "request_error", "GET_RANGE")
    except requests.RequestException:
        return ValidationResult(False, None, "request_error", "GET_RANGE")


def _decisive_result(code: int, *, method: str) -> ValidationResult | None:
    """Map an HTTP status code to a ValidationResult, or None if the
    code is non-decisive (caller should try the next phase)."""
    if code in _OK_CODES or 200 <= code < 300:
        return ValidationResult(True, code, "ok", method)
    if code in _CDN_BLOCK_CODES:
        return ValidationResult(True, code, "cdn_block", method)
    if code in _NOT_FOUND_CODES:
        return ValidationResult(False, code, "not_found", method)
    if 500 <= code < 600:
        return ValidationResult(True, code, "server_error", method)
    if 300 <= code < 400:
        # Should not normally happen because allow_redirects=True follows
        # 3xx; if we see one here, something is unusual but probably valid.
        return ValidationResult(True, code, "ok", method)
    # 400, 405, 408, etc. -- non-decisive on HEAD (need GET fallback).
    # On GET, caller treats None as other_4xx.
    return None


def log_validation(
    result: ValidationResult,
    *,
    url: str,
    ticker: str | None = None,
    fiscal_period: str | None = None,
    layer: str | None = None,
) -> None:
    """Append a single validation outcome to the JSONL audit log.

    Writes are best-effort -- log failures NEVER crash the caller."""
    record = {
        "captured_at":   datetime.now(timezone.utc).isoformat(),
        "url":           url,
        "ticker":        ticker or "",
        "fiscal_period": fiscal_period or "",
        "layer":         layer or "",
        **asdict(result),
    }
    try:
        _VALIDATION_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _VALIDATION_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except OSError as exc:
        logger.debug("validation log write failed: %s", exc)


def validate_url(url: str, *, timeout: float = 5.0) -> bool:
    """Backward-compat wrapper. Returns True iff the URL is accepted.

    Does NOT log -- callers that want auditability should use check_url()
    + log_validation() directly."""
    return check_url(url, timeout=timeout).valid
