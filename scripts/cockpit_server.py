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
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urljoin
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'
DB = Path(os.environ.get('HERMES_KANBAN_DB', ROOT / 'kanban.db'))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def remote_status_url() -> str | None:
    raw = os.environ.get('HERMES_AGENT_BASE_URL', '').strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        raise ValueError(f'invalid HERMES_AGENT_BASE_URL: {raw!r}')
    return urljoin(raw.rstrip('/') + '/', 'api/status')


def fetch_remote_status() -> dict:
    status_url = remote_status_url()
    if not status_url:
        raise RuntimeError('remote status requested without HERMES_AGENT_BASE_URL')
    request = Request(status_url, headers={'Accept': 'application/json'})
    try:
        with urlopen(request, timeout=10) as response:
            raw = response.read().decode('utf-8')
    except HTTPError as exc:
        raise RuntimeError(f'remote Hermes status request failed with HTTP {exc.code} from {status_url}') from exc
    except URLError as exc:
        raise RuntimeError(f'remote Hermes status request failed for {status_url}: {exc.reason}') from exc
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise RuntimeError(f'remote Hermes status response from {status_url} was not a JSON object')
    return payload


def run_status() -> str:
    proc = subprocess.run(['/opt/hermes/.venv/bin/hermes', 'status', '--all'], capture_output=True, text=True, check=False)
    return proc.stdout


def active_profile() -> str:
    return os.environ.get('HERMES_PROFILE', 'unknown')


def service_running() -> bool:
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
    status_url = remote_status_url()
    if status_url:
        payload = fetch_remote_status()
        payload.setdefault('notes', [
            f'Status is proxied from {status_url}.',
            'The cockpit refreshes remote data immediately and auto-refreshes every 30 seconds.',
        ])
        return payload

    kanban = read_kanban()
    return {
        'generated_at': now_iso(),
        'active_profile': active_profile(),
        'service_running': service_running(),
        'kanban': kanban,
        'notes': [
            'Profile comes from HERMES_PROFILE.',
            'Status uses the local Hermes status command and the kanban SQLite database.',
            'Refresh updates the page immediately; the browser also auto-refreshes every 30 seconds.',
        ],
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(SRC), **kwargs)

    def log_message(self, format: str, *args) -> None:
        return

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/api/status':
            try:
                payload = build_payload()
                body = json.dumps(payload, indent=2, sort_keys=True).encode('utf-8')
                self.send_response(200)
            except (ValueError, RuntimeError, sqlite3.Error, subprocess.SubprocessError) as exc:
                body = json.dumps({'error': str(exc), 'generated_at': now_iso()}, indent=2, sort_keys=True).encode('utf-8')
                self.send_response(502)
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
