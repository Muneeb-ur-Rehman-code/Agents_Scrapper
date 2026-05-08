from __future__ import annotations
# ============================================================
#  zillow_scraper.py  —  Zillow agent scraping logic
#
#  OPTIMISED CAPTCHA STRATEGY:
#  ───────────────────────────
#  1. Only poll/wait for captcha on the FIRST page load.
#  2. After that, only check for captcha REACTIVELY when a
#     click is intercepted or an element can't be found
#     (symptoms of captcha blocking the page).
#  3. Pagination uses the real "Next page" button inside
#     <nav role="navigation" aria-label="Agent pages">.
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
    ZILLOW_AGENT_URL,
    ZILLOW_SELECTORS as SEL,
    MAX_AGENTS,
    SEARCH_QUERY,
    BETWEEN_CARD_WAIT_MIN,
    BETWEEN_CARD_WAIT_MAX,
    RANDOM_SCROLL,
    MAX_RETRIES,
)
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
    (reactive strategy). Returns True if click succeeded.
    """
    try:
        scroll_to_element(driver, element)
        human_click(driver, element)
        return True
    except ElementClickInterceptedException:
        logger.warning(f"   🚫  Click intercepted [{context}] — checking captcha")
        if check_if_blocked(driver):
            # Captcha was there and we solved it — retry the click
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
#  STEP 1 — Navigate to agent search page
# ════════════════════════════════════════════════════════════

def navigate_to_agent_search(driver) -> None:
    """Open Zillow Agent Search and handle first-load captcha."""
    logger.info(f"🌐  Opening Zillow Agent Search: {ZILLOW_AGENT_URL}")
    driver.get(ZILLOW_AGENT_URL)
    time.sleep(2.0)

    # ── FIRST-LOAD captcha watch (the ONLY proactive poll) ────
    wait_for_captcha_on_first_load(driver, poll_secs=15)

    if RANDOM_SCROLL:
        random_scroll(driver, times=2)

    logger.info("✅  Zillow agent search page ready")


# ════════════════════════════════════════════════════════════
#  STEP 2 — Search for agents by location
# ════════════════════════════════════════════════════════════

def search_agents(driver) -> None:
    """Type location into search box, submit, verify results load."""
    logger.info(f"🔍  Searching Zillow agents in: '{SEARCH_QUERY}'")

    for attempt in range(1, MAX_RETRIES + 1):
        logger.info(f"   Search attempt {attempt}/{MAX_RETRIES}")
        try:
            # ── Find search box ──────────────────────────────
            box = None
            for sel in SEL["search_input"].split(","):
                sel = sel.strip()
                try:
                    box = _wait_visible(driver, sel, timeout=7)
                    if box:
                        logger.info(f"   🎯  Search input via: {sel}")
                        break
                except TimeoutException:
                    continue

            if not box:
                # Fallback: any visible input
                for inp in driver.find_elements(By.TAG_NAME, "input"):
                    if inp.is_displayed() and inp.is_enabled():
                        box = inp
                        logger.info("   🎯  Search input via generic fallback")
                        break

            if not box:
                raise NoSuchElementException("No search input found")

            # ── Clear and type ───────────────────────────────
            scroll_to_element(driver, box)
            human_click(driver, box)
            human_sleep(0.3, 0.6)
            box.send_keys(Keys.CONTROL + "a")
            box.send_keys(Keys.DELETE)
            human_sleep(0.2, 0.4)
            human_type(box, SEARCH_QUERY)
            human_sleep(1.5, 2.5)

            # ── Submit via Enter ─────────────────────────────
            box.send_keys(Keys.RETURN)
            logger.info("   ⌨️  Pressed Enter")
            time.sleep(3.0)

            # ── Verify we landed on results ──────────────────
            if ("locationText=" in driver.current_url or
                    "location=" in driver.current_url or
                    "/profile/" in driver.page_source[:5000]):
                logger.info("✅  Zillow agent search submitted")
                return

            # ── Fallback: try button click ───────────────────
            for sel in SEL["search_btn"].split(","):
                sel = sel.strip()
                try:
                    btn = driver.find_element(By.CSS_SELECTOR, sel)
                    if btn.is_displayed():
                        _safe_click(driver, btn, "search_btn")
                        logger.info("✅  Search submitted via button")
                        time.sleep(3.0)
                        return
                except NoSuchElementException:
                    continue

        except NoSuchElementException:
            logger.warning(f"   Search input not found (attempt {attempt})")
            # Reactive captcha check — maybe captcha is blocking
            check_if_blocked(driver)
            time.sleep(2.0)

        except Exception as e:
            logger.warning(f"   Search error (attempt {attempt}): {e}")
            check_if_blocked(driver)
            time.sleep(2.0)

    raise RuntimeError("Could not submit Zillow agent search after retries")


# ════════════════════════════════════════════════════════════
#  STEP 3 — Scrape agent details from profile page
# ════════════════════════════════════════════════════════════

def scrape_agent_details(driver) -> dict:
    """Extract name, phone, email from an agent's profile page."""
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

    try:
        data["name"] = driver.find_element(
            By.CSS_SELECTOR, SEL["agent_name"]
        ).text.strip()
    except NoSuchElementException:
        pass

    # ── Phone numbers ────────────────────────────────────────
    try:
        phone_els = driver.find_elements(By.CSS_SELECTOR, SEL["agent_phone"])
        phones = []
        for el in phone_els:
            href = el.get_attribute("href") or ""
            if href.startswith("tel:"):
                phones.append(href.replace("tel:", "").strip())
        if phones:
            data["phone"] = " | ".join(phones)
    except Exception:
        pass

    # ── Email ────────────────────────────────────────────────
    try:
        email_el = driver.find_element(By.CSS_SELECTOR, SEL["agent_email"])
        data["email"] = email_el.get_attribute("href").replace("mailto:", "").strip()
    except NoSuchElementException:
        pass

    return data


