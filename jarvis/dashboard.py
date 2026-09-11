"""
Jarvis Live Dashboard
A dependency-free HTTP server (Python stdlib only) that renders the JARVIS
HUD status page in a browser. Reads the same SQLite database the desktop
app writes to, so the page updates live as Jarvis converses and acts.

Usage:
    python -m jarvis.dashboard            # serves on http://127.0.0.1:8765
    python -m jarvis.dashboard --port 9000

Endpoints:
    GET /            -> single-page HUD dashboard (auto-refreshing)
    GET /api/state   -> JSON snapshot (messages, commands, memories, status)
    GET /healthz     -> liveness probe
"""

import argparse
import json
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

DB_PATH = Path(__file__).resolve().parent / "data" / "jarvis.db"
START_TIME = time.time()

# ── HTML dashboard ───────────────────────────────────────────────

_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>J.A.R.V.I.S — Live Status</title>
<style>
  :root {
    --cyan: #00d4ff;
    --cyan-dim: rgba(0, 212, 255, 0.35);
    --bg0: #000000;
    --bg1: #050f1e;
    --text: #e8ecf0;
    --muted: #7a8a99;
    --ok: #00ff88;
    --warn: #ffb347;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: radial-gradient(circle at 50% 40%, var(--bg1) 0%, var(--bg0) 75%);
    color: var(--text);
    font-family: "Consolas", "Cascadia Mono", monospace;
    min-height: 100vh;
    padding: 24px;
  }
  header {
    display: flex; align-items: center; gap: 16px;
    border-bottom: 1px solid var(--cyan-dim);
    padding-bottom: 14px; margin-bottom: 20px;
  }
  .orb {
    width: 46px; height: 46px; border-radius: 50%;
    background: radial-gradient(circle at 50% 45%, #bffaff 0%, var(--cyan) 35%, #005f7a 80%);
    box-shadow: 0 0 18px var(--cyan), 0 0 42px rgba(0, 212, 255, 0.45);
    animation: pulse 2.4s ease-in-out infinite;
  }
  @keyframes pulse {
    0%, 100% { transform: scale(1);    opacity: 1; }
    50%      { transform: scale(1.06); opacity: 0.88; }
  }
  h1 { font-size: 20px; letter-spacing: 6px; color: var(--cyan); font-weight: 700; }
  .sub { color: var(--muted); font-size: 11px; letter-spacing: 2px; }
  .live {
    margin-left: auto; font-size: 11px; color: var(--ok); letter-spacing: 2px;
  }
  .live::before {
    content: "●"; margin-right: 6px; animation: blink 1.2s steps(1) infinite;
  }
  @keyframes blink { 50% { opacity: 0.25; } }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 16px;
  }
  .panel {
    border: 1px solid var(--cyan-dim);
    border-radius: 8px;
    background: rgba(0, 20, 40, 0.35);
    padding: 14px;
    min-height: 140px;
  }
  .panel h2 {
    font-size: 11px; letter-spacing: 3px; color: var(--cyan);
    margin-bottom: 10px; font-weight: 700;
  }
  .stat-row { display: flex; gap: 12px; flex-wrap: wrap; }
  .stat {
    flex: 1 1 100px; border: 1px solid rgba(0, 212, 255, 0.18);
    border-radius: 6px; padding: 10px; text-align: center;
    background: rgba(0, 40, 70, 0.25);
  }
  .stat .num { font-size: 22px; color: var(--cyan); font-weight: 700; }
  .stat .lbl { font-size: 9px; color: var(--muted); letter-spacing: 2px; margin-top: 4px; }
  .msg { padding: 7px 10px; border-radius: 6px; margin-bottom: 6px; font-size: 12px;
         border: 1px solid rgba(0, 212, 255, 0.25); word-break: break-word; }
  .msg.user      { background: rgba(0, 60, 100, 0.35); color: #b4dcff; }
  .msg.assistant { background: rgba(0, 40, 60, 0.35); color: var(--cyan); }
  .msg .who { font-size: 9px; letter-spacing: 2px; opacity: 0.7; display: block; margin-bottom: 2px; }
  .row { display: flex; justify-content: space-between; font-size: 11px;
         padding: 5px 2px; border-bottom: 1px dashed rgba(0, 212, 255, 0.12); }
  .row:last-child { border-bottom: none; }
  .ok { color: var(--ok); } .warn { color: var(--warn); } .muted { color: var(--muted); }
  footer { margin-top: 20px; color: var(--muted); font-size: 10px; letter-spacing: 2px;
           text-align: center; }
  .err { color: var(--warn); font-size: 11px; }
</style>
</head>
<body>
<header>
  <div class="orb"></div>
  <div>
    <h1>J.A.R.V.I.S</h1>
    <div class="sub">PERSONAL AI ASSISTANT — LIVE STATUS</div>
  </div>
  <div class="live" id="live">LIVE</div>
</header>

<div class="grid">
  <div class="panel">
    <h2>SYSTEM STATUS</h2>
    <div class="stat-row" id="stats"></div>
  </div>

  <div class="panel">
    <h2>COMMUNICATION LOG</h2>
    <div id="messages"><div class="muted">No conversations yet. Start the desktop app and chat with Jarvis.</div></div>
  </div>

  <div class="panel">
    <h2>LONG-TERM MEMORY</h2>
    <div id="memories"><div class="muted">Memory empty. Tell Jarvis: "remember that ..."</div></div>
  </div>

  <div class="panel">
    <h2>COMMAND HISTORY</h2>
    <div id="commands"><div class="muted">No commands executed yet.</div></div>
  </div>
</div>

<footer>JARVIS DASHBOARD · DATA SOURCE: jarvis/data/jarvis.db · REFRESH 3s</footer>

<script>
  async function refresh() {
    try {
      const r = await fetch('/api/state', { cache: 'no-store' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const s = await r.json();
      document.getElementById('live').textContent = 'LIVE · uptime ' + s.uptime;
      document.getElementById('live').className = 'live';

      document.getElementById('stats').innerHTML =
        stat(s.counts.messages, 'MESSAGES') +
        stat(s.counts.commands, 'COMMANDS') +
        stat(s.counts.memories, 'MEMORIES') +
        stat(s.counts.sessions24h, '24H SESSIONS');

      const msgs = s.messages.map(m =>
        '<div class="msg ' + esc(m.role) + '"><span class="who">' +
        (m.role === 'user' ? 'USER' : 'JARVIS') + ' · ' + esc(m.time) +
        '</span>' + esc(m.content).slice(0, 220) + '</div>').join('');
      document.getElementById('messages').innerHTML =
        msgs || '<div class="muted">No conversations yet.</div>';

      const mems = s.memories.map(m =>
        '<div class="row"><span>(' + esc(m.category) + ') ' + esc(m.content).slice(0, 90) +
        '</span><span class="muted">' + esc(m.time) + '</span></div>').join('');
      document.getElementById('memories').innerHTML =
        mems || '<div class="muted">Memory empty. Tell Jarvis: "remember that ..."</div>';

      const cmds = s.commands.map(c =>
        '<div class="row"><span>' + esc(c.command).slice(0, 70) + '</span>' +
        '<span class="' + (c.success ? 'ok' : 'warn') + '">' +
        (c.success ? 'OK' : 'FAIL') + '</span></div>').join('');
      document.getElementById('commands').innerHTML =
        cmds || '<div class="muted">No commands executed yet.</div>';
    } catch (e) {
      document.getElementById('live').textContent = 'OFFLINE';
      document.getElementById('live').className = 'live warn';
    }
  }
  function stat(n, lbl) {
    return '<div class="stat"><div class="num">' + n + '</div><div class="lbl">' + lbl + '</div></div>';
  }
  function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g,
      c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }
  refresh();
  setInterval(refresh, 3000);
</script>
</body>
</html>
"""

# ── Database helpers ─────────────────────────────────────────────


def _read_only_connect():
    """Open the Jarvis DB read-only so the dashboard can never corrupt it."""
    uri = f"file:{DB_PATH.as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=2)
    except sqlite3.OperationalError:
        conn = sqlite3.connect(str(DB_PATH), timeout=2)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _fmt_time(ts) -> str:
    try:
        return time.strftime("%H:%M:%S", time.localtime(float(ts)))
    except (TypeError, ValueError):
        return "?"


def collect_state() -> dict:
    """Gather a JSON-safe snapshot from the Jarvis database."""
    state = {
        "ok": True,
        "uptime": _fmt_uptime(),
        "counts": {"messages": 0, "commands": 0, "memories": 0, "sessions24h": 0},
        "messages": [],
        "memories": [],
        "commands": [],
    }
    if not DB_PATH.exists():
        state["note"] = "Database not created yet — run the desktop app once."
        return state

    try:
        conn = _read_only_connect()
    except Exception as e:
        state["ok"] = False
        state["note"] = f"DB open failed: {e}"
        return state

    try:
        if _table_exists(conn, "conversations"):
            state["counts"]["messages"] = conn.execute(
                "SELECT COUNT(*) c FROM conversations").fetchone()["c"]
            rows = conn.execute(
                "SELECT role, content, timestamp FROM conversations "
                "ORDER BY timestamp DESC LIMIT 12").fetchall()
            state["messages"] = [
                {"role": r["role"], "content": r["content"],
                 "time": _fmt_time(r["timestamp"])}
                for r in reversed(rows)
            ]
            day_ago = time.time() - 86400
            state["counts"]["sessions24h"] = conn.execute(
                "SELECT COUNT(DISTINCT CAST(timestamp/900 AS INT)) c FROM conversations "
                "WHERE timestamp > ?", (day_ago,)).fetchone()["c"]

        if _table_exists(conn, "commands"):
            state["counts"]["commands"] = conn.execute(
                "SELECT COUNT(*) c FROM commands").fetchone()["c"]
            rows = conn.execute(
                "SELECT command, success, timestamp FROM commands "
                "ORDER BY timestamp DESC LIMIT 10").fetchall()
            state["commands"] = [
                {"command": r["command"], "success": bool(r["success"]),
                 "time": _fmt_time(r["timestamp"])}
                for r in reversed(rows)
            ]

        if _table_exists(conn, "memories"):
            state["counts"]["memories"] = conn.execute(
                "SELECT COUNT(*) c FROM memories").fetchone()["c"]
            rows = conn.execute(
                "SELECT content, category, created_at FROM memories "
                "ORDER BY created_at DESC LIMIT 10").fetchall()
            state["memories"] = [
                {"content": r["content"], "category": r["category"],
                 "time": _fmt_time(r["created_at"])}
                for r in rows
            ]
    except Exception as e:
        state["ok"] = False
        state["note"] = f"DB read failed: {e}"
    finally:
        conn.close()
    return state


def _fmt_uptime() -> str:
    secs = int(time.time() - START_TIME)
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}h{m:02d}m{s:02d}s"


# ── HTTP handler ─────────────────────────────────────────────────


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "JarvisDashboard/1.0"

    def log_message(self, fmt, *args):  # quiet access logs
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            self._send(200, _PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/state":
            body = json.dumps(collect_state()).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
        elif path == "/healthz":
            self._send(200, b"ok", "text/plain")
        else:
            self._send(404, b"not found", "text/plain")


def main():
    parser = argparse.ArgumentParser(description="Jarvis live dashboard server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(f"Jarvis dashboard serving on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
