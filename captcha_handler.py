from __future__ import annotations
# ============================================================
#  captcha_handler.py
#
#  Shared captcha solver for both Zillow and Realtor.com.
#  Both use PerimeterX — same hold-to-verify mechanism.
#
#  UPDATED: True cubic Bezier mouse movement instead of linear
#  interpolation — far more human-like velocity profile.
# ============================================================
import time, random, math, logging
import threading
import pyautogui
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, StaleElementReferenceException, WebDriverException
)
from config import CAPTCHA_HOLD_SECONDS

logger = logging.getLogger("AgentScraper")

pyautogui.FAILSAFE = False   # MUST be False — True crashes browser session
pyautogui.PAUSE    = 0.0

_WRAPPER   = "#px-captcha-wrapper"
_CONTAINER = "div.px-captcha-container"
_HOLD_DIV  = "div#px-captcha"
_ANY       = "[class*='px-captcha']"
_MAX_ATTEMPTS = 3
# Hard ceiling so one captcha cannot hang a scrape for minutes
_MAX_SOLVE_WALL_SECS = 55

# Parallel scrapes: os.environ DISPLAY is process-wide — lock + per-run Xvfb
_CAPTCHA_LOCK = threading.Lock()


def _safe_displayed(el) -> bool:
    """Never raise — stale/null elements are treated as not displayed."""
    if el is None:
        return False
    try:
        return bool(el.is_displayed())
    except Exception:
        return False


# ════════════════════════════════════════════════════════════
#  PUBLIC API
# ════════════════════════════════════════════════════════════

def is_captcha_present(driver) -> bool:
    """Check if a PerimeterX captcha is currently visible."""
    for sel in [_WRAPPER, _CONTAINER, _HOLD_DIV, _ANY,
                "div.px-captcha-message"]:
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            if _safe_displayed(el):
                return True
        except (NoSuchElementException, StaleElementReferenceException, WebDriverException, AttributeError):
            continue
        except Exception:
            continue
    return False


def check_and_handle_captcha(driver, context: str = "", max_attempts: int | None = None) -> bool:
    """
    If a captcha is present, attempt to solve it.
    Returns True if a captcha was found (solved or not).
    Wall-clock limited so a stuck captcha cannot freeze the whole run.

    Uses a process-wide lock while DISPLAY is switched to this run's Xvfb
    so pyautogui hits the correct Chrome window.
    """
    tag = f" [{context}]" if context else ""
    if not is_captcha_present(driver):
        return False

    logger.warning(f"⚠️  CAPTCHA detected{tag} — waiting for captcha lock")
    # Never block forever if the other parallel scrape is wedged on pyautogui
    acquired = _CAPTCHA_LOCK.acquire(timeout=90)
    if not acquired:
        logger.error(
            f"   ❌  Captcha lock busy >90s{tag} — other scrape may be stuck; skipping solve"
        )
        return True
    try:
        from driver_setup import run_display_context

        with run_display_context():
            # Another scrape may have changed focus; re-check under the lock
            if not is_captcha_present(driver):
                logger.info(f"   ✅  Captcha already gone{tag}")
                return True

            logger.warning(f"⚠️  CAPTCHA solving{tag}")
            # Bring this Chrome to a known place so pyautogui hits the right window
            try:
                driver.set_window_position(0, 0)
                driver.set_window_size(1400, 900)
            except Exception:
                pass

            attempts = max_attempts if max_attempts is not None else _MAX_ATTEMPTS
            attempts = max(1, min(attempts, _MAX_ATTEMPTS))
            deadline = time.time() + _MAX_SOLVE_WALL_SECS

            for attempt in range(1, attempts + 1):
                if time.time() >= deadline:
                    logger.error(f"   ❌  Captcha wall-clock limit{tag}")
                    break
                logger.info(f"   🔄  Captcha attempt {attempt}/{attempts}{tag}")
                if _solve(driver):
                    human_pause = random.uniform(0.8, 1.4)
                    time.sleep(human_pause)
                    if not is_captcha_present(driver):
                        logger.info(f"   ✅  Captcha cleared{tag}")
                        return True
                    logger.warning(f"   ⚠️  Captcha still visible after hold{tag}")
                else:
                    logger.warning(f"   ⚠️  Captcha hold attempt failed{tag}")
                time.sleep(random.uniform(0.6, 1.1))

            logger.error(f"   ❌  Could not clear captcha{tag}")
            return True
    finally:
        _CAPTCHA_LOCK.release()


