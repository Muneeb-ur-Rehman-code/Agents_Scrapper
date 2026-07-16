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
#  1. Launch a stealth Chrome via nodriver
#  2. Open realtor.com/realestateagents
#  3. Type location, pick first suggestion
#  4. Collect agent profile URLs from results page
#  5. Visit each profile and scrape contact info from DOM
#  6. Paginate via the next link until done
# ============================================================

import asyncio
import json
import time
import random
import logging
import re
from urllib.parse import urljoin

import nodriver as uc

import config
from data_saver import save_agents

logger = logging.getLogger("AgentScraper")


# ════════════════════════════════════════════════════════════
#  NODRIVER BROWSER SETUP
# ════════════════════════════════════════════════════════════

async def _build_nodriver_browser():
    """
    Build a stealth Chrome browser using nodriver.
    nodriver communicates directly via CDP — no WebDriver
    binary means Kasada can't detect the automation markers.

    On Linux VPS: ensure Xvfb is up when not headless, because
    cloud servers have no real display. Kasada also blocks
    headless Chrome much more often than headed+Xvfb.
    """
    import platform
    from driver_setup import start_virtual_display

    # Always prepare virtual display on Linux (safe no-op if already up)
    if platform.system() == "Linux":
        start_virtual_display()
        if config.HEADLESS:
            logger.warning(
                "⚠️  HEADLESS=true on Linux — Realtor/Kasada often blocks this. "
                "Prefer HEADLESS=false with Xvfb on the VPS."
            )

    browser_args = [
        f"--window-size={config.WINDOW_WIDTH},{config.WINDOW_HEIGHT}",
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-infobars",
        "--disable-gpu",
        "--lang=en-US,en;q=0.9",
    ]

    browser = await uc.start(
        headless=config.HEADLESS,
        browser_args=browser_args,
    )
    logger.info(
        "✅  Nodriver browser launched (stealth mode) | Headless=%s | DISPLAY=%s",
        config.HEADLESS,
        __import__("os").environ.get("DISPLAY", "(none)"),
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

def _is_blocked(page_text: str) -> bool:
    """Check if the page is showing a bot-block message."""
    block_indicators = [
        "access denied",
        "access to this page has been denied",
        "please verify you are a human",
        "checking your browser",
        "just a moment",
        "enable javascript and cookies",
        "ray id",
        "sorry, you have been blocked",
        "pardon our interruption",
        "request could not be processed",
        "reference id",
    ]
    lower = page_text.lower()[:2000]  # Only check first 2000 chars
    return any(indicator in lower for indicator in block_indicators)


# ════════════════════════════════════════════════════════════
#  URL CONSTRUCTION
# ════════════════════════════════════════════════════════════

def _build_search_url(location: str, page_num: int = 1) -> str:
    """
    Build a direct Realtor.com agent search URL.
    Format: https://www.realtor.com/realestateagents/<location>/pg-<page>
    """
    # Normalize location: "New York, NY" → "new-york_ny"
    loc = location.strip().lower()
    loc = re.sub(r'[,]+', '_', loc)       # Commas → underscores
    loc = re.sub(r'\s+', '-', loc)        # Spaces → hyphens
    loc = re.sub(r'[^a-z0-9_-]', '', loc) # Remove special chars
    loc = re.sub(r'[-]+', '-', loc)        # Collapse hyphens
    loc = re.sub(r'[_]+', '_', loc)        # Collapse underscores
    loc = loc.strip('-_')

    base = f"https://www.realtor.com/realestateagents/{loc}"
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
    """Navigate to a URL and retry if a block screen is detected."""
    for attempt in range(1, max_attempts + 1):
        await page.get(url)
        await _human_delay(3.0, 6.0)
        text = await page.evaluate("document.body?.innerText || ''")
        if not _is_blocked(text):
            return True
        logger.warning(f"🚫  Blocked on {url} (attempt {attempt}/{max_attempts})")
        await asyncio.sleep(random.uniform(6, 12))
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

    range_start = max(1, config.RANGE_START)
    range_end = max(range_start, config.RANGE_END)
    position = 0

    logger.info(f"🎯  Target range: agents {range_start}–{range_end}")

    try:
        browser = await _build_nodriver_browser()
        tab = await browser.get("about:blank")
        await asyncio.sleep(1)

        agent_search_url = config.REALTOR_AGENT_URL
        logger.info("🌐  Opening Realtor agent search page …")
        ok = await _load_with_retry(tab, agent_search_url, max_attempts=3)
        if not ok:
            logger.error("❌  Blocked on agent search page. Stopping.")
            return {
                "total_scraped": 0,
                "pages_scraped": 0,
                "output_path": None,
                "error": (
                    "Realtor.com blocked this server (Kasada). "
                    "Works on local IPs but datacenter/VPS IPs are often denied. "
                    "Try HEADLESS=false + Xvfb, or a residential proxy."
                ),
            }

        await _simulate_mouse_move(tab)
        await _random_scroll(tab)

        logger.info("🧭  Selecting intent: Both")
        await _select_intent_both(tab)

        logger.info("🔎  Entering location and selecting first suggestion …")
        if not await _search_location(tab, config.SEARCH_QUERY):
            logger.error("❌  Search failed. Stopping.")
            return {
                "total_scraped": 0,
                "pages_scraped": 0,
                "output_path": None,
                "error": "Realtor search UI failed (blocked page or selectors changed).",
            }

        # ── Main scraping loop ────────────────────────────────
        while position < range_end:
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
                break

            card_count = await _get_agent_card_count(tab)
            logger.info(f"   📄  DOM: found {card_count} agent cards")

            if card_count == 0:
                logger.warning("⚠️  No agent cards on this page — stopping")
                break

            agents_on_page = []
            skipped_on_page = 0

            for idx in range(card_count):
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

    return {
        "total_scraped": total_scraped,
        "pages_scraped": page_num,
        "output_path":   output_path,
    }

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
