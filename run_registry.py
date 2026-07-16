from __future__ import annotations
# ============================================================
#  run_registry.py — Replaces JSON file with PostgreSQL
# ============================================================
import uuid
import logging
from datetime import datetime
from db import execute

logger = logging.getLogger("AgentScraper")


def new_run_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    short = uuid.uuid4().hex[:6]
    return f"{stamp}-{short}"


def create_run(
    site: str,
    location: str,
    requested_agents: int,
    range_start: int = 1,
    range_end: int   = 100,
) -> dict:
    run_id = new_run_id()
    execute("""
        INSERT INTO runs (
            run_id, site, location, requested_agents,
            range_start, range_end, status, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, 'queued', NOW())
    """, (run_id, site, location, requested_agents, range_start, range_end))

    return get_run(run_id)


def update_run(run_id: str, **fields) -> dict | None:
    if not fields:
        return get_run(run_id)

    allowed = {
        "status", "scraped_agents", "pages_scraped",
        "file_name", "debug_file_name", "error",
        "debug_mode", "started_at", "completed_at",
        "range_start", "range_end",
    }
    filtered = {k: v for k, v in fields.items() if k in allowed}
    if not filtered:
        return get_run(run_id)

    set_clause = ", ".join(f"{k} = %s" for k in filtered)
    values     = list(filtered.values()) + [run_id]

    execute(
        f"UPDATE runs SET {set_clause} WHERE run_id = %s",
        values
    )
    return get_run(run_id)


def get_run(run_id: str) -> dict | None:
    row = execute(
        "SELECT * FROM runs WHERE run_id = %s",
        (run_id,), fetch="one"
    )
    return dict(row) if row else None


def list_runs(limit: int | None = None) -> list[dict]:
    query = "SELECT * FROM runs ORDER BY created_at DESC"
    if limit:
        query += f" LIMIT {limit}"
    rows = execute(query, fetch="all")
    return [dict(r) for r in rows] if rows else []


def delete_run(run_id: str, remove_files: bool = True) -> dict | None:
    import os
    from config import OUTPUT_DIR

    run = get_run(run_id)
    if not run:
        return None

    if remove_files:
        for key in ("file_name", "debug_file_name"):
            name = (run.get(key) or "").strip()
            if not name:
                continue
            path = os.path.join(OUTPUT_DIR, os.path.basename(name))
            if os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    execute("DELETE FROM runs WHERE run_id = %s", (run_id,))
    return run


def dashboard_stats() -> dict:
    runs      = list_runs()
    completed = [r for r in runs if r.get("status") == "completed"]
    return {
        "total_runs":           len(runs),
        "completed_runs":       len(completed),
        "total_agents_scraped": sum(int(r.get("scraped_agents") or 0) for r in completed),
        "latest_runs":          runs[:15],
    }