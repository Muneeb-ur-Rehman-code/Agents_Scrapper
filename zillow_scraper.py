from __future__ import annotations
# ============================================================
#  zillow_scraper.py  —  Zillow agent scraping logic
#
#  RANGE-BASED SCRAPING:
#  ──────────────────────
#  Instead of "give me N agents", the caller sets
#  config.RANGE_START / config.RANGE_END (e.g. 1-100, then
#  101-200 next run). After search we use 16 agents/page and
#  FAST-SKIP by opening the results URL with ?page=N directly
#  (e.g. .../new-york-ny/?page=32 for agent 500).
#  Cards before RANGE_START are counted but NEVER opened.
#  Cards inside the range are opened + scraped normally.
#  We stop the instant position > RANGE_END, even mid-page.
#
#  CAPTCHA STRATEGY (unchanged):
#  ───────────────────────────
#  1. On first page load, check captcha immediately (no fixed wait).
#  2. After that, only check for captcha REACTIVELY when a
#     click is intercepted or an element can't be found.
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
from browser_errors import humanize_browser_error, is_dead_browser_error
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

# One bad profile must not freeze the run. Hard cap — skip if stuck.
_PROFILE_BUDGET_SECS = 35


# ════════════════════════════════════════════════════════════
#  HELPERS
# ════════════════════════════════════════════════════════════

def _safe_displayed(el) -> bool:
    if el is None:
        return False
    try:
        return bool(el.is_displayed())
    except Exception:
        return False


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
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
    except TimeoutException:
        # Partial load is OK — scrape whatever is already painted
        pass


def _results_visible(driver) -> bool:
    try:
        links = driver.find_elements(By.CSS_SELECTOR, SEL["agent_card_link"])
        return any(_safe_displayed(link) for link in links if link is not None)
    except Exception:
        return False


def _query_in_results_url(driver) -> bool:
    """
    Check whether the current URL reflects a successful search for
    config.SEARCH_QUERY. Zillow uses two different URL styles
    depending on how the search was submitted:
      - Query-string style: ?locationText=new+york
      - Path style:          /real-estate-agent-reviews/new-york/
    (Path style is common right after a captcha-solve redirect.)
    """
    try:
        current_url = (driver.current_url or "").lower()
    except Exception:
        return False

    query = (config.SEARCH_QUERY or "").strip().lower()
    if not query:
        return False

    # Query-string style: ?locationText=new+york
    if "locationtext=" in current_url or "location=" in current_url:
        variants = [
            query,
            query.replace(" ", "+"),
            query.replace(" ", "%20"),
            query.replace(" ", "-"),
        ]
        if any(v in current_url for v in variants):
            return True

    # Path style: /real-estate-agent-reviews/new-york/
    slug = query.replace(",", "").replace(" ", "-")
    if slug and slug in current_url:
        return True

    return False


def _visible_profile_urls(driver, limit: int = 12) -> list[str]:
    urls = []
    try:
        links = driver.find_elements(By.CSS_SELECTOR, SEL["agent_card_link"])
        for link in links:
            if link is None or not _safe_displayed(link):
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
    """
    Poll for search results to appear. If a captcha shows up mid-wait,
    solve it and give the function a FRESH timeout window afterward —
    otherwise the captcha's own solve time (which can be 15-25s) eats
    the whole budget and we'd return False even after solving it.
    """
    end = time.time() + timeout
    baseline = baseline_urls or []
    fresh_seen_at = None

    while time.time() < end:
        try:
            if is_captcha_present(driver):
                logger.warning("   ⚠️  Captcha appeared after search submit — solving before scraping")
                check_and_handle_captcha(driver, context="zillow_search_submit")
                end = time.time() + timeout  # reset window — solving already used up time
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


def open_zillow_results(driver) -> bool:
    """
    Prefer opening results via direct location URL (works better on VPS):
      https://www.zillow.com/professionals/real-estate-agent-reviews/new-york-ny/
    Falls back to False so caller can use the search-box UI.
    """
    slug = _zillow_location_slug(config.SEARCH_QUERY)
    if not slug:
        return False

    url = (
        "https://www.zillow.com/professionals/real-estate-agent-reviews/"
        f"{slug}/"
    )
    logger.info("🌐  Direct Zillow results URL: %s", url)
    try:
        driver.get(url)
        human_sleep(1.2, 2.0)
        handle_first_load_captcha_if_present(driver, context="zillow_direct_results")
        if is_captcha_present(driver):
            check_and_handle_captcha(driver, context="zillow_direct_results", max_attempts=2)
        if wait_for_agent_cards(driver):
            logger.info("✅  Zillow results ready via direct URL")
            return True
        # Soft wait — SPA sometimes paints cards a bit later
        if _wait_for_results(driver, timeout=12):
            logger.info("✅  Zillow results ready via direct URL (delayed)")
            return True
    except Exception as e:
        logger.warning("⚠️  Direct results URL failed: %s", e)
    return False


# ════════════════════════════════════════════════════════════
#  STEP 2 — Search for agents by location
# ════════════════════════════════════════════════════════════

