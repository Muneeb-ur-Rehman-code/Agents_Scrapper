from __future__ import annotations
# ============================================================
#  realtor_scraper.py  —  Realtor.com agent scraping logic
#
#  FLOW:
#  ─────
#  1. Open https://www.realtor.com/realestateagents
#  2. Wait for first-load captcha (same PerimeterX provider)
#  3. Type location in search bar (#search-bar)
#  4. Click first autocomplete suggestion
#  5. On results page: collect agent card links
#  6. Visit each agent → scrape phone, address, website
#  7. Paginate via "Next" button until MAX_AGENTS reached
#
#  CAPTCHA STRATEGY (same as Zillow):
#  ───────────────────────────────────
#  • First-load: proactive poll for 15s
#  • After that: reactive only (when click intercepted)
# ============================================================

import time
import random
import logging

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    ElementClickInterceptedException,
    StaleElementReferenceException,
)

from config import (
    REALTOR_AGENT_URL,
    REALTOR_SELECTORS as SEL,
    BETWEEN_CARD_WAIT_MIN,
    BETWEEN_CARD_WAIT_MAX,
    RANDOM_SCROLL,
    MAX_RETRIES,
)
import config
from anti_detection import (
    human_sleep,
    human_click,
    human_type,
    random_scroll,
    scroll_to_element,
)
from captcha_handler import (
    wait_for_captcha_on_first_load,
    check_if_blocked,
    is_captcha_present,
    check_and_handle_captcha,
)
from data_saver import save_agents

logger = logging.getLogger("AgentScraper")


# ════════════════════════════════════════════════════════════
#  HELPERS
# ════════════════════════════════════════════════════════════

def _wait(driver, css: str, timeout: int = 15):
    return WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, css))
    )

def _wait_visible(driver, css: str, timeout: int = 15):
    return WebDriverWait(driver, timeout).until(
        EC.visibility_of_element_located((By.CSS_SELECTOR, css))
    )

def _wait_clickable(driver, css: str, timeout: int = 15):
    return WebDriverWait(driver, timeout).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, css))
    )


def _safe_click(driver, element, context: str = "") -> bool:
    """
    Try to click an element. If intercepted, check for captcha
    reactively. Returns True if click succeeded.
    """
    try:
        scroll_to_element(driver, element)
        human_click(driver, element)
        return True
    except ElementClickInterceptedException:
        logger.warning(f"   🚫  Click intercepted [{context}] — checking captcha")
        if check_if_blocked(driver):
            try:
                human_click(driver, element)
                return True
            except Exception:
                return False
        return False
    except Exception as e:
        logger.warning(f"   ❌  Click failed [{context}]: {e}")
        return False


# ════════════════════════════════════════════════════════════
#  STEP 1 — Navigate to Realtor.com agent search
# ════════════════════════════════════════════════════════════

def navigate_to_realtor(driver) -> None:
    """Open Realtor.com agent finder and handle first-load captcha."""
    logger.info(f"🌐  Opening Realtor.com: {REALTOR_AGENT_URL}")
    driver.get(REALTOR_AGENT_URL)
    time.sleep(3.0)

    # ── FIRST-LOAD captcha watch (the ONLY proactive poll) ────
    wait_for_captcha_on_first_load(driver, poll_secs=15)

    # Dismiss any cookie / consent banners
    _dismiss_popups(driver)

    if RANDOM_SCROLL:
        random_scroll(driver, times=1)

    logger.info("✅  Realtor.com agent search page ready")


def _dismiss_popups(driver) -> None:
    """Try to dismiss common popups (cookie consent, etc.)."""
    popup_selectors = [
        "button#onetrust-accept-btn-handler",   # OneTrust cookie banner
        "button[aria-label='Close']",
        "button[class*='close']",
        "button[class*='dismiss']",
    ]
    for sel in popup_selectors:
        try:
            btn = driver.find_element(By.CSS_SELECTOR, sel)
            if btn.is_displayed():
                btn.click()
                logger.info(f"   🗑️  Dismissed popup via: {sel}")
                human_sleep(0.5, 1.0)
        except (NoSuchElementException, ElementClickInterceptedException):
            continue


