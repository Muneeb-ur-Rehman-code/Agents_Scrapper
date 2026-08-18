from __future__ import annotations
# ============================================================
#  realtor_scraper.py  —  Realtor.com agent scraping logic
#
#  STRATEGY (updated for Kasada bypass):
#  ──────────────────────────────────────
#  Realtor.com uses Kasada bot protection which BLOCKS
#  undetected-chromedriver / Selenium instantly (no CAPTCHA).
#
#  Solution: use `nodriver` (CDP-only, no WebDriver binary)
#  and drive the live search UI to reach the agent list.
#
#  FLOW:
#  ─────
#  1. Launch a stealth Chrome via nodriver (headed + Xvfb on Linux VPS)
#  2. Try direct /realestateagents/<city_state> URL first (fewer Kasada triggers)
#  3. Fall back to UI search on /realestateagents if direct fails
#  4. Collect agent profile URLs from results page
#  5. Visit each profile and scrape contact info from DOM
#  6. Paginate via the next link until done
# ============================================================

import asyncio
import json
import time
import random
import logging
import os
import platform
import re
from urllib.parse import urljoin

import nodriver as uc

import config
from data_saver import save_agents

logger = logging.getLogger("AgentScraper")


# Soft challenges often clear after JS runs. Hard blocks do not.
_SOFT_CHALLENGE_MARKERS = (
    "checking your browser",
    "just a moment",
    "enable javascript and cookies",
    "please wait",
    "verifying you are human",
)
_HARD_BLOCK_MARKERS = (
    "access denied",
    "access to this page has been denied",
    "please verify you are a human",
    "sorry, you have been blocked",
    "pardon our interruption",
    "request could not be processed",
    "attention required",
)


# ════════════════════════════════════════════════════════════
#  NODRIVER BROWSER SETUP
# ════════════════════════════════════════════════════════════

def _realtor_should_headless() -> bool:
    """
    Kasada is far more aggressive against headless Chrome on VPS IPs.
    On Linux we force headed + Xvfb unless REALTOR_HEADLESS is set.
    """
    if config.REALTOR_HEADLESS is not None:
        return bool(config.REALTOR_HEADLESS)
    if platform.system() == "Linux":
        return False
    return bool(config.HEADLESS)


# Active localhost forwarder for Realtor proxy auth (no Chrome password popup)
_realtor_local_proxy = None
# Last egress check (VPS IP vs residential exit IP) for error messages
_last_proxy_egress: dict | None = None


def _stop_realtor_local_proxy() -> None:
    global _realtor_local_proxy
    if _realtor_local_proxy is not None:
        try:
            _realtor_local_proxy.stop()
        except Exception:
            pass
        _realtor_local_proxy = None


def _realtor_proxy() -> str:
    """Residential proxy for Realtor only (VPS/Linux). Ignored on local Windows."""
    try:
        from ip_settings import should_use_realtor_proxy, get_realtor_proxy
        if not should_use_realtor_proxy():
            return ""
        saved = (get_realtor_proxy() or "").strip()
        if saved:
            return saved
    except Exception:
        pass
    # Env proxy also only on Linux / FORCE_REALTOR_PROXY
    try:
        from ip_settings import should_use_realtor_proxy
        if not should_use_realtor_proxy():
            return ""
    except Exception:
        if platform.system() != "Linux":
            return ""
    return (config.REALTOR_PROXY_SERVER or config.PROXY_SERVER or "").strip()


def _realtor_proxy_parts() -> dict:
    from ip_settings import parse_proxy_parts
    return parse_proxy_parts(_realtor_proxy())


def _kasada_vps_error(egress: dict | None = None) -> str:
    """User-facing message when Kasada hard-blocks this host."""
    egress = egress if egress is not None else _last_proxy_egress
    lines = [
        "Realtor.com blocked this session (Kasada).",
    ]
    if platform.system() == "Linux":
        disp = os.environ.get("DISPLAY")
        if disp:
            lines.append(f"Browser display is OK ({disp}).")
        else:
            lines.append("Install xvfb and set HEADLESS=false.")
    parts = _realtor_proxy_parts()
    if egress and (egress.get("direct_ip") or egress.get("proxy_ip") or egress.get("detail")):
        if egress.get("direct_ip"):
            lines.append(f"VPS direct IP: {egress['direct_ip']}.")
        if egress.get("proxy_ip"):
            lines.append(f"Proxy exit IP: {egress['proxy_ip']}.")
        if egress.get("ok"):
            lines.append(
                "Proxy exit IP is active, but Kasada still rejected the browser "
                "(burned residential IP and/or bot fingerprint). Rotate IP with your provider."
            )
        elif egress.get("detail"):
            lines.append(egress["detail"])
    elif not parts.get("server"):
        lines.append(
            "No proxy in IP Settings — VPS datacenter IP is almost always blocked. "
            "Save http://user:pass@host:port and retry."
        )
    else:
        lines.append(
            f"Proxy gateway: {parts.get('server')} (auth={'yes' if parts.get('has_auth') else 'no'}). "
            "Local PC works on home ISP IP; VPS needs a clean residential exit IP."
        )
    return " ".join(lines)



def _fetch_ipify(timeout: float = 20.0, proxy_url: str | None = None) -> str:
    """Return public IPv4 via api.ipify.org (optional HTTP proxy URL)."""
    import urllib.request

    req = urllib.request.Request(
        "https://api.ipify.org",
        headers={"User-Agent": "Mozilla/5.0"},
    )
    if proxy_url:
        handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
        opener = urllib.request.build_opener(handler)
        with opener.open(req, timeout=timeout) as resp:
            return (resp.read().decode("utf-8", errors="replace") or "").strip()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return (resp.read().decode("utf-8", errors="replace") or "").strip()


def _verify_proxy_egress() -> dict:
    """
    Compare VPS direct IP vs IP through the configured Realtor proxy.
    If they match, Chrome is NOT exiting via the residential IP (proxy broken).
    """
    global _last_proxy_egress
    parts = _realtor_proxy_parts()
    result = {
        "direct_ip": "",
        "proxy_ip": "",
        "proxy_host": parts.get("host") or "",
        "ok": False,
        "detail": "",
    }
    try:
        result["direct_ip"] = _fetch_ipify(timeout=12.0)
    except Exception as e:
        result["detail"] = f"Could not read direct IP: {e}"
        logger.warning("⚠️  %s", result["detail"])
        _last_proxy_egress = result
        return result

    if not parts.get("server"):
        result["detail"] = "No Realtor proxy configured"
        _last_proxy_egress = result
        return result

    # Prefer local forwarder (already has auth). Else build user:pass@host URL for urllib.
    proxy_url = None
    if _realtor_local_proxy and _realtor_local_proxy.port:
        proxy_url = f"http://127.0.0.1:{_realtor_local_proxy.port}"
    elif parts.get("has_auth"):
        from urllib.parse import quote
        u = quote(parts.get("username") or "", safe="")
        p = quote(parts.get("password") or "", safe="")
        proxy_url = f"http://{u}:{p}@{parts['host']}:{parts['port']}"
    else:
        proxy_url = parts.get("server")

    try:
        result["proxy_ip"] = _fetch_ipify(timeout=25.0, proxy_url=proxy_url)
    except Exception as e:
        result["detail"] = (
            f"Proxy egress check FAILED ({e}). Auth/host/port may be wrong, "
            "or the provider blocks this VPS from using the proxy."
        )
        logger.error("❌  %s", result["detail"])
        _last_proxy_egress = result
        return result

    if not result["proxy_ip"]:
        result["detail"] = "Proxy returned empty IP"
        _last_proxy_egress = result
        return result

    if result["proxy_ip"] == result["direct_ip"]:
        result["detail"] = (
            f"Proxy NOT applied — internet still sees VPS IP {result['direct_ip']}. "
            "Kasada will keep blocking. Fix credentials / whitelist this VPS / try the other host IP."
        )
        logger.error("❌  %s", result["detail"])
        _last_proxy_egress = result
        return result

    result["ok"] = True
    result["detail"] = (
        f"Proxy OK — direct VPS IP {result['direct_ip']} → exit IP {result['proxy_ip']} "
        f"(gateway {result['proxy_host'] or 'n/a'})"
    )
    logger.info("✅  %s", result["detail"])
    _last_proxy_egress = result
    return result


