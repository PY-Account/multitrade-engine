from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def write_health(
    path: str | Path,
    status: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    health_path = Path(path)
    health_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "details": details or {},
    }
    temporary_path = health_path.with_name(f".{health_path.name}.tmp")
    temporary_path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary_path, health_path)
    return payload


def check_health(
    path: str | Path,
    max_age_seconds: int,
    *,
    now: datetime | None = None,
) -> tuple[bool, dict[str, Any]]:
    health_path = Path(path)
    if not health_path.is_file():
        return False, {"status": "missing", "path": str(health_path)}

    try:
        payload = json.loads(health_path.read_text(encoding="utf-8"))
        updated_at = datetime.fromisoformat(payload["updated_at"])
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return False, {"status": "invalid", "path": str(health_path)}

    if updated_at.tzinfo is None:
        return False, {"status": "invalid_timestamp"}

    checked_at = now or datetime.now(timezone.utc)
    age_seconds = (checked_at - updated_at).total_seconds()
    result = {
        "status": payload.get("status", "invalid"),
        "updated_at": updated_at.isoformat(),
        "age_seconds": max(0, round(age_seconds, 3)),
    }
    if age_seconds < -60:
        return False, {**result, "reason": "timestamp_is_in_the_future"}
    if age_seconds > max_age_seconds:
        return False, {**result, "reason": "heartbeat_is_stale"}
    if payload.get("status") != "ok":
        return False, {**result, "reason": "latest_heartbeat_failed"}
    return True, result