# ════════════════════════════════════════════════════════════
#  STEP 2 — Search for agents by location
# ════════════════════════════════════════════════════════════

def search_agents(driver) -> None:
    """
    Type location into Realtor.com search bar, click first
    autocomplete suggestion, verify results loaded.
    """
    logger.info(f"🔍  Searching Realtor.com agents in: '{config.SEARCH_QUERY}'")

    for attempt in range(1, MAX_RETRIES + 1):
        logger.info(f"   Search attempt {attempt}/{MAX_RETRIES}")
        try:
            # ── Find the search bar ──────────────────────────
            box = _wait_visible(driver, SEL["search_input"], timeout=10)
            logger.info("   🎯  Found search bar (#search-bar)")

            # ── Clear and type ───────────────────────────────
            scroll_to_element(driver, box)
            human_click(driver, box)
            human_sleep(0.3, 0.6)
            box.send_keys(Keys.CONTROL + "a")
            box.send_keys(Keys.DELETE)
            human_sleep(0.2, 0.4)
            human_type(box, config.SEARCH_QUERY)
            human_sleep(2.0, 3.0)   # Wait for autocomplete dropdown

            # ── Click first autocomplete suggestion ──────────
            suggestion_clicked = False
            for sel in SEL["autocomplete_item"].split(","):
                sel = sel.strip()
                try:
                    sug = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, sel))
                    )
                    logger.info(f"   💡  Autocomplete via: {sel}")
                    human_click(driver, sug)
                    suggestion_clicked = True
                    break
                except TimeoutException:
                    continue

            if not suggestion_clicked:
                logger.info("   No autocomplete — pressing Enter")
                box.send_keys(Keys.RETURN)

            time.sleep(4.0)

            # ── Verify we're on results ──────────────────────
            current_url = driver.current_url.lower()
            if ("realestateagents" in current_url and
                    len(current_url) > len(REALTOR_AGENT_URL) + 5):
                logger.info(f"✅  Realtor search submitted → {driver.current_url}")
                return

            # Check if agents are visible on page
            try:
                _wait(driver, "a[href*='/realestateagents/']", timeout=5)
                logger.info("✅  Agent results visible on page")
                return
            except TimeoutException:
                pass

        except TimeoutException:
            logger.warning(f"   Search bar not found (attempt {attempt})")
            check_if_blocked(driver)
            time.sleep(2.0)

        except Exception as e:
            logger.warning(f"   Search error (attempt {attempt}): {e}")
            check_if_blocked(driver)
            time.sleep(2.0)

    raise RuntimeError("Could not submit Realtor.com agent search after retries")


# ════════════════════════════════════════════════════════════
#  STEP 3 — Scrape agent detail page
# ════════════════════════════════════════════════════════════