async def _homepage_ip_blocked(page) -> bool:
    """Quick probe: datacenter IPs often get an instant hard deny on realtor.com."""
    try:
        await page.get("https://www.realtor.com/")
        await _human_delay(3.0, 5.0)
        text = await page.evaluate("document.body?.innerText || ''")
        return _page_block_kind(text) == "hard"
    except Exception as e:
        logger.debug("Homepage probe failed: %s", e)
        return False


async def _build_nodriver_browser():
    """
    Build a stealth Chrome browser using nodriver.
    On Linux VPS: Xvfb + headed mode + persistent profile.
    Optional PROXY_SERVER for residential IPs.
    """
    from driver_setup import start_virtual_display_for_run

    use_headless = _realtor_should_headless()

    if platform.system() == "Linux":
        display_str = start_virtual_display_for_run()
        if display_str:
            os.environ["DISPLAY"] = display_str
        if use_headless:
            logger.warning(
                "⚠️  Realtor running headless on Linux — Kasada may still block. "
                "Set REALTOR_HEADLESS=false (default) with Xvfb."
            )

    profile_name = "realtor_nodriver"
    if platform.system() != "Linux":
        # Separate local profile so old VPS/proxy prefs never show a login popup
        profile_name = "realtor_nodriver_local"
    profile_dir = os.path.join(config.CHROME_PROFILE_DIR, profile_name)
    os.makedirs(profile_dir, exist_ok=True)

    browser_args = [
        f"--window-size={config.WINDOW_WIDTH},{config.WINDOW_HEIGHT}",
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-infobars",
        "--disable-gpu",
        "--lang=en-US,en;q=0.9",
        "--disable-features=IsolateOrigins,site-per-process,WebRtcHideLocalIpsWithMdns",
        "--enforce-webrtc-ip-permission-check",
        "--force-webrtc-ip-permission-check",
        "--start-maximized",
    ]

    global _realtor_local_proxy
    _stop_realtor_local_proxy()

    proxy_parts = _realtor_proxy_parts()
    proxy_server = (proxy_parts.get("server") or "").strip()
    if proxy_server:
        # Auth proxies: Chrome talks to 127.0.0.1 (no popup); we add user/pass upstream.
        # Plain IP:port with no auth can point Chrome straight at the residential host.
        if proxy_parts.get("has_auth"):
            from local_auth_proxy import start_local_auth_proxy_from_parts

            _realtor_local_proxy = start_local_auth_proxy_from_parts(proxy_parts)
            if _realtor_local_proxy and _realtor_local_proxy.port:
                chrome_proxy = f"http://127.0.0.1:{_realtor_local_proxy.port}"
                browser_args.append(f"--proxy-server={chrome_proxy}")
                logger.info(
                    "🌐  Realtor proxy via local forwarder %s → %s (auth injected; no Chrome popup)",
                    chrome_proxy,
                    proxy_server,
                )
            else:
                logger.error(
                    "❌  Could not start local auth proxy — Chrome would show a password popup"
                )
                browser_args.append(f"--proxy-server={proxy_server}")
        else:
            browser_args.append(f"--proxy-server={proxy_server}")
            logger.info("🌐  Realtor proxy enabled (no auth): %s", proxy_server)
    elif platform.system() == "Linux":
        logger.warning(
            "⚠️  No Realtor proxy — Realtor.com often hard-blocks bare VPS IPs. "
            "Set one in IP Settings."
        )
    else:
        # Local Windows: never use proxy. Force direct so an old chrome_profile
        # cannot revive a saved proxy login popup.
        browser_args.append("--no-proxy-server")
        logger.info("🏠  Local mode — Realtor uses home internet (proxy disabled)")

    browser = await uc.start(
        headless=use_headless,
        user_data_dir=profile_dir,
        sandbox=False,
        browser_args=browser_args,
    )
    logger.info(
        "✅  Nodriver launched | headless=%s | DISPLAY=%s | profile=%s",
        use_headless,
        os.environ.get("DISPLAY", "(none)"),
        profile_dir,
    )
    return browser


# ════════════════════════════════════════════════════════════
#  HUMAN-LIKE BEHAVIOR HELPERS
# ════════════════════════════════════════════════════════════

async def _human_delay(mn=1.5, mx=4.0):
    """Random delay to mimic human pacing."""
    await asyncio.sleep(random.uniform(mn, mx))


async def _random_scroll(page):
    """Perform a few random scrolls to look human."""
    for _ in range(random.randint(1, 3)):
        dy = random.randint(150, 400)
        await page.evaluate(
            f"window.scrollBy({{top: {dy}, behavior: 'smooth'}})"
        )
        await asyncio.sleep(random.uniform(0.5, 1.2))


async def _simulate_mouse_move(page):
    """Move mouse to random viewport positions to generate events."""
    try:
        width = await page.evaluate("window.innerWidth")
        height = await page.evaluate("window.innerHeight")
        for _ in range(random.randint(2, 5)):
            x = random.randint(100, max(200, width - 100))
            y = random.randint(100, max(200, height - 100))
            await page.send(uc.cdp.input_.dispatch_mouse_event(
                type_="mouseMoved",
                x=x, y=y,
            ))
            await asyncio.sleep(random.uniform(0.1, 0.4))
    except Exception:
        pass  # Not critical


async def _inject_stealth(page) -> None:
    """Reduce obvious automation signals in nodriver sessions."""
    try:
        await page.evaluate(
            """
            (() => {
              Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
              Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
              window.chrome = window.chrome || { runtime: {} };
            })();
            """
        )
    except Exception:
        pass


async def _results_page_ready(page) -> bool:
    """True when listing page looks usable (cards or structured data)."""
    if await _wait_for_selector(page, config.REALTOR_SELECTORS["agent_card"], 10.0):
        return True
    next_data = await _extract_next_data(page)
    if next_data and _parse_agents_from_next_data(next_data):
        return True
    try:
        has_links = await page.evaluate(
            "document.querySelectorAll('a[href*=\"/realestateagents/\"]').length > 0"
        )
        return bool(has_links)
    except Exception:
        return False


