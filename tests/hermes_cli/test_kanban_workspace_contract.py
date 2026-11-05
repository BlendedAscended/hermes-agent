"""The workspace contract: a worker is told what its workspace IS, not left to guess.

`tasks.workspace_path` is the worker's working directory. ``scratch`` cards are
deliberately repo-less (#106342 — an explicit scratch card must not be upgraded
into a worktree of the board's repo), so a scratch workspace is usually an empty
pad. Workers read the field as "my checkout", found nothing, and blocked: on
2026-11-04 card ``t_2da84d09`` burned three runs on that, and 131 of the 174
card workspaces on the council board held no checkout.

The fix is a spoken contract, not a bigger checkout: the dispatcher publishes
``HERMES_KANBAN_WORKSPACE_KIND`` / ``..._IS_CHECKOUT``, ``kanban_show`` repeats
it in the workspace header, and the two disagreeing cases (a scratch pad with no
repo, a ``dir``/``worktree`` card whose checkout is gone) are named in words.
"""

from __future__ import annotations

import os
import subprocess

import pytest


def _task(kb, *, kind: str, path: str | None, branch: str | None = None):
    return kb.Task(
        id="t_ws",
        title="workspace contract",
        body=None,
        assignee="w",
        status="running",
        priority=0,
        created_by="test",
        created_at=1,
        started_at=None,
        completed_at=None,
        workspace_kind=kind,
        workspace_path=path,
        branch_name=branch,
        claim_lock="lock",
        claim_expires=None,
        tenant=None,
    )


@pytest.fixture
def repo(tmp_path):
    """A real git repo with one commit, so ``rev-parse`` and ``.git`` are genuine."""
    root = tmp_path / "repo"
    root.mkdir()
    git = ["git", "-C", str(root)]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "config", "user.email", "t@example.com"], check=True)
    subprocess.run([*git, "config", "user.name", "t"], check=True)
    (root / "README.md").write_text("hi\n", encoding="utf-8")
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "init"], check=True)
    return root


# --- what the path actually holds -------------------------------------------


def test_empty_scratch_pad_is_reported_as_not_a_checkout(tmp_path):
    from hermes_cli.kanban_db_workspace import workspace_checkout_state

    pad = tmp_path / "workspaces" / "t_ws"
    pad.mkdir(parents=True)
    state = workspace_checkout_state(pad)

    assert state["exists"] is True
    assert state["is_checkout"] is False
    assert state["toplevel"] is None
    assert state["entries"] == 0


def test_directory_inside_a_repo_is_a_checkout_and_names_the_toplevel(repo):
    from hermes_cli.kanban_db_workspace import workspace_checkout_state

    nested = repo / "src" / "deep"
    nested.mkdir(parents=True)
    state = workspace_checkout_state(nested)

    assert state["is_checkout"] is True
    assert state["toplevel"] == str(repo)


def test_linked_worktree_is_a_checkout(repo, tmp_path):
    """A worktree's ``.git`` is a FILE, not a directory — the check must accept both."""
    from hermes_cli.kanban_db_workspace import workspace_checkout_state

    wt = repo / ".worktrees" / "t_ws"
    wt.parent.mkdir(parents=True)
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(wt)], check=True)

    assert (wt / ".git").is_file()
    state = workspace_checkout_state(wt)
    assert state["is_checkout"] is True
    assert state["toplevel"] == str(wt)


def test_missing_path_never_raises(tmp_path):
    from hermes_cli.kanban_db_workspace import workspace_checkout_state

    state = workspace_checkout_state(tmp_path / "nope" / "deeper")
    assert state["exists"] is False
    assert state["is_checkout"] is False


def test_missing_path_under_a_repo_is_not_a_checkout(repo):
    """A pruned ``<repo>/.worktrees/<id>`` must not inherit its parent's ``.git``.

    This is the layout of every ``worktree`` card. Substituting the nearest
    existing ancestor answered ``exists=False`` next to ``is_checkout=True`` and
    ``toplevel=<parent repo>`` — a confident "you have a checkout" about a path
    that is not on disk, and the reason the missing-checkout warning stayed
    silent for exactly the cards it exists for.
    """
    from hermes_cli.kanban_db_workspace import workspace_checkout_state

    (repo / ".worktrees").mkdir()
    gone = repo / ".worktrees" / "t_gone"
    assert not gone.exists()

    state = workspace_checkout_state(gone)

    assert state["exists"] is False
    assert state["is_checkout"] is False
    assert state["own_checkout"] is False
    assert state["toplevel"] is None