def search_agents(driver) -> None:
    """Type location into search box, submit, verify results load."""
    logger.info(f"🔍  Searching Zillow agents in: '{config.SEARCH_QUERY}'")
    if is_captcha_present(driver):
        logger.warning("   ⚠️  Captcha on search page — solving before typing")
        check_and_handle_captcha(driver, context="zillow_search_start")

    # Extra attempts help when a parallel scrape is also hitting Zillow
    attempts = max(MAX_RETRIES, 4)
    for attempt in range(1, attempts + 1):
        logger.info(f"   Search attempt {attempt}/{attempts}")
        try:
            # ── Find search box ──────────────────────────────
            box = _wait_visible(driver, SEL["search_input"], timeout=10)
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
            if _wait_for_results(driver, timeout=14, baseline_urls=baseline_urls):
                logger.info("✅  Zillow results ready")
                return

            if check_if_blocked(driver):
                if _results_visible(driver):
                    logger.info("✅  Zillow results visible after captcha")
                    return
                if _wait_for_results(driver, timeout=10, baseline_urls=baseline_urls):
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
                        if _wait_for_results(driver, timeout=10, baseline_urls=baseline_urls):
                            logger.info("✅  Zillow results ready")
                            return
                        if check_if_blocked(driver) and _wait_for_results(driver, timeout=10, baseline_urls=baseline_urls):
                            logger.info("✅  Zillow results ready after captcha")
                            return
                except NoSuchElementException:
                    continue

        except NoSuchElementException:
            if _results_visible(driver):
                logger.info("✅  Zillow results already visible")
                return
            logger.warning(f"   Search input not found (attempt {attempt})")
            # Reactive captcha check — maybe captcha is blocking
            check_if_blocked(driver)
            if attempt < attempts:
                try:
                    driver.get(ZILLOW_AGENT_URL)
                    human_sleep(1.2, 2.0)
                    handle_first_load_captcha_if_present(driver, context="zillow_search_reload")
                except Exception:
                    pass
            time.sleep(0.8)

        except Exception as e:
            if _results_visible(driver):
                logger.info("✅  Zillow results already visible")
                return
            logger.warning(f"   Search error (attempt {attempt}): {e}")
            check_if_blocked(driver)
            if _results_visible(driver):
                logger.info("✅  Zillow results visible after retry")
                return
            if attempt < attempts:
                try:
                    driver.get(ZILLOW_AGENT_URL)
                    human_sleep(1.2, 2.0)
                    handle_first_load_captcha_if_present(driver, context="zillow_search_reload")
                except Exception:
                    pass
            time.sleep(0.8)

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

    # ── Phone (primary / top tel only — skip lower landline/office) ──
    try:
        phone_els = driver.find_elements(By.CSS_SELECTOR, SEL["agent_phone"])
        primary = None
        for el in phone_els:
            if not _safe_displayed(el):
                continue
            href = (el.get_attribute("href") or "").strip()
            if not href.lower().startswith("tel:"):
                continue
            text = (el.text or "").strip()
            number = text if text else href.split(":", 1)[-1].strip()
            if number:
                primary = number
                break  # upper / first tel: only
        if primary:
            data["phone"] = primary
    except Exception:
        pass

    # Fallback: visible phone text (some profiles render number without tel:)
    if data["phone"] == "N/A":
        try:
            import re as _re
            body = driver.find_element(By.TAG_NAME, "body").text or ""
            m = _re.search(r"\(\d{3}\)\s*\d{3}[-.\s]?\d{4}", body)
            if m:
                data["phone"] = m.group(0).strip()
        except Exception:
            pass

    # ── Email ────────────────────────────────────────────────
    try:
        email_el = driver.find_element(By.CSS_SELECTOR, SEL["agent_email"])
        href = (email_el.get_attribute("href") or "").strip()
        data["email"] = href.replace("mailto:", "").strip() or "N/A"
    except NoSuchElementException:
        pass

    return data


# ════════════════════════════════════════════════════════════
#  STEP 4 — Page-level card handling (range logic)
# ════════════════════════════════════════════════════════════

def wait_for_agent_cards(driver, retries: int = 3) -> bool:
    """
    Handle captcha and wait until agent cards are present.
    Retries with captcha solve + soft reload — parallel VPS runs often
    briefly lose the results DOM; one miss must not abort the whole range.
    """
    for attempt in range(1, max(1, retries) + 1):
        try:
            if is_captcha_present(driver):
                logger.warning(
                    "⚠️  Captcha on results page — solving (attempt %s/%s)",
                    attempt, retries,
                )
                check_and_handle_captcha(
                    driver,
                    context=f"zillow_results_pre_scrape_{attempt}",
                    max_attempts=2,
                )
                human_sleep(0.8, 1.4)

            if _results_visible(driver):
                return True

            try:
                _wait(driver, SEL["agent_card_link"], timeout=12)
                if _results_visible(driver) or driver.find_elements(
                    By.CSS_SELECTOR, SEL["agent_card_link"]
                ):
                    return True
            except TimeoutException:
                pass

            logger.warning(
                "⚠️  No agent cards yet (attempt %s/%s) — captcha/reload",
                attempt, retries,
            )
            check_if_blocked(driver)

            if attempt < retries:
                # Soft refresh of current results URL / page
                try:
                    cur = driver.current_url or ""
                    page_num = _read_current_page_number(driver)
                    if page_num and page_num > 1:
                        _goto_page_via_url(driver, page_num)
                    elif cur:
                        driver.get(cur)
                        human_sleep(1.0, 1.8)
                        handle_first_load_captcha_if_present(
                            driver, context=f"zillow_cards_reload_{attempt}"
                        )
                    else:
                        driver.refresh()
                        human_sleep(1.0, 1.8)
                except Exception as e:
                    logger.warning("⚠️  Results reload failed: %s", e)
                human_sleep(0.6, 1.0)
        except WebDriverException as e:
            if is_dead_browser_error(e):
                logger.error("❌  Browser dead while waiting for cards: %s", e)
                return False
            logger.warning("⚠️  wait_for_agent_cards error: %s", e)
            human_sleep(0.5, 0.9)

    logger.error("❌  Still no agent cards after %s attempts", retries)
    return False


