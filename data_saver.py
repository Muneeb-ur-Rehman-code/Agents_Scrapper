from __future__ import annotations
# ============================================================
#  data_saver.py — Saves agents to PostgreSQL + Excel file
# ============================================================
import logging
import os
from datetime import datetime

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

from config import OUTPUT_DIR
from db import execute

logger = logging.getLogger("AgentScraper")

HEADERS = [
    "Run ID", "Site", "Name", "Phone", "Email",
    "Address", "Website", "Profile URL", "Scrape Date",
]


def _safe_location(location: str) -> str:
    return location.lower().replace(" ", "_").replace(",", "")


def _make_path(website: str, location: str, run_id: str | None) -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    safe_loc  = _safe_location(location)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_label = run_id or "manual"
    filename  = f"{website}_run-{run_label}_agents_{safe_loc}_{timestamp}.xlsx"
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


def _save_to_db(
    agents:  list[dict],
    website: str,
    run_id:  str | None,
) -> int:
    """
    Insert agents into zillow_agents or realtor_agents table.
    Skips duplicates based on UNIQUE(email, name).
    Returns count of newly inserted agents.
    """
    table    = "zillow_agents" if website == "zillow" else "realtor_agents"
    run_label = run_id or "manual"
    inserted  = 0

    for a in agents:
        try:
            execute(f"""
                INSERT INTO {table}
                    (run_id, name, phone, email, address, website, profile_url, scraped_at)
                VALUES
                    (%s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (email, name) DO NOTHING
            """, (
                run_label,
                a.get("name",        "N/A"),
                a.get("phone",       "N/A"),
                a.get("email",       "N/A"),
                a.get("address",     "N/A"),
                a.get("website",     "N/A"),
                a.get("profile_url", "N/A"),
            ))
            inserted += 1
        except Exception as e:
            logger.warning(f"   ⚠️  DB insert skipped ({a.get('name')}): {e}")

    return inserted


def save_agents(
    agents:      list[dict],
    page:        int,
    website:     str,
    location:    str,
    output_path: str | None = None,
    run_id:      str | None = None,
) -> str:
    """
    Save agents to:
      1. PostgreSQL (zillow_agents or realtor_agents table)
      2. Excel file in /output folder

    Returns the Excel file path.
    """
    if not agents:
        return output_path or ""

    # ── 1. Save to PostgreSQL ─────────────────────────────────
    inserted = _save_to_db(agents, website, run_id)
    logger.info(f"   🗄️  DB: {inserted}/{len(agents)} agents inserted (duplicates skipped)")

    # ── 2. Save to Excel ─────────────────────────────────────
    if not output_path:
        output_path = _make_path(website, location, run_id=run_id)

    if not os.path.isfile(output_path):
        _init_workbook(output_path)

    # Record the file on the run right away so a cancelled/stalled run
    # still has a downloadable file with everything scraped so far.
    if run_id:
        try:
            from run_registry import update_run
            update_run(run_id, file_name=os.path.basename(output_path))
        except Exception as exc:
            logger.warning("Could not record file name for run %s: %s", run_id, exc)

    wb = load_workbook(output_path)
    ws = wb.active

    ts         = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    site_label = website.capitalize()
    run_label  = run_id or "manual"

    for a in agents:
        ws.append([
            run_label,
            site_label,
            a.get("name",        "N/A"),
            a.get("phone",       "N/A"),
            a.get("email",       "N/A"),
            a.get("address",     "N/A"),
            a.get("website",     "N/A"),
            a.get("profile_url", "N/A"),
            ts,
        ])

    wb.save(output_path)
    logger.info(f"💾  Page {page}: {len(agents)} agents saved → {os.path.basename(output_path)}")
    return output_path