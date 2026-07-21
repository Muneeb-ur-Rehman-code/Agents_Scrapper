from __future__ import annotations
# ============================================================
#  driver_setup.py
#  Fixed: winreg (Windows only) wrapped in platform check
#  Added: Xvfb virtual display for Linux VPS
# ============================================================
import os
import sys
import platform
import random
import logging
import subprocess
import re
import threading

import undetected_chromedriver as uc
from selenium_stealth import stealth

import config

logger = logging.getLogger("AgentScraper")

# Serialize Chrome/driver startup — parallel Zillow jobs race on chromedriver download
_DRIVER_BUILD_LOCK = threading.Lock()

# ── Virtual Display (Linux/VPS only) ─────────────────────────
_display = None


def start_virtual_display():
    """Start Xvfb virtual display on Linux if not already running."""
    global _display
    if platform.system() != "Linux":
        return
    if _display is not None:
        return  # already running — no-op
    try:
        from pyvirtualdisplay import Display
        _display = Display(visible=False, size=(1920, 1080))
        _display.start()
        logger.info("✅  Virtual display started (Xvfb)")
    except Exception as e:
        logger.warning(f"⚠️  Could not start virtual display: {e}")


def stop_virtual_display():
    """Stop Xvfb virtual display."""
    global _display
    if _display:
        try:
            _display.stop()
            logger.info("🛑  Virtual display stopped")
        except Exception:
            pass
        _display = None


# ── Chrome Version Detection ──────────────────────────────────
def _get_chrome_version() -> int:
    # ── Windows only ─────────────────────────────────────────
    if platform.system() == "Windows":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Google\Chrome\BLBeacon"
            )
            v, _ = winreg.QueryValueEx(key, "version")
            major = int(v.split(".")[0])
            logger.info(f"🔍  Chrome {v} → major={major}")
            return major
        except Exception:
            pass

    # ── Linux / Mac ───────────────────────────────────────────
    for path in [
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]:
        try:
            r = subprocess.run(
                [path, "--version"],
                capture_output=True, text=True, timeout=5
            )
            m = re.search(r"(\d+)\.", r.stdout)
            if m:
                major = int(m.group(1))
                logger.info(f"🔍  Chrome major={major}")
                return major
        except Exception:
            continue

    logger.warning("⚠️  Could not detect Chrome version — defaulting to 136")
    return 136


def build_driver() -> uc.Chrome:
    """Build and return an undetected Chrome driver (serialized for parallel runs)."""
    with _DRIVER_BUILD_LOCK:
        return _build_driver_unlocked()


def _profile_dir_for_run() -> str:
    """
    Always isolate each scrape into chrome_profile/runs/<run_id>.
    Sharing one profile across parallel jobs locks/crashes Chrome on Linux.
    """
    base = os.path.join(os.path.dirname(__file__), "chrome_profile")
    run_id = getattr(config, "RUN_ID", None)
    if run_id:
        return os.path.join(base, "runs", str(run_id))
    # Fallback for CLI / tests without a run id
    return getattr(config, "CHROME_PROFILE_DIR", None) or base


def _build_driver_unlocked() -> uc.Chrome:
    """Internal: create Chrome after the build lock is held."""

    # Start virtual display on Linux before launching Chrome
    start_virtual_display()

    chrome_version = _get_chrome_version()
    profile_dir = _profile_dir_for_run()
    os.makedirs(profile_dir, exist_ok=True)
    logger.info(f"📁  Profile dir: {profile_dir}")

    opts = uc.ChromeOptions()

    opts.add_argument("--profile-directory=Default")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-infobars")
    opts.add_argument("--disable-popup-blocking")
    opts.add_argument("--ignore-certificate-errors")
    opts.add_argument("--lang=en-US,en;q=0.9")
    opts.add_argument("--disable-gpu")
    # Lower RAM use on small VPS when several Chromes run together
    opts.add_argument("--disable-extensions")
    opts.add_argument("--disable-background-networking")
    opts.add_argument("--disable-sync")
    opts.add_argument("--metrics-recording-only")
    opts.add_argument("--mute-audio")
    opts.add_argument("--renderer-process-limit=3")

    w = config.WINDOW_WIDTH  + random.randint(-20, 20)
    h = config.WINDOW_HEIGHT + random.randint(-20, 20)
    opts.add_argument(f"--window-size={w},{h}")
    opts.add_argument("--start-maximized")

    if config.HEADLESS:
        opts.add_argument("--headless=new")

    logger.info(f"🔧  Building driver  |  Chrome {chrome_version}  |  Headless={config.HEADLESS}")

    # user_data_dir= isolates this job; build lock serializes UC binary patching
    driver = uc.Chrome(
        options=opts,
        version_main=chrome_version,
        use_subprocess=True,
        user_data_dir=profile_dir,
    )

    stealth(driver,
            languages=["en-US", "en"],
            vendor="Google Inc.",
            platform="Win32",
            webgl_vendor="Intel Inc.",
            renderer="Intel Iris OpenGL Engine",
            fix_hairline=True)

    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
            window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){}, app: {} };
        """
    })

    logger.info("✅  Driver ready")
    return driver