def get_page_profile_urls(driver, seen_profile_urls: set[str]) -> list[str]:
    """
    Return every NEW agent profile URL on the current page, in the
    order they appear in the DOM. Does NOT visit any of them — this
    is the cheap "counting" pass used to figure out position.
    """
    links = driver.find_elements(By.CSS_SELECTOR, SEL["agent_card_link"])
    urls = []
    for link in links:
        url = link.get_attribute("href")
        if not url:
            continue
        base = url.split("?")[0].rstrip("/")
        if "/profile/" in base and base not in urls and base not in seen_profile_urls:
            urls.append(base)
    return urls


def scrape_selected_profiles(
    driver,
    urls: list[str],
    seen_profile_urls: set[str],
) -> list[dict]:
    """
    Open each profile in a new tab and scrape details.
    Hard per-profile budget; never pyautogui-solve captcha on profile tabs
    (that can mis-click Next on results); restore results page if it drifts.

    Only successful scrapes are marked seen. Failures get one retry so
    parallel/captcha glitches do not silently drop agents from the range.
    """
    agents_data = []
    try:
        main_window = driver.current_window_handle
    except WebDriverException:
        logger.error("   ❌  Browser session already dead before profile scrape")
        return agents_data

    results_page = _read_current_page_number(driver) or 1
    try:
        results_url = driver.current_url
    except Exception:
        results_url = None

    def _recover_main() -> bool:
        nonlocal main_window
        try:
            handles = driver.window_handles
            if not handles:
                return False
            if main_window in handles:
                driver.switch_to.window(main_window)
            else:
                main_window = handles[0]
                driver.switch_to.window(main_window)
            return True
        except Exception:
            return False

    def _restore_results_page() -> None:
        try:
            actual = _read_current_page_number(driver)
            if actual is not None and actual != results_page:
                logger.warning(
                    "📄  Results page drifted %s→%s during profiles — restoring page %s",
                    actual, results_page, results_page,
                )
                if not _goto_page_via_url(driver, results_page):
                    if results_url:
                        driver.get(results_url)
                        human_sleep(0.8, 1.2)
                        wait_for_agent_cards(driver)
        except Exception as e:
            logger.warning("📄  Could not restore results page: %s", e)

    def _scrape_one(url: str, idx: int, total: int) -> dict | None:
        if not _recover_main():
            logger.error("   ❌  No browser windows left — stopping")
            return None
        _restore_results_page()

        try:
            if is_captcha_present(driver):
                logger.warning("   ⚠️  Captcha on results — solving before profile")
                check_and_handle_captcha(
                    driver,
                    context=f"zillow_results_before_open_{idx}",
                    max_attempts=2,
                )
                if is_captcha_present(driver):
                    logger.warning("   ⚠️  Captcha still active — will retry later")
                    return None
        except Exception as e:
            logger.warning("   ⚠️  Captcha check error (%s) — continuing", e)

        logger.info(f"   👤  [{idx}/{total}] Visiting: {url}")
        started = time.time()
        opened_tab = False
        details = None

        try:
            driver.execute_script("window.open(arguments[0], '_blank');", url)
            driver.switch_to.window(driver.window_handles[-1])
            opened_tab = True
        except Exception as e:
            logger.warning("   ⚠️  Could not open profile tab (%s) — skip", e)
            _recover_main()
            return None

        try:
            if time.time() - started > _PROFILE_BUDGET_SECS:
                raise TimeoutError("profile budget exceeded before ready")

            _wait_page_ready(driver, timeout=8)

            # Never pyautogui-solve on profile tabs — can click Next on results underneath
            if is_captcha_present(driver):
                raise RuntimeError("captcha on profile — retry later")

            if time.time() - started > _PROFILE_BUDGET_SECS:
                raise TimeoutError("profile budget exceeded")

            details = scrape_agent_details(driver)
            logger.info(f"      ✅  {details['name']} | {details['phone']}")
        except Exception as e:
            logger.warning(f"      ⚠️  Profile scrape skip: {e}")
            details = None

        if opened_tab:
            try:
                driver.close()
            except Exception:
                logger.warning("      ⚠️  Could not close profile tab")
        if not _recover_main():
            return details
        _restore_results_page()
        human_sleep(0.25, 0.55)
        return details

    pending = list(urls)
    failed: list[str] = []

    for idx, url in enumerate(pending, 1):
        try:
            from run_control import is_cancelled
            if is_cancelled(getattr(config, "RUN_ID", None)):
                logger.info("   🛑  Cancelled — stopping profile scrape")
                failed.extend(pending[idx - 1:])
                break
        except Exception:
            pass

        details = _scrape_one(url, idx, len(pending))
        if details:
            agents_data.append(details)
            seen_profile_urls.add(url)
        else:
            failed.append(url)

    # One retry pass for misses (captcha / timeout / tab glitch)
    if failed:
        logger.info("   🔁  Retrying %s failed profile(s)…", len(failed))
        still_failed: list[str] = []
        for idx, url in enumerate(failed, 1):
            try:
                from run_control import is_cancelled
                if is_cancelled(getattr(config, "RUN_ID", None)):
                    still_failed.extend(failed[idx - 1:])
                    break
            except Exception:
                pass
            details = _scrape_one(url, idx, len(failed))
            if details:
                agents_data.append(details)
                seen_profile_urls.add(url)
            else:
                still_failed.append(url)
                # Mark seen after final failure so pagination can advance
                seen_profile_urls.add(url)
                logger.warning("   ❌  Gave up on profile after retry: %s", url)
        if still_failed:
            logger.warning(
                "   ⚠️  %s profile(s) still missing after retry",
                len(still_failed),
            )

    _recover_main()
    _restore_results_page()
    return agents_data


