from __future__ import annotations

import json
import os
import subprocess
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable


UpdateRunner = Callable[[Path], tuple[int, str]]


MANAGED_ENV_KEYS = {
    "ALPACA_API_KEY_ID",
    "ALPACA_API_SECRET_KEY",
    "ALPACA_OPTIONS_API_KEY_ID",
    "ALPACA_OPTIONS_API_SECRET_KEY",
    "ANALYST_API_TOKEN",
    "DASHBOARD_DOMAIN",
    "TRADING_ALLOW_INDICATIVE_PAPER_OPTIONS",
    "TRADING_ALPACA_OPTIONS_ACCOUNT_UUID",
    "TRADING_AUTOMATION_ENABLED",
    "TRADING_EMERGENCY_STOP",
    "TRADING_PAPER_ORDER_SUBMISSION_ENABLED",
    "TRADING_STRATEGY_LAB_LOOKBACK_DAYS",
}

SECRET_ENV_MARKERS = ("KEY", "SECRET", "TOKEN", "PASSWORD")


def _default_update_runner(workdir: Path) -> tuple[int, str]:
    result = subprocess.run(
        ["bash", "ops/update.sh", str(workdir)],
        cwd=workdir,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=900,
        check=False,
    )
    return result.returncode, result.stdout


class AdminAgentState:
    def __init__(
        self,
        workdir: str | Path,
        token: str,
        *,
        update_runner: UpdateRunner = _default_update_runner,
    ) -> None:
        self.workdir = Path(workdir)
        self.token = token
        self.update_runner = update_runner
        self.lock = threading.Lock()
        self.last_action: dict[str, Any] = {
            "state": "idle",
            "updated_at": None,
        }

    def status(self) -> dict[str, Any]:
        with self.lock:
            action = json.loads(json.dumps(self.last_action))
        return {
            "status": "ok",
            "component": "admin_agent",
            "workdir": str(self.workdir),
            "last_action": action,
        }

    def settings(self) -> dict[str, Any]:
        values = self._read_env_file()
        keys = []
        for key in sorted(MANAGED_ENV_KEYS):
            value = values.get(key, "")
            secret = self._is_secret_key(key)
            keys.append(
                {
                    "key": key,
                    "configured": bool(value),
                    "secret": secret,
                    "value": "********" if secret and value else value,
                }
            )
        return {
            "status": "ok",
            "component": "admin_agent",
            "env_path": str(self.workdir / ".env"),
            "settings": keys,
        }

    def update_setting(
        self,
        key: str,
        value: str,
        requested_by: str,
    ) -> dict[str, Any]:
        if key not in MANAGED_ENV_KEYS:
            raise ValueError("setting_key_not_allowed")
        if len(value) > 4096:
            raise ValueError("setting_value_too_long")
        env_path = self.workdir / ".env"
        if not env_path.exists():
            raise ValueError("env_file_not_found")
        backup_path = (
            self.workdir
            / "local-backups"
            / f".env.before-admin.{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        )
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        original = env_path.read_text(encoding="utf-8")
        backup_path.write_text(original, encoding="utf-8")
        lines = original.splitlines()
        rendered = f"{key}={self._quote_env_value(value)}"
        updated = False
        next_lines = []
        for line in lines:
            if line.startswith(f"{key}=") or line.startswith(f"export {key}="):
                next_lines.append(rendered)
                updated = True
            else:
                next_lines.append(line)
        if not updated:
            next_lines.append(rendered)
        env_path.write_text("\n".join(next_lines) + "\n", encoding="utf-8")
        now = datetime.now(timezone.utc).isoformat()
        with self.lock:
            self.last_action = {
                "state": "completed",
                "action": "setting_update",
                "action_id": f"setting:{key}:{int(datetime.now(timezone.utc).timestamp())}",
                "requested_by": requested_by,
                "updated_at": now,
                "result": {
                    "key": key,
                    "backup_path": str(backup_path),
                    "secret": self._is_secret_key(key),
                },
            }
        return self.last_action.copy()

    def _read_env_file(self) -> dict[str, str]:
        env_path = self.workdir / ".env"
        if not env_path.exists():
            return {}
        values: dict[str, str] = {}
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("export "):
                stripped = stripped[7:].strip()
            if "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            if key in MANAGED_ENV_KEYS:
                values[key] = value.strip().strip("'\"")
        return values

    @staticmethod
    def _is_secret_key(key: str) -> bool:
        return any(marker in key for marker in SECRET_ENV_MARKERS)

    @staticmethod
    def _quote_env_value(value: str) -> str:
        if value == "":
            return ""
        if all(char.isalnum() or char in "._:/@+-" for char in value):
            return value
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    def start_update(self, requested_by: str) -> dict[str, Any]:
        with self.lock:
            if self.last_action.get("state") == "running":
                raise ValueError("admin_action_already_running")
            action_id = f"update:{int(datetime.now(timezone.utc).timestamp())}"
            now = datetime.now(timezone.utc).isoformat()
            self.last_action = {
                "state": "running",
                "action": "update",
                "action_id": action_id,
                "requested_by": requested_by,
                "requested_at": now,
                "updated_at": now,
            }
        thread = threading.Thread(
            target=self._run_update,
            args=(action_id, requested_by),
            daemon=True,
        )
        thread.start()
        return self.status()["last_action"]

    def _run_update(self, action_id: str, requested_by: str) -> None:
        try:
            returncode, output = self.update_runner(self.workdir)
            state = "completed" if returncode == 0 else "failed"
            payload = {
                "returncode": returncode,
                "output_tail": output[-12000:],
            }
        except Exception as exc:
            state = "failed"
            payload = {
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
        now = datetime.now(timezone.utc).isoformat()
        with self.lock:
            self.last_action = {
                "state": state,
                "action": "update",
                "action_id": action_id,
                "requested_by": requested_by,
                "updated_at": now,
                "result": payload,
            }


class AdminAgentHandler(BaseHTTPRequestHandler):
    state: AdminAgentState

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._send_json(200, {"status": "ok"})
            return
        if self.path == "/api/admin/status":
            if not self._authorized():
                self._send_json(401, {"error": "authentication_required"})
                return
            self._send_json(200, self.state.status())
            return
        if self.path == "/api/admin/settings":
            if not self._authorized():
                self._send_json(401, {"error": "authentication_required"})
                return
            self._send_json(200, self.state.settings())
            return
        self._send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if self.path not in {"/api/admin/update", "/api/admin/settings"}:
            self._send_json(404, {"error": "not_found"})
            return
        if not self._authorized():
            self._send_json(401, {"error": "authentication_required"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "invalid_content_length"})
            return
        if length > 4096:
            self._send_json(413, {"error": "request_size_invalid"})
            return
        body = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise ValueError("json_object_required")
            if self.path == "/api/admin/update":
                if payload.get("confirmation") != "RUN SERVER UPDATE":
                    raise ValueError("confirmation_required")
                action = self.state.start_update(
                    str(payload.get("requested_by") or "dashboard")
                )
            else:
                if payload.get("confirmation") != "UPDATE SERVER SETTING":
                    raise ValueError("confirmation_required")
                action = self.state.update_setting(
                    str(payload.get("key") or ""),
                    str(payload.get("value") or ""),
                    str(payload.get("requested_by") or "dashboard"),
                )
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(400, {"error": "invalid_json"})
            return
        except ValueError as exc:
            status = (
                409
                if str(exc) == "admin_action_already_running"
                else 400
            )
            self._send_json(status, {"error": str(exc)})
            return
        self._send_json(202, {"status": "accepted", "action": action})

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        return header == f"Bearer {self.state.token}"

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def create_admin_agent_server(
    host: str,
    port: int,
    *,
    workdir: str | Path,
    token: str,
    update_runner: UpdateRunner = _default_update_runner,
) -> ThreadingHTTPServer:
    if len(token) < 32:
        raise ValueError("ADMIN_AGENT_TOKEN must contain at least 32 characters")
    state = AdminAgentState(workdir, token, update_runner=update_runner)

    class ConfiguredHandler(AdminAgentHandler):
        pass

    ConfiguredHandler.state = state
    return ThreadingHTTPServer((host, port), ConfiguredHandler)


def run_admin_agent() -> int:
    host = os.getenv("ADMIN_AGENT_HOST", "127.0.0.1")
    port = int(os.getenv("ADMIN_AGENT_PORT", "8090"))
    token = os.getenv("ADMIN_AGENT_TOKEN", "")
    workdir = os.getenv("ADMIN_AGENT_WORKDIR", "/workspace")
    server = create_admin_agent_server(
        host, port, workdir=workdir, token=token
    )
    print(
        json.dumps(
            {
                "status": "listening",
                "component": "admin_agent",
                "address": f"{host}:{port}",
                "workdir": str(workdir),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