def test_gitless_dir_under_worktrees_is_inside_a_repo_but_not_its_own_checkout(repo):
    """An empty leftover directory is still not a work tree of its own."""
    from hermes_cli.kanban_db_workspace import workspace_checkout_state

    stale = repo / ".worktrees" / "t_stale"
    stale.mkdir(parents=True)

    state = workspace_checkout_state(stale)

    assert state["exists"] is True
    assert state["entries"] == 0
    assert state["is_checkout"] is True  # it does sit inside the parent repo
    assert state["own_checkout"] is False  # but it carries no ``.git`` of its own
    assert state["toplevel"] == str(repo)


# --- what the worker is told -------------------------------------------------


def test_scratch_pad_gets_an_explicit_not_a_checkout_note(tmp_path):
    from hermes_cli.kanban_db_workspace import SCRATCH_PAD_NOTE, workspace_contract_note

    pad = tmp_path / "t_ws"
    pad.mkdir()
    note = workspace_contract_note("scratch", pad)

    assert note == SCRATCH_PAD_NOTE
    assert "SCRATCH PAD" in note
    assert "not a git checkout" in note
    assert "do NOT block" in note


def test_scratch_pad_that_happens_to_hold_a_repo_is_not_nagged(repo):
    from hermes_cli.kanban_db_workspace import workspace_contract_note

    assert workspace_contract_note("scratch", repo) is None


def test_missing_worktree_is_a_warning_naming_the_branch(tmp_path):
    from hermes_cli.kanban_db_workspace import workspace_contract_note

    note = workspace_contract_note("worktree", tmp_path / "gone", branch="wt/t_ws")

    assert note is not None
    assert "worktree" in note
    assert "missing" in note
    assert "wt/t_ws" in note


def test_present_worktree_checkout_is_silent(repo):
    from hermes_cli.kanban_db_workspace import workspace_contract_note

    assert workspace_contract_note("worktree", repo) is None


def test_dir_kind_that_is_not_a_checkout_is_a_warning(tmp_path):
    from hermes_cli.kanban_db_workspace import workspace_contract_note

    shared = tmp_path / "shared"
    shared.mkdir()
    note = workspace_contract_note("dir", shared)

    assert note is not None
    assert "not a git work tree" in note
    assert "origin/main" in note  # branch fallback when the task carries none


def test_pruned_worktree_inside_a_repo_is_a_warning(repo):
    """The case the whole card is about: ``<repo>/.worktrees/<id>`` gone.

    The path sits inside its parent repo, so an ancestor-based answer reported
    it healthy and returned no note. A ``worktree`` card is judged on the path
    itself.
    """
    from hermes_cli.kanban_db_workspace import workspace_contract_note

    (repo / ".worktrees").mkdir()
    note = workspace_contract_note(
        "worktree", repo / ".worktrees" / "t_gone", branch="wt/t_gone")

    assert note is not None
    assert "worktree" in note
    assert "missing" in note
    assert "wt/t_gone" in note


def test_stale_empty_dir_under_worktrees_is_a_warning(repo):
    from hermes_cli.kanban_db_workspace import workspace_contract_note

    stale = repo / ".worktrees" / "t_stale"
    stale.mkdir(parents=True)

    note = workspace_contract_note("worktree", stale)

    assert note is not None
    assert "not a git work tree" in note


def test_real_linked_worktree_is_silent(repo):
    from hermes_cli.kanban_db_workspace import workspace_contract_note

    wt = repo / ".worktrees" / "t_ok"
    wt.parent.mkdir(parents=True)
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(wt)], check=True)

    try:
        assert workspace_contract_note("worktree", wt) is None
    finally:
        subprocess.run(["git", "-C", str(repo), "worktree", "remove", str(wt)], check=True)


def test_dir_kind_inside_a_repo_stays_silent(repo):
    """The ancestor walk is still the right test for ``dir`` — only for ``dir``."""
    from hermes_cli.kanban_db_workspace import workspace_contract_note

    shared = repo / "shared"
    shared.mkdir()

    assert workspace_contract_note("dir", shared) is None


