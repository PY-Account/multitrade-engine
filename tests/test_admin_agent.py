import json
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from multitrade.admin_agent import create_admin_agent_server


class AdminAgentTests(TestCase):
    def test_rejects_short_token(self) -> None:
        with self.assertRaises(ValueError):
            create_admin_agent_server(
                "127.0.0.1",
                0,
                workdir=".",
                token="short",
            )

    def test_status_requires_bearer_token(self) -> None:
        token = "x" * 40
        server = create_admin_agent_server(
            "127.0.0.1",
            0,
            workdir=".",
            token=token,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        try:
            with self.assertRaises(HTTPError) as error:
                urlopen(
                    f"http://127.0.0.1:{port}/api/admin/status",
                    timeout=3,
                )
            self.assertEqual(error.exception.code, 401)
            error.exception.close()

            request = Request(
                f"http://127.0.0.1:{port}/api/admin/status",
                headers={"Authorization": f"Bearer {token}"},
            )
            with urlopen(request, timeout=3) as response:
                result = json.loads(response.read())
            self.assertEqual(result["component"], "admin_agent")
            self.assertEqual(result["last_action"]["state"], "idle")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_update_runs_allowlisted_runner(self) -> None:
        token = "x" * 40
        calls: list[Path] = []

        def fake_runner(workdir: Path) -> tuple[int, str]:
            calls.append(workdir)
            return 0, "MULTITRADE_UPDATE_OK\n"

        with TemporaryDirectory() as directory:
            server = create_admin_agent_server(
                "127.0.0.1",
                0,
                workdir=directory,
                token=token,
                update_runner=fake_runner,
            )
            thread = threading.Thread(
                target=server.serve_forever, daemon=True
            )
            thread.start()
            port = server.server_address[1]
            try:
                request = Request(
                    f"http://127.0.0.1:{port}/api/admin/update",
                    data=json.dumps(
                        {
                            "confirmation": "RUN SERVER UPDATE",
                            "requested_by": "operator",
                        }
                    ).encode("utf-8"),
                    method="POST",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                )
                with urlopen(request, timeout=3) as response:
                    accepted = json.loads(response.read())
                self.assertEqual(accepted["status"], "accepted")

                deadline = time.monotonic() + 3
                result = {}
                while time.monotonic() < deadline:
                    status_request = Request(
                        f"http://127.0.0.1:{port}/api/admin/status",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    with urlopen(status_request, timeout=3) as response:
                        result = json.loads(response.read())
                    if result["last_action"]["state"] == "completed":
                        break
                    time.sleep(0.05)

                self.assertEqual(
                    result["last_action"]["state"], "completed"
                )
                self.assertEqual(
                    result["last_action"]["result"]["returncode"], 0
                )
                self.assertEqual(calls, [Path(directory)])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_managed_setting_update_rewrites_env_with_backup(self) -> None:
        token = "x" * 40
        with TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text(
                "DASHBOARD_DOMAIN=old.example.com\n",
                encoding="utf-8",
            )
            server = create_admin_agent_server(
                "127.0.0.1",
                0,
                workdir=directory,
                token=token,
            )
            thread = threading.Thread(
                target=server.serve_forever, daemon=True
            )
            thread.start()
            port = server.server_address[1]
            try:
                request = Request(
                    f"http://127.0.0.1:{port}/api/admin/settings",
                    data=json.dumps(
                        {
                            "confirmation": "UPDATE SERVER SETTING",
                            "requested_by": "operator",
                            "key": "DASHBOARD_DOMAIN",
                            "value": "trade.example.com",
                        }
                    ).encode("utf-8"),
                    method="POST",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                )
                with urlopen(request, timeout=3) as response:
                    result = json.loads(response.read())
                self.assertEqual(result["status"], "accepted")
                self.assertIn(
                    "DASHBOARD_DOMAIN=trade.example.com",
                    env_path.read_text(encoding="utf-8"),
                )
                backups = list((Path(directory) / "local-backups").iterdir())
                self.assertEqual(len(backups), 1)

                status_request = Request(
                    f"http://127.0.0.1:{port}/api/admin/settings",
                    headers={"Authorization": f"Bearer {token}"},
                )
                with urlopen(status_request, timeout=3) as response:
                    settings = json.loads(response.read())
                domain = [
                    item
                    for item in settings["settings"]
                    if item["key"] == "DASHBOARD_DOMAIN"
                ][0]
                self.assertEqual(domain["value"], "trade.example.com")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)
