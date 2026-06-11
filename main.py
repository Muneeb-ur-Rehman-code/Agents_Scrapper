from __future__ import annotations
# ============================================================
#  main.py  —  Interactive Real Estate Agent Scraper
#
#  Supports:  Zillow  |  Realtor.com
#
#  The user is prompted for:
#    1. Which website to scrape
#    2. Which location to search
#    3. How many agents to collect
#
#  Then the scraper runs fully automated.
# ============================================================
import sys
import logging
import colorlog


def setup_logging():
    """Configure coloured console logging."""
    h = colorlog.StreamHandler()
    h.setFormatter(colorlog.ColoredFormatter(
        "%(log_color)s%(asctime)s  [%(levelname)-8s]%(reset)s  %(message)s",
        datefmt="%H:%M:%S",
        log_colors={
            "DEBUG":    "cyan",
            "INFO":     "green",
            "WARNING":  "yellow",
            "ERROR":    "red",
            "CRITICAL": "bold_red",
        }
    ))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # Avoid duplicate handlers on re-run
    if not root.handlers:
        root.addHandler(h)
    for lib in ("selenium", "urllib3", "undetected_chromedriver"):
        logging.getLogger(lib).setLevel(logging.WARNING)


# ════════════════════════════════════════════════════════════
#  INTERACTIVE PROMPTS
# ════════════════════════════════════════════════════════════

BANNER = r"""
╔══════════════════════════════════════════════════════════╗
║                                                          ║
║   🏠  Real Estate Agent Scraper                          ║
║                                                          ║
║   Supported websites:                                    ║
║     [1]  Zillow                                          ║
║     [2]  Realtor.com                                     ║
║                                                          ║
╚══════════════════════════════════════════════════════════╝
"""


def prompt_website() -> str:
    """Ask user which website to scrape. Returns 'zillow' or 'realtor'."""
    print(BANNER)
    while True:
        choice = input("  👉  Choose a website [1/2]: ").strip()
        if choice == "1":
            print("  ✅  Selected: Zillow\n")
            return "zillow"
        elif choice == "2":
            print("  ✅  Selected: Realtor.com\n")
            return "realtor"
        else:
            print("  ⚠️   Please enter 1 or 2.\n")


def prompt_location() -> str:
    """Ask user for the location to search."""
    while True:
        loc = input("  📍  Enter location (city, state, or zip): ").strip()
        if loc:
            print(f"  ✅  Location: {loc}\n")
            return loc
        print("  ⚠️   Location cannot be empty.\n")


def prompt_agent_count() -> int:
    """Ask user how many agents to scrape."""
    while True:
        raw = input("  👥  How many agents to scrape? [default: 10]: ").strip()
        if not raw:
            print("  ✅  Agent count: 10 (default)\n")
            return 10
        try:
            n = int(raw)
            if n < 1:
                raise ValueError
            print(f"  ✅  Agent count: {n}\n")
            return n
        except ValueError:
            print("  ⚠️   Enter a positive whole number.\n")


# ════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════

def main():
    setup_logging()
    log = logging.getLogger("AgentScraper")

    # ── 1. Get user inputs ────────────────────────────────────
    website  = prompt_website()
    location = prompt_location()
    count    = prompt_agent_count()

    # ── 2. Push inputs into config (runtime override) ─────────
    import config
    config.WEBSITE      = website
    config.SEARCH_QUERY = location
    config.MAX_AGENTS   = count

    site_label = "Zillow" if website == "zillow" else "Realtor.com"
    log.info("=" * 60)
    log.info(f"  🏠  {site_label}  —  {location}  —  {count} agents")
    log.info("=" * 60)

    driver = None
    try:
        if website == "zillow":
            # ── Zillow: uses Selenium + undetected-chromedriver ───
            from driver_setup import build_driver
            driver = build_driver()
            from zillow_scraper import run_zillow
            run_zillow(driver)
        else:
            # ── Realtor.com: uses nodriver (Kasada blocks Selenium)
            log.info("ℹ️  Realtor.com uses nodriver (no Selenium needed)")
            from realtor_scraper import run_realtor
            run_realtor()  # No driver arg — nodriver is built internally

    except KeyboardInterrupt:
        log.warning("⚠️  Interrupted by user")
    except Exception as e:
        log.critical(f"💥  Fatal: {e}", exc_info=True)
        sys.exit(1)
    finally:
        if driver:
            log.info("🛑  Closing browser …")
            try:
                driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    main()