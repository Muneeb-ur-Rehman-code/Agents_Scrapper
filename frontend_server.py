from __future__ import annotations
# ============================================================
#  frontend_server.py — Flask API + Dashboard
#  Updated: range_start/range_end support, .env loading
# ============================================================
import logging
import os
import threading
from datetime import datetime
from typing import Any

from dotenv import load_dotenv
from flask import (
    Flask, abort, jsonify, render_template,
    request, send_from_directory, url_for,
    redirect, session
)

load_dotenv()

import config
from config import OUTPUT_DIR
from run_registry import (
    create_run, dashboard_stats, delete_run,
    get_agents_for_run, get_run, list_runs,
    serialize_run, update_run,
)
from db import test_connection, execute
from location_utils import is_valid_realtor_location, normalize_realtor_location
from openpyxl import load_workbook
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "fallback_secret_key")
app.config["TEMPLATES_AUTO_RELOAD"] = True

_RUN_LOCK      = threading.Lock()
_ACTIVE_RUN_ID: str | None = None


# ── Auth / Users ─────────────────────────────────────────────
def _current_user_role() -> str | None:
    return session.get("role")


def _require_logged_in() -> bool:
    return session.get("user_id") is not None


def _require_admin() -> bool:
    return _current_user_role() == "admin"


def ensure_users_table() -> None:
    """
    One-time DB bootstrap for login/user management.
    Creates `users` table if missing and (optionally) a default admin if empty.
    """
    execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id            SERIAL PRIMARY KEY,
            username      TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL DEFAULT 'operator',
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )

    row = execute("SELECT COUNT(*) AS c FROM users", fetch="one")
    count = int((row or {}).get("c") or 0)
    if count > 0:
        return

    admin_username = os.getenv("ADMIN_USERNAME", "admin")
    admin_password = os.getenv("ADMIN_PASSWORD", "admin123")

    execute(
        """
        INSERT INTO users (username, password_hash, role)
        VALUES (%s, %s, 'admin')
        """,
        (admin_username, generate_password_hash(admin_password)),
    )
    logging.warning("Created initial admin: %s / %s", admin_username, admin_password)


@app.before_request
def _auth_guard():
    # Allow static assets and login routes
    if request.path.startswith("/static/"):
        return None
    if request.path in {"/login", "/api/login", "/api/logout"}:
        return None
    if request.method == "OPTIONS":
        return None

    if _require_logged_in():
        return None

    # For API calls, return JSON 401 to avoid breaking the JS redirect logic.
    if request.path.startswith("/api/") or request.path.startswith("/download/"):
        return jsonify({"error": "Unauthorized"}), 401
    return redirect(url_for("login_page"))


@app.get("/login")
def login_page():
    if _require_logged_in():
        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.post("/api/login")
def api_login():
    payload = request.get_json(silent=True) or request.form
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""

    if not username or not password:
        return jsonify({"error": "Username and password are required."}), 400

    user = execute(
        """
        SELECT id, username, password_hash, role
        FROM users
        WHERE username = %s
        """,
        (username,),
        fetch="one",
    )

    if not user:
        return jsonify({"error": "Invalid username or password."}), 401

    if not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "Invalid username or password."}), 401

    session["user_id"] = int(user["id"])
    session["username"] = user["username"]
    session["role"] = user.get("role") or "operator"

    return jsonify({"ok": True, "username": session["username"], "role": session["role"]})


@app.post("/api/logout")
def api_logout():
    session.clear()
    return jsonify({"ok": True})


@app.get("/api/me")
def api_me():
    if not _require_logged_in():
        return jsonify({"logged_in": False})
    user_id = session.get("user_id")
    row = execute(
        "SELECT id, username, role, created_at FROM users WHERE id = %s",
        (user_id,),
        fetch="one",
    )
    if not row:
        return jsonify({"logged_in": False})
    created = row.get("created_at")
    if created and hasattr(created, "isoformat"):
        created = created.isoformat()
    return jsonify({
        "logged_in": True,
        "id": int(row["id"]),
        "username": row["username"],
        "role": row.get("role") or "operator",
        "created_at": created,
    })