def _location_slugs(location: str) -> list[str]:
    """Build Realtor location slug variants, e.g. Austin, TX -> austin_tx."""
    from location_utils import normalize_realtor_location

    raw = normalize_realtor_location(location or "").strip().lower()
    variants: list[str] = []

    _city_only_aliases: dict[str, list[str]] = {
        "new york": ["new_york_ny", "new-york-ny"],
        "los angeles": ["los-angeles_ca", "los_angeles_ca"],
        "chicago": ["chicago_il"],
        "houston": ["houston_tx"],
        "phoenix": ["phoenix_az"],
        "philadelphia": ["philadelphia_pa"],
        "san antonio": ["san-antonio_tx", "san_antonio_tx"],
        "san diego": ["san-diego_ca", "san_diego_ca"],
        "dallas": ["dallas_tx"],
        "austin": ["austin_tx"],
        "miami": ["miami_fl"],
    }
    if raw in _city_only_aliases:
        variants.extend(_city_only_aliases[raw])

    if "," in raw:
        city, state = raw.split(",", 1)
        city_slug = re.sub(r"[^a-z0-9]+", "-", city.strip()).strip("-")
        state_slug = re.sub(r"[^a-z0-9]+", "", state.strip())
        if city_slug and state_slug:
            variants.append(f"{city_slug}_{state_slug}")
            variants.append(f"{city_slug}-{state_slug}")
    plain = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    if plain and plain not in variants:
        variants.append(plain)
    # de-dupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for v in variants:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out or ["united-states"]


async def _open_realtor_results(page, location: str) -> tuple[bool, str | None]:
    """
    Open agent results using the lowest-friction path first (direct URL).
    Falls back to UI search only if direct navigation fails.
    """
    await _inject_stealth(page)

    if await _homepage_ip_blocked(page):
        if not _realtor_proxy():
            logger.error("❌  Kasada hard block on homepage (no proxy configured)")
            return False, _kasada_vps_error()
        logger.warning("⚠️  Homepage hard block with proxy — continuing anyway")

    # ── Strategy 1: direct deep links (best on VPS) ───────────
    for slug in _location_slugs(location):
        # Prefer full listing path with /pg-1 (matches live Realtor URLs)
        candidates = [
            (
                f"https://www.realtor.com/realestateagents/{slug}"
                f"/intent-buy/sort-relevantagents/agenttype-all/pg-1"
            ),
            f"https://www.realtor.com/realestateagents/{slug}/pg-1",
            f"https://www.realtor.com/realestateagents/{slug}",
        ]
        for direct in candidates:
            logger.info("🌐  Trying direct results URL: %s", direct)
            if not await _load_with_retry(page, direct, max_attempts=2):
                if platform.system() == "Linux" and not _realtor_proxy():
                    text = await page.evaluate("document.body?.innerText || ''")
                    if _page_block_kind(text) == "hard":
                        return False, _kasada_vps_error()
                continue
            await _simulate_mouse_move(page)
            await _random_scroll(page)
            if await _results_page_ready(page):
                logger.info("✅  Direct URL flow ready (%s)", slug)
                return True, None
            logger.warning("⚠️  Direct URL loaded but results not detected (%s)", direct)

    if platform.system() == "Linux" and not _realtor_proxy():
        return False, _kasada_vps_error()

    # ── Strategy 2: legacy UI flow ────────────────────────────
    logger.info("↪️  Falling back to Realtor UI search flow …")
    await _warm_up_session(page)

    agent_search_url = config.REALTOR_AGENT_URL
    if not await _load_with_retry(page, agent_search_url, max_attempts=2):
        return False, _kasada_vps_error()

    await _simulate_mouse_move(page)
    await _random_scroll(page)
    await _select_intent_both(page)

    if not await _search_location(page, location):
        return False, "Realtor search UI failed (blocked page or selectors changed)."

    if await _results_page_ready(page):
        logger.info("✅  UI search flow ready")
        return True, None

    return False, "Realtor results page did not load agent listings."


async def _warm_up_session(page):
    """Visit homepage first so cookies/JS challenges settle before search."""
    try:
        logger.info("🔥  Warming up Realtor session (homepage) …")
        await page.get("https://www.realtor.com/")
        await _human_delay(4.0, 7.0)
        await _simulate_mouse_move(page)
        await _random_scroll(page)
        await _wait_out_challenge(page, timeout_sec=45.0)
        await _human_delay(1.5, 3.0)
    except Exception as e:
        logger.warning("⚠️  Warm-up failed (continuing): %s", e)


# ════════════════════════════════════════════════════════════
#  __NEXT_DATA__ EXTRACTION
# ════════════════════════════════════════════════════════════

async def _extract_next_data(page) -> dict | None:
    """
    Extract the __NEXT_DATA__ JSON object from a Next.js page.
    This contains ALL the structured data that the page renders.
    """
    try:
        json_text = await page.evaluate("""
            (() => {
                const el = document.getElementById('__NEXT_DATA__');
                return el ? el.textContent : null;
            })()
        """)
        if json_text:
            return json.loads(json_text)
    except Exception as e:
        logger.debug(f"__NEXT_DATA__ extraction error: {e}")
    return None


def _parse_agents_from_next_data(data: dict) -> list[dict]:
    """
    Navigate the __NEXT_DATA__ JSON tree to find agent listings.
    The structure varies but typically lives under:
      props.pageProps.agents  OR
      props.pageProps.searchResults.agents  OR
      props.pageProps.agentList
    """
    agents = []

    if not data:
        return agents

    props = data.get("props", {}).get("pageProps", {})

    # Try multiple known paths for agent data
    agent_list = None
    for key_path in [
        ["agents"],
        ["searchResults", "agents"],
        ["agentList"],
        ["pageData", "agents"],
        ["initialReduxState", "agents", "agentList"],
    ]:
        obj = props
        for key in key_path:
            if isinstance(obj, dict):
                obj = obj.get(key)
            else:
                obj = None
                break
        if obj and isinstance(obj, list):
            agent_list = obj
            logger.info(f"   📦  Found agents via __NEXT_DATA__ path: {'.'.join(key_path)}")
            break

    if not agent_list:
        # Deep search: look for any list containing agent-like dicts
        agent_list = _deep_find_agents(props)

    if not agent_list:
        return agents

    for ag in agent_list:
        if not isinstance(ag, dict):
            continue
        agent = _normalize_agent_data(ag)
        if agent:
            agents.append(agent)

    return agents


def _deep_find_agents(obj, depth=0, max_depth=6) -> list | None:
    """
    Recursively search JSON for a list of dicts that look like agents.
    An "agent" dict has keys like 'name', 'person_name', 'full_name',
    'phone', 'office', etc.
    """
    if depth > max_depth:
        return None

    agent_keys = {"name", "person_name", "full_name", "first_name",
                  "agent_name", "advertiser_id", "nrds_id"}

    if isinstance(obj, list) and len(obj) >= 1:
        # Check if list items look like agents
        sample = obj[0] if obj else {}
        if isinstance(sample, dict) and agent_keys & set(sample.keys()):
            return obj

    if isinstance(obj, dict):
        for key, val in obj.items():
            result = _deep_find_agents(val, depth + 1, max_depth)
            if result:
                return result

    return None


