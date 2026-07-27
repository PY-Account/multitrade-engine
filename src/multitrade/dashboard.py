from __future__ import annotations

import base64
import json
import secrets
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hmac import compare_digest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

from multitrade.audit import SqliteAuditReader
from multitrade.health import check_health


_DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>MultiTrade Operations</title>
  <style nonce="{{NONCE}}">
    :root {
      color-scheme: dark;
      --bg: #071018;
      --panel: #0e1a24;
      --panel-2: #132330;
      --text: #edf6f8;
      --muted: #8ba4ad;
      --line: #203744;
      --ok: #50d890;
      --bad: #ff6b6b;
      --accent: #62b8ff;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background:
        radial-gradient(circle at 90% 0, #123044 0, transparent 32rem),
        var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, sans-serif;
    }
    main { width: min(1180px, 94vw); margin: 0 auto; padding: 32px 0 56px; }
    header { display: flex; gap: 24px; align-items: end; justify-content: space-between; }
    h1 { margin: 0; font-size: clamp(1.7rem, 4vw, 2.8rem); letter-spacing: -.04em; }
    .eyebrow { color: var(--accent); font-size: .72rem; font-weight: 800; letter-spacing: .16em; }
    .muted, #updated { color: var(--muted); }
    .grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin: 26px 0; }
    .card, .panel {
      background: color-mix(in srgb, var(--panel) 92%, transparent);
      border: 1px solid var(--line);
      border-radius: 16px;
      box-shadow: 0 18px 45px rgb(0 0 0 / 18%);
    }
    .card { padding: 18px; min-height: 112px; }
    .label { color: var(--muted); font-size: .76rem; text-transform: uppercase; letter-spacing: .08em; }
    .value { margin-top: 12px; font-size: 1.45rem; font-weight: 760; }
    .ok { color: var(--ok); }
    .bad { color: var(--bad); }
    .panel { padding: 20px; margin-top: 14px; overflow: hidden; }
    .panel h2 { margin: 0 0 16px; font-size: 1rem; }
    table { width: 100%; border-collapse: collapse; font-size: .86rem; }
    th, td { padding: 12px 10px; border-top: 1px solid var(--line); text-align: left; vertical-align: top; }
    th { color: var(--muted); font-size: .7rem; text-transform: uppercase; letter-spacing: .08em; }
    code { color: #b8d7e4; white-space: pre-wrap; overflow-wrap: anywhere; }
    .bar { height: 8px; background: var(--panel-2); border-radius: 99px; overflow: hidden; margin-top: 14px; }
    .bar span { display: block; height: 100%; width: 0; background: var(--accent); transition: width .25s; }
    @media (max-width: 820px) {
      header { align-items: start; flex-direction: column; }
      .grid { grid-template-columns: repeat(2, 1fr); }
      .events { overflow-x: auto; }
    }
    @media (max-width: 480px) { .grid { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
<main>
  <header>
    <div>
      <div class="eyebrow">PAPER ENVIRONMENT</div>
      <h1>MultiTrade Operations</h1>
      <p class="muted">Read-only engine, account, risk, and audit monitoring.</p>
    </div>
    <div id="updated">Waiting for data…</div>
  </header>

  <section class="grid" aria-label="System summary">
    <article class="card"><div class="label">Engine</div><div class="value" id="engine">—</div></article>
    <article class="card"><div class="label">Paper equity</div><div class="value" id="equity">—</div></article>
    <article class="card"><div class="label">Reserved risk</div><div class="value" id="risk">—</div></article>
    <article class="card"><div class="label">Gross notional</div><div class="value" id="notional">—</div></article>
  </section>

  <section class="panel">
    <h2>Risk capacity</h2>
    <div id="risk-copy" class="muted">Waiting for data…</div>
    <div class="bar"><span id="risk-bar"></span></div>
  </section>

  <section class="panel events">
    <h2>Recent audit events</h2>
    <table>
      <thead><tr><th>Time</th><th>Event</th><th>Correlation</th><th>Details</th></tr></thead>
      <tbody id="events"></tbody>
    </table>
  </section>
</main>
<script nonce="{{NONCE}}">
  const money = value => value == null
    ? "—"
    : new Intl.NumberFormat("en-US", {style:"currency", currency:"USD"}).format(Number(value));

  function setText(id, value) { document.getElementById(id).textContent = value; }

  function renderEvents(events) {
    const body = document.getElementById("events");
    body.replaceChildren();
    for (const event of events) {
      const row = document.createElement("tr");
      for (const value of [
        new Date(event.occurred_at).toLocaleString(),
        event.event_type,
        event.correlation_id,
      ]) {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.appendChild(cell);
      }
      const detail = document.createElement("td");
      const code = document.createElement("code");
      code.textContent = JSON.stringify(event.payload);
      detail.appendChild(code);
      row.appendChild(detail);
      body.appendChild(row);
    }
  }

  async function refresh() {
    try {
      const response = await fetch("/api/overview?limit=40", {cache:"no-store"});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      const engine = document.getElementById("engine");
      engine.textContent = data.engine.healthy ? "Healthy" : "Attention";
      engine.className = `value ${data.engine.healthy ? "ok" : "bad"}`;
      setText("equity", money(data.account?.equity));
      setText("risk", money(data.risk.active_amount));
      setText("notional", money(data.account?.gross_notional));
      setText("updated", `Updated ${new Date(data.generated_at).toLocaleTimeString()}`);
      const pct = Number(data.risk.utilization_percent || 0);
      setText("risk-copy", `${pct.toFixed(2)}% of the configured aggregate risk ceiling is reserved.`);
      document.getElementById("risk-bar").style.width = `${Math.min(100, Math.max(0, pct))}%`;
      renderEvents(data.events);
    } catch (error) {
      const engine = document.getElementById("engine");
      engine.textContent = "Unavailable";
      engine.className = "value bad";
      setText("updated", "Dashboard data unavailable");
    }
  }
  refresh();
  setInterval(refresh, 15000);
</script>
</body>
</html>
"""


class DashboardData:
    def __init__(
        self,
        db_path: str | Path,
        health_path: str | Path,
        health_max_age_seconds: int,
        max_total_open: Decimal,
    ) -> None:
        self.reader = SqliteAuditReader(db_path)
        self.health_path = Path(health_path)
        self.health_max_age_seconds = health_max_age_seconds
        self.max_total_open = max_total_open

    @staticmethod
    def _decimal(value: Any) -> Decimal:
        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return Decimal("0")

    def overview(self, event_limit: int = 40) -> dict[str, Any]:
        healthy, health = check_health(
            self.health_path, self.health_max_age_seconds
        )
        try:
            heartbeat = self.reader.latest_event("account_heartbeat")
            active_risk = self.reader.active_risk()
            reservations = self.reader.reservation_summary()
            events = self.reader.recent_events(event_limit)
            storage: dict[str, Any] = {"status": "ok"}
        except (FileNotFoundError, OSError, sqlite3.Error):
            heartbeat = None
            active_risk = Decimal("0")
            reservations = {}
            events = []
            storage = {"status": "unavailable"}

        account = heartbeat["payload"] if heartbeat else None
        equity = self._decimal(account.get("equity")) if account else Decimal("0")
        capacity = equity * self.max_total_open
        utilization = (
            active_risk / capacity * Decimal("100")
            if capacity > 0
            else Decimal("0")
        )
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "environment": "paper",
            "engine": {"healthy": healthy, "details": health},
            "storage": storage,
            "account": account,
            "risk": {
                "active_amount": format(active_risk, "f"),
                "aggregate_ceiling_fraction": format(
                    self.max_total_open, "f"
                ),
                "aggregate_capacity_amount": format(capacity, "f"),
                "utilization_percent": format(utilization, ".4f"),
                "reservations": reservations,
            },
            "events": events,
        }


class DashboardRequestHandler(BaseHTTPRequestHandler):
    server_version = "MultiTradeDashboard/0.1"
    sys_version = ""
    data_service: DashboardData
    expected_authorization: str

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self._send_json(200, {"status": "ok"}, authenticated=False)
            return
        if not self._authorized():
            self.send_response(401)
            self.send_header(
                "WWW-Authenticate",
                'Basic realm="MultiTrade Operations", charset="UTF-8"',
            )
            self._security_headers()
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if parsed.path == "/":
            nonce = secrets.token_urlsafe(18)
            payload = _DASHBOARD_HTML.replace(
                "{{NONCE}}", nonce
            ).encode("utf-8")
            self.send_response(200)
            self._security_headers(nonce)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if parsed.path == "/api/overview":
            values = parse_qs(parsed.query).get("limit", ["40"])
            try:
                limit = max(1, min(int(values[0]), 200))
            except ValueError:
                self._send_json(400, {"error": "invalid_limit"})
                return
            self._send_json(200, self.data_service.overview(limit))
            return
        self._send_json(404, {"error": "not_found"})

    def _authorized(self) -> bool:
        supplied = self.headers.get("Authorization", "")
        return compare_digest(supplied, self.expected_authorization)

    def _security_headers(self, nonce: str | None = None) -> None:
        script_source = f"'nonce-{nonce}'" if nonce else "'none'"
        style_source = f"'nonce-{nonce}'" if nonce else "'none'"
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; "
            f"script-src {script_source}; style-src {style_source}; "
            "connect-src 'self'; img-src 'self'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
        )

    def _send_json(
        self,
        status: int,
        value: Any,
        *,
        authenticated: bool = True,
    ) -> None:
        del authenticated
        payload = json.dumps(
            value, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:
        del format, args


def create_dashboard_server(
    host: str,
    port: int,
    data_service: DashboardData,
    username: str,
    password: str,
) -> ThreadingHTTPServer:
    credentials = base64.b64encode(
        f"{username}:{password}".encode("utf-8")
    ).decode("ascii")

    class ConfiguredHandler(DashboardRequestHandler):
        pass

    ConfiguredHandler.data_service = data_service
    ConfiguredHandler.expected_authorization = f"Basic {credentials}"
    return ThreadingHTTPServer((host, port), ConfiguredHandler)


def dashboard_healthcheck(port: int) -> tuple[bool, str]:
    try:
        with urlopen(
            f"http://127.0.0.1:{port}/healthz", timeout=3
        ) as response:
            return response.status == 200, f"http_{response.status}"
    except (OSError, URLError):
        return False, "unreachable"