def wait_for_captcha_on_first_load(driver, poll_secs: int = 15) -> None:
    """
    FIRST-LOAD ONLY captcha watch.
    PerimeterX JS fires 2-8s AFTER page load. We poll for
    `poll_secs` seconds so we catch it the instant it appears.
    After solving (or no captcha), returns once.
    """
    logger.info(f"   👁️  First-load captcha watch ({poll_secs}s) …")
    deadline = time.time() + poll_secs

    while time.time() < deadline:
        try:
            if is_captcha_present(driver):
                logger.warning("   ⚠️  Captcha appeared on first load — solving …")
                check_and_handle_captcha(driver, context="first_load")
                # Give extra time after solve for page to settle
                time.sleep(3.0)
                return
        except Exception:
            pass
        time.sleep(0.5)

    logger.info("   ✅  No captcha on first load")


def handle_first_load_captcha_if_present(driver, context: str = "first_load") -> bool:
    """
    Fast first-load check:
    - If captcha is visible now, solve it.
    - If not visible, continue immediately (no polling delay).
    Returns True if captcha was present.
    """
    if not is_captcha_present(driver):
        logger.info("   ✅  No captcha on first load (immediate check)")
        return False

    logger.warning("   ⚠️  Captcha present on first load — solving …")
    check_and_handle_captcha(driver, context=context)
    return True


def check_if_blocked(driver) -> bool:
    """
    Quick check — used AFTER a click fails or is intercepted.
    If captcha is present, solve it and return True.
    Otherwise return False.
    """
    if is_captcha_present(driver):
        logger.warning("   🚫  Click blocked by captcha — solving …")
        check_and_handle_captcha(driver, context="click_blocked")
        return True
    return False


# ════════════════════════════════════════════════════════════
#  SOLVER
# ════════════════════════════════════════════════════════════

def _solve(driver) -> bool:
    time.sleep(2.0)
    el = _find_hold_element(driver)
    if el is None:
        logger.warning("      No hold element — centre fallback")
        return _centre_hold(driver)

    # Scroll into view
    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({behavior:'smooth',block:'center'});", el)
        time.sleep(1.0)
    except Exception:
        pass

    # Re-fetch after scroll
    el = _find_hold_element(driver)
    if el is None:
        return _centre_hold(driver)

    # Get coords
    try:
        sx, sy = _screen_coords(driver, el)
        logger.info(f"      🎯  ({sx}, {sy})")
    except Exception as exc:
        logger.warning(f"      Coords failed: {exc}")
        return _centre_hold(driver)

    # Validate coords
    sw, sh = pyautogui.size()
    if not (30 < sx < sw - 30 and 30 < sy < sh - 30):
        logger.warning(f"      Coords ({sx},{sy}) out of safe zone")
        return _centre_hold(driver)

    return _do_hold(driver, sx, sy)


def _find_hold_element(driver):
    for sel in [_HOLD_DIV, _CONTAINER, _WRAPPER]:
        try:
            el = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, sel)))
            if _safe_displayed(el):
                return el
        except (TimeoutException, NoSuchElementException, AttributeError):
            continue
        except Exception:
            continue
    return None


# ════════════════════════════════════════════════════════════
#  SCREEN COORDINATES
# ════════════════════════════════════════════════════════════