def _list_page_profile_urls(driver) -> list[str]:
    """Unique visible /profile/ links on the current results page."""
    urls: list[str] = []
    try:
        links = driver.find_elements(By.CSS_SELECTOR, SEL["agent_card_link"])
        for link in links:
            try:
                if not _safe_displayed(link):
                    continue
            except Exception:
                pass
            url = link.get_attribute("href") or ""
            if not url:
                continue
            base = url.split("?")[0].rstrip("/")
            if "/profile/" in base and base not in urls:
                urls.append(base)
    except Exception:
        pass
    return urls


def _read_current_page_number(driver) -> int | None:
    """Best-effort current page index from pagination UI."""
    try:
        el = driver.find_element(
            By.CSS_SELECTOR,
            "nav[role='navigation'][aria-label='Agent pages'] li[aria-current='page'], "
            "nav[aria-label='Agent pages'] [aria-current='page']",
        )
        text = (el.text or "").strip()
        if text.isdigit():
            return int(text)
    except Exception:
        pass
    readout = _pagination_readout(driver)
    if readout:
        import re as _re
        m = _re.search(r"(\d+)", readout)
        if m:
            return int(m.group(1))
    return None


def _visible_page_numbers(driver) -> list[int]:
    """Numeric page buttons currently shown in the Agent pages nav."""
    nums: list[int] = []
    try:
        nav = driver.find_element(By.CSS_SELECTOR, SEL["pagination_nav"])
    except NoSuchElementException:
        return nums
    for el in nav.find_elements(By.CSS_SELECTOR, "button, a, [role='button']"):
        try:
            text = (el.text or "").strip()
            if not text.isdigit():
                continue
            if not _safe_displayed(el):
                continue
            n = int(text)
            if n not in nums:
                nums.append(n)
        except Exception:
            continue
    return sorted(nums)


def _click_pagination_page_number(driver, page_num: int) -> bool:
    """Click a numbered page button; verify landed page + cards."""
    try:
        nav = driver.find_element(By.CSS_SELECTOR, SEL["pagination_nav"])
    except NoSuchElementException:
        return False

    label = str(page_num)
    target = None
    for el in nav.find_elements(By.CSS_SELECTOR, "button, a, [role='button']"):
        try:
            if (el.text or "").strip() != label:
                continue
            if _safe_displayed(el) and el.is_enabled():
                target = el
                break
        except StaleElementReferenceException:
            continue
    if target is None:
        return False

    prev_url = driver.current_url
    prev_readout = _pagination_readout(driver)
    prev_urls = _visible_profile_urls(driver, limit=6)
    logger.info("📄  Jumping via pagination button → page %s", page_num)
    clicked = _safe_click(driver, target, f"pagination_page_{page_num}")
    if not clicked:
        try:
            driver.execute_script("arguments[0].click();", target)
            clicked = True
        except Exception:
            clicked = False
    if not clicked:
        return False
    try:
        WebDriverWait(driver, 14).until(
            lambda d: (
                d.current_url != prev_url
                or (_pagination_readout(d) and _pagination_readout(d) != prev_readout)
                or (_visible_profile_urls(d, limit=6) and _visible_profile_urls(d, limit=6) != prev_urls)
            )
        )
    except TimeoutException:
        logger.warning("📄  Page-number click did not change results")
        return False

    human_sleep(0.8, 1.4)
    if not wait_for_agent_cards(driver):
        return False
    actual = _read_current_page_number(driver)
    if actual is not None and actual != page_num:
        logger.warning("📄  Landed on page %s but expected %s", actual, page_num)
        return False
    return len(_list_page_profile_urls(driver)) > 0


def _zillow_location_slug(location: str) -> str:
    """
    Build Zillow path slug, e.g. 'New York' / 'New York, NY' → 'new-york-ny'.
    """
    import re
    from location_utils import normalize_realtor_location

    loc = normalize_realtor_location(location or "")
    if not loc:
        return ""
    # ZIP → keep digits only
    if re.fullmatch(r"\d{5}", loc):
        return loc
    city, _, rest = loc.partition(",")
    city_slug = re.sub(r"[^a-z0-9]+", "-", city.strip().lower()).strip("-")
    st = rest.strip().lower()
    if re.fullmatch(r"[a-z]{2}", st):
        return f"{city_slug}-{st}" if city_slug else st
    if city_slug:
        return city_slug
    return re.sub(r"[^a-z0-9]+", "-", loc.lower()).strip("-")


def _zillow_results_base_url(driver) -> str:
    """
    Base results URL (no page=) — always prefer City-ST slug:
      https://www.zillow.com/professionals/real-estate-agent-reviews/new-york-ny/
    Do NOT keep a bare /new-york/ path from search (high ?page=N fails / clamps there).
    """
    from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

    slug = _zillow_location_slug(config.SEARCH_QUERY)
    if slug:
        return (
            "https://www.zillow.com/professionals/real-estate-agent-reviews/"
            f"{slug}/"
        )

    cur = (driver.current_url or "").strip()
    parsed = urlparse(cur)
    path = (parsed.path or "").rstrip("/") + "/"
    marker = "/professionals/real-estate-agent-reviews/"
    if marker in path:
        after = path.split(marker, 1)[-1].strip("/")
        if after:
            return urlunparse((
                parsed.scheme or "https",
                parsed.netloc or "www.zillow.com",
                f"{marker}{after}/",
                "",
                "",
                "",
            ))
    qs = parse_qs(parsed.query)
    qs.pop("page", None)
    return urlunparse(parsed._replace(
        query=urlencode({k: v[-1] for k, v in qs.items() if v}),
        fragment="",
    ))


