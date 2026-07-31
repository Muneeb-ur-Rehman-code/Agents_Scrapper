from __future__ import annotations
# ============================================================
#  ip_settings.py — Realtor.com proxy (UI IP Settings)
#  Zillow is never affected; only Realtor reads this value.
# ============================================================
import json
import logging
import os
import threading
from urllib.parse import urlparse

logger = logging.getLogger("AgentScraper")

_LOCK = threading.Lock()
_STORE_PATH = os.path.join(os.path.dirname(__file__), "realtor_proxy.json")


def _normalize_proxy(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    if "://" not in value:
        # Accept host:port or user:pass@host:port → treat as http
        value = f"http://{value}"
    return value


def _validate_proxy(value: str) -> str | None:
    """Return error message or None if OK."""
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https", "socks5", "socks4"}:
        return "Proxy must start with http://, https://, or socks5://"
    if not parsed.hostname:
        return "Proxy must include a host (e.g. http://user:pass@host:port)"
    return None


def mask_proxy(value: str) -> str:
    """Hide credentials for display."""
    value = (value or "").strip()
    if not value:
        return ""
    if "@" in value:
        scheme, rest = value.split("://", 1) if "://" in value else ("", value)
        after = rest.split("@", 1)[-1]
        return f"{scheme}://***@{after}" if scheme else f"***@{after}"
    return value


def get_realtor_proxy() -> str:
    """Saved UI proxy for Realtor only (empty if unset)."""
    with _LOCK:
        try:
            if not os.path.isfile(_STORE_PATH):
                return ""
            with open(_STORE_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return (data.get("realtor_proxy") or "").strip()
        except Exception as e:
            logger.warning("Could not read realtor proxy settings: %s", e)
            return ""


def set_realtor_proxy(raw: str) -> tuple[bool, str]:
    """
    Persist Realtor proxy. Empty string clears the UI override
    (env REALTOR_PROXY_SERVER still applies as fallback).
    Returns (ok, message).
    """
    value = _normalize_proxy(raw)
    err = _validate_proxy(value)
    if err:
        return False, err

    with _LOCK:
        try:
            payload = {"realtor_proxy": value}
            with open(_STORE_PATH, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            if value:
                logger.info("Realtor proxy saved via IP Settings → %s", mask_proxy(value))
                return True, "Realtor.com proxy saved. New Realtor scrapes will use it."
            logger.info("Realtor proxy cleared via IP Settings")
            return True, "Realtor.com proxy cleared. Env fallback (if any) still applies."
        except Exception as e:
            logger.exception("Failed to save realtor proxy")
            return False, f"Could not save: {e}"


def realtor_proxy_status() -> dict:
    saved = get_realtor_proxy()
    return {
        "realtor_proxy": saved,
        "realtor_proxy_masked": mask_proxy(saved),
        "configured": bool(saved),
    }