# ════════════════════════════════════════════════════════════
#  STEP 4 — Scrape all agents on current results page
# ════════════════════════════════════════════════════════════

def scrape_agents_on_page(driver, remaining: int) -> list[dict]:
    """
    Find all agent profile links on the current page,
    visit each in a new tab, scrape details, return list.
    Stops after `remaining` agents.
    """
    agents_data = []

    # ── Wait for agent cards to appear ───────────────────────
    try:
        _wait(driver, SEL["agent_card_link"], timeout=10)
    except TimeoutException:
        logger.warning("⚠️  No agent links found — checking captcha")
        if check_if_blocked(driver):
            try:
                _wait(driver, SEL["agent_card_link"], timeout=10)
            except TimeoutException:
                logger.error("❌  Still no agents after captcha solve")
                return agents_data
        else:
            return agents_data

    # ── Collect unique profile URLs ──────────────────────────
    links = driver.find_elements(By.CSS_SELECTOR, SEL["agent_card_link"])
    profile_urls = []
    for link in links:
        url = link.get_attribute("href")
        if url:
            base = url.split("?")[0].rstrip("/")
            if "/profile/" in base and base not in profile_urls:
                profile_urls.append(base)

    # Limit to what we still need
    profile_urls = profile_urls[:remaining]
    logger.info(f"👥  Found {len(profile_urls)} agent profiles to scrape on this page")

    main_window = driver.current_window_handle

    for idx, url in enumerate(profile_urls, 1):
        logger.info(f"   👤  [{idx}/{len(profile_urls)}] Visiting: {url}")

        # Open in new tab
        driver.execute_script(f"window.open('{url}', '_blank');")
        human_sleep(0.5, 1.0)
        driver.switch_to.window(driver.window_handles[-1])

        try:
            time.sleep(2.0)
            # Reactive captcha check on profile page
            if is_captcha_present(driver):
                check_and_handle_captcha(driver, context=f"zillow_profile_{idx}")

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
#  STEP 5 — Pagination (using real "Next page" button)
# ════════════════════════════════════════════════════════════

def click_next_page(driver) -> bool:
    """
    Click the "Next page" button inside the pagination nav.
    Uses: nav[role="navigation"][aria-label="Agent pages"]
          → button[title="Next page"]:not([disabled])
    Returns True if navigated, False if no more pages.
    """
    try:
        nav = driver.find_element(By.CSS_SELECTOR, SEL["pagination_nav"])
    except NoSuchElementException:
        logger.info("📄  No pagination nav found — single page of results")
        return False

    try:
        next_btn = nav.find_element(By.CSS_SELECTOR, SEL["next_page_btn"])
    except NoSuchElementException:
        logger.info("📄  No 'Next page' button (or it's disabled) — done")
        return False

    if not next_btn.is_displayed() or not next_btn.is_enabled():
        logger.info("📄  Next page button not interactable — done")
        return False

    # Check aria-disabled
    aria = (next_btn.get_attribute("aria-disabled") or "").lower()
    if aria == "true":
        logger.info("📄  Next page button aria-disabled — done")
        return False

    logger.info("📄  Clicking 'Next page' …")
    if not _safe_click(driver, next_btn, "pagination_next"):
        return False

    # Wait for page to load
    time.sleep(3.0)
    return True


# ════════════════════════════════════════════════════════════
#  ORCHESTRATOR
# ════════════════════════════════════════════════════════════

def run_zillow(driver) -> None:
    """
    Full Zillow agent scrape:
      1. Navigate to agent search
      2. Search by location
      3. Loop pages: scrape agents → save → next page
      4. Stop when MAX_AGENTS reached or no more pages
    """
    navigate_to_agent_search(driver)
    search_agents(driver)

    total_scraped = 0
    page = 1
    output_path = None

    while total_scraped < MAX_AGENTS:
        remaining = MAX_AGENTS - total_scraped

        logger.info(f"\n{'─' * 55}")
        logger.info(
            f"📑  Zillow Agent Page {page}  "
            f"({total_scraped}/{MAX_AGENTS} collected, need {remaining} more)"
        )

        agents = scrape_agents_on_page(driver, remaining)

        if agents:
            output_path = save_agents(
                agents, page, "zillow", SEARCH_QUERY, output_path
            )
            total_scraped += len(agents)

        if total_scraped >= MAX_AGENTS:
            logger.info(f"🎯  Reached target of {MAX_AGENTS} agents!")
            break

        if not agents:
            logger.warning("⚠️  No agents found on this page — stopping")
            break

        if not click_next_page(driver):
            logger.info("📄  No more pages — done")
            break

        page += 1

    logger.info(
        f"\n🎉  Zillow scrape complete!\n"
        f"    Total agents : {total_scraped}\n"
        f"    Pages scraped: {page}\n"
        f"    Output       : {output_path or 'none'}"
    )