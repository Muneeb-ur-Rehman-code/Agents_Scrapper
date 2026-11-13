from __future__ import annotations
# ============================================================
#  config.py — Runtime configuration (loads from .env)
# ============================================================
import os
from dotenv import load_dotenv

load_dotenv()

# ── URLs ─────────────────────────────────────────────────────
ZILLOW_AGENT_URL  = "https://www.zillow.com/professionals/real-estate-agent-reviews/"
REALTOR_AGENT_URL = "https://www.realtor.com/realestateagents"

# ── Runtime values (set by frontend_server.py before scraping)
WEBSITE       = "zillow"
SEARCH_QUERY  = ""
MAX_AGENTS    = 100
RANGE_START   = 1        # ← NEW: e.g. 1
RANGE_END     = 100      # ← NEW: e.g. 100

# ── Chrome Profile ───────────────────────────────────────────
CHROME_PROFILE_DIR = os.path.join(os.path.dirname(__file__), "chrome_profile")

# ── Timing ───────────────────────────────────────────────────
PAGE_LOAD_WAIT          = 6
SEARCH_RESULTS_WAIT     = 6
CAPTCHA_HOLD_SECONDS    = 12
PAGINATION_WAIT         = 5
BETWEEN_CARD_WAIT_MIN   = 1.5
BETWEEN_CARD_WAIT_MAX   = 3.0

# ── Browser ──────────────────────────────────────────────────
HEADLESS        = os.getenv("HEADLESS", "false").lower() == "true"  # ← from .env
# Realtor/Kasada: optional override. Empty = auto (headed on Linux VPS).
_REALTOR_HEADLESS_ENV = (os.getenv("REALTOR_HEADLESS") or "").strip().lower()
REALTOR_HEADLESS = (
    True if _REALTOR_HEADLESS_ENV in {"1", "true", "yes"}
    else False if _REALTOR_HEADLESS_ENV in {"0", "false", "no"}
    else None
)
PROXY_SERVER = (os.getenv("PROXY_SERVER") or "").strip()  # e.g. http://user:pass@host:port
# Realtor-only proxy (recommended on VPS — keeps Zillow on direct IP if unset)
REALTOR_PROXY_SERVER = (os.getenv("REALTOR_PROXY_SERVER") or "").strip()
WINDOW_WIDTH    = 1400
WINDOW_HEIGHT   = 900
RANDOM_SCROLL   = True
MAX_RETRIES     = 3
# Parallel scrapes: max concurrent browser jobs (Chrome is heavy)
# On VPS + shared Xvfb, 2 is the practical max; 3+ often loses result pages.
try:
    MAX_CONCURRENT_SCRAPES = max(1, int(os.getenv("MAX_CONCURRENT_SCRAPES", "2")))
except ValueError:
    MAX_CONCURRENT_SCRAPES = 2

# ── Output ───────────────────────────────────────────────────
OUTPUT_DIR = os.getenv("OUTPUT_DIR", os.path.join(os.path.dirname(__file__), "output"))

# ── Debug ────────────────────────────────────────────────────
DEBUG_REALTOR = False


# ════════════════════════════════════════════════════════════
#  SELECTORS — Zillow
# ════════════════════════════════════════════════════════════
ZILLOW_SELECTORS = {
    "search_input": (
        "input#location-search-input,"
        "input[id*='location-search'],"
        "input[placeholder*='City'],"
        "input[placeholder*='neighborhood'],"
        "input[aria-label*='location'],"
        "input[name='locationText']"
    ),
    "search_btn": (
        "button#location-search-button,"
        "button[id*='location-search-button'],"
        "button[class*='SearchButton'],"
        "button[type='submit']"
    ),
    "agent_card_link":  "a[href*='/profile/']",
    "agent_name":       "h1",
    "agent_phone":      "a[href^='tel:']",
    "agent_email":      "a[href^='mailto:']",
    # Zillow's "Agent pages" bar sometimes has no role="navigation" attribute
    "pagination_nav":   "nav[aria-label='Agent pages']",
    "next_page_btn":    "button[title='Next page']:not([disabled])",
    "captcha_wrapper":  "#px-captcha-wrapper",
    "captcha_container":"div.px-captcha-container",
    "captcha_hold_div": "div#px-captcha",
    "captcha_any":      "[class*='px-captcha']",
}


# ════════════════════════════════════════════════════════════
#  SELECTORS — Realtor.com
# ════════════════════════════════════════════════════════════
REALTOR_SELECTORS = {
    "search_input": "input#search-bar",
    "search_btn":   "button[aria-label='Search']",
    "intent_both":  "a.NavLinkstyles__StyledNavLink-rui__sc-1bc5j16-0.cqAFYe",
    "autocomplete_item": (
        "ul#search-bar-listbox li[role='option'],"
        ".search-bar-dropdown ul#search-bar-listbox li[role='option'],"
        "li[role='option'],"
        "[data-testid='suggestion-item']"
    ),
    "autocomplete_list": (
        "ul#search-bar-listbox,"
        ".search-bar-dropdown ul#search-bar-listbox,"
        "[role='listbox']"
    ),
    "agent_card": (
        "div#agent-card,"
        "div[id='agent-card'],"
        "div[data-testid='component-agentCard'],"
        "div[class*='agent-list-card'],"
        "div[class*='AgentCard']"
    ),
    "agent_card_link": (
        "a[href*='/realestateagents/'],"
        "a[data-testid='agent-name']"
    ),
    "agent_name":    "h1",
    "agent_phone":   "a[href^='tel:']",
    "agent_address": "div[data-testid='icon-location'] ~ div p, svg[data-testid='icon-location'] ~ div p",
    "agent_website": "div[data-testid='icon-globe'] ~ div a, svg[data-testid='icon-globe'] ~ div a",
    "next_page_btn": (
        "a[aria-label='Next'],"
        "a[title='Next Page'],"
        "button[aria-label='Next'],"
        "a[rel='next']"
    ),
    "captcha_wrapper":   "#px-captcha-wrapper",
    "captcha_container": "div.px-captcha-container",
    "captcha_hold_div":  "div#px-captcha",
    "captcha_any":       "[class*='px-captcha']",
}

# ── Thread-local / concurrent isolation ──────────────────────
import sys
import threading

class _ConfigModule(object):
    def __init__(self, original_module):
        self._original = original_module
        self._local = threading.local()

    def __getattr__(self, name):
        if name in {"_original", "_local"}:
            return super().__getattribute__(name)
        if hasattr(self._local, name):
            return getattr(self._local, name)
        if name == "CHROME_PROFILE_DIR":
            run_id = getattr(self._local, "RUN_ID", None)
            base_dir = os.path.join(os.path.dirname(__file__), "chrome_profile")
            if run_id:
                return os.path.join(base_dir, "runs", run_id)
            return base_dir
        return getattr(self._original, name)

    def __setattr__(self, name, value):
        if name in {"_original", "_local"}:
            super().__setattr__(name, value)
        else:
            setattr(self._local, name, value)

sys.modules[__name__] = _ConfigModule(sys.modules[__name__])