def _normalize_agent_data(ag: dict) -> dict | None:
    """Convert a raw JSON agent dict into our standard format."""
    # Extract name
    name = (
        ag.get("full_name")
        or ag.get("person_name")
        or ag.get("name")
        or ag.get("agent_name")
        or ""
    )
    if isinstance(name, dict):
        # Sometimes name is {first: ..., last: ...}
        name = f"{name.get('first', '')} {name.get('last', '')}".strip()

    if not name:
        first = ag.get("first_name", "")
        last = ag.get("last_name", "")
        name = f"{first} {last}".strip()

    if not name:
        return None  # Skip entries without names

    # Extract phone(s)
    phones = []
    for phone_key in ["phone", "phones", "phone1", "office_phone"]:
        p = ag.get(phone_key)
        if isinstance(p, str) and p:
            phones.append(p)
        elif isinstance(p, dict):
            # {number: "...", type: "..."}
            num = p.get("number") or p.get("phone_number", "")
            if num:
                phones.append(num)
        elif isinstance(p, list):
            for item in p:
                if isinstance(item, str) and item:
                    phones.append(item)
                elif isinstance(item, dict):
                    num = item.get("number") or item.get("phone_number", "")
                    ptype = item.get("type", "")
                    if num:
                        phones.append(f"{ptype}: {num}" if ptype else num)

    # Also check nested office
    office = ag.get("office", {})
    if isinstance(office, dict):
        op = office.get("phone") or office.get("phone1", "")
        if isinstance(op, str) and op:
            phones.append(f"Office: {op}")
        elif isinstance(op, dict):
            num = op.get("number", "")
            if num:
                phones.append(f"Office: {num}")

    # Extract address
    address_parts = []
    addr = ag.get("address") or ag.get("office_address") or {}
    if isinstance(addr, dict):
        for k in ["line", "line1", "street", "address_line1"]:
            if addr.get(k):
                address_parts.append(addr[k])
                break
        city = addr.get("city", "")
        state = addr.get("state_code") or addr.get("state", "")
        postal = addr.get("postal_code") or addr.get("zip", "")
        if city:
            address_parts.append(city)
        if state:
            address_parts.append(state)
        if postal:
            address_parts.append(postal)
    elif isinstance(addr, str) and addr:
        address_parts.append(addr)

    # Extract website
    website = ag.get("website") or ag.get("web_url") or ag.get("href") or "N/A"

    # Build profile URL
    slug = ag.get("permalink") or ag.get("slug") or ag.get("advertiser_id", "")
    nrds = ag.get("nrds_id", "")
    profile_url = "N/A"
    if slug:
        profile_url = f"https://www.realtor.com/realestateagents/{slug}"
    elif nrds:
        profile_url = f"https://www.realtor.com/realestateagents/{nrds}"

    return {
        "name":        name,
        "phone":       " | ".join(phones) if phones else "N/A",
        "email":       ag.get("email", "N/A") or "N/A",
        "address":     ", ".join(address_parts) if address_parts else "N/A",
        "website":     website if website else "N/A",
        "profile_url": profile_url,
    }


# ════════════════════════════════════════════════════════════
#  DOM-BASED FALLBACK EXTRACTION
# ════════════════════════════════════════════════════════════

async def _extract_agents_from_dom(page) -> list[dict]:
    """
    Fallback: if __NEXT_DATA__ doesn't have the data we need,
    scrape directly from the rendered DOM.
    """
    agents = []
    try:
        # Try to get agent cards from the DOM
        result = await page.evaluate("""
            (() => {
                const agents = [];
                // Look for agent card links
                const links = document.querySelectorAll('a[href*="/realestateagents/"]');
                const seen = new Set();

                for (const link of links) {
                    const href = link.href;
                    if (!href) continue;
                    const base = href.split('?')[0].replace(/\\/$/, '');

                    // Only individual agent URLs (have name slug)
                    const path = base.replace('https://www.realtor.com/realestateagents/', '');
                    const parts = path.split('/');
                    if (parts.length < 1 || parts[0].length < 5) continue;
                    if (seen.has(base)) continue;
                    seen.add(base);

                    // Try to extract name from the link or parent
                    let name = '';
                    const nameEl = link.querySelector('span, div');
                    if (nameEl) name = nameEl.textContent.trim();
                    if (!name) name = link.textContent.trim();

                    // Only keep if it looks like a name
                    if (name.length > 50 || name.length < 3) continue;

                    agents.push({
                        name: name,
                        profile_url: base,
                    });
                }
                return agents;
            })()
        """)

        if result:
            for ag in result:
                agents.append({
                    "name":        ag.get("name", "N/A"),
                    "phone":       "N/A",
                    "email":       "N/A",
                    "address":     "N/A",
                    "website":     "N/A",
                    "profile_url": ag.get("profile_url", "N/A"),
                })

    except Exception as e:
        logger.warning(f"   DOM extraction failed: {e}")

    return agents


# ════════════════════════════════════════════════════════════
#  AGENT DETAIL PAGE SCRAPING (via __NEXT_DATA__)
# ════════════════════════════════════════════════════════════

async def _scrape_agent_profile(page, url: str) -> dict | None:
    """Navigate to an individual agent profile URL and scrape contact info."""
    try:
        await page.get(url)
        await _human_delay(2.0, 4.0)
        return await _scrape_current_profile(page, url)
    except Exception as e:
        logger.error(f"   ❌  Profile scrape error: {e}")
        return None


async def _scrape_current_profile(page, url: str) -> dict | None:
    """Scrape contact info from the current page (already on profile)."""
    data = {
        "name":        "N/A",
        "phone":       "N/A",
        "email":       "N/A",
        "address":     "N/A",
        "website":     "N/A",
        "profile_url": url or "N/A",
    }

    # Check if we got blocked
    page_text = await page.evaluate("document.body?.innerText || ''")
    if _is_blocked(page_text):
        logger.warning(f"   🚫  Blocked on profile page: {url}")
        return None

    # Ensure the Contact information tab is active (if present)
    await page.evaluate(
        """
        (() => {
            const tab = Array.from(document.querySelectorAll('button[role="tab"]'))
                .find(el => (el.textContent || '').trim().toLowerCase() === 'contact information');
            if (tab) {
                tab.click();
                return true;
            }
            return false;
        })()
        """
    )
    await _human_delay(1.0, 2.0)

    # DOM scraping for individual page (contact info section)
    name = await page.evaluate(
        """
        (() => {
            const nameEl = document.querySelector('h1');
            return nameEl ? nameEl.textContent.trim() : '';
        })()
        """
    )
    if name:
        data["name"] = name

    phones = await page.evaluate(
        """
        (() => {
            const root = document.querySelector('#professional-info-section') || document;
            const phoneLinks = root.querySelectorAll('a[href^="tel:"]');
            const out = [];
            phoneLinks.forEach((el) => {
                const text = (el.textContent || '').trim() || (el.href || '').replace('tel:', '');
                if (text) out.push(text);
            });
            return out;
        })()
        """
    )
    if phones and isinstance(phones, list):
        normalized_phones = []
        for p in phones:
            text = _unwrap_dom_value(p)
            if text:
                normalized_phones.append(text)
        if normalized_phones:
            data["phone"] = " | ".join(normalized_phones)

    address_lines = await page.evaluate(
        """
        (() => {
            const root = document.querySelector('#professional-info-section') || document;
            const addrBlocks = root.querySelectorAll('svg[data-testid="icon-location"] ~ div p');
            const out = [];
            addrBlocks.forEach((el) => {
                const text = (el.textContent || '').trim();
                if (text) out.push(text);
            });
            return out;
        })()
        """
    )
    if address_lines and isinstance(address_lines, list):
        normalized_address = []
        for line in address_lines:
            text = _unwrap_dom_value(line)
            if text:
                normalized_address.append(text)
        if normalized_address:
            data["address"] = ", ".join(normalized_address)

    website = await page.evaluate(
        """
        (() => {
            const root = document.querySelector('#professional-info-section') || document;
            const webEl = root.querySelector('svg[data-testid="icon-globe"] ~ div a');
            return webEl && webEl.href ? webEl.href : '';
        })()
        """
    )
    if website:
        data["website"] = _unwrap_dom_value(website) or website

    if config.DEBUG_REALTOR:
        section_html = await page.evaluate(
            """
            (() => {
                const root = document.querySelector('#professional-info-section');
                return root ? root.outerHTML : '';
            })()
            """
        )
        if section_html:
            logger.debug("Contact section HTML: %s", section_html)

    # Try __NEXT_DATA__ only if DOM failed to give a name
    if data["name"] == "N/A":
        next_data = await _extract_next_data(page)
        if next_data:
            props = next_data.get("props", {}).get("pageProps", {})
            agent_obj = (
                props.get("agent")
                or props.get("agentDetails")
                or props.get("profile")
                or props
            )

            if isinstance(agent_obj, dict):
                parsed = _normalize_agent_data(agent_obj)
                if parsed:
                    parsed["profile_url"] = url or parsed.get("profile_url", "N/A")
                    return parsed

    return data


