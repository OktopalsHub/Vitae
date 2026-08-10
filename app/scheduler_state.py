"""In-memory catalogue sync status for UI / ops (single process)."""

from __future__ import annotations

from datetime import datetime
from threading import Lock
from typing import Any

_lock = Lock()
_state: dict[str, Any] = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "source": "",
    "fetched": 0,
    "created": 0,
    "updated": 0,
    "ok": None,
    "error": "",
    "last_result": None,
}


def catalogue_sync_status() -> dict[str, Any]:
    with _lock:
        return dict(_state)


def mark_sync_started() -> None:
    with _lock:
        _state.update(
            {
                "running": True,
                "started_at": datetime.utcnow().isoformat() + "Z",
                "finished_at": None,
                "source": "",
                "fetched": 0,
                "created": 0,
                "updated": 0,
                "ok": None,
                "error": "",
            }
        )


def mark_sync_progress(
    *,
    source: str,
    fetched: int,
    created: int,
    updated: int,
) -> None:
    with _lock:
        _state.update(
            {
                "running": True,
                "source": source,
                "fetched": fetched,
                "created": created,
                "updated": updated,
            }
        )


def mark_sync_finished(
    *,
    ok: bool,
    result: dict[str, Any] | None = None,
    error: str = "",
) -> None:
    with _lock:
        _state.update(
            {
                "running": False,
                "finished_at": datetime.utcnow().isoformat() + "Z",
                "ok": ok,
                "error": error or "",
                "last_result": result,
            }
        )
        if result:
            _state["fetched"] = result.get("fetched", _state["fetched"])
            _state["created"] = result.get("created", _state["created"])
            _state["updated"] = result.get("updated", _state["updated"])
