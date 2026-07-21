from __future__ import annotations
"""Cross-thread cancel + browser resource registry for parallel scrapes."""
import logging
import threading
from typing import Any

logger = logging.getLogger("AgentScraper")

_lock = threading.Lock()
_cancel_events: dict[str, threading.Event] = {}
_resources: dict[str, Any] = {}


def begin_run(run_id: str) -> threading.Event:
    ev = threading.Event()
    with _lock:
        _cancel_events[run_id] = ev
        _resources.pop(run_id, None)
    return ev


def end_run(run_id: str) -> None:
    with _lock:
        _cancel_events.pop(run_id, None)
        _resources.pop(run_id, None)


def bind_resource(run_id: str, resource: Any) -> None:
    """Attach a live browser/driver so cancel can stop it immediately."""
    if not run_id or resource is None:
        return
    with _lock:
        _resources[run_id] = resource


def is_cancelled(run_id: str | None) -> bool:
    if not run_id:
        return False
    with _lock:
        ev = _cancel_events.get(run_id)
    return bool(ev and ev.is_set())


def cancel_run(run_id: str) -> bool:
    """
    Signal cancel and try to close the bound browser/driver.
    Returns True if this run was known/active.
    """
    with _lock:
        ev = _cancel_events.get(run_id)
        resource = _resources.get(run_id)
    if not ev and resource is None:
        return False
    if ev:
        ev.set()
    if resource is not None:
        try:
            if hasattr(resource, "quit"):
                resource.quit()
            elif hasattr(resource, "stop"):
                resource.stop()
        except Exception as e:
            logger.debug("Cancel close resource for %s: %s", run_id, e)
    return True
