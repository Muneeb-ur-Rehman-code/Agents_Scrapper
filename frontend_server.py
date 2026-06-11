from __future__ import annotations

import logging
import os
import threading
from datetime import datetime
from typing import Any

from flask import Flask, abort, jsonify, render_template, request, send_from_directory, url_for

import config
from config import OUTPUT_DIR
from run_registry import create_run, dashboard_stats, delete_run, get_run, update_run

app = Flask(__name__)

_RUN_LOCK = threading.Lock()
_ACTIVE_RUN_ID: str | None = None


def setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if root.handlers:
        return
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    handler.setFormatter(formatter)
    root.addHandler(handler)


def _set_active_run(run_id: str | None) -> None:
    global _ACTIVE_RUN_ID
    with _RUN_LOCK:
        _ACTIVE_RUN_ID = run_id


def _get_active_run() -> str | None:
    with _RUN_LOCK:
        return _ACTIVE_RUN_ID


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _attach_run_log_handler(run_id: str, debug_mode: bool) -> tuple[logging.FileHandler | None, int]:
    if not debug_mode:
        return None, logging.getLogger().level

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    log_name = f"run_{run_id}_debug.log"
    log_path = os.path.join(OUTPUT_DIR, log_name)

    root = logging.getLogger()
    previous_level = root.level
    root.setLevel(logging.DEBUG)

    handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s - %(message)s"))
    root.addHandler(handler)

    update_run(run_id, debug_file_name=log_name)
    return handler, previous_level


def _detach_run_log_handler(handler: logging.FileHandler | None, previous_level: int) -> None:
    root = logging.getLogger()
    if handler:
        root.removeHandler(handler)
        handler.close()
    root.setLevel(previous_level)


def _run_scrape_job(run_id: str, site: str, location: str, max_agents: int, debug_mode: bool) -> None:
    logging.info("Starting run %s (%s)", run_id, site)
    update_run(run_id, status="running", started_at=datetime.now().isoformat())
    file_handler, old_level = _attach_run_log_handler(run_id, debug_mode)

    driver = None
    try:
        config.WEBSITE = site
        config.SEARCH_QUERY = location
        config.MAX_AGENTS = max_agents
        config.DEBUG_REALTOR = debug_mode

        if site == "zillow":
            from driver_setup import build_driver
            driver = build_driver()
            from zillow_scraper import run_zillow
            result = run_zillow(driver, run_id=run_id)
        else:
            # Realtor.com uses nodriver internally (Kasada blocks Selenium)
            from realtor_scraper import run_realtor
            result = run_realtor(run_id=run_id)

        output_path = result.get("output_path") or ""
        output_name = os.path.basename(output_path) if output_path else ""
        update_run(
            run_id,
            status="completed",
            scraped_agents=result.get("total_scraped", 0),
            pages_scraped=result.get("pages_scraped", 0),
            file_name=output_name,
            completed_at=datetime.now().isoformat(),
        )
        logging.info("Completed run %s", run_id)
    except Exception as e:
        update_run(
            run_id,
            status="failed",
            error=str(e),
            completed_at=datetime.now().isoformat(),
        )
        logging.exception("Run %s failed", run_id)
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        _detach_run_log_handler(file_handler, old_level)
        if _get_active_run() == run_id:
            _set_active_run(None)


@app.get("/")
def dashboard():
    stats = dashboard_stats()
    runs = stats["latest_runs"]
    return render_template(
        "dashboard.html",
        stats=stats,
        runs=runs,
        active_run_id=_get_active_run(),
    )


@app.get("/api/dashboard")
def dashboard_api():
    stats = dashboard_stats()
    return jsonify(
        {
            **stats,
            "active_run_id": _get_active_run(),
        }
    )


@app.post("/api/runs")
def start_run():
    payload = request.get_json(silent=True) or request.form
    site = (payload.get("site") or "zillow").strip().lower()
    location = (payload.get("location") or "").strip()
    max_agents_raw = str(payload.get("max_agents") or "10").strip()
    debug_mode = _as_bool(payload.get("debug_mode"))

    if site not in ("zillow", "realtor"):
        return jsonify({"error": "Supported sites: zillow, realtor"}), 400
    if not location:
        return jsonify({"error": "Location is required."}), 400
    try:
        max_agents = int(max_agents_raw)
    except ValueError:
        return jsonify({"error": "max_agents must be a whole number."}), 400
    if max_agents < 1:
        return jsonify({"error": "max_agents must be greater than 0."}), 400

    active_run = _get_active_run()
    if active_run:
        active_entry = get_run(active_run)
        if active_entry and active_entry.get("status") in {"queued", "running"}:
            return jsonify({"error": f"Run {active_run} is already in progress."}), 409
        _set_active_run(None)

    run = create_run(site=site, location=location, requested_agents=max_agents)
    run_id = run["run_id"]
    update_run(run_id, debug_mode=debug_mode)
    _set_active_run(run_id)

    thread = threading.Thread(
        target=_run_scrape_job,
        args=(run_id, site, location, max_agents, debug_mode),
        daemon=True,
    )
    thread.start()

    return jsonify(
        {
            "run_id": run_id,
            "status": "queued",
            "status_url": url_for("run_status", run_id=run_id),
            "debug_mode": debug_mode,
        }
    )


@app.get("/api/runs/<run_id>")
def run_status(run_id: str):
    run = get_run(run_id)
    if not run:
        return jsonify({"error": "Run not found."}), 404

    payload = dict(run)
    if run.get("status") == "completed" and run.get("file_name"):
        payload["download_url"] = url_for("download_file", filename=run["file_name"])

    return jsonify(payload)


@app.delete("/api/runs/<run_id>")
def remove_run(run_id: str):
    run = get_run(run_id)
    if not run:
        return jsonify({"error": "Run not found."}), 404
    if run.get("status") in {"queued", "running"}:
        return jsonify({"error": "Cannot delete a run that is in progress."}), 409

    delete_run(run_id, remove_files=True)
    return jsonify({"ok": True})


@app.get("/download/<path:filename>")
def download_file(filename: str):
    safe_name = os.path.basename(filename)
    if safe_name != filename:
        abort(404)

    file_path = os.path.join(OUTPUT_DIR, safe_name)
    if not os.path.isfile(file_path):
        abort(404)
    return send_from_directory(OUTPUT_DIR, safe_name, as_attachment=True)


if __name__ == "__main__":
    setup_logging()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    app.run(host="0.0.0.0", port=5000, debug=False)