def scrape_agent_details(driver) -> dict:
    """
    Extract contact info from a Realtor.com agent profile page.
    Looks for phone (tel: links), address, website (globe icon section).
    """
    data = {
        "name":        "N/A",
        "phone":       "N/A",
        "email":       "N/A",
        "address":     "N/A",
        "website":     "N/A",
        "profile_url": driver.current_url,
    }

    # Scroll to load all content
    random_scroll(driver, times=3)
    human_sleep(1.0, 2.0)

    # ── Name ─────────────────────────────────────────────────
    try:
        data["name"] = driver.find_element(
            By.CSS_SELECTOR, SEL["agent_name"]
        ).text.strip()
    except NoSuchElementException:
        pass

    # ── Phone numbers (from tel: links) ──────────────────────
    try:
        phone_els = driver.find_elements(By.CSS_SELECTOR, SEL["agent_phone"])
        phones = []
        for el in phone_els:
            href = el.get_attribute("href") or ""
            if href.startswith("tel:"):
                phone_text = el.text.strip()
                if not phone_text:
                    phone_text = href.replace("tel:", "")
                # Include the type label if next sibling has it
                try:
                    parent = el.find_element(By.XPATH, "..")
                    full_text = parent.text.strip()
                    if full_text and full_text != phone_text:
                        phones.append(full_text)
                    else:
                        phones.append(phone_text)
                except Exception:
                    phones.append(phone_text)
        if phones:
            data["phone"] = " | ".join(phones)
    except Exception:
        pass

    # ── Address (location section) ───────────────────────────
    try:
        # Try multiple approaches for address
        addr_parts = []
        for sel in SEL["agent_address"].split(","):
            sel = sel.strip()
            try:
                els = driver.find_elements(By.CSS_SELECTOR, sel)
                for el in els:
                    txt = el.text.strip()
                    if txt and "View map" not in txt:
                        addr_parts.append(txt)
                if addr_parts:
                    break
            except Exception:
                continue

        if addr_parts:
            data["address"] = ", ".join(addr_parts)
    except Exception:
        pass

    # ── Website (globe section) ──────────────────────────────
    try:
        for sel in SEL["agent_website"].split(","):
            sel = sel.strip()
            try:
                web_els = driver.find_elements(By.CSS_SELECTOR, sel)
                for el in web_els:
                    href = el.get_attribute("href") or ""
                    if href and "maps.google" not in href:
                        data["website"] = href
                        break
                if data["website"] != "N/A":
                    break
            except Exception:
                continue
    except Exception:
        pass

    return data


# ════════════════════════════════════════════════════════════
#  STEP 4 — Scrape agents from results page
# ════════════════════════════════════════════════════════════

def scrape_agents_on_page(driver, remaining: int) -> list[dict]:
    """
    Find agent cards on the current Realtor.com results page.
    Click each to go to their profile, scrape details, return.
    """
    agents_data = []

    # ── Wait for agent cards to appear ───────────────────────
    agent_links_found = False
    for sel in SEL["agent_card_link"].split(","):
        sel = sel.strip()
        try:
            _wait(driver, sel, timeout=8)
            agent_links_found = True
            break
        except TimeoutException:
            continue

    if not agent_links_found:
        logger.warning("⚠️  No agent cards found — checking captcha")
        if check_if_blocked(driver):
            time.sleep(3.0)
            for sel in SEL["agent_card_link"].split(","):
                sel = sel.strip()
                try:
                    _wait(driver, sel, timeout=8)
                    agent_links_found = True
                    break
                except TimeoutException:
                    continue

        if not agent_links_found:
            logger.error("❌  No agents found on this page")
            return agents_data

    # ── Collect unique profile URLs from agent cards ─────────
    profile_urls = []
    for sel in SEL["agent_card_link"].split(","):
        sel = sel.strip()
        try:
            links = driver.find_elements(By.CSS_SELECTOR, sel)
            for link in links:
                url = link.get_attribute("href")
                if url:
                    base = url.split("?")[0].rstrip("/")
                    if base not in profile_urls and "/realestateagents/" in base:
                        # Skip the main listing URL, only get individual agent URLs
                        # Agent URLs are typically longer (have agent name slug)
                        parts = base.replace("https://www.realtor.com/realestateagents/", "").split("/")
                        if len(parts) >= 2:  # e.g., "agent-name_12345"
                            profile_urls.append(base)
            if profile_urls:
                break
        except Exception:
            continue

    # If we couldn't find individual agent URLs, try clicking cards directly
    if not profile_urls:
        logger.info("   Trying to find agent links by card structure …")
        try:
            # Look for any links within agent card containers
            cards = driver.find_elements(By.CSS_SELECTOR,
                "div[class*='agent-list-card'] a[href],"
                "div[class*='AgentCard'] a[href],"
                "div[data-testid='component-agentCard'] a[href]"
            )
            for card in cards:
                url = card.get_attribute("href")
                if url and "/realestateagents/" in url:
                    base = url.split("?")[0].rstrip("/")
                    if base not in profile_urls:
                        profile_urls.append(base)
        except Exception:
            pass

    # Limit to what we still need
    profile_urls = profile_urls[:remaining]
    logger.info(f"👥  Found {len(profile_urls)} agent profiles to scrape on this page")

    if not profile_urls:
        return agents_data

    main_window = driver.current_window_handle

    for idx, url in enumerate(profile_urls, 1):
        logger.info(f"   👤  [{idx}/{len(profile_urls)}] Visiting: {url}")

        # Open in new tab
        driver.execute_script(f"window.open('{url}', '_blank');")
        human_sleep(0.5, 1.0)
        driver.switch_to.window(driver.window_handles[-1])

        try:
            time.sleep(2.5)
            # Reactive captcha check
            if is_captcha_present(driver):
                check_and_handle_captcha(driver, context=f"realtor_profile_{idx}")

            details = scrape_agent_details(driver)
            agents_data.append(details)
            logger.info(
                f"      ✅  {details['name']} | {details['phone']}"
            )
        except Exception as e:
            logger.error(f"      ❌  Error scraping {url}: {e}")

        # Close tab and return to results
        driver.close()
        driver.switch_to.window(main_window)
        human_sleep(BETWEEN_CARD_WAIT_MIN, BETWEEN_CARD_WAIT_MAX)

    return agents_data


