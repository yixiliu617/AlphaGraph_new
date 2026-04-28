"""URL reachability validator.

HEAD-first with browser User-Agent; GET-with-Range fallback for CDNs that
reject HEAD (Cloudflare default, certain IR vendors). Range-aware servers
respond 206 Partial Content (1 byte); Range-unaware servers respond 200 OK
and we abort the read after the first chunk via stream=True.

ASCII-only print/log per CLAUDE.md.
"""
from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def validate_url(url: str, *, timeout: float = 5.0) -> bool:
    """Return True if `url` returns a 2xx response (or 206 Partial Content).

    Step 1: HEAD with browser User-Agent.
    Step 2 (HEAD failed/4xx/5xx): GET with Range: bytes=0-0.
    """
    headers = {"User-Agent": _BROWSER_UA, "Accept": "*/*"}
    try:
        r = requests.head(url, headers=headers, allow_redirects=True,
                          timeout=timeout)
        if 200 <= r.status_code < 300:
            return True
    except requests.RequestException as exc:
        logger.debug("HEAD failed for %s: %s", url, exc)

    try:
        r = requests.get(
            url,
            headers={**headers, "Range": "bytes=0-0"},
            allow_redirects=True,
            timeout=timeout,
            stream=True,
        )
        try:
            return r.status_code in (200, 206)
        finally:
            r.close()
    except requests.RequestException as exc:
        logger.debug("GET-with-Range failed for %s: %s", url, exc)
        return False
