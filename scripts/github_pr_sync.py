#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
DB = Path(os.environ.get('HERMES_KANBAN_DB', ROOT / 'kanban.db'))
STATE_DIR = Path(os.environ.get('HERMES_HOME', Path.home() / '.hermes')) / 'cockpit'
SYNC_EVENT_KIND = 'github_pr_synced'


@dataclass(slots=True)
class Task:
    task_id: str
    title: str
    body: str | None
    branch_name: str | None
    workspace_path: str | None
    completed_at: int | None
    current_run_id: int | None
    result: str | None
    summary: str | None


@dataclass(slots=True)
class PRInfo:
    number: int
    url: str
    state: str
    title: str | None = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def repo_root() -> Path:
    proc = run(['git', 'rev-parse', '--show-toplevel'], cwd=ROOT, check=True)
    return Path(proc.stdout.strip())


def run(args: list[str], *, cwd: Path | None = None, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=str(cwd or ROOT), capture_output=True, text=True, check=check)


def gh(args: list[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
    return run(['gh', *args], check=check)


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def base_branch() -> str:
    proc = run(['git', 'symbolic-ref', '--quiet', 'refs/remotes/origin/HEAD'])
    if proc.returncode == 0:
        ref = proc.stdout.strip()
        if ref.startswith('refs/remotes/origin/'):
            return ref.rsplit('/', 1)[-1]
    return 'main'


def current_branch() -> str:
    proc = run(['git', 'branch', '--show-current'])
    return proc.stdout.strip()


def local_branch_exists(branch: str) -> bool:
    proc = run(['git', 'show-ref', '--verify', '--quiet', f'refs/heads/{branch}'])
    return proc.returncode == 0


def git_status_clean() -> bool:
    proc = run(['git', 'status', '--porcelain'])
    return proc.stdout.strip() == ''


def task_rows(conn: sqlite3.Connection, task_id: str | None = None) -> list[Task]:
    sql = """
        SELECT
            t.id,
            t.title,
            t.body,
            t.branch_name,
            t.workspace_path,
            t.completed_at,
            t.current_run_id,
            t.result,
            (
                SELECT tr.summary
                FROM task_runs AS tr
                WHERE tr.id = t.current_run_id
            ) AS run_summary
        FROM tasks AS t
        WHERE t.completed_at IS NOT NULL
          AND COALESCE(t.branch_name, '') != ''
    """
    params: list[str] = []
    if task_id:
        sql += ' AND t.id = ?'
        params.append(task_id)
    sql += ' ORDER BY t.completed_at DESC, t.created_at DESC'
    rows = conn.execute(sql, params).fetchall()
    return [
        Task(
            task_id=row['id'],
            title=row['title'],
            body=row['body'],
            branch_name=row['branch_name'],
            workspace_path=row['workspace_path'],
            completed_at=row['completed_at'],
            current_run_id=row['current_run_id'],
            result=row['result'],
            summary=row['run_summary'],
        )
        for row in rows
    ]


def already_synced(conn: sqlite3.Connection, task_id: str) -> bool:
    row = conn.execute(
        'SELECT 1 FROM task_events WHERE task_id = ? AND kind = ? LIMIT 1',
        (task_id, SYNC_EVENT_KIND),
    ).fetchone()
    return row is not None


def existing_pr(branch: str) -> PRInfo | None:
    proc = gh(['pr', 'list', '--head', branch, '--state', 'all', '--json', 'number,url,state,title'])
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or 'gh pr list failed')
    payload = json.loads(proc.stdout or '[]')
    if not payload:
        return None
    data = payload[0]
    return PRInfo(number=int(data['number']), url=data['url'], state=data['state'].upper(), title=data.get('title'))


def push_branch(branch: str) -> None:
    proc = run(['git', 'push', '-u', 'origin', branch])
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f'failed to push branch {branch}')


def pr_body(task: Task, branch: str) -> str:
    workspace = task.workspace_path or str(ROOT)
    lines = [
        '## Summary',
        task.body.strip() if task.body and task.body.strip() else task.title,
        '',
        '## Traceability',
        f'- Hermes task: `{task.task_id}`',
        f'- Branch: `{branch}`',
        f'- Workspace: `{workspace}`',
        f'- Completed at: `{task.completed_at}`',
    ]
    if task.summary:
        lines.extend(['', '## Task run summary', task.summary.strip()])
    if task.result:
        lines.extend(['', '## Task result', task.result.strip()])
    lines.extend([
        '',
        'This PR was opened automatically from the completed Hermes coding task.',
    ])
    return '\n'.join(lines)


def pr_title(task: Task) -> str:
    title = task.title.strip()
    if task.task_id not in title:
        return f'{title} ({task.task_id})'
    return title


