from __future__ import annotations
# ============================================================
#  zillow_scraper.py  —  Zillow agent scraping logic
#
#  OPTIMISED CAPTCHA STRATEGY:
#  ───────────────────────────
#  1. On first page load, check captcha immediately (no fixed wait).
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
    WebDriverException,
)

from config import (
    ZILLOW_AGENT_URL,
    ZILLOW_SELECTORS as SEL,
    MAX_RETRIES,
)
import config
from anti_detection import (
    human_sleep,
    human_click,
    scroll_to_element,
)
from captcha_handler import (
    handle_first_load_captcha_if_present,
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


def _wait_page_ready(driver, timeout: int = 10) -> None:
    WebDriverWait(driver, timeout).until(
        lambda d: d.execute_script("return document.readyState") == "complete"
    )


def _results_visible(driver) -> bool:
    try:
        links = driver.find_elements(By.CSS_SELECTOR, SEL["agent_card_link"])
        return any(link.is_displayed() for link in links)
    except Exception:
        return False


def _query_in_results_url(driver) -> bool:
    try:
        current_url = (driver.current_url or "").lower()
    except Exception:
        return False

    if "locationtext=" not in current_url and "location=" not in current_url:
        return False

    query = (config.SEARCH_QUERY or "").strip().lower()
    if not query:
        return False

    variants = [
        query,
        query.replace(" ", "+"),
        query.replace(" ", "%20"),
        query.replace(" ", "-"),
    ]
    return any(v in current_url for v in variants)


def _visible_profile_urls(driver, limit: int = 12) -> list[str]:
    urls = []
    try:
        links = driver.find_elements(By.CSS_SELECTOR, SEL["agent_card_link"])
        for link in links:
            if not link.is_displayed():
                continue
            href = (link.get_attribute("href") or "").split("?")[0].rstrip("/")
            if href and "/profile/" in href and href not in urls:
                urls.append(href)
            if len(urls) >= limit:
                break
    except Exception:
        return []
    return urls


def _pagination_readout(driver) -> str:
    try:
        text = driver.find_element(
            By.CSS_SELECTOR,
            "li[aria-current='page'] span, span.Text-c11n-8-111-2__sc-aiai24-0",
        ).text.strip()
        return text
    except Exception:
        return ""


def _wait_for_results(driver, timeout: int = 10, baseline_urls: list[str] | None = None) -> bool:
    end = time.time() + timeout
    baseline = baseline_urls or []
    fresh_seen_at = None

    while time.time() < end:
        try:
            if is_captcha_present(driver):
                logger.warning("   ⚠️  Captcha appeared after search submit — solving before scraping")
                check_and_handle_captcha(driver, context="zillow_search_submit")
                continue

            urls_now = _visible_profile_urls(driver)
            changed = bool(urls_now) and urls_now != baseline
            query_in_url = _query_in_results_url(driver)

            if urls_now and (changed or query_in_url):
                if fresh_seen_at is None:
                    fresh_seen_at = time.time()
                elif time.time() - fresh_seen_at >= 0.2:
                    return True
            else:
                fresh_seen_at = None
        except WebDriverException:
            fresh_seen_at = None
        time.sleep(0.2)
    return False


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

    # ── First-load captcha check (no proactive wait loop) ──────
    handle_first_load_captcha_if_present(driver, context="zillow_first_load")

    logger.info("✅  Zillow agent search page ready")


# ════════════════════════════════════════════════════════════
#  STEP 2 — Search for agents by location
# ════════════════════════════════════════════════════════════

def search_agents(driver) -> None:
    """Type location into search box, submit, verify results load."""
    logger.info(f"🔍  Searching Zillow agents in: '{config.SEARCH_QUERY}'")
    if is_captcha_present(driver):
        logger.warning("   ⚠️  Captcha on search page — solving before typing")
        check_and_handle_captcha(driver, context="zillow_search_start")

    for attempt in range(1, MAX_RETRIES + 1):
        logger.info(f"   Search attempt {attempt}/{MAX_RETRIES}")
        try:
            # ── Find search box ──────────────────────────────
            box = _wait_visible(driver, SEL["search_input"], timeout=8)
            logger.info("   🎯  Search input ready")

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
            box.send_keys(Keys.CONTROL + "a")
            box.send_keys(Keys.DELETE)
            box.send_keys(config.SEARCH_QUERY)
            baseline_urls = _visible_profile_urls(driver)

            # ── Submit via Enter ─────────────────────────────
            box.send_keys(Keys.RETURN)
            logger.info("   ⌨️  Pressed Enter")
            if _wait_for_results(driver, timeout=10, baseline_urls=baseline_urls):
                logger.info("✅  Zillow results ready")
                return

            if check_if_blocked(driver):
                if _results_visible(driver) and _query_in_results_url(driver):
                    logger.info("✅  Zillow results visible after captcha")
                    return
                if _wait_for_results(driver, timeout=8, baseline_urls=baseline_urls):
                    logger.info("✅  Zillow results ready after captcha")
                    return

            # ── Fallback: try button click ───────────────────
            for sel in SEL["search_btn"].split(","):
                sel = sel.strip()
                try:
                    btn = driver.find_element(By.CSS_SELECTOR, sel)
                    if btn.is_displayed():
                        _safe_click(driver, btn, "search_btn")
                        logger.info("✅  Search submitted via button")
                        if _wait_for_results(driver, timeout=8, baseline_urls=baseline_urls):
                            logger.info("✅  Zillow results ready")
                            return
                        if check_if_blocked(driver) and _wait_for_results(driver, timeout=8, baseline_urls=baseline_urls):
                            logger.info("✅  Zillow results ready after captcha")
                            return
                except NoSuchElementException:
                    continue

        except NoSuchElementException:
            if _results_visible(driver) and _query_in_results_url(driver):
                logger.info("✅  Zillow results already visible")
                return
            logger.warning(f"   Search input not found (attempt {attempt})")
            # Reactive captcha check — maybe captcha is blocking
            check_if_blocked(driver)
            time.sleep(0.5)

        except Exception as e:
            if _results_visible(driver) and _query_in_results_url(driver):
                logger.info("✅  Zillow results already visible")
                return
            logger.warning(f"   Search error (attempt {attempt}): {e}")
            check_if_blocked(driver)
            if _results_visible(driver) and _query_in_results_url(driver):
                logger.info("✅  Zillow results visible after retry")
                return
            time.sleep(0.5)

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

def scrape_agents_on_page(driver, remaining: int, seen_profile_urls: set[str] | None = None) -> list[dict]:
    """
    Find all agent profile links on the current page,
    visit each in a new tab, scrape details, return list.
    Stops after `remaining` agents.
    """
    agents_data = []

    # Never start opening agent profiles while results page captcha is active.
    if is_captcha_present(driver):
        logger.warning("⚠️  Captcha on results page before scraping — solving now")
        check_and_handle_captcha(driver, context="zillow_results_pre_scrape")
        time.sleep(1.0)
        if is_captcha_present(driver):
            logger.warning("⚠️  Captcha still active on results page — skipping this page")
            return agents_data

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
    seen = seen_profile_urls if seen_profile_urls is not None else set()
    for link in links:
        url = link.get_attribute("href")
        if url:
            base = url.split("?")[0].rstrip("/")
            if "/profile/" in base and base not in profile_urls and base not in seen:
                profile_urls.append(base)

    # Limit to what we still need
    profile_urls = profile_urls[:remaining]
    logger.info(f"👥  Found {len(profile_urls)} agent profiles to scrape on this page")

    main_window = driver.current_window_handle

    for idx, url in enumerate(profile_urls, 1):
        if is_captcha_present(driver):
            logger.warning("   ⚠️  Captcha appeared before opening profile — solving")
            check_and_handle_captcha(driver, context=f"zillow_results_before_open_{idx}")
            if is_captcha_present(driver):
                logger.warning("   ⚠️  Captcha still active on results page — stopping page scrape")
                break

        logger.info(f"   👤  [{idx}/{len(profile_urls)}] Visiting: {url}")
        seen.add(url)

        # Open in new tab
        driver.execute_script(f"window.open('{url}', '_blank');")
        driver.switch_to.window(driver.window_handles[-1])

        try:
            _wait_page_ready(driver, timeout=10)
            if is_captcha_present(driver):
                check_and_handle_captcha(driver, context=f"zillow_profile_{idx}")
            details = scrape_agent_details(driver)
            agents_data.append(details)
            logger.info(
                f"      ✅  {details['name']} | {details['phone']}"
            )
        except Exception as e:
            logger.warning(f"      ⚠️  Profile scrape error: {e}")
            if check_if_blocked(driver):
                try:
                    _wait_page_ready(driver, timeout=10)
                    details = scrape_agent_details(driver)
                    agents_data.append(details)
                    logger.info(
                        f"      ✅  {details['name']} | {details['phone']}"
                    )
                except Exception as retry_err:
                    logger.error(f"      ❌  Error scraping {url} after captcha solve: {retry_err}")
            else:
                logger.error(f"      ❌  Error scraping {url}: {e}")

        # Close tab and return to results
        driver.close()
        driver.switch_to.window(main_window)

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
    current_url = driver.current_url
    prev_readout = _pagination_readout(driver)
    prev_urls = _visible_profile_urls(driver, limit=6)

    if not _safe_click(driver, next_btn, "pagination_next"):
        return False

    try:
        WebDriverWait(driver, 8).until(
            lambda d: (
                d.current_url != current_url
                or (_pagination_readout(d) and _pagination_readout(d) != prev_readout)
                or (_visible_profile_urls(d, limit=6) and _visible_profile_urls(d, limit=6) != prev_urls)
            )
        )
    except TimeoutException:
        logger.warning("📄  Next page did not change — staying on current page")
        return False
    return True


# ════════════════════════════════════════════════════════════
#  ORCHESTRATOR
# ════════════════════════════════════════════════════════════

def run_zillow(driver, run_id: str | None = None) -> dict:
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
    seen_profile_urls: set[str] = set()

    while total_scraped < config.MAX_AGENTS:
        remaining = config.MAX_AGENTS - total_scraped

        logger.info(f"\n{'─' * 55}")
        logger.info(
                f"📑  Zillow Agent Page {page}  "
                f"({total_scraped}/{config.MAX_AGENTS} collected, need {remaining} more)"
        )

        agents = scrape_agents_on_page(driver, remaining, seen_profile_urls=seen_profile_urls)

        if agents:
            output_path = save_agents(
                agents,
                page,
                "zillow",
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
        f"\n🎉  Zillow scrape complete!\n"
        f"    Total agents : {total_scraped}\n"
        f"    Pages scraped: {page}\n"
        f"    Output       : {output_path or 'none'}"
    )
    return {
        "total_scraped": total_scraped,
        "pages_scraped": page,
        "output_path": output_path,
    }