async def _safe_text(page, selector: str) -> str:
    """Safely extract text from a CSS selector."""
    try:
        result = await page.evaluate(f"""
            (() => {{
                const el = document.querySelector('{selector}');
                return el ? el.textContent.trim() : '';
            }})()
        """)
        return result or ""
    except Exception:
        return ""


# ════════════════════════════════════════════════════════════
#  BLOCK DETECTION
# ════════════════════════════════════════════════════════════

def _page_block_kind(page_text: str) -> str | None:
    """
    Return 'hard', 'soft', or None.
    Soft = temporary challenge that often clears after waiting.
    Hard = permanent deny page.
    """
    lower = (page_text or "").lower()[:3000]
    if any(m in lower for m in _HARD_BLOCK_MARKERS):
        return "hard"
    if any(m in lower for m in _SOFT_CHALLENGE_MARKERS):
        return "soft"
    # Cloudflare ray id alone is not always a hard block
    if "ray id" in lower and ("blocked" in lower or "attention required" in lower):
        return "hard"
    return None


def _is_blocked(page_text: str) -> bool:
    """Check if the page is showing a bot-block / challenge message."""
    return _page_block_kind(page_text) is not None


async def _wait_out_challenge(page, timeout_sec: float = 60.0) -> bool:
    """
    Poll until soft Kasada/CF challenge clears, or hard-block is confirmed.
    Returns True if page looks clear.
    """
    start = time.monotonic()
    while time.monotonic() - start < timeout_sec:
        try:
            text = await page.evaluate("document.body?.innerText || ''")
        except Exception:
            await asyncio.sleep(1.5)
            continue

        kind = _page_block_kind(text)
        if kind is None:
            # Also require that we are not stuck on empty challenge shell
            try:
                ready = await page.evaluate(
                    "Boolean(document.querySelector('input, a[href*=\"/realestateagents\"], #__NEXT_DATA__'))"
                )
            except Exception:
                ready = True
            if ready:
                return True

        if kind == "hard":
            logger.warning("🚫  Hard block page detected")
            return False

        # Soft challenge — keep waiting / nudge human signals
        logger.info("⏳  Challenge in progress — waiting for clearance …")
        try:
            await _simulate_mouse_move(page)
        except Exception:
            pass
        await asyncio.sleep(random.uniform(2.5, 4.5))

    logger.warning("⚠️  Challenge did not clear within %.0fs", timeout_sec)
    return False


# ════════════════════════════════════════════════════════════
#  URL CONSTRUCTION
# ════════════════════════════════════════════════════════════

def _build_search_url(location: str, page_num: int = 1) -> str:
    """
    Build a direct Realtor.com agent search URL.
    Format: .../realestateagents/<location>/intent-buy/sort-relevantagents/agenttype-all/pg-<page>
    """
    slug = _location_slugs(location)[0]
    base = (
        f"https://www.realtor.com/realestateagents/{slug}"
        f"/intent-buy/sort-relevantagents/agenttype-all"
    )
    return _with_pg(base, page_num)


def _with_pg(url: str, page_num: int) -> str:
    """
    Set /pg-N on a Realtor listing URL (keeps intent/sort/agenttype path).
    Examples:
      .../new-york_ny/.../pg-1
      .../new-york_ny/.../pg-2
    """
    if not url:
        return url
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url.strip())
    path = (parts.path or "").rstrip("/")
    path = re.sub(r"/pg-\d+$", "", path, flags=re.I)
    page_num = max(1, int(page_num))
    path = f"{path}/pg-{page_num}"
    return urlunsplit((parts.scheme or "https", parts.netloc, path, parts.query, ""))


def _strip_pg(url: str) -> str:
    """Listing URL without trailing /pg-N."""
    if not url:
        return url
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url.strip())
    path = re.sub(r"/pg-\d+$", "", (parts.path or "").rstrip("/"), flags=re.I)
    return urlunsplit((parts.scheme or "https", parts.netloc, path, parts.query, ""))


def _pg_from_url(url: str) -> int:
    m = re.search(r"/pg-(\d+)/?$", (url or "").split("?")[0], flags=re.I)
    return int(m.group(1)) if m else 1


async def _wait_for_selector(page, selector: str, timeout_sec: float = 20.0) -> bool:
    """Wait until a selector exists in DOM (simple polling)."""
    start = time.monotonic()
    selector_json = json.dumps(selector)
    while time.monotonic() - start < timeout_sec:
        found = await page.evaluate(
            f"document.querySelector({selector_json}) !== null"
        )
        if found:
            return True
        await asyncio.sleep(0.4)
    return False


async def _load_with_retry(page, url: str, max_attempts: int = 3) -> bool:
    """Navigate to a URL, wait out soft challenges, retry on hard blocks."""
    for attempt in range(1, max_attempts + 1):
        await page.get(url)
        await _human_delay(4.0, 7.5)

        # Give Kasada time to finish "checking your browser"
        cleared = await _wait_out_challenge(page, timeout_sec=55.0 if attempt == 1 else 40.0)
        if cleared:
            text = await page.evaluate("document.body?.innerText || ''")
            if not _is_blocked(text):
                return True

        text = await page.evaluate("document.body?.innerText || ''")
        kind = _page_block_kind(text) or "unknown"
        logger.warning(
            "🚫  Blocked on %s (attempt %s/%s, kind=%s)",
            url, attempt, max_attempts, kind,
        )
        if (
            kind == "hard"
            and platform.system() == "Linux"
            and not _realtor_proxy()
        ):
            break
        await _simulate_mouse_move(page)
        await asyncio.sleep(random.uniform(8, 14))
    return False


