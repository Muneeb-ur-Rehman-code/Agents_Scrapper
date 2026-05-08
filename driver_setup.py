from __future__ import annotations
# ============================================================
#  driver_setup.py
#  Uses a PERSISTENT local chrome_profile/ folder.
#  First run  → fresh profile
#  Later runs → profile reused (cookies valid)
# ============================================================
import os, random, logging
import undetected_chromedriver as uc
from selenium_stealth import stealth
from config import HEADLESS, WINDOW_WIDTH, WINDOW_HEIGHT, CHROME_PROFILE_DIR

logger = logging.getLogger("AgentScraper")


def _get_chrome_version() -> int:
    import subprocess, re
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
    for path in [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
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
    logger.warning("⚠️  Could not detect Chrome version — defaulting to 147")
    return 147


def build_driver() -> uc.Chrome:
    """
    Build and return an undetected Chrome driver.
    Always uses CHROME_PROFILE_DIR as the persistent profile.
    """
    chrome_version = _get_chrome_version()

    os.makedirs(CHROME_PROFILE_DIR, exist_ok=True)
    logger.info(f"📁  Profile dir: {CHROME_PROFILE_DIR}")

    opts = uc.ChromeOptions()

    # ── Persistent profile ────────────────────────────────────
    opts.add_argument(f"--user-data-dir={CHROME_PROFILE_DIR}")
    opts.add_argument("--profile-directory=Default")

    # ── Anti-detection ────────────────────────────────────────
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-infobars")
    opts.add_argument("--disable-popup-blocking")
    opts.add_argument("--ignore-certificate-errors")
    opts.add_argument("--lang=en-US,en;q=0.9")
    opts.add_argument("--disable-gpu")

    # ── Window ────────────────────────────────────────────────
    w = WINDOW_WIDTH  + random.randint(-20, 20)
    h = WINDOW_HEIGHT + random.randint(-20, 20)
    opts.add_argument(f"--window-size={w},{h}")
    opts.add_argument("--start-maximized")

    if HEADLESS:
        opts.add_argument("--headless=new")

    logger.info(f"🔧  Building driver  |  Chrome {chrome_version}")

    driver = uc.Chrome(
        options=opts,
        version_main=chrome_version,
        use_subprocess=True,
        driver_executable_path=None,
    )

    # ── selenium-stealth ──────────────────────────────────────
    stealth(driver,
            languages=["en-US", "en"],
            vendor="Google Inc.",
            platform="Win32",
            webgl_vendor="Intel Inc.",
            renderer="Intel Iris OpenGL Engine",
            fix_hairline=True)

    # ── CDP reinforcements ────────────────────────────────────
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
            Object.defineProperty(navigator, 'webdriver',
                {get: () => undefined});

            Object.defineProperty(navigator, 'plugins', {
                get: () => {
                    const p = [
                        {name:'Chrome PDF Plugin',
                         filename:'internal-pdf-viewer'},
                        {name:'Chrome PDF Viewer',
                         filename:'mhjfbmdgcfjbbpaeojofohoefgiehjai'},
                        {name:'Native Client',
                         filename:'internal-nacl-plugin'},
                    ];
                    p.refresh=()=>{};
                    p.item=(i)=>p[i];
                    p.namedItem=(n)=>p.find(x=>x.name===n);
                    p.length=p.length;
                    return p;
                }
            });

            Object.defineProperty(navigator, 'languages',
                {get: () => ['en-US', 'en']});

            Object.defineProperty(navigator, 'platform',
                {get: () => 'Win32'});

            window.chrome = {
                runtime: {
                    connect:     ()=>{},
                    sendMessage: ()=>{},
                    onMessage:   {addListener:()=>{},removeListener:()=>{}},
                },
                loadTimes: function(){},
                csi:       function(){},
                app:       {},
            };

            const origQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (p) =>
                p.name === 'notifications'
                ? Promise.resolve({state: Notification.permission})
                : origQuery(p);
        """
    })

    logger.info("✅  Driver ready")
    return driver