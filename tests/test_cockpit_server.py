from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from urllib import error, request

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import cockpit_server


class CockpitServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = os.environ.get('HERMES_KANBAN_DB')

    def tearDown(self) -> None:
        if self._env is None:
            os.environ.pop('HERMES_KANBAN_DB', None)
        else:
            os.environ['HERMES_KANBAN_DB'] = self._env

    def test_read_kanban_raises_clear_error_for_missing_db_path(self) -> None:
        os.environ['HERMES_KANBAN_DB'] = '/tmp/no-such-dir/subdir/kanban.db'

        with self.assertRaises(RuntimeError) as cm:
            cockpit_server.read_kanban()

        message = str(cm.exception)
        self.assertIn('/tmp/no-such-dir/subdir/kanban.db', message)
        self.assertIn('inside this cockpit container', message)

    def test_read_kanban_reads_valid_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / 'kanban.db'
            conn = sqlite3.connect(db_path)
            conn.executescript(
                '''
                CREATE TABLE tasks (
                    id TEXT,
                    title TEXT,
                    assignee TEXT,
                    status TEXT,
                    priority INTEGER,
                    started_at INTEGER,
                    last_heartbeat_at INTEGER,
                    current_run_id INTEGER,
                    created_at INTEGER
                );
                CREATE TABLE task_runs (
                    task_id TEXT,
                    profile TEXT,
                    summary TEXT,
                    started_at INTEGER,
                    ended_at INTEGER,
                    outcome TEXT,
                    status TEXT,
                    error TEXT
                );
                INSERT INTO tasks (id, title, assignee, status, priority, started_at, last_heartbeat_at, current_run_id, created_at)
                VALUES ('t1', 'Example task', 'coder', 'running', 10, 100, 110, 1, 90);
                INSERT INTO task_runs (task_id, profile, summary, started_at, ended_at, outcome, status, error)
                VALUES ('t1', 'coder', 'Completed example task', 100, 120, 'completed', 'done', NULL);
                '''
            )
            conn.commit()
            conn.close()

            os.environ['HERMES_KANBAN_DB'] = str(db_path)
            payload = cockpit_server.read_kanban()

        self.assertEqual(payload['counts']['running'], 1)
        self.assertEqual(payload['running_task']['id'], 't1')
        self.assertEqual(payload['last_success']['task_id'], 't1')
        self.assertIsNone(payload['last_error'])

    def test_status_endpoint_returns_json_error_instead_of_crashing(self) -> None:
        os.environ['HERMES_KANBAN_DB'] = '/tmp/no-such-dir/subdir/kanban.db'
        server = cockpit_server.ThreadingHTTPServer(('127.0.0.1', 0), cockpit_server.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            url = f'http://127.0.0.1:{server.server_address[1]}/api/status'
            with self.assertRaises(error.HTTPError) as cm:
                request.urlopen(url, timeout=5)

            self.assertEqual(cm.exception.code, 500)
            body = json.loads(cm.exception.read().decode('utf-8'))
            self.assertFalse(body['ok'])
            self.assertIn('/tmp/no-such-dir/subdir/kanban.db', body['error'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