def ensure_pr(task: Task, *, dry_run: bool = False) -> PRInfo:
    branch = task.branch_name or ''
    if not branch:
        raise RuntimeError(f'task {task.task_id} has no branch_name')
    if not local_branch_exists(branch):
        raise RuntimeError(f'local branch {branch!r} for task {task.task_id} does not exist')

    title = pr_title(task)
    body = pr_body(task, branch)

    if dry_run:
        return PRInfo(number=0, url=f'https://github.com/dry-run/{branch}', state='OPEN', title=title)

    push_branch(branch)

    existing = existing_pr(branch)

    if existing:
        if existing.state == 'MERGED':
            return existing
        if existing.state == 'CLOSED':
            proc = gh(['pr', 'reopen', str(existing.number)])
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f'failed to reopen PR #{existing.number}')
        proc = gh(['pr', 'edit', str(existing.number), '--title', title, '--body', body])
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f'failed to update PR #{existing.number}')
        refreshed = existing_pr(branch)
        return refreshed or existing

    proc = gh([
        'pr', 'create',
        '--base', base_branch(),
        '--head', branch,
        '--title', title,
        '--body', body,
    ])
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f'failed to create PR for {branch}')

    match = re.search(r'https://\S+', proc.stdout)
    url = match.group(0) if match else proc.stdout.strip()
    if not url:
        raise RuntimeError('gh pr create did not return a PR URL')
    pr_number_match = re.search(r'/pull/(\d+)', url)
    number = int(pr_number_match.group(1)) if pr_number_match else 0
    return PRInfo(number=number, url=url, state='OPEN', title=title)


def record_sync(conn: sqlite3.Connection, task: Task, pr: PRInfo) -> None:
    payload = json.dumps({
        'pr_number': pr.number,
        'pr_url': pr.url,
        'branch': task.branch_name,
        'completed_at': task.completed_at,
        'synced_at': now_iso(),
    }, sort_keys=True)
    timestamp = int(time.time())
    conn.execute(
        'INSERT INTO task_events(task_id, run_id, kind, payload, created_at) VALUES (?, ?, ?, ?, ?)',
        (task.task_id, task.current_run_id, SYNC_EVENT_KIND, payload, timestamp),
    )
    conn.execute(
        'INSERT INTO task_comments(task_id, author, body, created_at) VALUES (?, ?, ?, ?)',
        (
            task.task_id,
            'github-pr-sync',
            f'GitHub PR synced automatically: {pr.url}\n\nBranch: `{task.branch_name}`\nTask: `{task.task_id}`',
            timestamp,
        ),
    )
    conn.commit()


def sync_once(*, task_id: str | None = None, dry_run: bool = False) -> list[dict[str, str]]:
    if not dry_run and not git_status_clean():
        raise RuntimeError('working tree must be clean before syncing completed tasks')

    conn = db_connect()
    results: list[dict[str, str]] = []
    try:
        for task in task_rows(conn, task_id):
            if already_synced(conn, task.task_id):
                results.append({'task_id': task.task_id, 'status': 'skipped', 'reason': 'already-synced'})
                continue
            branch = task.branch_name or ''
            if branch != current_branch() and not local_branch_exists(branch):
                results.append({'task_id': task.task_id, 'status': 'skipped', 'reason': 'branch-not-local'})
                continue
            pr = ensure_pr(task, dry_run=dry_run)
            if not dry_run:
                record_sync(conn, task, pr)
            results.append({'task_id': task.task_id, 'status': 'synced', 'pr_url': pr.url, 'branch': branch})
    finally:
        conn.close()
    return results


class SyncLoop(threading.Thread):
    def __init__(self, interval_seconds: int = 60) -> None:
        super().__init__(name='github-pr-sync', daemon=True)
        self.interval_seconds = interval_seconds
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                sync_once()
            except Exception as exc:  # pragma: no cover - best-effort background loop
                print(f'[github-pr-sync] {exc}', file=sys.stderr)
            self._stop_event.wait(self.interval_seconds)


def start_background_loop(enabled: bool = True, interval_seconds: int = 60) -> SyncLoop | None:
    if not enabled:
        return None
    loop = SyncLoop(interval_seconds=interval_seconds)
    loop.start()
    return loop


def main() -> int:
    parser = argparse.ArgumentParser(description='Sync completed Hermes coding tasks to GitHub pull requests.')
    parser.add_argument('--task-id', help='Only sync the given task id.')
    parser.add_argument('--dry-run', action='store_true', help='Show what would happen without creating or updating PRs.')
    parser.add_argument('--interval', type=int, default=60, help='Background loop interval in seconds.')
    parser.add_argument('--watch', action='store_true', help='Keep polling for new sync candidates.')
    args = parser.parse_args()

    if args.watch:
        loop = start_background_loop(True, args.interval)
        assert loop is not None
        print('GitHub PR sync loop running; press Ctrl-C to stop.')
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            loop.stop()
            return 0

    results = sync_once(task_id=args.task_id, dry_run=args.dry_run)
    print(json.dumps(results, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