def _screen_coords(driver, el) -> tuple[int, int]:
    win = driver.get_window_rect()
    wx  = int(win.get("x", 0))
    wy  = int(win.get("y", 0))

    dims = driver.execute_script("""
        return {
            ow: window.outerWidth,  iw: window.innerWidth,
            oh: window.outerHeight, ih: window.innerHeight
        };
    """)
    border  = (int(dims["ow"]) - int(dims["iw"])) // 2
    top_bar = int(dims["oh"]) - int(dims["ih"]) - border

    rect = driver.execute_script("""
        const root = arguments[0];
        const rootRect = root.getBoundingClientRect();
        const pool = new Set([root]);
        const sels = [
            "button",
            "[role='button']",
            "iframe",
            "canvas",
            "div[tabindex]",
            "div[id*='captcha']",
            "div[class*='captcha']",
        ];
        for (const sel of sels) {
            for (const n of root.querySelectorAll(sel)) pool.add(n);
        }

        const visible = (n) => {
            const r = n.getBoundingClientRect();
            if (!r || r.width < 16 || r.height < 16) return null;
            const s = window.getComputedStyle(n);
            if (!s || s.display === "none" || s.visibility === "hidden" || s.opacity === "0") return null;
            return r;
        };

        const rootCenterX = rootRect.left + rootRect.width / 2;
        const rootTargetY = rootRect.top + rootRect.height * 0.38;
        let best = null;
        let bestScore = Number.POSITIVE_INFINITY;

        for (const n of pool) {
            const r = visible(n);
            if (!r) continue;
            if (r.width > rootRect.width * 0.98 && r.height > rootRect.height * 0.98) continue;

            const centerX = r.left + r.width / 2;
            const targetY = r.top + r.height * (r.height >= 50 ? 0.38 : 0.5);
            const areaPenalty = (r.width * r.height) / 6000;
            const score =
                Math.abs(centerX - rootCenterX) +
                Math.abs(targetY - rootTargetY) * 0.9 +
                areaPenalty;

            if (score < bestScore) {
                bestScore = score;
                best = r;
            }
        }

        const use = best || rootRect;
        return {x: use.left, y: use.top, w: use.width, h: use.height};
    """, el)

    cx = rect["x"] + rect["w"] / 2.0
    y_ratio = 0.38 if rect["h"] >= 50 else 0.5
    cy = rect["y"] + rect["h"] * y_ratio

    sx = int(wx + border  + cx)
    sy = int(wy + top_bar + cy)
    return sx, sy


# ════════════════════════════════════════════════════════════
#  PYAUTOGUI HOLD
# ════════════════════════════════════════════════════════════

def _do_hold(driver, sx: int, sy: int) -> bool:
    # Add a tiny random offset to the target
    sx += random.randint(-2, 2)
    sy += random.randint(-2, 2)

    _bezier_move(sx, sy)
    time.sleep(random.uniform(0.5, 0.8))

    # Randomise hold duration slightly around the config value
    hold_duration = CAPTCHA_HOLD_SECONDS + random.uniform(-0.5, 2.5)

    logger.info(f"      🖱️  Hold start ({sx},{sy}) for {hold_duration:.1f}s")
    try:
        pyautogui.mouseDown(button="left")
    except Exception as e:
        logger.error(f"      mouseDown failed: {e}")
        return False

    # Perform the jitter hold
    _jitter_hold(sx, sy, hold_duration)

    # Small pause before release
    time.sleep(random.uniform(0.2, 0.5))

    try:
        pyautogui.mouseUp(button="left")
    except Exception:
        pass

    logger.info("      🖱️  Released")

    # Wait for the captcha to disappear (capped — don't hang forever)
    return _poll_until_gone(driver, 12)


def _centre_hold(driver) -> bool:
    try:
        win = driver.get_window_rect()
        wx  = int(win.get("x", 0))
        wy  = int(win.get("y", 0))

        dims = driver.execute_script("""
            return {
                ow: window.outerWidth,  iw: window.innerWidth,
                oh: window.outerHeight, ih: window.innerHeight
            };
        """)
        border  = (int(dims["ow"]) - int(dims["iw"])) // 2
        top_bar = int(dims["oh"]) - int(dims["ih"]) - border

        cx = wx + border  + int(dims["iw"]) // 2
        cy = wy + top_bar + int(dims["ih"]) // 2

        sw, sh = pyautogui.size()
        cx = max(100, min(cx, sw - 100))
        cy = max(100, min(cy, sh - 100))

        logger.info(f"      🔁  Centre fallback ({cx},{cy})")
        return _do_hold(driver, cx, cy)
    except Exception as e:
        logger.error(f"      Centre fallback error: {e}")
        return False