def _zillow_page_url(driver, page_num: int) -> str:
    """Exact paginated URL: .../new-york-ny/?page=32"""
    from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

    base = _zillow_results_base_url(driver)
    parsed = urlparse(base)
    qs = parse_qs(parsed.query)
    flat = {k: (v[-1] if isinstance(v, list) and v else v) for k, v in qs.items()}
    flat["page"] = str(page_num)
    return urlunparse(parsed._replace(query=urlencode(flat), fragment=""))


def _is_last_results_page(driver) -> bool:
    """True when Next is missing/disabled — Zillow end of agent list."""
    try:
        nav = driver.find_element(By.CSS_SELECTOR, SEL["pagination_nav"])
    except NoSuchElementException:
        return True
    nexts = nav.find_elements(
        By.CSS_SELECTOR,
        "button[title='Next page'], a[title='Next page']",
    )
    if not nexts:
        return True
    for el in nexts:
        try:
            if el.get_attribute("disabled") is not None:
                return True
            if (el.get_attribute("aria-disabled") or "").lower() == "true":
                return True
            if not el.is_enabled():
                return True
            return False
        except Exception:
            return True
    return True


def _goto_page_via_url(driver, page_num: int) -> bool:
    """
    Fast jump: open .../new-york-ny/?page=N directly.
    If Zillow clamps to a lower last page, still returns True (caller checks page #).
    """
    if page_num < 1:
        return False
    try:
        target_url = _zillow_page_url(driver, page_num)
        logger.info("📄  Direct URL → %s", target_url)
        driver.get(target_url)
        human_sleep(1.2, 2.0)
        handle_first_load_captcha_if_present(driver, context=f"zillow_url_page_{page_num}")
        if not wait_for_agent_cards(driver):
            logger.warning("📄  URL page %s loaded but no agent cards", page_num)
            return False

        for _ in range(8):
            actual = _read_current_page_number(driver)
            if actual == page_num:
                logger.info("✅  URL jump confirmed on page %s", page_num)
                return True
            human_sleep(0.35, 0.55)

        actual = _read_current_page_number(driver)
        cur = driver.current_url or ""
        if actual == page_num:
            return True

        # Zillow often has only ~25 pages for a city — ?page=38 lands on last page 25
        if actual is not None and actual < page_num and _is_last_results_page(driver):
            logger.warning(
                "📄  Requested page %s but list ends at page %s (Next disabled)",
                page_num, actual,
            )
            return True

        if f"page={page_num}" in cur.lower() and actual is None:
            logger.info("✅  URL jump accepted via URL+cards (page %s)", page_num)
            return True

        logger.warning(
            "📄  URL jump mismatch (UI page=%s, wanted %s, url=%s)",
            actual, page_num, cur,
        )
        return False
    except Exception as e:
        logger.warning("📄  URL jump failed: %s", e)
        return False


def _jump_to_page(driver, target_page: int) -> bool:
    """
    Go straight to target_page via URL (?page=N). No button hopping.
    If the list ends earlier, stop on the last page (no reload loop).
    """
    if target_page <= 1:
        return True

    logger.info("🚀  Direct URL jump to page %s", target_page)

    if _goto_page_via_url(driver, target_page):
        current = _read_current_page_number(driver) or 1
        if current == target_page:
            return True
        if current < target_page and _is_last_results_page(driver):
            logger.warning(
                "⚠️  Zillow last page is %s (wanted %s) — end of agent list",
                current, target_page,
            )
            return True
        if abs(current - target_page) <= 1:
            return True

    # One corrective retry with freshly built city-st URL only
    if _goto_page_via_url(driver, target_page):
        current = _read_current_page_number(driver) or 1
        if current == target_page or (
            current < target_page and _is_last_results_page(driver)
        ):
            return True

    current = _read_current_page_number(driver) or 1
    if _is_last_results_page(driver):
        logger.warning("⚠️  On last page %s — cannot go further", current)
        return True

    logger.error("❌  Could not reach page %s (at %s)", target_page, current)
    return False


