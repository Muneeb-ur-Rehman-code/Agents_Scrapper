from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime

from config import OUTPUT_DIR

_LOCK = threading.Lock()
_REGISTRY_PATH = os.path.join(OUTPUT_DIR, "runs_registry.json")


def _ensure_output_dir() -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)


def _read_registry() -> list[dict]:
    _ensure_output_dir()
    if not os.path.isfile(_REGISTRY_PATH):
        return []

    with open(_REGISTRY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_registry(data: list[dict]) -> None:
    _ensure_output_dir()
    with open(_REGISTRY_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=True, indent=2)


def new_run_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    short = uuid.uuid4().hex[:6]
    return f"{stamp}-{short}"


def create_run(site: str, location: str, requested_agents: int) -> dict:
    run = {
        "run_id": new_run_id(),
        "site": site,
        "location": location,
        "requested_agents": requested_agents,
        "debug_mode": False,
        "status": "queued",
        "scraped_agents": 0,
        "pages_scraped": 0,
        "file_name": "",
        "debug_file_name": "",
        "error": "",
        "created_at": datetime.now().isoformat(),
        "completed_at": "",
    }

    with _LOCK:
        data = _read_registry()
        data.append(run)
        _write_registry(data)

    return run


def update_run(run_id: str, **fields) -> dict | None:
    with _LOCK:
        data = _read_registry()
        for idx, run in enumerate(data):
            if run["run_id"] == run_id:
                data[idx] = {**run, **fields}
                _write_registry(data)
                return data[idx]
    return None


def get_run(run_id: str) -> dict | None:
    with _LOCK:
        data = _read_registry()
        for run in data:
            if run["run_id"] == run_id:
                return run
    return None


def list_runs(limit: int | None = None) -> list[dict]:
    with _LOCK:
        data = _read_registry()

    ordered = sorted(data, key=lambda r: r.get("created_at", ""), reverse=True)
    if limit is not None:
        return ordered[:limit]
    return ordered


def dashboard_stats() -> dict:
    runs = list_runs()
    completed = [r for r in runs if r.get("status") == "completed"]
    return {
        "total_runs": len(runs),
        "completed_runs": len(completed),
        "total_agents_scraped": sum(int(r.get("scraped_agents", 0) or 0) for r in completed),
        "latest_runs": runs[:15],
    }


def delete_run(run_id: str, remove_files: bool = True) -> dict | None:
    with _LOCK:
        data = _read_registry()
        target = None
        kept = []
        for run in data:
            if run.get("run_id") == run_id:
                target = run
            else:
                kept.append(run)

        if target is None:
            return None

        _write_registry(kept)

    if remove_files:
        for key in ("file_name", "debug_file_name"):
            name = (target.get(key) or "").strip()
            if not name:
                continue
            file_path = os.path.join(OUTPUT_DIR, os.path.basename(name))
            if os.path.isfile(file_path):
                try:
                    os.remove(file_path)
                except OSError:
                    pass

    return target
