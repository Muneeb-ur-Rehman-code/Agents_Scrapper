from __future__ import annotations

import logging
import os
from datetime import datetime

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

from config import OUTPUT_DIR

logger = logging.getLogger("AgentScraper")

HEADERS = [
    "Run ID",
    "Site",
    "Name",
    "Phone",
    "Email",
    "Address",
    "Website",
    "Profile URL",
    "Scrape Date",
]


def _safe_location(location: str) -> str:
    return location.lower().replace(" ", "_").replace(",", "")


def _make_path(website: str, location: str, run_id: str | None) -> str:
    """Build the output XLSX path based on website + run + location."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    safe_loc = _safe_location(location)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_label = run_id or "manual"
    filename = f"{website}_run-{run_label}_agents_{safe_loc}_{timestamp}.xlsx"
    return os.path.join(OUTPUT_DIR, filename)


def _init_workbook(output_path: str) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Leads"
    ws.append(HEADERS)

    header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)

    for idx, header in enumerate(HEADERS, start=1):
        cell = ws.cell(row=1, column=idx, value=header)
        cell.fill = header_fill
        cell.font = header_font
        ws.column_dimensions[cell.column_letter].width = max(len(header) + 2, 14)

    wb.save(output_path)


def save_agents(
    agents: list[dict],
    page: int,
    website: str,
    location: str,
    output_path: str | None = None,
    run_id: str | None = None,
) -> str:
    """
    Append agent records to an XLSX file.

    Returns the path that was written to (so callers can reuse
    the same file across pages).
    """
    if not agents:
        return output_path or ""

    if not output_path:
        output_path = _make_path(website, location, run_id=run_id)

    if not os.path.isfile(output_path):
        _init_workbook(output_path)

    wb = load_workbook(output_path)
    ws = wb.active

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    site_label = website.capitalize()
    run_label = run_id or "manual"

    for a in agents:
        ws.append([
            run_label,
            site_label,
            a.get("name", "N/A"),
            a.get("phone", "N/A"),
            a.get("email", "N/A"),
            a.get("address", "N/A"),
            a.get("website", "N/A"),
            a.get("profile_url", "N/A"),
            ts,
        ])

    wb.save(output_path)
    logger.info(f"💾  Page {page}: {len(agents)} agents saved → {os.path.basename(output_path)}")
    return output_path