def click_next_page(driver) -> bool:
    """Click Next page with several retries (parallel runs need this)."""
    for attempt in range(1, 5):
        try:
            try:
                nav = driver.find_element(By.CSS_SELECTOR, SEL["pagination_nav"])
                driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});", nav
                )
                human_sleep(0.25, 0.5)
            except Exception:
                pass

            try:
                nav = driver.find_element(By.CSS_SELECTOR, SEL["pagination_nav"])
            except NoSuchElementException:
                logger.info("📄  No pagination nav found — single page of results")
                return False
            try:
                next_btn = nav.find_element(By.CSS_SELECTOR, SEL["next_page_btn"])
            except NoSuchElementException:
                next_btn = None
                for sel in (
                    "button[title='Next page']",
                    "a[title='Next page']",
                    "button[aria-label*='Next']",
                    "a[aria-label*='Next']",
                ):
                    try:
                        cand = nav.find_element(By.CSS_SELECTOR, sel)
                        if _safe_displayed(cand):
                            next_btn = cand
                            break
                    except NoSuchElementException:
                        continue
                if next_btn is None:
                    logger.info("📄  No 'Next page' button (or it's disabled) — done")
                    return False
            try:
                if not _safe_displayed(next_btn) or not next_btn.is_enabled():
                    logger.info("📄  Next page button not interactable — done")
                    return False
            except StaleElementReferenceException:
                continue
            if (next_btn.get_attribute("aria-disabled") or "").lower() == "true":
                logger.info("📄  Next page button aria-disabled — done")
                return False
            if (next_btn.get_attribute("disabled") or "").lower() in {"true", "disabled"}:
                logger.info("📄  Next page button disabled — done")
                return False

            logger.info("📄  Clicking 'Next page' … (attempt %s)", attempt)
            current_url = driver.current_url
            prev_readout = _pagination_readout(driver)
            prev_page = _read_current_page_number(driver)
            prev_urls = _visible_profile_urls(driver, limit=6)
            try:
                scroll_to_element(driver, next_btn)
            except Exception:
                pass
            clicked = _safe_click(driver, next_btn, "pagination_next")
            if not clicked:
                try:
                    driver.execute_script("arguments[0].click();", next_btn)
                    clicked = True
                except Exception:
                    clicked = False
            if not clicked:
                human_sleep(0.5, 0.9)
                continue
            try:
                WebDriverWait(driver, 14).until(
                    lambda d: (
                        d.current_url != current_url
                        or (
                            _read_current_page_number(d) is not None
                            and prev_page is not None
                            and _read_current_page_number(d) != prev_page
                        )
                        or (_pagination_readout(d) and _pagination_readout(d) != prev_readout)
                        or (
                            _visible_profile_urls(d, limit=6)
                            and _visible_profile_urls(d, limit=6) != prev_urls
                        )
                    )
                )
                # Guard: Next must not jump backwards (loop symptom)
                after = _read_current_page_number(driver)
                if (
                    prev_page is not None
                    and after is not None
                    and after < prev_page
                ):
                    logger.warning(
                        "📄  Next moved backwards (%s→%s) — treating as failure",
                        prev_page, after,
                    )
                    continue
                human_sleep(0.5, 1.0)
                return True
            except TimeoutException:
                logger.warning("📄  Next page did not change (attempt %s)", attempt)
                if is_captcha_present(driver):
                    check_and_handle_captcha(driver, context="zillow_pagination", max_attempts=1)
                human_sleep(0.8, 1.4)
        except StaleElementReferenceException:
            human_sleep(0.4, 0.7)
            continue
        except Exception as e:
            logger.warning("📄  Next page error (attempt %s): %s", attempt, e)
            human_sleep(0.5, 0.9)
    logger.warning("📄  Next page did not change — staying on current page")
    return False


def _fast_forward_to_range(
    driver,
    range_start: int,
    seen_profile_urls: set[str],
    deadline: float | None = None,
) -> tuple[int, int]:
    """
    Advance to the page that contains agent `range_start` via direct ?page=N URL.

    Zillow city lists often end around page 25 (~400 agents). If the list ends
    before range_start, we stop on the last page (no reload loops).
    """
    if range_start <= 1:
        return 0, 1
    if not wait_for_agent_cards(driver):
        return 0, 1

    measured = len(_list_page_profile_urls(driver))
    per_page = 16
    if measured and measured != 16:
        logger.info(
            "ℹ️  Page 1 shows %s cards — still using %s/page for jump math",
            measured, per_page,
        )

    target_page = (range_start - 1) // per_page + 1
    logger.info(
        "⏩  Skip plan: %s agents/page → need page %s for range start %s",
        per_page, target_page, range_start,
    )

    position = 0
    page = 1

    if target_page >= 2:
        if deadline and time.time() > deadline:
            logger.error("❌  Fast-forward timed out before page jump")
            return position, page
        _jump_to_page(driver, target_page)
        actual = _read_current_page_number(driver) or 1
        page = max(1, actual)
        position = (page - 1) * per_page
        logger.info(
            "✅  Landed page %s (cursor ≈ %s, need agent %s)",
            page, position, range_start,
        )
        try:
            from run_registry import update_run
            update_run(getattr(config, "RUN_ID", None), pages_scraped=page)
        except Exception:
            pass

        if page < target_page and _is_last_results_page(driver):
            page_urls = _list_page_profile_urls(driver)
            available = position + len(page_urls)
            logger.error(
                "❌  Zillow list ends at page %s (~%s agents). "
                "Range start %s is past the end — nothing to scrape.",
                page, available, range_start,
            )
            # Mark so main loop does not thrash
            for u in page_urls:
                seen_profile_urls.add(u)
            return position + len(page_urls), page

    while True:
        if deadline and time.time() > deadline:
            logger.error(
                "❌  Fast-forward timed out at page %s (seeking agent %s)",
                page, range_start,
            )
            break
        from run_control import is_cancelled
        if is_cancelled(getattr(config, "RUN_ID", None)):
            break
        if not wait_for_agent_cards(driver):
            logger.warning("⚠️  No cards while skipping — stopping at page %s", page)
            break
        page_urls = _list_page_profile_urls(driver)
        if not page_urls:
            logger.warning("⚠️  Empty results page while skipping (page %s)", page)
            break

        ui_page = _read_current_page_number(driver)
        if ui_page is not None:
            page = ui_page

        if position + len(page_urls) < range_start:
            for u in page_urls:
                seen_profile_urls.add(u)
            position += len(page_urls)
            logger.info(
                "   ⏭️  Skip page %s entirely (%s agents) → position %s",
                page, len(page_urls), position,
            )
            if _is_last_results_page(driver):
                logger.error(
                    "❌  Reached last page %s with only ~%s agents; "
                    "cannot reach agent %s",
                    page, position, range_start,
                )
                break
            next_page = page + 1
            if not click_next_page(driver):
                logger.warning(
                    "📄  Next failed while skipping page %s — URL → %s",
                    page, next_page,
                )
                if not _goto_page_via_url(driver, next_page):
                    logger.error(
                        "❌  Could not advance past page %s while seeking agent %s",
                        page, range_start,
                    )
                    break
            page = _read_current_page_number(driver) or next_page
            try:
                from run_registry import update_run
                update_run(getattr(config, "RUN_ID", None), pages_scraped=page)
            except Exception:
                pass
            continue

        logger.info(
            "✅  Ready on page %s (position %s, %s cards) for range start %s",
            page, position, len(page_urls), range_start,
        )
        return position, page
    return position, page