async def _return_to_results_page(page, url: str) -> bool:
    """Faster reload of the same listing /pg-N after a profile visit."""
    try:
        await page.get(url)
        await _human_delay(2.0, 3.5)
        await _wait_out_challenge(page, timeout_sec=25.0)
        if await _wait_for_selector(page, config.REALTOR_SELECTORS["agent_card"], 18.0):
            return True
        # One harder retry
        return await _load_with_retry(page, url, max_attempts=1)
    except Exception as e:
        logger.warning("⚠️  Return to results failed: %s", e)
        return False


async def _search_location(page, location: str) -> bool:
    """Type a location into the search bar and pick the first suggestion."""
    if not await _wait_for_selector(page, config.REALTOR_SELECTORS["search_input"], 20.0):
        logger.error("❌  Search input not found")
        return False

    loc_json = json.dumps(location)
    selector_json = json.dumps(config.REALTOR_SELECTORS["search_input"])
    await page.evaluate(
        f"""
        (() => {{
            const inp = document.querySelector({selector_json});
            if (!inp) return false;
            inp.focus();
            inp.value = '';
            const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
            setter.call(inp, '');
            inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
            return true;
        }})()
        """
    )

    for ch in location:
        ch_json = json.dumps(ch)
        await page.evaluate(
            f"""
            (() => {{
                const inp = document.querySelector({selector_json});
                if (!inp) return false;
                const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                setter.call(inp, inp.value + {ch_json});
                inp.dispatchEvent(new KeyboardEvent('keydown', {{ key: {ch_json}, bubbles: true }}));
                inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                inp.dispatchEvent(new KeyboardEvent('keyup', {{ key: {ch_json}, bubbles: true }}));
                return true;
            }})()
            """
        )
        await asyncio.sleep(random.uniform(0.05, 0.15))

    # Nudge autocomplete by adding/removing a space
    await page.evaluate(
        f"""
        (() => {{
            const inp = document.querySelector({selector_json});
            if (!inp) return false;
            const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
            setter.call(inp, inp.value + ' ');
            inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
            setter.call(inp, inp.value.trimEnd());
            inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
            return true;
        }})()
        """
    )

    if not await _wait_for_selector(page, config.REALTOR_SELECTORS["autocomplete_list"], 20.0):
        # Fallback: try hitting Enter or clicking a search button
        await page.evaluate(
            f"""
            (() => {{
                const inp = document.querySelector({selector_json});
                if (inp) {{
                    const evDown = new KeyboardEvent('keydown', {{ key: 'Enter', bubbles: true }});
                    const evUp = new KeyboardEvent('keyup', {{ key: 'Enter', bubbles: true }});
                    inp.dispatchEvent(evDown);
                    inp.dispatchEvent(evUp);
                }}
                const btn = document.querySelector('button[aria-label="Search"], button[type="submit"]');
                if (btn) btn.click();
                return true;
            }})()
            """
        )

        if not await _wait_for_selector(page, config.REALTOR_SELECTORS["agent_card"], 20.0):
            logger.error("❌  Autocomplete list not found")
            return False
        return True

    if not await _wait_for_selector(page, config.REALTOR_SELECTORS["autocomplete_item"], 15.0):
        logger.error("❌  Autocomplete list not found")
        return False

    autocomplete_json = json.dumps(config.REALTOR_SELECTORS["autocomplete_item"])
    await page.evaluate(
        f"""
        (() => {{
            const list = document.querySelectorAll({autocomplete_json});
            if (list && list.length) {{
                list[0].click();
                return true;
            }}
            return false;
        }})()
        """
    )
    await _human_delay(3.0, 6.0)
    return True


async def _select_intent_both(page) -> None:
    """Click the 'Both' intent tab before searching."""
    selector_json = json.dumps(config.REALTOR_SELECTORS["intent_both"])
    await page.evaluate(
        f"""
        (() => {{
            const links = Array.from(document.querySelectorAll({selector_json}));
            const both = links.find(el => (el.textContent || '').trim().toLowerCase() === 'both');
            if (both) {{
                both.click();
                return true;
            }}
            return false;
        }})()
        """
    )
    await _human_delay(1.0, 2.0)


async def _extract_agent_urls(page) -> list[str]:
    """Collect agent profile URLs from the current results page."""
    selector_json = json.dumps(config.REALTOR_SELECTORS["agent_card_link"])
    urls = await page.evaluate(
        f"""
        (() => {{
            const links = document.querySelectorAll({selector_json});
            const out = [];
            const seen = new Set();
            for (const a of links) {{
                const href = a.href || a.getAttribute('href');
                if (!href) continue;
                const abs = href.startsWith('http') ? href : new URL(href, window.location.origin).href;
                if (abs.includes('/realestateagents/') && !seen.has(abs)) {{
                    seen.add(abs);
                    out.push(abs);
                }}
            }}
            return out;
        }})()
        """
    )
    if not urls:
        return []

    normalized = []
    for item in urls:
        if isinstance(item, str):
            url = item
        elif isinstance(item, dict):
            url = item.get("value", "")
        else:
            url = ""
        if not url:
            continue
        if "apps.realtor.com" in url:
            continue
        if url.rstrip("/") == "https://www.realtor.com/realestateagents":
            continue
        if "/realestateagents/" not in url:
            continue
        normalized.append(url)

    # De-dupe while preserving order
    seen = set()
    result = []
    for url in normalized:
        if url in seen:
            continue
        seen.add(url)
        result.append(url)

    return result


async def _get_agent_card_count(page) -> int:
    """Return the number of agent cards visible on the results page."""
    selector_json = json.dumps(config.REALTOR_SELECTORS["agent_card"])
    count = await page.evaluate(
        f"""
        (() => {{
            const nodes = document.querySelectorAll({selector_json});
            return nodes ? nodes.length : 0;
        }})()
        """
    )
    return int(count or 0)


async def _click_agent_card(page, index: int) -> bool:
    """Click an agent card by index to open the profile page."""
    selector_json = json.dumps(config.REALTOR_SELECTORS["agent_card"])
    index_json = json.dumps(index)
    clicked = await page.evaluate(
        f"""
        (() => {{
            const cards = document.querySelectorAll({selector_json});
            if (!cards || cards.length <= {index_json}) return false;
            const card = cards[{index_json}];
            const link = card.querySelector('a[href*="/realestateagents/"]');
            if (link) {{
                link.click();
                return true;
            }}
            card.click();
            return true;
        }})()
        """
    )
    if clicked:
        await _human_delay(2.0, 4.0)
    return bool(clicked)


async def _wait_for_contact_info(page, timeout_sec: float = 35.0) -> bool:
    """Wait until the contact info section exists on the page."""
    start = time.monotonic()
    while time.monotonic() - start < timeout_sec:
        ready = await page.evaluate(
            """
            (() => !!document.querySelector('#professional-info-section'))()
            """
        )
        if ready:
            return True
        await asyncio.sleep(0.5)
    return False


def _has_contact_data(detail: dict | None) -> bool:
    """Return True when a scraped profile has any real contact fields."""
    if not detail or not isinstance(detail, dict):
        return False
    for key in ("phone", "address", "website", "name"):
        value = detail.get(key, "")
        if value and value != "N/A":
            return True
    return False


def _unwrap_dom_value(value):
    """Normalize nodriver DOM values into plain strings."""
    if isinstance(value, dict):
        if value.get("type") == "string":
            return value.get("value", "")
        return value.get("value", "") or ""
    return value


