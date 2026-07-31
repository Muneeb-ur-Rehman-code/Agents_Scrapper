from __future__ import annotations
# ============================================================
#  ip_settings.py — Realtor.com proxy (UI IP Settings)
#  Zillow is never affected; only Realtor reads this value.
# ============================================================
import json
import logging
import os
import threading
from urllib.parse import unquote, urlparse

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


def parse_proxy_parts(raw: str) -> dict:
    """
    Split a proxy URL into Chrome-safe pieces.
    Chrome --proxy-server must NOT include user:pass (causes ERR_NO_SUPPORTED_PROXIES).
    Auth is applied separately via a tiny extension.
    """
    value = _normalize_proxy(raw)
    if not value:
        return {
            "raw": "",
            "server": "",
            "scheme": "",
            "host": "",
            "port": None,
            "username": "",
            "password": "",
            "has_auth": False,
        }
    parsed = urlparse(value)
    scheme = (parsed.scheme or "http").lower()
    host = parsed.hostname or ""
    port = parsed.port
    if port is None:
        port = 1080 if scheme.startswith("socks") else 80
    username = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    # Chrome flag: scheme://host:port only
    if scheme in {"socks5", "socks4"}:
        server = f"{scheme}://{host}:{port}"
    else:
        server = f"http://{host}:{port}"
    return {
        "raw": value,
        "server": server,
        "scheme": scheme,
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "has_auth": bool(username),
    }


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
                parts = parse_proxy_parts(value)
                logger.info(
                    "Realtor proxy saved via IP Settings → %s (auth=%s)",
                    mask_proxy(value),
                    "yes" if parts["has_auth"] else "no",
                )
                return True, (
                    "Realtor.com proxy saved. New Realtor scrapes will use it "
                    "(Chrome gets host:port; login is handled automatically)."
                )
            logger.info("Realtor proxy cleared via IP Settings")
            return True, "Realtor.com proxy cleared. Env fallback (if any) still applies."
        except Exception as e:
            logger.exception("Failed to save realtor proxy")
            return False, f"Could not save: {e}"


def realtor_proxy_status() -> dict:
    saved = get_realtor_proxy()
    parts = parse_proxy_parts(saved) if saved else parse_proxy_parts("")
    return {
        "realtor_proxy": saved,
        "realtor_proxy_masked": mask_proxy(saved),
        "proxy_server": parts.get("server") or "",
        "has_auth": bool(parts.get("has_auth")),
        "configured": bool(saved),
    }
