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


def list_runs(
    limit: int | None = None,
    site: str | None = None,
    status: str | None = None,
    q: str | None = None,
) -> list[dict]:
    clauses = []
    params: list = []

    if site in ("zillow", "realtor"):
        clauses.append("site = %s")
        params.append(site)
    if status:
        clauses.append("status = %s")
        params.append(status)
    if q:
        clauses.append("(location ILIKE %s OR file_name ILIKE %s OR run_id ILIKE %s)")
        like = f"%{q}%"
        params.extend([like, like, like])

    query = "SELECT * FROM runs"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY created_at DESC"
    if limit:
        query += f" LIMIT {int(limit)}"

    rows = execute(query, params, fetch="all")
    return [dict(r) for r in rows] if rows else []


def get_agents_for_run(run_id: str, site: str) -> list[dict]:
    table = "zillow_agents" if site == "zillow" else "realtor_agents"
    rows = execute(
        f"""
        SELECT id, run_id, name, phone, email, address, website, profile_url, scraped_at
        FROM {table}
        WHERE run_id = %s
        ORDER BY id ASC
        """,
        (run_id,),
        fetch="all",
    )
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


def _iso(value) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def serialize_run(run: dict | None) -> dict | None:
    if not run:
        return None
    out = dict(run)
    for key in ("created_at", "started_at", "completed_at"):
        if key in out:
            out[key] = _iso(out.get(key))
    return out


def dashboard_stats() -> dict:
    runs      = list_runs()
    completed = [r for r in runs if r.get("status") == "completed"]
    failed    = [r for r in runs if r.get("status") == "failed"]
    running   = [r for r in runs if r.get("status") in {"queued", "running"}]

    zillow_runs  = [r for r in runs if r.get("site") == "zillow"]
    realtor_runs = [r for r in runs if r.get("site") == "realtor"]

    # Agents scraped by day (last 14 days of completed runs)
    by_day: dict[str, int] = {}
    for r in completed:
        created = r.get("created_at")
        if not created:
            continue
        day = _iso(created)[:10]
        by_day[day] = by_day.get(day, 0) + int(r.get("scraped_agents") or 0)
    timeline = sorted(by_day.items())[-14:]

    return {
        "total_runs":           len(runs),
        "completed_runs":       len(completed),
        "failed_runs":          len(failed),
        "active_runs":          len(running),
        "total_agents_scraped": sum(int(r.get("scraped_agents") or 0) for r in completed),
        "zillow_runs":          len(zillow_runs),
        "realtor_runs":         len(realtor_runs),
        "zillow_agents":        sum(int(r.get("scraped_agents") or 0) for r in completed if r.get("site") == "zillow"),
        "realtor_agents":       sum(int(r.get("scraped_agents") or 0) for r in completed if r.get("site") == "realtor"),
        "latest_runs":          [serialize_run(r) for r in runs[:5]],
        "charts": {
            "status": {
                "labels": ["Completed", "Failed", "Active"],
                "values": [len(completed), len(failed), len(running)],
            },
            "sites": {
                "labels": ["Zillow", "Realtor"],
                "values": [len(zillow_runs), len(realtor_runs)],
            },
            "agents_timeline": {
                "labels": [d for d, _ in timeline],
                "values": [v for _, v in timeline],
            },
        },
    }