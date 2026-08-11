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
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'
DB = Path(os.environ.get('HERMES_KANBAN_DB', ROOT / 'kanban.db'))

try:
    from github_pr_sync import start_background_loop
except Exception:  # pragma: no cover - optional runtime dependency on the sync helper
    start_background_loop = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    sync_enabled = os.environ.get('HERMES_ENABLE_GITHUB_PR_SYNC', '1').lower() not in {'0', 'false', 'no'}
    sync_interval = int(os.environ.get('HERMES_GITHUB_PR_SYNC_INTERVAL', '60'))
    sync_loop = None
    if sync_enabled and start_background_loop is not None:
        sync_loop = start_background_loop(True, sync_interval)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f'Serving Hermes cockpit from {SRC} on http://{args.host}:{args.port}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if sync_loop is not None:
            sync_loop.stop()
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
