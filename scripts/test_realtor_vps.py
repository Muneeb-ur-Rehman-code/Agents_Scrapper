#!/usr/bin/env python3
"""
One-shot Realtor.com access test for VPS or local machine.

Usage:
  python scripts/test_realtor_vps.py
  python scripts/test_realtor_vps.py "Austin, TX"

Exit code 0 = results page reachable; 1 = blocked or failed.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import platform
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config
from realtor_scraper import (
    _build_nodriver_browser,
    _extract_next_data,
    _get_agent_card_count,
    _location_slugs,
    _open_realtor_results,
    _page_block_kind,
    _parse_agents_from_next_data,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("realtor_test")


async def _run(location: str) -> int:
    print("=" * 60)
    print("Realtor.com connectivity test")
    print(f"  OS           : {platform.system()} {platform.release()}")
    print(f"  DISPLAY      : {os.environ.get('DISPLAY', '(not set)')}")
    print(f"  HEADLESS     : {config.HEADLESS}")
    print(f"  REALTOR_HEADLESS: {config.REALTOR_HEADLESS}")
    print(f"  PROXY_SERVER : {'set' if config.PROXY_SERVER else '(none)'}")
    print(f"  Location     : {location}")
    print(f"  URL slugs    : {', '.join(_location_slugs(location))}")
    print("=" * 60)

    browser = None
    try:
        browser = await _build_nodriver_browser()
        tab = await browser.get("about:blank")
        await asyncio.sleep(1)

        ok, err = await _open_realtor_results(tab, location)
        body = ""
        try:
            body = await tab.evaluate("document.body ? document.body.innerText.slice(0, 4000) : ''")
        except Exception:
            pass
        block = _page_block_kind(body or "")

        cards = 0
        try:
            cards = await _get_agent_card_count(tab)
        except Exception:
            pass

        nd_count = 0
        nd = await _extract_next_data(tab)
        if nd:
            nd_count = len(_parse_agents_from_next_data(nd))

        print()
        print(f"  Results ready : {ok}")
        print(f"  Block kind    : {block or 'none'}")
        print(f"  Agent cards   : {cards}")
        print(f"  __NEXT_DATA__ : {nd_count} agents")
        print(f"  Current URL   : {getattr(tab, 'url', '(unknown)')}")
        if err:
            print(f"  Error         : {err}")

        if ok and (cards > 0 or nd_count > 0):
            print("\nPASS — Realtor listings are reachable from this server.")
            return 0

        if block:
            print(
                "\nFAIL — Kasada/block detected. On VPS: apt install xvfb, "
                "HEADLESS=false, REALTOR_HEADLESS=false, optional PROXY_SERVER."
            )
        else:
            print("\nFAIL — Page loaded but no listings detected (selectors or slug).")
        return 1
    finally:
        if browser:
            try:
                browser.stop()
            except Exception:
                pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Test Realtor.com access from this host")
    parser.add_argument(
        "location",
        nargs="?",
        default=config.SEARCH_QUERY or "Austin, TX",
        help="Search location (default: SEARCH_QUERY from config)",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args.location)))


if __name__ == "__main__":
    main()