def run_zillow(driver, run_id: str | None = None) -> dict:
    """
    Full Zillow agent scrape, range-based:
      1. Open results (direct URL preferred; search UI fallback)
      2. Skip pages before RANGE_START (count only)
      3. Scrape [RANGE_START, RANGE_END]
    """
    # VPS-friendly path: skip fragile search box — open .../new-york-ny/ directly
    # Soft progress at 18% + 0 agents usually means stuck here (captcha / dual scrape)
    startup_deadline = time.time() + 180
    try:
        if not open_zillow_results(driver):
            logger.warning("↪️  Direct URL failed — falling back to search UI")
            if time.time() > startup_deadline:
                return {
                    "total_scraped": 0,
                    "pages_scraped": 0,
                    "output_path": None,
                    "error": (
                        "Timed out loading Zillow results (captcha/block). "
                        "Cancel other sessions and run ONE scrape at a time."
                    ),
                }
            navigate_to_agent_search(driver)
            search_agents(driver)
    except Exception as e:
        if is_dead_browser_error(e):
            msg = humanize_browser_error(e)
            logger.error("❌  Browser died before scrape started: %s", e)
            return {
                "total_scraped": 0,
                "pages_scraped": 0,
                "output_path": None,
                "error": msg,
            }
        raise

    if not wait_for_agent_cards(driver, retries=2):
        return {
            "total_scraped": 0,
            "pages_scraped": 0,
            "output_path": None,
            "error": (
                "Could not load Zillow agent cards after open "
                "(captcha or page block). Cancel other sessions and "
                "run ONE scrape at a time."
            ),
        }

    range_start = max(1, config.RANGE_START)
    range_end = max(range_start, config.RANGE_END)

    position = 0
    total_scraped = 0
    page = 1
    output_path = None
    seen_profile_urls: set[str] = set()
    stop_reason = ""

    logger.info(f"🎯  Target range: agents {range_start}–{range_end}")

    try:
        position, page = _fast_forward_to_range(
            driver, range_start, seen_profile_urls, deadline=startup_deadline + 120
        )
    except Exception as e:
        if is_dead_browser_error(e):
            stop_reason = humanize_browser_error(e)
            logger.error("❌  Browser died during fast-skip: %s", stop_reason)
            position, page = 0, 1
        else:
            logger.warning("⚠️  Fast-skip failed (%s) — continuing from page 1", e)
            position, page = 0, 1

    try:
        while position < range_end and not stop_reason:
            from run_control import is_cancelled
            if is_cancelled(run_id):
                logger.info("🛑  Run cancelled — stopping Zillow scrape")
                stop_reason = "Cancelled by user"
                break

            if total_scraped == 0 and time.time() > (startup_deadline + 120):
                stop_reason = (
                    "Stuck with 0 agents for several minutes "
                    "(captcha or Zillow block). Cancel extras and run "
                    "ONE scrape at a time, then retry this range."
                )
                logger.error("❌  %s", stop_reason)
                break

            logger.info(f"\n{'─' * 55}")
            logger.info(f"📑  Zillow Agent Page {page}  (position so far: {position})")

            if not wait_for_agent_cards(driver, retries=3):
                # Last chance: hard jump to this page via URL, then one more wait
                logger.warning(
                    "📄  Cards missing on page %s — hard URL reload then retry",
                    page,
                )
                recovered = False
                try:
                    if _goto_page_via_url(driver, page) and wait_for_agent_cards(
                        driver, retries=2
                    ):
                        recovered = True
                except Exception as e:
                    logger.warning("📄  Hard reload failed: %s", e)
                if not recovered:
                    stop_reason = (
                        f"Could not read agent cards on page {page} "
                        f"(captcha/load failed after retries — "
                        f"scraped {total_scraped} so far; re-run remaining range)"
                    )
                    logger.warning("⚠️  %s — stopping", stop_reason)
                    break

            page_urls = [u for u in _list_page_profile_urls(driver) if u not in seen_profile_urls]
            if not page_urls:
                if position < range_start:
                    if _is_last_results_page(driver):
                        stop_reason = (
                            f"Zillow list ends at page {page} (~{position} agents available). "
                            f"Your range starts at {range_start}, which is past the end of results "
                            f"(this location typically maxes around page 25 / ~400 agents)."
                        )
                    else:
                        stop_reason = (
                            f"Could not reach agent {range_start} (stuck near page {page})"
                        )
                    logger.warning("⚠️  %s — stopping", stop_reason)
                    break
                if position < range_end and click_next_page(driver):
                    page = _read_current_page_number(driver) or (page + 1)
                    continue
                stop_reason = f"No new agents found on page {page}"
                logger.warning("⚠️  %s — stopping", stop_reason)
                break

            to_scrape = []
            for url in page_urls:
                position += 1
                if position < range_start:
                    seen_profile_urls.add(url)
                    continue
                if position > range_end:
                    break
                to_scrape.append(url)

            if to_scrape:
                logger.info(f"   🎯  {len(to_scrape)} agent(s) on this page fall inside the range")
                agents = []
                try:
                    agents = scrape_selected_profiles(driver, to_scrape, seen_profile_urls)
                except Exception as e:
                    if is_dead_browser_error(e):
                        stop_reason = humanize_browser_error(e)
                        logger.error("❌  Browser died mid-profile batch: %s", stop_reason)
                        break
                    # Never fail the whole 101–200 / 500–1000 run on one batch glitch
                    logger.error("❌  Profile batch error (continuing pagination): %s", e)
                    for u in to_scrape:
                        seen_profile_urls.add(u)
                if agents:
                    output_path = save_agents(
                        agents, page, "zillow", config.SEARCH_QUERY, output_path, run_id=run_id,
                    )
                    total_scraped += len(agents)
                    try:
                        from run_registry import update_run
                        update_run(run_id, scraped_agents=total_scraped, pages_scraped=page)
                    except Exception:
                        pass
                # Failures are already marked seen inside scrape_selected_profiles after retry
            else:
                logger.info("   ⏭️  Entire page is before the target range — skipping without opening profiles")
                for u in page_urls:
                    seen_profile_urls.add(u)

            if position >= range_end:
                logger.info(f"🎯  Reached end of range ({range_end})")
                break

            expected_next = page + 1
            before_page = _read_current_page_number(driver) or page
            advanced = click_next_page(driver)
            if not advanced:
                # Button/nav often flaky on VPS — jump via ?page=N before giving up
                logger.warning(
                    "📄  Next click failed on page %s — trying URL page %s",
                    page, expected_next,
                )
                if _goto_page_via_url(driver, expected_next):
                    landed = _read_current_page_number(driver)
                    if landed is not None and landed > before_page:
                        page = landed
                        continue
                    if landed == expected_next or (
                        f"page={expected_next}" in (driver.current_url or "").lower()
                    ):
                        page = landed or expected_next
                        continue
                    if landed is not None and landed == before_page and _is_last_results_page(driver):
                        stop_reason = (
                            f"Zillow list ends at page {page} (position {position})"
                        )
                        logger.info("📄  %s", stop_reason)
                        break
                if _is_last_results_page(driver):
                    stop_reason = (
                        f"Zillow list ends at page {page} (position {position})"
                    )
                else:
                    stop_reason = (
                        f"Could not open page {expected_next} after page {page} "
                        f"(position {position}) — pagination blocked or captcha"
                    )
                logger.info("📄  %s", stop_reason)
                break
            actual = _read_current_page_number(driver)
            if actual is None:
                page = expected_next
            elif actual == expected_next:
                page = actual
            elif actual == before_page:
                # Next claimed success but page unchanged — force URL
                logger.warning("📄  Next did not advance (still %s) — URL to %s", actual, expected_next)
                if _goto_page_via_url(driver, expected_next):
                    page = _read_current_page_number(driver) or expected_next
                else:
                    stop_reason = f"Could not advance from page {before_page}"
                    break
            elif actual > expected_next:
                # Accidental skip (5→6) — pull back to the intended next page
                logger.warning(
                    "📄  Skipped ahead to page %s (wanted %s) — correcting via URL",
                    actual, expected_next,
                )
                if _goto_page_via_url(driver, expected_next):
                    page = _read_current_page_number(driver) or expected_next
                else:
                    page = actual  # continue from where we landed rather than abort
            else:
                page = actual
            try:
                from run_registry import update_run
                update_run(run_id, pages_scraped=page, scraped_agents=total_scraped)
            except Exception:
                pass
    except Exception as e:
        if is_dead_browser_error(e):
            stop_reason = humanize_browser_error(e)
            logger.error("❌  Browser died during scrape: %s", e)
        else:
            raise

    logger.info(
        f"\n🎉  Zillow scrape complete!\n"
        f"    Range        : {range_start}-{range_end}\n"
        f"    Total agents : {total_scraped}\n"
        f"    Pages scraped: {page}\n"
        f"    Output       : {output_path or 'none'}"
    )
    expected = range_end - range_start + 1
    result = {
        "total_scraped": total_scraped,
        "pages_scraped": page,
        "output_path": output_path,
        "expected_agents": expected,
    }
    if str(stop_reason).startswith("Cancelled"):
        result["error"] = stop_reason
    elif stop_reason:
        result["error"] = stop_reason
        logger.error("❌  %s", result["error"])
    elif total_scraped == 0:
        result["error"] = (
            f"No agents scraped for range {range_start}–{range_end}. "
            "Pagination/skip failed — retry this range."
        )
        logger.error("❌  %s", result["error"])
    elif total_scraped < expected:
        # Partial success — do not pretend the full range was collected
        result["error"] = (
            f"Incomplete: scraped {total_scraped} of {expected} agents "
            f"for range {range_start}–{range_end}. "
            "Re-run this range (or the missing slice) to fill gaps."
        )
        logger.error("❌  %s", result["error"])
    return result
