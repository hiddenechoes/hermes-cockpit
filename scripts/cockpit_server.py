#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'
DB = Path(os.environ.get('HERMES_KANBAN_DB', ROOT / 'kanban.db'))


def remote_agent_base_url() -> str:
    return os.environ.get('HERMES_AGENT_BASE_URL', '').strip()



def remote_agent_timeout() -> float:
    return float(os.environ.get('HERMES_AGENT_TIMEOUT', '5'))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_status() -> str:
    proc = subprocess.run(['/opt/hermes/.venv/bin/hermes', 'status', '--all'], capture_output=True, text=True, check=False)
    return proc.stdout


def fetch_remote_status() -> dict:
    base_url = remote_agent_base_url().rstrip('/')
    request = Request(f'{base_url}/api/status', headers={'Accept': 'application/json'})
    with urlopen(request, timeout=remote_agent_timeout()) as response:
        payload = json.loads(response.read().decode('utf-8'))
    if not isinstance(payload, dict):
        raise ValueError('Remote Hermes Agent status payload was not a JSON object')
    return payload


def remote_gateway_running(payload: dict) -> bool | None:
    gateway = payload.get('gateway')
    if isinstance(gateway, dict):
        if isinstance(gateway.get('running'), bool):
            return gateway['running']
        for key in ('status', 'state'):
            value = gateway.get(key)
            if isinstance(value, str):
                lowered = value.strip().lower()
                if lowered in {'running', 'started', 'up', 'healthy', 'online', 'active'}:
                    return True
                if lowered in {'stopped', 'stopping', 'down', 'offline', 'inactive'}:
                    return False
    for key in ('gateway_status', 'gateway_state'):
        value = payload.get(key)
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {'running', 'started', 'up', 'healthy', 'online', 'active'}:
                return True
            if lowered in {'stopped', 'stopping', 'down', 'offline', 'inactive'}:
                return False
    if isinstance(payload.get('gateway_running'), bool):
        return payload['gateway_running']
    return None


def active_profile() -> str:
    return os.environ.get('HERMES_PROFILE', 'unknown')


def service_running() -> bool:
    if remote_agent_base_url():
        try:
            payload = fetch_remote_status()
        except (OSError, URLError, json.JSONDecodeError, ValueError):
            return False
        remote = remote_gateway_running(payload)
        if remote is not None:
            return remote
    text = run_status()
    section = ''.join(['Gate', 'way', ' Service'])
    seen = False
    for line in text.splitlines():
        if section in line:
            seen = True
        elif seen and line.strip().startswith('Status:'):
            return 'stopped' not in line.lower()
    return False


def read_kanban() -> dict:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    counts = {row['status']: row['count'] for row in cur.execute('SELECT status, COUNT(*) AS count FROM tasks GROUP BY status')}
    running = cur.execute("""
        SELECT id, title, assignee, started_at, last_heartbeat_at, current_run_id
        FROM tasks
        WHERE status = 'running'
        ORDER BY priority DESC, started_at ASC, created_at ASC
        LIMIT 1
    """).fetchone()
    success = cur.execute("""
        SELECT task_id, profile, summary, started_at, ended_at
        FROM task_runs
        WHERE outcome = 'completed' OR status = 'done'
        ORDER BY COALESCE(ended_at, started_at) DESC
        LIMIT 1
    """).fetchone()
    error = cur.execute("""
        SELECT task_id, profile, error, summary, started_at, ended_at
        FROM task_runs
        WHERE error IS NOT NULL AND trim(error) != ''
        ORDER BY COALESCE(ended_at, started_at) DESC
        LIMIT 1
    """).fetchone()
    return {
        'counts': counts,
        'running_task': dict(running) if running else None,
        'last_success': dict(success) if success else None,
        'last_error': dict(error) if error else None,
    }


def build_payload() -> dict:
    kanban = read_kanban()
    notes = [
        'Profile comes from HERMES_PROFILE.',
        'Status uses the local Hermes status command and the kanban SQLite database.',
        'Refresh updates the page immediately; the browser also auto-refreshes every 30 seconds.',
    ]
    if remote_agent_base_url():
        notes.insert(1, f'Status reads from {remote_agent_base_url().rstrip("/")}/api/status.')
    return {
        'generated_at': now_iso(),
        'active_profile': active_profile(),
        'service_running': service_running(),
        'kanban': kanban,
        'notes': notes,
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(SRC), **kwargs)

    def log_message(self, format: str, *args) -> None:
        return

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/api/status':
            payload = build_payload()
            body = json.dumps(payload, indent=2, sort_keys=True).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == '/api/health':
            body = json.dumps({'ok': True, 'generated_at': now_iso()}).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path in {'/', '/index.html'}:
            self.path = '/index.html'
        return super().do_GET()


def main() -> int:
    parser = argparse.ArgumentParser(description='Serve the Hermes cockpit with live status data.')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f'Serving Hermes cockpit from {SRC} on http://{args.host}:{args.port}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
