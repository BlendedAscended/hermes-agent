"""Cold worker import order: session_kanban must never load kanban_db_dispatch first.

kanban_db_dispatch -> kanban_db_heartbeat -> kanban_db -> ``from kanban_db_dispatch
import DEFAULT_FAILURE_LIMIT`` raises ImportError on the half loaded dispatch module,
which crashed every managed Kanban worker before its first tool call.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _cold(code):
    return subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True, text=True, timeout=120, check=False)


def test_bind_worker_context_import_path_is_cold_safe():
    src = (ROOT / 'gateway' / 'session_kanban.py').read_text()
    for fn in ('def bind_worker_context', 'def _watch_submitter'):
        body = src.split(fn, 1)[1]
        db_idx = body.index('from hermes_cli import kanban_db as _kb_first')
        dispatch_idx = body.index('from hermes_cli.kanban_db_dispatch import')
        assert db_idx < dispatch_idx, f'{fn} must import kanban_db before kanban_db_dispatch'


def test_cold_import_kanban_db_first_then_dispatch_succeeds():
    r = _cold('from hermes_cli import kanban_db\n'
              'from hermes_cli.kanban_db_dispatch import _worker_terminal_timeout_env, _worker_alive\n'
              'print("ok")')
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == 'ok'