# --- the header the worker actually reads ------------------------------------


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    """Isolated HERMES_HOME with an empty kanban DB (the worker-context builder needs a conn)."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    from hermes_cli import kanban_db as kb

    kb.init_db()
    return home


def _worker_context(kb, *, workspace_kind: str, workspace_path: str) -> str:
    from hermes_cli.kanban_db_connect import connect_closing

    with connect_closing() as conn:
        task_id = kb.create_task(
            conn, title="workspace contract", assignee="w",
            workspace_kind=workspace_kind, workspace_path=workspace_path,
        )
        return kb.build_worker_context(conn, task_id)


def test_worker_context_carries_the_note(kanban_home, tmp_path):
    from hermes_cli import kanban_db as kb

    pad = tmp_path / "t_ws"
    pad.mkdir()
    text = _worker_context(kb, workspace_kind="scratch", workspace_path=str(pad))

    assert f"Workspace: scratch @ {pad}" in text
    assert "SCRATCH PAD" in text


def test_worker_context_stays_quiet_for_a_real_checkout(kanban_home, repo):
    from hermes_cli import kanban_db as kb

    text = _worker_context(kb, workspace_kind="dir", workspace_path=str(repo))

    assert "SCRATCH PAD" not in text
    assert "Workspace warning" not in text


def test_worker_context_warns_about_a_pruned_worktree(kanban_home, repo):
    """The header a worker actually reads must carry the warning, not silence."""
    from hermes_cli import kanban_db as kb

    (repo / ".worktrees").mkdir()
    text = _worker_context(
        kb, workspace_kind="worktree", workspace_path=str(repo / ".worktrees" / "t_gone"))

    assert "Workspace warning" in text
    assert "missing" in text


# --- the env the dispatcher hands the worker --------------------------------


def _capture_spawn_env(kb, monkeypatch, workspace: str, *, kind: str):
    from hermes_cli import kanban_db_dispatch as kbd

    monkeypatch.setattr(kbd, "_resolve_hermes_argv", lambda: ["hermes"])
    # This test is about the two workspace vars, not about env sanitisation, and
    # build_subprocess_env resolves the install's payload manifest (a read of the
    # real ~/.hermes on a source checkout that lives under it). Stub the seam.
    import tools.environments.local as _local
    import tools.process_registry as _registry

    monkeypatch.setattr(_local, "build_subprocess_env", lambda **kw: dict(os.environ))
    monkeypatch.setattr(_registry, "systemd_user_bus_env", lambda env: env)

    captured: dict = {}

    class FakeProc:
        pid = 4242

    def fake_popen(cmd, *args, **kwargs):
        captured["env"] = dict(kwargs.get("env") or {})
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    kbd._default_spawn(_task(kb, kind=kind, path=workspace), workspace)
    return captured["env"]


def _isolate_home(monkeypatch, tmp_path):
    root = tmp_path / ".hermes"
    (root / "profiles" / "w").mkdir(parents=True)
    (root / "profiles" / "w" / "config.yaml").write_text("toolsets:\n  - kanban\n", encoding="utf-8")
    root.joinpath("config.yaml").write_text("toolsets:\n  - kanban\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(root))


def test_spawn_env_says_the_scratch_pad_is_not_a_checkout(tmp_path, monkeypatch):
    from hermes_cli import kanban_db as kb

    _isolate_home(monkeypatch, tmp_path)
    pad = tmp_path / "t_ws"
    pad.mkdir()

    env = _capture_spawn_env(kb, monkeypatch, str(pad), kind="scratch")

    assert env["HERMES_KANBAN_WORKSPACE"] == str(pad)
    assert env["HERMES_KANBAN_WORKSPACE_KIND"] == "scratch"
    assert env["HERMES_KANBAN_WORKSPACE_IS_CHECKOUT"] == "0"
    assert "HERMES_KANBAN_WORKSPACE_TOPLEVEL" not in env


def test_spawn_env_says_a_worktree_is_a_checkout(repo, tmp_path, monkeypatch):
    from hermes_cli import kanban_db as kb

    _isolate_home(monkeypatch, tmp_path)
    env = _capture_spawn_env(kb, monkeypatch, str(repo), kind="worktree")

    assert env["HERMES_KANBAN_WORKSPACE_KIND"] == "worktree"
    assert env["HERMES_KANBAN_WORKSPACE_IS_CHECKOUT"] == "1"
    assert env["HERMES_KANBAN_WORKSPACE_TOPLEVEL"] == str(repo)


def test_spawn_env_refuses_a_pruned_worktree(repo, tmp_path, monkeypatch):
    """A worktree card whose checkout is gone must never be told it has one."""
    from hermes_cli import kanban_db as kb

    _isolate_home(monkeypatch, tmp_path)
    (repo / ".worktrees").mkdir()
    gone = repo / ".worktrees" / "t_gone"

    env = _capture_spawn_env(kb, monkeypatch, str(gone), kind="worktree")

    assert env["HERMES_KANBAN_WORKSPACE_KIND"] == "worktree"
    assert env["HERMES_KANBAN_WORKSPACE_IS_CHECKOUT"] == "0"
    assert "HERMES_KANBAN_WORKSPACE_TOPLEVEL" not in env


def test_spawn_env_refuses_a_gitless_dir_under_worktrees(repo, tmp_path, monkeypatch):
    from hermes_cli import kanban_db as kb

    _isolate_home(monkeypatch, tmp_path)
    stale = repo / ".worktrees" / "t_stale"
    stale.mkdir(parents=True)

    env = _capture_spawn_env(kb, monkeypatch, str(stale), kind="worktree")

    assert env["HERMES_KANBAN_WORKSPACE_IS_CHECKOUT"] == "0"
    assert "HERMES_KANBAN_WORKSPACE_TOPLEVEL" not in env