async def _get_next_page_url(page, current_url: str, next_page_num: int | None = None) -> str | None:
    """
    Build the next listing page URL.
    Prefer explicit /pg-N (reliable). Fall back to DOM next-link if needed.
    """
    target_pg = next_page_num if next_page_num is not None else (_pg_from_url(current_url) + 1)

    # 1) Construct from current listing URL (keeps intent-buy / sort / agenttype)
    if current_url and "realestateagents" in (current_url or "").lower():
        built = _with_pg(current_url, target_pg)
        if built and built.rstrip("/") != (current_url or "").rstrip("/"):
            return built

    # 2) DOM fallback
    href = await page.evaluate(
        """
        (() => {
            const sels = [
              'a.next-link',
              'a[aria-label="Go to next page"]',
              'a[aria-label="Next"]',
              'a[rel="next"]',
              'a[title="Next Page"]',
              'nav[aria-label*="agination"] a[aria-label*="ext"]',
            ];
            for (const s of sels) {
              const next = document.querySelector(s);
              if (next) {
                const h = next.getAttribute('href') || '';
                if (h) return h;
              }
            }
            // Any /pg-(current+1) link
            const links = Array.from(document.querySelectorAll('a[href*="/pg-"]'));
            for (const a of links) {
              const h = a.getAttribute('href') || '';
              if (/\\/pg-\\d+/.test(h)) return h;
            }
            return '';
        })()
        """
    )
    if not href:
        # Still return constructed URL if we have a listing base
        if current_url and "realestateagents" in current_url.lower():
            return _with_pg(current_url, target_pg)
        return None
    if href.startswith("http"):
        return _with_pg(href, target_pg) if "/pg-" in href.lower() or "realestateagents" in href.lower() else href
    if href.startswith("pg-") or href.startswith("/pg-"):
        base = _strip_pg(current_url)
        if not base.endswith("/"):
            base += "/"
        return f"{base}pg-{target_pg}"
    joined = urljoin(current_url if current_url.endswith("/") else f"{current_url}/", href)
    return _with_pg(joined, target_pg) if "realestateagents" in joined.lower() else joined



def _realtor_list_end_note(total_scraped: int, range_start: int, range_end: int) -> str:
    return (
        f"List ended ({total_scraped} agents available). "
        f"Requested range {range_start}–{range_end}."
    )


def _signal_realtor_list_ended_run(
    run_id: str | None,
    total_scraped: int,
    pages: int,
    output_path: str | None,
) -> None:
    """Mark run completed in DB immediately so UI stops showing 'running'."""
    if not run_id or total_scraped <= 0:
        return
    try:
        from datetime import datetime
        from run_registry import get_run, update_run

        cur = get_run(run_id)
        if cur and (cur.get("status") or "").lower() == "completed":
            return
        rs = max(1, int(getattr(config, "RANGE_START", 1) or 1))
        re = max(rs, int(getattr(config, "RANGE_END", rs) or rs))
        note = _realtor_list_end_note(total_scraped, rs, re)
        fname = None
        if output_path:
            fname = os.path.basename(output_path)
        elif cur:
            fname = cur.get("file_name")
        update_run(
            run_id,
            status="completed",
            scraped_agents=total_scraped,
            pages_scraped=pages,
            file_name=fname,
            error=note,
            completed_at=datetime.now().isoformat(),
        )
        logger.info(
            "✅  Run %s → completed (Realtor list ended, %s agents)",
            run_id,
            total_scraped,
        )
    except Exception as exc:
        logger.warning("Realtor list-end DB update failed for %s: %s", run_id, exc)


def _apply_realtor_list_end_stop(
    run_id: str | None,
    page: int,
    position: int,
    total_scraped: int,
    output_path: str | None,
) -> str:
    reason = (
        f"Realtor list ends at page {page} "
        f"(scraped {total_scraped}; position {position})"
    )
    logger.info("✅  %s", reason)
    _signal_realtor_list_ended_run(run_id, total_scraped, page, output_path)
    return reason


# ════════════════════════════════════════════════════════════
#  MAIN SCRAPING FLOW
# ════════════════════════════════════════════════════════════

