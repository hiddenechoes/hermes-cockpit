from __future__ import annotations

import http.server
import importlib.util
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / 'scripts' / 'cockpit_server.py'


class StatusHandler(http.server.BaseHTTPRequestHandler):
    response_body = b''

    def do_GET(self):
        if self.path == '/api/status':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(self.response_body)))
            self.end_headers()
            self.wfile.write(self.response_body)
            return
        self.send_error(404)

    def log_message(self, format: str, *args) -> None:  # noqa: A003 - stdlib callback name
        return


def make_temp_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE tasks (
                id TEXT,
                title TEXT,
                assignee TEXT,
                status TEXT,
                priority INTEGER,
                started_at TEXT,
                last_heartbeat_at TEXT,
                current_run_id INTEGER,
                created_at TEXT
            );
            CREATE TABLE task_runs (
                task_id TEXT,
                profile TEXT,
                summary TEXT,
                started_at TEXT,
                ended_at TEXT,
                outcome TEXT,
                status TEXT,
                error TEXT
            );
            """
        )
        conn.executemany(
            'INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            [
                ('t_running', 'Running task', 'coder', 'running', 20, '2026-01-02T03:04:05+00:00', None, 1, '2026-01-02T03:04:05+00:00'),
                ('t_todo', 'Queued task', 'coder', 'todo', 10, None, None, None, '2026-01-02T03:00:00+00:00'),
                ('t_done', 'Completed task', 'coder', 'done', 5, None, None, None, '2026-01-01T01:00:00+00:00'),
            ],
        )
        conn.executemany(
            'INSERT INTO task_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            [
                ('t_done', 'coder', 'Finished successfully', '2026-01-01T01:00:00+00:00', '2026-01-01T01:01:00+00:00', 'completed', 'done', None),
                ('t_running', 'coder', 'Still running', '2026-01-02T03:04:05+00:00', None, 'running', 'running', ''),
                ('t_broken', 'coder', 'Failed run', '2026-01-01T02:00:00+00:00', '2026-01-01T02:01:00+00:00', 'failed', 'done', 'boom'),
            ],
        )
        conn.commit()
    finally:
        conn.close()


class CockpitServerTests(unittest.TestCase):
    def load_module(self, db_path: Path):
        spec = importlib.util.spec_from_file_location('cockpit_server_test', MODULE_PATH)
        assert spec is not None
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module

    def test_remote_status_source_avoids_local_hermes_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / 'kanban.db'
            make_temp_db(db_path)

            StatusHandler.response_body = json.dumps({'gateway_status': 'running'}).encode('utf-8')
            server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), StatusHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.dict(
                    os.environ,
                    {
                        'HERMES_KANBAN_DB': str(db_path),
                        'HERMES_PROFILE': 'coder',
                        'HERMES_AGENT_BASE_URL': f'http://127.0.0.1:{server.server_port}',
                    },
                    clear=False,
                ):
                    module = self.load_module(db_path)
                    with patch.object(module, 'run_status', side_effect=AssertionError('local hermes binary should not be used')):
                        payload = module.build_payload()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

            self.assertTrue(payload['service_running'])
            self.assertEqual(payload['kanban']['counts']['running'], 1)
            self.assertEqual(payload['kanban']['counts']['todo'], 1)
            self.assertEqual(payload['kanban']['counts']['done'], 1)
            self.assertIn('Status reads from http://127.0.0.1', ' '.join(payload['notes']))

    def test_remote_gateway_parser_accepts_nested_status_shapes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / 'kanban.db'
            make_temp_db(db_path)
            with patch.dict(os.environ, {'HERMES_KANBAN_DB': str(db_path), 'HERMES_PROFILE': 'coder'}, clear=False):
                module = self.load_module(db_path)
        self.assertTrue(module.remote_gateway_running({'gateway': {'status': 'running'}}))
        self.assertFalse(module.remote_gateway_running({'gateway': {'running': False}}))
        self.assertTrue(module.remote_gateway_running({'gateway_status': 'healthy'}))
        self.assertIsNone(module.remote_gateway_running({'auth_required': True}))

    def test_local_status_fallback_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / 'kanban.db'
            make_temp_db(db_path)
            with patch.dict(os.environ, {'HERMES_KANBAN_DB': str(db_path), 'HERMES_PROFILE': 'coder'}, clear=False):
                module = self.load_module(db_path)
            setattr(module, 'run_status', lambda: 'Gateway Service\nStatus: running\n')
            self.assertTrue(module.service_running())


if __name__ == '__main__':
    unittest.main()