# ════════════════════════════════════════════════════════════
#  STEP 5 — Pagination
# ════════════════════════════════════════════════════════════

def click_next_page(driver) -> bool:
    """
    Click the "Next" page button on Realtor.com results.
    Returns True if navigated, False if no more pages.
    """
    for sel in SEL["next_page_btn"].split(","):
        sel = sel.strip()
        try:
            btn = driver.find_element(By.CSS_SELECTOR, sel)
            if btn.is_displayed() and btn.is_enabled():
                aria = (btn.get_attribute("aria-disabled") or "").lower()
                if aria == "true":
                    continue

                logger.info(f"📄  Clicking Next page via: {sel}")
                if _safe_click(driver, btn, "realtor_next_page"):
                    time.sleep(3.0)
                    return True
        except NoSuchElementException:
            continue

    logger.info("📄  No more pages — done")
    return False


# ════════════════════════════════════════════════════════════
#  ORCHESTRATOR
# ════════════════════════════════════════════════════════════

def run_realtor(driver, run_id: str | None = None) -> dict:
    """
    Full Realtor.com agent scrape:
      1. Navigate to agent search
      2. Search by location
      3. Loop pages: scrape agents → save → next
      4. Stop when MAX_AGENTS reached or no more pages
    """
    navigate_to_realtor(driver)
    search_agents(driver)

    total_scraped = 0
    page = 1
    output_path = None

    while total_scraped < config.MAX_AGENTS:
        remaining = config.MAX_AGENTS - total_scraped

        logger.info(f"\n{'─' * 55}")
        logger.info(
                f"📑  Realtor Agent Page {page}  "
                f"({total_scraped}/{config.MAX_AGENTS} collected, need {remaining} more)"
        )

        agents = scrape_agents_on_page(driver, remaining)

        if agents:
            output_path = save_agents(
                agents,
                page,
                "realtor",
                config.SEARCH_QUERY,
                output_path,
                run_id=run_id,
            )
            total_scraped += len(agents)

        if total_scraped >= config.MAX_AGENTS:
            logger.info(f"🎯  Reached target of {config.MAX_AGENTS} agents!")
            break

        if not agents:
            logger.warning("⚠️  No agents found on this page — stopping")
            break

        if not click_next_page(driver):
            logger.info("📄  No more pages — done")
            break

        page += 1

    logger.info(
        f"\n🎉  Realtor.com scrape complete!\n"
        f"    Total agents : {total_scraped}\n"
        f"    Pages scraped: {page}\n"
        f"    Output       : {output_path or 'none'}"
    )
    return {
        "total_scraped": total_scraped,
        "pages_scraped": page,
        "output_path": output_path,
    }