@app.post("/api/me/password")
def api_me_password():
    if not _require_logged_in():
        return jsonify({"error": "Unauthorized"}), 401

    payload = request.get_json(silent=True) or request.form
    current_password = payload.get("current_password") or ""
    new_password = payload.get("new_password") or ""
    confirm_password = payload.get("confirm_password") or ""

    if not current_password or not new_password:
        return jsonify({"error": "Current and new password are required."}), 400
    if len(new_password) < 6:
        return jsonify({"error": "New password must be at least 6 characters."}), 400
    if new_password != confirm_password:
        return jsonify({"error": "New password and confirmation do not match."}), 400
    if current_password == new_password:
        return jsonify({"error": "New password must be different from the current one."}), 400

    user_id = session.get("user_id")
    row = execute(
        "SELECT id, password_hash FROM users WHERE id = %s",
        (user_id,),
        fetch="one",
    )
    if not row:
        return jsonify({"error": "User not found."}), 404
    if not check_password_hash(row["password_hash"], current_password):
        return jsonify({"error": "Current password is incorrect."}), 400

    execute(
        "UPDATE users SET password_hash = %s WHERE id = %s",
        (generate_password_hash(new_password), user_id),
    )
    return jsonify({"ok": True, "message": "Password updated successfully."})


@app.get("/api/users")
def api_users_list():
    if not _require_logged_in():
        return jsonify({"error": "Unauthorized"}), 401
    rows = execute(
        "SELECT id, username, role, created_at FROM users ORDER BY id DESC",
        fetch="all",
    )
    users = [dict(r) for r in (rows or [])]
    for u in users:
        if u.get("created_at") and hasattr(u["created_at"], "isoformat"):
            u["created_at"] = u["created_at"].isoformat()
    return jsonify({"users": users})


@app.post("/api/users")
def api_users_add():
    if not _require_admin():
        return jsonify({"error": "Forbidden"}), 403

    payload = request.get_json(silent=True) or request.form
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    role = (payload.get("role") or "operator").strip().lower()

    if not username or len(username) < 3:
        return jsonify({"error": "Username must be at least 3 characters."}), 400
    if not password or len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters."}), 400
    if role not in {"admin", "operator"}:
        role = "operator"

    try:
        execute(
            """
            INSERT INTO users (username, password_hash, role)
            VALUES (%s, %s, %s)
            """,
            (username, generate_password_hash(password), role),
        )
    except Exception as e:
        return jsonify({"error": f"Could not create user: {e}"}), 400

    return jsonify({"ok": True})


@app.delete("/api/users/<int:user_id>")
def api_users_delete(user_id: int):
    if not _require_admin():
        return jsonify({"error": "Forbidden"}), 403

    if session.get("user_id") == user_id:
        return jsonify({"error": "You cannot delete your own account."}), 400

    target = execute(
        "SELECT id, username, role FROM users WHERE id = %s",
        (user_id,),
        fetch="one",
    )
    if not target:
        return jsonify({"error": "User not found."}), 404

    if (target.get("role") or "").lower() == "admin":
        admins = execute(
            "SELECT COUNT(*) AS c FROM users WHERE role = 'admin'",
            fetch="one",
        )
        if int((admins or {}).get("c") or 0) <= 1:
            return jsonify({"error": "Cannot delete the last admin user."}), 400

    execute("DELETE FROM users WHERE id = %s", (user_id,))
    return jsonify({"ok": True})


# ── Logging ───────────────────────────────────────────────────
def setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if root.handlers:
        return
    handler   = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    handler.setFormatter(formatter)
    root.addHandler(handler)


# ── Active run helpers ────────────────────────────────────────
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


# ── Debug log handler ─────────────────────────────────────────
def _attach_run_log_handler(
    run_id: str, debug_mode: bool
) -> tuple[logging.FileHandler | None, int]:
    if not debug_mode:
        return None, logging.getLogger().level

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    log_name = f"run_{run_id}_debug.log"
    log_path = os.path.join(OUTPUT_DIR, log_name)

    root           = logging.getLogger()
    previous_level = root.level
    root.setLevel(logging.DEBUG)

    handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s - %(message)s")
    )
    root.addHandler(handler)
    update_run(run_id, debug_file_name=log_name)
    return handler, previous_level


def _detach_run_log_handler(
    handler: logging.FileHandler | None, previous_level: int
) -> None:
    root = logging.getLogger()
    if handler:
        root.removeHandler(handler)
        handler.close()
    root.setLevel(previous_level)


