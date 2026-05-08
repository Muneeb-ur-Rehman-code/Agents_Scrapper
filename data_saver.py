from __future__ import annotations
# ============================================================
#  data_saver.py  —  Save scraped agent data to CSV
#
#  Supports both Zillow and Realtor.com agent data.
#  Each run creates a timestamped CSV file in output/.
# ============================================================
import os, logging, csv
from datetime import datetime
from config import OUTPUT_DIR

logger = logging.getLogger("AgentScraper")


def _make_path(website: str, location: str) -> str:
    """Build the output CSV path based on website + location."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    safe_loc  = location.lower().replace(" ", "_").replace(",", "")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename  = f"{website}_agents_{safe_loc}_{timestamp}.csv"
    return os.path.join(OUTPUT_DIR, filename)


def save_agents(
    agents: list[dict],
    page: int,
    website: str,
    location: str,
    output_path: str | None = None,
) -> str:
    """
    Append agent records to a CSV file.

    Returns the path that was written to (so callers can reuse
    the same file across pages).
    """
    if not agents:
        return output_path or ""

    # Create path on first call, reuse on subsequent calls
    if not output_path:
        output_path = _make_path(website, location)

    file_exists = os.path.isfile(output_path)

    # Universal field set that works for both sites
    fieldnames = [
        "Name",
        "Phone",
        "Email",
        "Address",
        "Website",
        "Profile URL",
        "Source",
        "Scrape Date",
    ]

    with open(output_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)

        if not file_exists:
            writer.writeheader()

        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for a in agents:
            writer.writerow({
                "Name":        a.get("name",        "N/A"),
                "Phone":       a.get("phone",       "N/A"),
                "Email":       a.get("email",       "N/A"),
                "Address":     a.get("address",     "N/A"),
                "Website":     a.get("website",     "N/A"),
                "Profile URL": a.get("profile_url", "N/A"),
                "Source":      website.capitalize(),
                "Scrape Date": ts,
            })

    logger.info(f"💾  Page {page}: {len(agents)} agents saved → {os.path.basename(output_path)}")
    return output_path