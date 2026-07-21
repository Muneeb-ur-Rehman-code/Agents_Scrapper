from __future__ import annotations
"""Detect and humanize dead Chrome / chromedriver connection failures."""

_DEAD_BROWSER_MARKERS = (
    "connection refused",
    "max retries exceeded",
    "failed to establish a new connection",
    "httpconnectionpool",
    "invalid session id",
    "chrome not reachable",
    "disconnected: not connected to devtools",
    "session deleted because of page crash",
    "cannot connect to chrome",
    "errno 111",
    "remotendisconnected",
    "target window already closed",
)


def is_dead_browser_error(exc: BaseException | str | None) -> bool:
    text = str(exc or "").lower()
    if not text:
        return False
    return any(m in text for m in _DEAD_BROWSER_MARKERS)


def humanize_browser_error(exc: BaseException | str | None) -> str:
    """
    Turn raw urllib3/Selenium connection noise into a short UI-facing reason.
    """
    raw = str(exc or "").strip()
    if not is_dead_browser_error(raw):
        return raw or "Unknown scrape error"

    return (
        "Chrome browser crashed or was killed mid-scrape "
        "(connection to chromedriver lost). "
        "On a VPS this is usually low memory or too many parallel scrapes — "
        "retry with fewer concurrent runs (MAX_CONCURRENT_SCRAPES=1 or 2)."
    )