# ════════════════════════════════════════════════════════════
#  TRUE CUBIC BEZIER MOUSE MOVEMENT
# ════════════════════════════════════════════════════════════

def _bezier_point(p0, p1, p2, p3, t):
    """Cubic Bezier formula B(t) = (1-t)³P0 + 3(1-t)²tP1 + 3(1-t)t²P2 + t³P3"""
    u = 1 - t
    return (
        u**3 * p0[0] + 3*u**2*t * p1[0] + 3*u*t**2 * p2[0] + t**3 * p3[0],
        u**3 * p0[1] + 3*u**2*t * p1[1] + 3*u*t**2 * p2[1] + t**3 * p3[1],
    )


def _bezier_move(tx: int, ty: int) -> None:
    """
    Move mouse from current position to (tx, ty) via a cubic Bezier curve.
    Control points are randomised to produce different curves each call.
    """
    sw, sh  = pyautogui.size()
    cx, cy  = pyautogui.position()

    p0 = (cx, cy)
    p3 = (tx, ty)

    dx = tx - cx
    dy = ty - cy

    p1 = (
        cx + dx * random.uniform(0.25, 0.40) + random.randint(-60, 60),
        cy + dy * random.uniform(0.25, 0.40) + random.randint(-40, 40),
    )
    p2 = (
        cx + dx * random.uniform(0.60, 0.75) + random.randint(-60, 60),
        cy + dy * random.uniform(0.60, 0.75) + random.randint(-40, 40),
    )

    dist  = math.hypot(dx, dy)
    steps = max(20, int(dist / 8))

    prev_x, prev_y = cx, cy
    for i in range(1, steps + 1):
        t = i / steps
        t_eased = t * t * (3 - 2 * t)

        bx, by = _bezier_point(p0, p1, p2, p3, t_eased)
        bx += random.gauss(0, 0.4)
        by += random.gauss(0, 0.4)

        bx = int(max(1, min(bx, sw - 1)))
        by = int(max(1, min(by, sh - 1)))

        speed_factor = 1.0 - abs(t - 0.5) * 0.8
        step_dur = random.uniform(0.008, 0.018) * speed_factor

        if (bx, by) != (prev_x, prev_y):
            pyautogui.moveTo(bx, by, duration=step_dur)
            prev_x, prev_y = bx, by

    time.sleep(random.uniform(0.05, 0.12))
    fx = int(max(1, min(tx + random.randint(-1, 1), sw - 1)))
    fy = int(max(1, min(ty + random.randint(-1, 1), sh - 1)))
    pyautogui.moveTo(fx, fy, duration=random.uniform(0.04, 0.08))


def _jitter_hold(bx: int, by: int, seconds: float) -> None:
    """Micro-jitter while holding — real hand tremor."""
    sw, sh   = pyautogui.size()
    end_time = time.time() + seconds
    start_time = time.time()

    while time.time() < end_time:
        progress = (time.time() - start_time) / seconds

        drift_x = math.sin(progress * 10) * 0.9
        drift_y = math.cos(progress * 8) * 0.9

        jx = int(max(1, min(bx + drift_x + random.gauss(0, 0.55), sw - 1)))
        jy = int(max(1, min(by + drift_y + random.gauss(0, 0.55), sh - 1)))

        pyautogui.moveTo(jx, jy, duration=random.uniform(0.01, 0.03))
        time.sleep(random.uniform(0.1, 0.25))


# ════════════════════════════════════════════════════════════
#  POLL UNTIL GONE
# ════════════════════════════════════════════════════════════

def _poll_until_gone(driver, timeout=15) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_captcha_present(driver):
            logger.info("      ✅  Captcha cleared!")
            return True
        time.sleep(0.5)
    logger.warning(f"      ⏳  Still present after {timeout}s")
    return False
