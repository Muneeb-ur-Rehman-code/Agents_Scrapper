from __future__ import annotations
# ============================================================
#  proxy_auth_extension.py
#  Tiny MV2 Chrome extension so --proxy-server can use host:port
#  while username/password are supplied via onAuthRequired.
#  Used by Realtor only (fixes ERR_NO_SUPPORTED_PROXIES).
# ============================================================
import json
import logging
import os
import re

logger = logging.getLogger("AgentScraper")


def _js_string(value: str) -> str:
    """Safe JS string literal."""
    return json.dumps(value or "")


def build_proxy_auth_extension(username: str, password: str, dest_dir: str) -> str:
    """
    Write a minimal Chrome extension into dest_dir and return that path.
    """
    os.makedirs(dest_dir, exist_ok=True)
    # Keep filenames simple — Chrome loads this folder as an unpacked extension
    manifest = {
        "version": "1.0.0",
        "manifest_version": 2,
        "name": "Realtor Proxy Auth",
        "permissions": [
            "webRequest",
            "webRequestBlocking",
            "<all_urls>",
        ],
        "background": {
            "scripts": ["background.js"],
            "persistent": True,
        },
    }
    background = (
        "chrome.webRequest.onAuthRequired.addListener(\n"
        "  function () {\n"
        "    return {\n"
        "      authCredentials: {\n"
        f"        username: {_js_string(username)},\n"
        f"        password: {_js_string(password)}\n"
        "      }\n"
        "    };\n"
        "  },\n"
        "  { urls: [\"<all_urls>\"] },\n"
        "  [\"blocking\"]\n"
        ");\n"
    )

    man_path = os.path.join(dest_dir, "manifest.json")
    bg_path = os.path.join(dest_dir, "background.js")
    with open(man_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    with open(bg_path, "w", encoding="utf-8") as fh:
        fh.write(background)

    # Absolute path required for --load-extension on Windows/Linux
    abs_dir = os.path.abspath(dest_dir)
    logger.info("🔐  Proxy auth extension ready at %s", abs_dir)
    return abs_dir


def sanitize_ext_dirname(label: str) -> str:
    safe = re.sub(r"[^\w.\-]+", "_", (label or "proxy")[:40])
    return safe or "proxy"