# ── Scrape job ────────────────────────────────────────────────
def _run_scrape_job(
    run_id:     str,
    site:       str,
    location:   str,
    max_agents: int,
    range_start: int,
    range_end:   int,
    debug_mode: bool,
) -> None:
    logging.info("Starting run %s (%s) range %d-%d", run_id, site, range_start, range_end)
    update_run(run_id, status="running", started_at=datetime.now().isoformat())
    file_handler, old_level = _attach_run_log_handler(run_id, debug_mode)

    driver = None
    try:
        config.WEBSITE      = site
        config.SEARCH_QUERY = location
        config.MAX_AGENTS   = max_agents
        config.RANGE_START  = range_start
        config.RANGE_END    = range_end
        config.DEBUG_REALTOR = debug_mode

        if site == "zillow":
            from driver_setup import build_driver
            driver = build_driver()
            from zillow_scraper import run_zillow
            result = run_zillow(driver, run_id=run_id)
        else:
            from realtor_scraper import run_realtor
            result = run_realtor(run_id=run_id)

        output_path = result.get("output_path") or ""
        output_name = os.path.basename(output_path) if output_path else ""
        scrape_error = (result.get("error") or "").strip()

        if scrape_error and not result.get("total_scraped"):
            update_run(
                run_id,
                status="failed",
                scraped_agents=result.get("total_scraped", 0),
                pages_scraped=result.get("pages_scraped", 0),
                error=scrape_error,
                completed_at=datetime.now().isoformat(),
            )
            logging.error("Run %s failed: %s", run_id, scrape_error)
        else:
            update_run(
                run_id,
                status         = "completed",
                scraped_agents = result.get("total_scraped", 0),
                pages_scraped  = result.get("pages_scraped", 0),
                file_name      = output_name,
                completed_at   = datetime.now().isoformat(),
            )
            logging.info("Completed run %s", run_id)

    except Exception as e:
        update_run(
            run_id,
            status       = "failed",
            error        = str(e),
            completed_at = datetime.now().isoformat(),
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


def _agents_from_excel(file_name: str) -> list[dict]:
    if not file_name:
        return []
    path = os.path.join(OUTPUT_DIR, os.path.basename(file_name))
    if not os.path.isfile(path):
        return []
    try:
        wb = load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        wb.close()
        if not rows:
            return []
        headers = [str(h or "").strip().lower() for h in rows[0]]
        mapping = {
            "name": "name",
            "phone": "phone",
            "email": "email",
            "address": "address",
            "website": "website",
            "profile url": "profile_url",
            "profile_url": "profile_url",
            "scrape date": "scraped_at",
            "run id": "run_id",
            "site": "site",
        }
        agents = []
        for i, row in enumerate(rows[1:], start=1):
            item = {"id": i}
            for idx, header in enumerate(headers):
                key = mapping.get(header)
                if key and idx < len(row):
                    item[key] = row[idx] if row[idx] is not None else "N/A"
            if item.get("name"):
                agents.append(item)
        return agents
    except Exception:
        logging.exception("Failed reading excel %s", file_name)
        return []


def _serialize_agent(row: dict) -> dict:
    out = dict(row)
    if "scraped_at" in out and hasattr(out["scraped_at"], "isoformat"):
        out["scraped_at"] = out["scraped_at"].isoformat()
    return out


# ── Routes ────────────────────────────────────────────────────
def _render_app():
    return render_template(
        "dashboard.html",
        current_username=session.get("username"),
        current_role=session.get("role"),
    )


@app.get("/")
@app.get("/dashboard")
@app.get("/scrape")
@app.get("/data")
@app.get("/userhub")
@app.get("/settings")
@app.get("/detail/<run_id>")
def dashboard(run_id: str | None = None):
    return _render_app()


@app.get("/api/dashboard")
def dashboard_api():
    stats = dashboard_stats()
    return jsonify({**stats, "active_run_id": _get_active_run()})


@app.get("/api/runs")
def runs_list():
    site   = (request.args.get("site") or "").strip().lower() or None
    status = (request.args.get("status") or "").strip().lower() or None
    q      = (request.args.get("q") or "").strip() or None
    runs   = list_runs(site=site, status=status, q=q)
    payload = []
    for i, run in enumerate(runs, start=1):
        item = serialize_run(run) or {}
        item["serial"] = i
        if item.get("status") == "completed" and item.get("file_name"):
            item["download_url"] = url_for("download_file", filename=item["file_name"])
        payload.append(item)
    return jsonify({"runs": payload, "total": len(payload)})


@app.get("/api/runs/<run_id>/agents")
def run_agents(run_id: str):
    run = get_run(run_id)
    if not run:
        return jsonify({"error": "Run not found."}), 404

    agents = get_agents_for_run(run_id, run.get("site") or "zillow")
    if not agents and run.get("file_name"):
        agents = _agents_from_excel(run["file_name"])

    return jsonify({
        "run": serialize_run(run),
        "agents": [_serialize_agent(a) for a in agents],
        "total": len(agents),
    })


@app.post("/api/runs")
def start_run():
    payload     = request.get_json(silent=True) or request.form
    site        = (payload.get("site") or "zillow").strip().lower()
    location    = (payload.get("location") or "").strip()
    debug_mode  = _as_bool(payload.get("debug_mode"))

    # ── Range ─────────────────────────────────────────────────
    try:
        range_start = int(payload.get("range_start") or 1)
        range_end   = int(payload.get("range_end")   or 100)
    except ValueError:
        return jsonify({"error": "range_start and range_end must be numbers."}), 400

    max_agents = range_end - range_start + 1

    # ── Validation ────────────────────────────────────────────
    if site not in ("zillow", "realtor"):
        return jsonify({"error": "Supported sites: zillow, realtor"}), 400
    if not location:
        return jsonify({"error": "Location is required."}), 400
    if site == "realtor":
        if not is_valid_realtor_location(location):
            return jsonify({
                "error": (
                    "Realtor.com needs City, ST (e.g. New York, NY), City ST without comma, "
                    "full state name (Austin, Texas), or a 5-digit ZIP. "
                    f"Could not parse: “{location.strip()[:80]}”."
                ),
            }), 400
        location = normalize_realtor_location(location)
    if range_start < 1:
        return jsonify({"error": "range_start must be at least 1."}), 400
    if range_end < range_start:
        return jsonify({"error": "range_end must be greater than range_start."}), 400
    if max_agents > 500:
        return jsonify({"error": "Max 500 agents per run."}), 400

    # ── Check active run ──────────────────────────────────────
    active_run = _get_active_run()
    if active_run:
        active_entry = get_run(active_run)
        if active_entry and active_entry.get("status") in {"queued", "running"}:
            return jsonify({"error": f"Run {active_run} is already in progress."}), 409
        _set_active_run(None)

    # ── Create run ────────────────────────────────────────────
    run    = create_run(
        site             = site,
        location         = location,
        requested_agents = max_agents,
        range_start      = range_start,
        range_end        = range_end,
    )
    run_id = run["run_id"]
    update_run(run_id, debug_mode=debug_mode)
    _set_active_run(run_id)

    thread = threading.Thread(
        target = _run_scrape_job,
        args   = (run_id, site, location, max_agents, range_start, range_end, debug_mode),
        daemon = True,
    )
    thread.start()

    return jsonify({
        "run_id":     run_id,
        "status":     "queued",
        "status_url": url_for("run_status", run_id=run_id),
        "debug_mode": debug_mode,
        "range":      f"{range_start}-{range_end}",
    })


@app.get("/api/runs/<run_id>")
def run_status(run_id: str):
    run = get_run(run_id)
    if not run:
        return jsonify({"error": "Run not found."}), 404

    payload = serialize_run(run) or {}
    if run.get("status") == "completed" and run.get("file_name"):
        payload["download_url"] = url_for("download_file", filename=run["file_name"])

    return jsonify(payload)


@app.delete("/api/runs/<run_id>")
def remove_run(run_id: str):
    run = get_run(run_id)
    if not run:
        return jsonify({"error": "Run not found."}), 404
    if run.get("status") in {"queued", "running"}:
        return jsonify({"error": "Cannot delete a run in progress."}), 409

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
    test_connection()
    ensure_users_table()

    from driver_setup import start_virtual_display, stop_virtual_display
    import atexit
    start_virtual_display()
    atexit.register(stop_virtual_display)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    port = int(os.getenv("FLASK_PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)