async def _async_run_realtor(run_id: str | None = None) -> dict:
    """
    Async orchestrator for Realtor.com scraping.
    Range-based: walks result pages, counting every agent card as
    `position`. Cards before RANGE_START are counted but never
    clicked open. Cards in [RANGE_START, RANGE_END] are opened and
    scraped. Stops the instant position > RANGE_END.
    """
    browser = None
    total_scraped = 0
    page_num = 1
    output_path = None
    failure_reason = None
    list_ended = False
    stop_reason = None

    range_start = max(1, config.RANGE_START)
    range_end = max(range_start, config.RANGE_END)
    position = 0

    logger.info(f"🎯  Target range: agents {range_start}–{range_end}")

    try:
        browser = await _build_nodriver_browser()
        try:
            from run_control import bind_resource
            bind_resource(run_id, browser)
        except Exception:
            pass

        # Prove egress IP BEFORE hitting Realtor (strict on Linux VPS only).
        # Local Windows: home ISP is fine — warn on bad proxy but do not abort.
        if _realtor_proxy():
            egress = _verify_proxy_egress()
            if not egress.get("ok"):
                if platform.system() == "Linux":
                    return {
                        "total_scraped": 0,
                        "pages_scraped": 0,
                        "output_path": None,
                        "error": _kasada_vps_error(egress),
                    }
                logger.warning(
                    "⚠️  Proxy egress check failed on local — continuing on home IP. %s",
                    egress.get("detail") or "",
                )

        tab = await browser.get("about:blank")
        await asyncio.sleep(1)

        ok, open_err = await _open_realtor_results(tab, config.SEARCH_QUERY)
        if not ok:
            logger.error("❌  Could not open Realtor results: %s", open_err)
            return {
                "total_scraped": 0,
                "pages_scraped": 0,
                "output_path": None,
                "error": open_err or "Realtor.com blocked this server.",
            }

        # Anchor listing URL (with /pg-N) so pagination never depends on fragile DOM next-links
        results_list_url = _with_pg(tab.url or "", 1)
        if "realestateagents" not in results_list_url.lower():
            results_list_url = _build_search_url(config.SEARCH_QUERY, 1)
        logger.info("📌  Listing base locked: %s", results_list_url)

        # ── Main scraping loop ────────────────────────────────
        empty_pages_streak = 0
        while position < range_end:
            from run_control import is_cancelled
            if is_cancelled(run_id):
                logger.info("🛑  Run cancelled — stopping Realtor scrape")
                break

            logger.info(f"\n{'─' * 55}")
            logger.info(f"📑  Realtor Agent Page {page_num}  (position so far: {position})")

            current_results_url = _with_pg(results_list_url, page_num)
            # Always land on the exact /pg-N URL for this page
            if _pg_from_url(tab.url or "") != page_num or "realestateagents" not in (tab.url or "").lower():
                logger.info("🔗  Loading results URL: %s", current_results_url)
                ok = await _load_with_retry(tab, current_results_url, max_attempts=2)
                if not ok:
                    logger.error("❌  Could not load results page %s", page_num)
                    failure_reason = failure_reason or f"Could not load page {page_num}."
                    break
            else:
                logger.info("🔗  Results URL: %s", tab.url)

            page_text = await tab.evaluate("document.body?.innerText || ''")
            if _is_blocked(page_text):
                logger.warning(f"🚫  Blocked on results page {page_num} — retrying …")
                ok = await _load_with_retry(tab, current_results_url, max_attempts=2)
                if not ok:
                    logger.error("❌  Still blocked after retry. Stopping.")
                    failure_reason = failure_reason or "Blocked on results page."
                    break

            await _simulate_mouse_move(tab)
            await _random_scroll(tab)

            if not await _wait_for_selector(tab, config.REALTOR_SELECTORS["agent_card"], 20.0):
                empty_pages_streak += 1
                logger.warning(
                    "⚠️  Agent cards not found on page %s (empty streak %s)",
                    page_num, empty_pages_streak,
                )
                if empty_pages_streak >= 2 or page_num > 1:
                    stop_reason = _apply_realtor_list_end_stop(
                        run_id, page_num, position, total_scraped, output_path
                    )
                    list_ended = True
                    failure_reason = None
                    break
                failure_reason = failure_reason or "No agent cards found on results page."
                break

            card_count = await _get_agent_card_count(tab)
            logger.info(f"   📄  DOM: found {card_count} agent cards")

            if card_count == 0:
                empty_pages_streak += 1
                logger.warning("⚠️  No agent cards on page %s — list ended", page_num)
                stop_reason = _apply_realtor_list_end_stop(
                    run_id, page_num, position, total_scraped, output_path
                )
                list_ended = True
                failure_reason = None
                break

            empty_pages_streak = 0
            agents_on_page = []
            skipped_on_page = 0
            mid_page_break = False

            for idx in range(card_count):
                from run_control import is_cancelled
                if is_cancelled(run_id):
                    logger.info("🛑  Run cancelled — stopping card loop")
                    break

                position += 1

                if position < range_start:
                    skipped_on_page += 1
                    continue  # never click — cheap skip

                if position > range_end:
                    break

                logger.info(f"   👤  Opening card #{idx + 1} (position {position})")
                opened = await _click_agent_card(tab, idx)
                if not opened:
                    logger.warning(f"   ⚠️  Could not click card #{idx + 1}")
                    continue

                await _wait_for_selector(tab, "#professional-info-section", 35.0)
                loaded = await _wait_for_contact_info(tab, 35.0)
                if not loaded:
                    logger.warning("   ⚠️  Contact info did not load in time")

                scraped = None
                start_wait = time.monotonic()
                while time.monotonic() - start_wait < 35.0:
                    current_url = await tab.evaluate("window.location.href || ''")
                    detail = await _scrape_current_profile(tab, current_url)
                    if _has_contact_data(detail):
                        scraped = detail
                        break
                    await asyncio.sleep(0.75)

                if not scraped:
                    logger.warning("   ⚠️  Missing contact data after wait")
                else:
                    agents_on_page.append(scraped)

                # Reload the same /pg-N results page (history.back loses pagination)
                logger.info("↩️  Returning to results: %s", current_results_url)
                ok_back = await _return_to_results_page(tab, current_results_url)
                if not ok_back:
                    logger.warning("⚠️  Could not restore results page — will retry page")
                    mid_page_break = True
                    break
                await _human_delay(0.8, 1.5)

            if skipped_on_page:
                logger.info(f"   ⏭️  Skipped {skipped_on_page} agent(s) before target range on this page")

            if agents_on_page:
                output_path = save_agents(
                    agents_on_page,
                    page_num,
                    "realtor",
                    config.SEARCH_QUERY,
                    output_path,
                    run_id=run_id,
                )
                total_scraped += len(agents_on_page)
                try:
                    from run_registry import update_run
                    update_run(run_id, scraped_agents=total_scraped, pages_scraped=page_num)
                except Exception:
                    pass

                for ag in agents_on_page:
                    logger.info(f"      ✅  {ag['name']} | {ag['phone']}")

            if position >= range_end:
                logger.info(f"🎯  Reached end of range ({range_end})")
                break

            if mid_page_break:
                # Re-open same page and continue from next unscanned cards is hard;
                # advance carefully: stay on same page once more only if zero scraped this pass
                logger.warning("⚠️  Mid-page restore failed on pg-%s — moving to next page", page_num)

            # Pagination via explicit /pg-(n+1) — never rely on missing DOM next-link
            next_page = page_num + 1
            next_url = _with_pg(results_list_url, next_page)
            logger.info("➡️  Next page URL: %s", next_url)

            await _human_delay(2.0, 4.0)
            ok = await _load_with_retry(tab, next_url, max_attempts=2)
            if not ok:
                logger.error("❌  Could not load next page — stopping")
                if total_scraped > 0:
                    stop_reason = _apply_realtor_list_end_stop(
                        run_id, page_num, position, total_scraped, output_path
                    )
                    list_ended = True
                    failure_reason = None
                else:
                    failure_reason = failure_reason or "Could not load next page."
                break

            # Confirm next page has cards; if not, list truly ended
            if not await _wait_for_selector(tab, config.REALTOR_SELECTORS["agent_card"], 15.0):
                logger.info("✅  Page %s has no cards — Realtor list ended", next_page)
                stop_reason = _apply_realtor_list_end_stop(
                    run_id, page_num, position, total_scraped, output_path
                )
                list_ended = True
                failure_reason = None
                break

            page_num = next_page
            results_list_url = _with_pg(tab.url or results_list_url, page_num)
            # keep path segments from live URL (intent/sort) but store as pg-1 base
            results_list_url = _with_pg(_strip_pg(results_list_url), 1)
    except Exception as e:
        logger.error(f"💥  Error during Realtor scraping: {e}", exc_info=True)
        failure_reason = failure_reason or str(e)

    finally:
        if browser:
            try:
                browser.stop()
            except Exception:
                pass
        _stop_realtor_local_proxy()

    logger.info(
        f"\n🎉  Realtor.com scrape complete!\n"
        f"    Range        : {range_start}-{range_end}\n"
        f"    Total agents : {total_scraped}\n"
        f"    Pages scraped: {page_num}\n"
        f"    Output       : {output_path or 'none'}"
    )

    result = {
        "total_scraped": total_scraped,
        "pages_scraped": page_num,
        "output_path":   output_path,
    }
    if list_ended and total_scraped > 0:
        result["list_ended"] = True
        result["completion_note"] = (
            f"List ended ({total_scraped} agents available). "
            f"Requested range {range_start}–{range_end}."
        )
        result.pop("error", None)
    elif total_scraped == 0 and failure_reason:
        result["error"] = failure_reason
    return result

# ════════════════════════════════════════════════════════════
#  SYNC WRAPPER (called from main.py)
# ════════════════════════════════════════════════════════════

def run_realtor(driver=None, run_id: str | None = None) -> dict:
    """
    Sync entry point for Realtor.com scraping.

    NOTE: The `driver` parameter (Selenium WebDriver) is IGNORED
    because Realtor.com uses Kasada which blocks Selenium.
    We use nodriver internally instead.

    If a Selenium driver is passed, it will NOT be used.
    """
    if driver is not None:
        logger.info("ℹ️  Selenium driver provided but NOT used for Realtor.com")
        logger.info("   Kasada blocks Selenium — switching to nodriver (CDP-only)")

    return asyncio.run(_async_run_realtor(run_id=run_id))
