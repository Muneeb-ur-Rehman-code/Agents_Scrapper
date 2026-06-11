from __future__ import annotations
# ============================================================
#  config.py  —  Runtime configuration
#
#  Values that were hardcoded (SEARCH_QUERY, MAX_AGENTS, etc.)
#  are now set at runtime from main.py user prompts.
# ============================================================
import os

# ── URLs ─────────────────────────────────────────────────────
ZILLOW_AGENT_URL  = "https://www.zillow.com/professionals/real-estate-agent-reviews/"
REALTOR_AGENT_URL = "https://www.realtor.com/realestateagents"

# ── Runtime values (set by main.py before scraping) ──────────
WEBSITE       = "zillow"     # "zillow" or "realtor"
SEARCH_QUERY  = ""           # location typed by user
MAX_AGENTS    = 10           # how many agents to collect

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
HEADLESS        = False
WINDOW_WIDTH    = 1400
WINDOW_HEIGHT   = 900
RANDOM_SCROLL   = True
MAX_RETRIES     = 3

# ── Output ───────────────────────────────────────────────────
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")

# ── Debug ──────────────────────────────────────────────────
DEBUG_REALTOR = False


# ════════════════════════════════════════════════════════════
#  SELECTORS  —  Zillow
# ════════════════════════════════════════════════════════════
ZILLOW_SELECTORS = {
    # ── Agent Search ─────────────────────────────────────────
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

    # ── Agent Cards ──────────────────────────────────────────
    "agent_card_link": "a[href*='/profile/']",

    # ── Agent Profile ────────────────────────────────────────
    "agent_name":  "h1",
    "agent_phone": "a[href^='tel:']",
    "agent_email": "a[href^='mailto:']",

    # ── Pagination (from user-provided nav HTML) ─────────────
    "pagination_nav":  "nav[role='navigation'][aria-label='Agent pages']",
    "next_page_btn":   "button[title='Next page']:not([disabled])",

    # ── Captcha (PerimeterX) ─────────────────────────────────
    "captcha_wrapper":   "#px-captcha-wrapper",
    "captcha_container": "div.px-captcha-container",
    "captcha_hold_div":  "div#px-captcha",
    "captcha_any":       "[class*='px-captcha']",
}


# ════════════════════════════════════════════════════════════
#  SELECTORS  —  Realtor.com
# ════════════════════════════════════════════════════════════
REALTOR_SELECTORS = {
    # ── Search ───────────────────────────────────────────────
    "search_input": "input#search-bar",
    "search_btn":   "button[aria-label='Search']",
    "intent_both":   "a.NavLinkstyles__StyledNavLink-rui__sc-1bc5j16-0.cqAFYe",

    # ── Autocomplete ─────────────────────────────────────────
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

    # ── Agent Cards (container + individual) ─────────────────
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

    # ── Agent Detail ─────────────────────────────────────────
    "agent_name":    "h1",
    "agent_phone":   "a[href^='tel:']",
    "agent_address": "div[data-testid='icon-location'] ~ div p, svg[data-testid='icon-location'] ~ div p",
    "agent_website": "div[data-testid='icon-globe'] ~ div a, svg[data-testid='icon-globe'] ~ div a",

    # ── Pagination ───────────────────────────────────────────
    "next_page_btn": (
        "a[aria-label='Next'],"
        "a[title='Next Page'],"
        "button[aria-label='Next'],"
        "a[rel='next']"
    ),

    # ── Captcha (PerimeterX — same provider) ─────────────────
    "captcha_wrapper":   "#px-captcha-wrapper",
    "captcha_container": "div.px-captcha-container",
    "captcha_hold_div":  "div#px-captcha",
    "captcha_any":       "[class*='px-captcha']",
}