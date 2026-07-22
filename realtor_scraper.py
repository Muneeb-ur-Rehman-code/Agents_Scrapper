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


def _realtor_proxy() -> str:
    """Residential proxy for Realtor only; falls back to global PROXY_SERVER."""
    return (config.REALTOR_PROXY_SERVER or config.PROXY_SERVER or "").strip()


def _kasada_vps_error() -> str:
    """User-facing message when Kasada hard-blocks this host."""
    lines = [
        "Realtor.com blocked this server's IP (Kasada — common on VPS/datacenter hosts).",
    ]
    if platform.system() == "Linux":
        disp = os.environ.get("DISPLAY")
        if disp:
            lines.append(f"Browser display is OK ({disp}); Xvfb/HEADLESS=false is not the issue.")
        else:
            lines.append("Install xvfb and set HEADLESS=false (Realtor uses headed Chrome on Linux).")
    if not _realtor_proxy():
        lines.append(
            "Fix: add a residential proxy to .env as REALTOR_PROXY_SERVER=http://user:pass@host:port "
            "(Zillow can stay without a proxy). Restart the app and run scripts/test_realtor_vps.py."
        )
    else:
        lines.append(
            "A proxy is configured but Kasada still blocked — check credentials, "
            "use a residential (not datacenter) endpoint, or rotate the IP."
        )
    return " ".join(lines)


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

    profile_dir = os.path.join(config.CHROME_PROFILE_DIR, "realtor_nodriver")
    os.makedirs(profile_dir, exist_ok=True)

    browser_args = [
        f"--window-size={config.WINDOW_WIDTH},{config.WINDOW_HEIGHT}",
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-infobars",
        "--disable-gpu",
        "--lang=en-US,en;q=0.9",
        "--disable-features=IsolateOrigins,site-per-process",
        "--start-maximized",
    ]

    proxy = _realtor_proxy()
    if proxy:
        browser_args.append(f"--proxy-server={proxy}")
        masked = proxy.split("@")[-1] if "@" in proxy else proxy
        logger.info("🌐  Realtor proxy enabled: %s", masked)
    elif platform.system() == "Linux":
        logger.warning(
            "⚠️  No REALTOR_PROXY_SERVER — Realtor.com often hard-blocks bare VPS IPs."
        )

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
        direct = f"https://www.realtor.com/realestateagents/{slug}"
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
        logger.warning("⚠️  Direct URL loaded but results not detected (%s)", slug)

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
    Format: https://www.realtor.com/realestateagents/<location>/pg-<page>
    """
    slug = _location_slugs(location)[0]
    base = f"https://www.realtor.com/realestateagents/{slug}"
    if page_num > 1:
        base += f"/pg-{page_num}"
    return base


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


async def _get_next_page_url(page, current_url: str) -> str | None:
    """Find the next page URL from pagination controls."""
    href = await page.evaluate(
        """
        (() => {
            const next = document.querySelector('a.next-link, a[aria-label="Go to next page"], a[rel="next"]');
            return next ? (next.getAttribute('href') || '') : '';
        })()
        """
    )
    if not href:
        return None
    if href.startswith("http"):
        return href
    if href.startswith("pg-"):
        base = current_url
        m = re.search(r"/pg-\d+/?$", base)
        if m:
            base = base[:m.start()]
        if not base.endswith("/"):
            base += "/"
        return f"{base}{href}"
    return urljoin(current_url if current_url.endswith("/") else f"{current_url}/", href)


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

        # ── Main scraping loop ────────────────────────────────
        while position < range_end:
            from run_control import is_cancelled
            if is_cancelled(run_id):
                logger.info("🛑  Run cancelled — stopping Realtor scrape")
                break

            logger.info(f"\n{'─' * 55}")
            logger.info(f"📑  Realtor Agent Page {page_num}  (position so far: {position})")

            current_results_url = tab.url
            logger.info(f"🔗  Results URL: {current_results_url}")

            page_text = await tab.evaluate("document.body?.innerText || ''")
            if _is_blocked(page_text):
                logger.warning(f"🚫  Blocked on results page {page_num} — retrying …")
                ok = await _load_with_retry(tab, current_results_url, max_attempts=2)
                if not ok:
                    logger.error("❌  Still blocked after retry. Stopping.")
                    break

            await _simulate_mouse_move(tab)
            await _random_scroll(tab)

            if not await _wait_for_selector(tab, config.REALTOR_SELECTORS["agent_card"], 20.0):
                logger.warning("⚠️  Agent cards not found — stopping")
                failure_reason = failure_reason or "No agent cards found on results page."
                break

            card_count = await _get_agent_card_count(tab)
            logger.info(f"   📄  DOM: found {card_count} agent cards")

            if card_count == 0:
                logger.warning("⚠️  No agent cards on this page — stopping")
                break

            agents_on_page = []
            skipped_on_page = 0

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

                # Return to results before continuing to next index
                await tab.evaluate("history.back()")
                await _human_delay(2.5, 4.5)
                if not await _wait_for_selector(tab, config.REALTOR_SELECTORS["agent_card"], 20.0):
                    logger.warning("⚠️  Agent cards not found after back navigation")
                    break

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

            # Pagination
            next_url = await _get_next_page_url(tab, current_results_url)
            if not next_url:
                logger.info("✅  No next page link found — stopping")
                break

            await _human_delay(2.5, 5.5)
            ok = await _load_with_retry(tab, next_url, max_attempts=2)
            if not ok:
                logger.error("❌  Could not load next page — stopping")
                break

            page_num += 1

    except Exception as e:
        logger.error(f"💥  Error during Realtor scraping: {e}", exc_info=True)

    finally:
        if browser:
            try:
                browser.stop()
            except Exception:
                pass

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
    if total_scraped == 0 and failure_reason:
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
