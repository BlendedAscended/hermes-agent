#!/usr/bin/env python3
"""Run and verify the local Luna continuity goal acceptance suite."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path


HERMES_ROOT = Path(__file__).resolve().parents[2]
HERMES_TASK_BASELINE = "d923a5eb487a17c0a68801bba9ef0f56c81040a8"
HERMES_MAIN_PREFLIGHT = HERMES_ROOT / ".session/evidence/hermes-main-merge-preflight-20261030.json"
HERMES_MAIN_INTEGRATION_RECORD = HERMES_ROOT / ".session/evidence/hermes-main-integration-20261030.json"
PROJECTS = Path("/Users/sandeepsingh/Projects/Project26")
JACK_ROOT = PROJECTS / "Agents/jack-exec"
JACK_WORKTREE = PROJECTS / "Agents/.worktrees/jack-exec-luna-integration-20261030"
VFLOW_ROOT = PROJECTS / "Agents/vflow2.0"
VFLOW_WORKTREE = VFLOW_ROOT / ".worktrees/ops-studio-integration-20261030"
AICT_ROOT = PROJECTS / "ai-config-transfer"
SWEEP_ROOT = Path.home() / ".hermes/profiles/architect/skills/state-anchor-discriminator-sweep"
PROFILE_ROOT = Path.home() / ".hermes/profiles"
PACKET_ROOT = Path.home() / ".hermes/state/discriminator-packets"
ANCHOR_FILE = Path.home() / ".hermes/state/us-51-state-anchors-2026.json"
AICT_RECEIPT_COMMIT = "534ce55"
JACK_SOURCE_COMMIT = "010875a89d97d93ca4c9235b0ffa077d8306917f"
VFLOW_SOURCE_COMMIT = "2c99a2e82bb67c5eb12a69a0c9d5f3a7c4e3b540"
WORKER_SKILL_SHA256 = {
    "validator": "46087adab79cdaa4174f1d2e6f9278d0e651cd58bbe8301004a74aa344c16cc1",
    "sales": "2a404902aee6eff5afef4557bdb377ee72fbf1d1b1589a408a3d13ac5057fc02",
    "backend": "917fdea9e2e9bc553b8924aac923ea1c0b90d0238e90675cd5f8142a4bd816d0",
    "architect": "7338ba386156ee92e11783b5884341450a347c2e6632352e33ad4937bdf361dd",
    "designer": "6661842de1981fa0cf013fecfd0db22a366e7982079d40229dd0c0e77a821b41",
    "delivery": "7940d4e1e140400b2e78ff6050eae4a2141d6b848dc3f338b310dd3ca1aad164",
}
CANONICAL_WORKER_SKILL_SHA256 = "c85e84270524c085a806c34fccdb4b23f5bd7169c73c800f4de52bd9cbf09ee7"
RECEIPT_SYNC_SHA256 = "4fd8fb3d2655ad984dd817e0838f6df4b1548dc0a7cbca234448ffe5c8fc91fb"
CODE_SUFFIXES = {".py", ".sh", ".ts", ".tsx", ".js", ".jsx", ".sql", ".json", ".yaml", ".yml"}

HERMES_FOCUSED = [
    "tests/agent/test_kanban_stop.py",
    "tests/agent/test_kanban_turn_recovery.py",
    "tests/hermes_cli/test_kanban_core_functionality.py",
    "tests/hermes_cli/test_kanban_recovery_continuity.py",
    "tests/hermes_cli/test_kanban_review_lifecycle.py",
    "tests/hermes_cli/test_kanban_review_lifecycle_complete.py",
    "tests/hermes_cli/test_kanban_review_surfaces.py",
    "tests/hermes_cli/test_kanban_task_outcomes.py",
    "tests/hermes_cli/test_kanban_runtime_policy.py",
    "tests/hermes_cli/test_kanban_per_profile_cap.py",
    "tests/hermes_cli/test_kanban_disposable_dispatcher.py",
    "tests/tools/test_kanban_tools.py",
    "tests/tools/test_kanban_redaction.py",
]
SWEEP_TESTS = [
    "tests/skills/test_state_anchor_discriminator_sweep.py",
    "tests/scripts/test_check_profile_archive_boundary.py",
]
VFLOW_FOCUSED = [
    "src/lib/forge/task-continuity.test.ts",
    "src/lib/forge/task-continuity-store.test.ts",
    "src/lib/jack/ops-studio-data.test.ts",
    "src/lib/jack/task-outcomes.test.ts",
    "src/app/api/ops-studio/task-outcomes/route.test.ts",
    "src/app/ops-studio/task-recovery-panel.test.tsx",
]
VFLOW_BUILD_BASELINE_FILES = [
    "src/lib/outreach/dns-security-scanner.ts",
    "src/lib/outreach/campaign-scheduler.ts",
    "src/lib/autopilot/engine.ts",
]


def utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def run_bounded(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else exc.stdout or ""
        stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else exc.stderr or ""
        stderr += f"\nCommand timed out after {timeout} seconds.\n"
        return subprocess.CompletedProcess(command, 124, stdout, stderr)


def run_git(repo: Path, *args: str, check: bool = True) -> str:
    result = run_bounded(["git", "-C", str(repo), *args])
    if check and result.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed in {repo}: {result.stderr.strip()}")
    return result.stdout.strip()


def resolve_commit(repo: Path, revision: str) -> str:
    return run_git(repo, "rev-parse", f"{revision}^{{commit}}")


def task_paths(repo: Path, revision: str) -> list[str]:
    parent = HERMES_TASK_BASELINE if repo.resolve() == HERMES_ROOT.resolve() else resolve_commit(repo, f"{revision}^")
    return [
        path
        for path in run_git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", parent, revision).splitlines()
        if Path(path).suffix in CODE_SUFFIXES and not path.startswith(".session/evidence/")
    ]


def source_snapshot(repo: Path, revision: str) -> dict:
    paths = task_paths(repo, revision)
    digest = hashlib.sha256()
    missing: list[str] = []
    for relative in sorted(paths):
        path = repo / relative
        digest.update(relative.encode())
        digest.update(b"\0")
        if not path.is_file():
            missing.append(relative)
            digest.update(b"MISSING\0")
            continue
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return {
        "repo": str(repo),
        "source_revision": revision,
        "checkout_revision": run_git(repo, "rev-parse", "HEAD"),
        "branch": run_git(repo, "branch", "--show-current"),
        "paths": sorted(paths),
        "sha256": digest.hexdigest(),
        "missing_paths": missing,
    }


def parse_counts(output: str) -> dict[str, int] | None:
    parallel = re.findall(
        r"Summary: \d+ files, (\d+) tests passed, (\d+) failed(?:[^\n]*?, (\d+) skipped)?",
        output,
    )
    if parallel:
        passed, failed, skipped = parallel[-1]
        return {"passed": int(passed), "failed": int(failed), "skipped": int(skipped or 0)}

    tap = re.findall(
        r"(?m)^# tests (\d+)\s*\n# pass (\d+)\s*\n# fail (\d+)\s*\n(?:# cancelled (\d+)\s*\n)?# skipped (\d+)",
        output,
    )
    if tap:
        total, passed, failed, cancelled, skipped = tap[-1]
        return {
            "passed": int(passed),
            "failed": int(failed) + int(cancelled or 0),
            "skipped": int(skipped),
            "total": int(total),
        }

    pytest = re.findall(
        r"(?m)^=+\s*(?:(\d+) passed)?(?:,\s*)?(?:(\d+) failed)?(?:,\s*)?(?:(\d+) skipped)?[^\n]*=+$",
        output,
    )
    if pytest:
        passed, failed, skipped = pytest[-1]
        if passed or failed or skipped:
            return {"passed": int(passed or 0), "failed": int(failed or 0), "skipped": int(skipped or 0)}

    terse = re.findall(r"(?m)^(\d+) passed(?:, (\d+) skipped)?(?:, (\d+) failed)?[^\n]*$", output)
    if terse:
        passed, skipped, failed = terse[-1]
        return {"passed": int(passed), "failed": int(failed or 0), "skipped": int(skipped or 0)}
    return None


def minimal_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "TZ")
        if os.environ.get(key)
    }
    env.setdefault("LANG", "C.UTF-8")
    env.setdefault("LC_ALL", "C.UTF-8")
    env.setdefault("TZ", "UTC")
    if extra:
        env.update(extra)
    return env


def log_path(evidence_dir: Path, name: str) -> Path:
    return evidence_dir / f"{name}.log"


def execute_check(
    evidence_dir: Path,
    name: str,
    repo: Path,
    source_revision: str,
    command: list[str],
    env: dict[str, str],
    expected_counts: dict[str, int] | None = None,
    expected_exit: int = 0,
    diagnostic_signature: str | None = None,
    extra_inputs: list[Path] | None = None,
    expected_empty_json: bool = False,
) -> dict:
    print(f"RUN {name}: {shlex.join(command)}", flush=True)
    before = source_snapshot(repo, source_revision)
    input_hashes_before = {str(path): sha256_file(path) for path in (extra_inputs or [])}
    result = run_bounded(command, cwd=repo, env=env, timeout=1800)
    output = result.stdout + result.stderr
    path = log_path(evidence_dir, name)
    path.write_text(output, encoding="utf-8")
    after = source_snapshot(repo, source_revision)
    input_hashes_after = {str(path): sha256_file(path) for path in (extra_inputs or [])}
    counts = parse_counts(output) if expected_counts is not None else None
    signature_ok = diagnostic_signature in output if diagnostic_signature else True
    empty_json = False
    if expected_empty_json:
        try:
            empty_json = json.loads(output) == []
        except json.JSONDecodeError:
            pass
    record = {
        "name": name,
        "command": command,
        "cwd": str(repo),
        "exit_code": result.returncode,
        "expected_exit_code": expected_exit,
        "log": str(path),
        "log_sha256": sha256_file(path),
        "source_before": before,
        "source_after": after,
        "extra_input_hashes_before": input_hashes_before,
        "extra_input_hashes_after": input_hashes_after,
        "counts": counts,
        "expected_counts": expected_counts,
        "diagnostic_signature": diagnostic_signature,
        "expected_empty_json": expected_empty_json,
        "empty_json": empty_json if expected_empty_json else None,
        "diagnostic_signature_found": signature_ok if diagnostic_signature else None,
    }
    record["passed"] = (
        result.returncode == expected_exit
        and before["sha256"] == after["sha256"]
        and not after["missing_paths"]
        and (expected_counts is None or counts == expected_counts)
        and signature_ok
        and (not expected_empty_json or empty_json)
    )
    print(f"{('PASS' if record['passed'] else 'FAIL')} {name}: exit={result.returncode} counts={counts}", flush=True)
    return record


def branch_evidence(hermes_source: str, aict_source: str) -> dict:
    hermes_branch = run_git(HERMES_ROOT, "branch", "--show-current")
    hermes_main_reachable = run_bounded(
        ["git", "-C", str(HERMES_ROOT), "merge-base", "--is-ancestor", hermes_source, "main"]
    ).returncode == 0
    aict_main_reachable = run_bounded(
        ["git", "-C", str(AICT_ROOT), "merge-base", "--is-ancestor", AICT_RECEIPT_COMMIT, "main"]
    ).returncode == 0
    jack_reachable = run_bounded(
        ["git", "-C", str(JACK_ROOT), "merge-base", "--is-ancestor", JACK_SOURCE_COMMIT, "main"]
    ).returncode == 0
    vflow_reachable = run_bounded(
        ["git", "-C", str(VFLOW_ROOT), "merge-base", "--is-ancestor", VFLOW_SOURCE_COMMIT, "main"]
    ).returncode == 0
    divergence = run_git(HERMES_ROOT, "rev-list", "--left-right", "--count", "main...fleet-py311-pin").split()
    main_integration = hermes_main_integration_evidence(hermes_source)
    return {
        "hermes": {
            "branch": hermes_branch,
            "source_commit": hermes_source,
            "source_on_target_branch": run_bounded(
                ["git", "-C", str(HERMES_ROOT), "merge-base", "--is-ancestor", hermes_source, hermes_branch]
            ).returncode == 0,
            "source_on_default_main": hermes_main_reachable,
            "default_main_ahead_by": int(divergence[0]),
            "target_branch_ahead_by": int(divergence[1]),
            "default_main_integration": main_integration,
            "integration_note": "The task source is committed on fleet-py311-pin; the default main port is pending the documented conflict decision.",
        },
        "jack_exec": {"branch": run_git(JACK_WORKTREE, "branch", "--show-current"), "source_commit": JACK_SOURCE_COMMIT, "source_on_main": jack_reachable},
        "vflow": {"branch": run_git(VFLOW_WORKTREE, "branch", "--show-current"), "source_commit": VFLOW_SOURCE_COMMIT, "source_on_main": vflow_reachable},
        "ai_config_transfer": {
            "branch": run_git(AICT_ROOT, "branch", "--show-current"),
            "tested_revision": aict_source,
            "receipt_implementation_commit": resolve_commit(AICT_ROOT, AICT_RECEIPT_COMMIT),
            "receipt_implementation_on_main": aict_main_reachable,
        },
    }


def hermes_main_integration_evidence(hermes_source: str) -> dict:
    preflight = {"path": str(HERMES_MAIN_PREFLIGHT), "exists": HERMES_MAIN_PREFLIGHT.is_file()}
    if preflight["exists"]:
        preflight["sha256"] = sha256_file(HERMES_MAIN_PREFLIGHT)
        try:
            record = json.loads(HERMES_MAIN_PREFLIGHT.read_text(encoding="utf-8-sig"))
            output_hash = sha256_bytes(record.get("output", "").encode())
            preflight.update({
                "exit_code": record.get("exit_code"),
                "source_commit_matches": resolve_commit(HERMES_ROOT, record.get("source_commit", "")) == hermes_source,
                "target_branch": record.get("target_branch"),
                "conflict_paths": record.get("conflict_paths", []),
                "output_hash_valid": output_hash == record.get("output_sha256"),
            })
            preflight["valid"] = (
                preflight["exit_code"] == 1
                and preflight["source_commit_matches"]
                and preflight["target_branch"] == "main"
                and preflight["output_hash_valid"]
                and bool(preflight["conflict_paths"])
            )
        except (OSError, RuntimeError, ValueError, TypeError, json.JSONDecodeError):
            preflight["valid"] = False
            preflight["error"] = "preflight receipt is malformed or does not match the task source"
    else:
        preflight["valid"] = False

    integration = {
        "path": str(HERMES_MAIN_INTEGRATION_RECORD),
        "recorded": HERMES_MAIN_INTEGRATION_RECORD.is_file(),
        "source_commit_matches": False,
        "integration_commit": None,
        "integration_commit_on_default_main": False,
        "tests_passed": False,
        "changed_code_health_passed": False,
    }
    if integration["recorded"]:
        integration["sha256"] = sha256_file(HERMES_MAIN_INTEGRATION_RECORD)
        try:
            record = json.loads(HERMES_MAIN_INTEGRATION_RECORD.read_text(encoding="utf-8-sig"))
            integration_commit = resolve_commit(HERMES_ROOT, record.get("integration_commit", ""))
            integration.update({
                "integration_commit": integration_commit,
                "source_commit_matches": resolve_commit(
                    HERMES_ROOT, record.get("task_source_commit", "")) == hermes_source,
                "integration_commit_on_default_main": run_bounded([
                    "git", "-C", str(HERMES_ROOT), "merge-base", "--is-ancestor", integration_commit, "main",
                ]).returncode == 0,
                "tests_passed": record.get("tests_passed") is True,
                "changed_code_health_passed": record.get("changed_code_health_passed") is True,
                "record_branch": record.get("target_branch"),
            })
        except (OSError, RuntimeError, ValueError, TypeError, json.JSONDecodeError):
            integration["record_error"] = "integration receipt is malformed or references an unknown commit"
    integration["complete"] = (
        integration["source_commit_matches"]
        and integration["integration_commit_on_default_main"]
        and integration["tests_passed"]
        and integration["changed_code_health_passed"]
        and integration.get("record_branch") == "main"
    )
    integration["preflight"] = preflight
    return integration


def runtime_policy() -> dict:
    configs = [Path.home() / ".hermes/config.yaml", *sorted(PROFILE_ROOT.glob("*/config.yaml"))]
    results = []
    for path in configs:
        content = path.read_text(encoding="utf-8-sig")
        decomposition = re.findall(r"(?m)^\s*auto_decompose:\s*(true|false)\s*(?:#.*)?$", content)
        dispatch = re.findall(r"(?m)^\s*dispatch_in_gateway:\s*(true|false)\s*(?:#.*)?$", content)
        results.append({
            "path": str(path),
            "sha256": sha256_file(path),
            "auto_decompose_values": decomposition,
            "dispatch_in_gateway_values": dispatch,
        })
    all_false = len(results) == 17 and all(r["auto_decompose_values"] == ["false"] for r in results)
    root_dispatch = results[0]["dispatch_in_gateway_values"] if results else []
    profile_dispatch_true = sorted(Path(r["path"]).parent.name for r in results[1:] if r["dispatch_in_gateway_values"] == ["true"])
    expected_profiles = ["comms", "content", "council", "finance", "growth", "research", "sales", "video"]
    return {
        "config_count": len(results),
        "all_auto_decompose_false": all_false,
        "root_dispatch_in_gateway": root_dispatch,
        "profile_dispatch_true": profile_dispatch_true,
        "expected_profile_dispatch_true": expected_profiles,
        "dispatch_policy_preserved": root_dispatch == ["false"] and profile_dispatch_true == expected_profiles,
        "configs": results,
    }


def runtime_dispatch_observation(evidence_dir: Path) -> dict:
    """Capture scheduler and gateway ownership probes without reading process environments."""
    def probe(command: list[str], cwd: Path | None = None) -> dict:
        result = run_bounded(command, cwd=cwd, timeout=20)
        output = result.stdout + result.stderr
        return {
            "command": command,
            "exit_code": result.returncode,
            "output": output,
            "output_sha256": sha256_bytes(output.encode()),
        }

    service = probe(["launchctl", "list", "ai.hermes.gateway"])
    pid_match = re.search(r'"PID"\s*=\s*(\d+)', service["output"])
    pid = pid_match.group(1) if pid_match else None
    process = probe(["ps", "-ww", "-o", "pid=,ppid=,lstart=,comm=", "-p", pid]) if pid else None
    executable = probe(["lsof", "-a", "-p", pid, "-d", "txt,cwd", "-Fn"]) if pid else None

    cron = probe(["crontab", "-l"])
    cron["matching_lines"] = [line for line in cron["output"].splitlines()
                              if re.search(r"kanban|dispatcher|hermes", line, re.IGNORECASE)]
    cron["output"] = "\n".join(cron["matching_lines"])
    cron["output_sha256"] = sha256_bytes(cron["output"].encode())
    labels = probe(["launchctl", "list"])
    labels["matching_lines"] = [line for line in labels["output"].splitlines()
                                if re.search(r"hermes|kanban|dispatcher", line, re.IGNORECASE)]

    jobs_path = Path.home() / ".hermes/cron/jobs.json"
    jobs: list[dict] = []
    jobs_error = None
    try:
        raw_jobs = json.loads(jobs_path.read_text(encoding="utf-8-sig")).get("jobs", [])
        for job in raw_jobs:
            name = str(job.get("name", ""))
            toolsets = job.get("enabled_toolsets") or []
            if re.search(r"kanban|dispatch|worker|drain|task", name, re.IGNORECASE) or "kanban" in toolsets:
                jobs.append({
                    "id": job.get("id"),
                    "name": name,
                    "enabled": job.get("enabled"),
                    "schedule": job.get("schedule_display"),
                    "kanban_toolset_enabled": "kanban" in toolsets,
                })
    except (OSError, json.JSONDecodeError, AttributeError, TypeError) as exc:
        jobs_error = f"{type(exc).__name__}: {exc}"

    workflow = probe([
        "rg", "-l", "-i", "--glob", "*.yml", "--glob", "*.yaml",
        "kanban.*dispatch|dispatch.*kanban", ".github/workflows",
    ], cwd=HERMES_ROOT)
    workflow["matching_paths"] = workflow["output"].splitlines() if workflow["exit_code"] == 0 else []
    gateway_log = probe([
        "rg", "-n", "gateway.run: kanban dispatcher: (disabled|enabled)",
        str(Path.home() / ".hermes/logs/gateway.log"),
    ])
    gateway_log["matching_lines"] = gateway_log["output"].splitlines()[-20:]

    observation = {
        "schema_version": 1,
        "observed_at": datetime.now(UTC).isoformat(),
        "gateway_service": service,
        "gateway_pid": int(pid) if pid else None,
        "gateway_process": process,
        "gateway_executable_and_cwd": executable,
        "system_crontab": cron,
        "launchd_labels": labels,
        "hermes_cron_jobs_path": str(jobs_path),
        "hermes_relevant_cron_jobs": jobs,
        "hermes_cron_jobs_error": jobs_error,
        "repository_workflow_matches": workflow,
        "gateway_dispatch_log": gateway_log,
        "loaded_revision": "unverified; process metadata does not report the imported source revision",
        "active_dispatch_tick_owner": "not proven by the inspected scheduler and process evidence",
    }
    path = evidence_dir / "runtime-dispatch-observation.json"
    path.write_text(json.dumps(observation, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"path": str(path), "sha256": sha256_file(path), "observation": observation}


def hash_tree(root: Path) -> dict:
    digest = hashlib.sha256()
    count = 0
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
        count += 1
    return {"path": str(root), "files": count, "sha256": digest.hexdigest()}


def worker_guidance_evidence() -> dict:
    canonical = AICT_ROOT / "configs/skills/devops/kanban-worker/SKILL.md"
    active_script = Path.home() / ".hermes/scripts/linear-kanban-receipt-sync.py"
    canonical_script = AICT_ROOT / "configs/hermes/scripts/linear-kanban-receipt-sync.py"
    copies = []
    for profile in ("validator", "sales", "backend", "architect", "designer", "delivery"):
        path = PROFILE_ROOT / profile / "skills/devops/kanban-worker/SKILL.md"
        content = path.read_text(encoding="utf-8-sig")
        copies.append({
            "profile": profile,
            "path": str(path),
            "sha256": sha256_file(path),
            "matches_prior_propagation_receipt": sha256_file(path) == WORKER_SKILL_SHA256[profile],
            "has_review_tool": "kanban_request_review" in content,
            "has_profile_custom_content": len(content) > len(canonical.read_text(encoding="utf-8-sig")),
        })
    return {
        "canonical_skill": str(canonical),
        "canonical_skill_sha256": sha256_file(canonical),
        "canonical_skill_matches_prior_propagation_receipt": sha256_file(canonical) == CANONICAL_WORKER_SKILL_SHA256,
        "installed_copies": copies,
        "all_six_copies_preserve_review_and_custom_content": all(
            item["has_review_tool"] and item["has_profile_custom_content"] for item in copies
        ),
        "canonical_receipt_script": str(canonical_script),
        "canonical_receipt_script_sha256": sha256_file(canonical_script),
        "active_receipt_script": str(active_script),
        "active_receipt_script_sha256": sha256_file(active_script),
        "receipt_script_matches": sha256_file(canonical_script) == sha256_file(active_script),
        "receipt_script_matches_prior_receipt": sha256_file(canonical_script) == RECEIPT_SYNC_SHA256,
    }


def sweep_evidence() -> dict:
    paths = {
        "skill": SWEEP_ROOT / "SKILL.md",
        "contract": SWEEP_ROOT / "scripts/disc1_contract.py",
        "runner": SWEEP_ROOT / "scripts/disc1_runner.py",
        "aggregator": SWEEP_ROOT / "scripts/disc1_aggregate.py",
        "driver": Path.home() / ".hermes/scripts/veldon-vertical-discriminator.py",
        "gate_helper": Path.home() / ".hermes/scripts/jev-decide.py",
        "presets": Path.home() / ".hermes/scripts/jev-presets.json",
        "state_anchors": ANCHOR_FILE,
    }
    hashes = {name: {"path": str(path), "sha256": sha256_file(path)} for name, path in paths.items()}
    skill = paths["skill"].read_text(encoding="utf-8-sig")
    frontmatter_ok = skill.startswith("---\n") and "name: state-anchor-discriminator-sweep" in skill and "version: 1.2.0" in skill
    links_ok = all(paths[name].is_file() for name in ("contract", "runner", "aggregator"))
    return {
        "hashes": hashes,
        "skill_frontmatter_valid": frontmatter_ok,
        "linked_scripts_exist": links_ok,
        "negative_regression_test": "test_aggregator_rejects_saved_passes_true_below_score_floor_and_cost_is_unknown",
        "historical_packet_tree": hash_tree(PACKET_ROOT),
    }


def parse_dispatcher_proof(path: Path, source_revision: str) -> dict:
    proof = json.loads(path.read_text(encoding="utf-8-sig"))
    expected_exe = str((HERMES_ROOT / "venv/bin/python").resolve())
    checks = {
        "proof_verified": proof.get("verified") is True,
        "owner_executable_matches_test_python": proof.get("executable") == expected_exe,
        "owner_revision_matches_source": proof.get("source_commit") == source_revision,
        "owner_has_tick_lock": proof.get("owner_result", {}).get("skipped_locked") is False,
        "competitor_respects_tick_lock": proof.get("competitor", {}).get("skipped_locked") is True,
        "isolated_profile_and_board": proof.get("owner", {}).get("profile") == "default" and proof.get("owner", {}).get("board") == "default",
        "disposable_db": "/pytest-" in proof.get("owner", {}).get("database", ""),
        "task_remains_ready": proof.get("task_status_after_tick") == "ready",
    }
    return {"path": str(path), "sha256": sha256_file(path), "proof": proof, "checks": checks, "passed": all(checks.values())}


def run_collect(evidence_dir: Path, receipt_path: Path) -> dict:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    gate_self_test_errors = self_test()
    hermes_source = resolve_commit(HERMES_ROOT, "HEAD")
    aict_checkout = resolve_commit(AICT_ROOT, "main")
    aict_source = resolve_commit(AICT_ROOT, AICT_RECEIPT_COMMIT)
    jack_source = resolve_commit(JACK_ROOT, JACK_SOURCE_COMMIT)
    vflow_source = resolve_commit(VFLOW_ROOT, VFLOW_SOURCE_COMMIT)
    python = str((HERMES_ROOT / "venv/bin/python").resolve())
    hermes_env = minimal_env({"HERMES_PYTHON": python})
    vflow_env = minimal_env({"NEXT_TELEMETRY_DISABLED": "1", "CI": "1"})
    checks: list[dict] = []

    checks.append(execute_check(evidence_dir, "hermes-focused", HERMES_ROOT, hermes_source,
        ["scripts/run_tests.sh", "-j", "4", *HERMES_FOCUSED, "-q", "-rs", "--tb=short"],
        hermes_env, {"passed": 263, "failed": 0, "skipped": 2}))

    dispatcher_proof = evidence_dir / "disposable-dispatcher-proof.json"
    dispatcher_env = minimal_env({"HERMES_PYTHON": python, "LUNA_DISPATCHER_EVIDENCE_PATH": str(dispatcher_proof)})
    checks.append(execute_check(evidence_dir, "hermes-disposable-dispatcher", HERMES_ROOT, hermes_source,
        ["scripts/run_tests.sh", "-j", "1", "tests/hermes_cli/test_kanban_disposable_dispatcher.py", "-q", "--tb=short"],
        dispatcher_env, {"passed": 1, "failed": 0, "skipped": 0}))

    checks.append(execute_check(evidence_dir, "sweep-offline", HERMES_ROOT, hermes_source,
        ["scripts/run_tests.sh", "-j", "2", *SWEEP_TESTS, "-q", "-rs", "--tb=short"],
        hermes_env, {"passed": 23, "failed": 0, "skipped": 0}))

    checks.append(execute_check(evidence_dir, "jack-exec", JACK_WORKTREE, jack_source,
        ["uv", "run", "--frozen", "pytest", "-q", "--tb=short"], minimal_env(),
        {"passed": 124, "failed": 0, "skipped": 0}))

    checks.append(execute_check(evidence_dir, "aict-receipt-sync", AICT_ROOT, aict_source,
        ["python3", "-m", "pytest", "-q", "configs/hermes/scripts/tests/test_linear_kanban_receipt_sync.py", "--tb=short"],
        minimal_env(), {"passed": 16, "failed": 0, "skipped": 0}))

    checks.append(execute_check(evidence_dir, "vflow-rendered-focused", VFLOW_WORKTREE, vflow_source,
        ["node", "--test", "--import", "tsx", "--experimental-test-module-mocks", *VFLOW_FOCUSED],
        vflow_env, {"passed": 48, "failed": 0, "skipped": 0}))

    db_replay = VFLOW_WORKTREE / ".session/evidence/luna-task-state-db-replay-20261030.sh"
    checks.append(execute_check(evidence_dir, "vflow-disposable-postgres", VFLOW_WORKTREE, vflow_source,
        ["bash", str(db_replay)], vflow_env, {"passed": 9, "failed": 0, "skipped": 0}, extra_inputs=[db_replay]))

    vflow_code_paths = [p for p in task_paths(VFLOW_ROOT, vflow_source) if Path(p).suffix in {".ts", ".tsx", ".js", ".jsx"}]
    checks.append(execute_check(evidence_dir, "vflow-eslint", VFLOW_WORKTREE, vflow_source,
        ["./node_modules/.bin/eslint", *vflow_code_paths], vflow_env))
    checks.append(execute_check(evidence_dir, "vflow-full-typecheck", VFLOW_WORKTREE, vflow_source,
        ["./node_modules/.bin/tsc", "--noEmit", "--pretty", "false"], vflow_env))

    build = execute_check(evidence_dir, "vflow-build-baseline-diagnostic", VFLOW_WORKTREE, vflow_source,
        ["./node_modules/.bin/next", "build", "--no-lint"], vflow_env,
        expected_exit=1, diagnostic_signature="node:dns/promises")
    build_base = resolve_commit(VFLOW_ROOT, f"{vflow_source}^")
    build["baseline_files_unchanged"] = all(
        run_bounded(
            ["git", "-C", str(VFLOW_WORKTREE), "diff", "--quiet", build_base, vflow_source, "--", path],
        ).returncode == 0
        for path in VFLOW_BUILD_BASELINE_FILES
    )
    build["baseline_file_hashes"] = {
        path: sha256_file(VFLOW_WORKTREE / path) for path in VFLOW_BUILD_BASELINE_FILES
    }
    build["passed"] = build["passed"] and build["baseline_files_unchanged"]
    checks.append(build)

    checks.append(execute_check(
        evidence_dir, "hermes-repository-check", HERMES_ROOT, hermes_source,
        ["venv/bin/python", "scripts/check"], minimal_env(), expected_exit=1,
        diagnostic_signature="scripts/check: 12 checks, FAILED: health"))
    checks.append(execute_check(
        evidence_dir, "hermes-changed-code-health", HERMES_ROOT, hermes_source,
        ["venv/bin/python", "-m", "scripts.code_health", "--base", HERMES_TASK_BASELINE,
         "--head", hermes_source, "--json"], minimal_env(), expected_empty_json=True))

    runtime_log = log_path(evidence_dir, "infra-truth-final")
    print("RUN infra-truth-final: bash ~/.hermes/scripts/infra-truth.sh", flush=True)
    infra = run_bounded(["bash", str(Path.home() / ".hermes/scripts/infra-truth.sh")],
                        cwd=HERMES_ROOT, env=minimal_env(), timeout=90)
    runtime_log.write_text(infra.stdout + infra.stderr, encoding="utf-8")
    runtime = {"command": ["bash", str(Path.home() / ".hermes/scripts/infra-truth.sh")],
        "exit_code": infra.returncode, "log": str(runtime_log), "log_sha256": sha256_file(runtime_log),
        "passed": infra.returncode == 0}
    dispatch_observation = runtime_dispatch_observation(evidence_dir)

    policy = runtime_policy()
    workers = worker_guidance_evidence()
    sweep = sweep_evidence()
    dispatcher = parse_dispatcher_proof(dispatcher_proof, hermes_source) if dispatcher_proof.is_file() else {"passed": False, "checks": {"proof_file_exists": False}}
    branches = branch_evidence(hermes_source, aict_checkout)
    skill_load_log = log_path(evidence_dir, "architect-skill-load")
    skill_load = run_bounded(
        ["hermes", "-p", "architect", "skills", "list", "--enabled-only", "--source", "local"],
        cwd=HERMES_ROOT, env=minimal_env(), timeout=90)
    skill_load_log.write_text(skill_load.stdout + skill_load.stderr, encoding="utf-8")
    skill_load_evidence = {
        "command": ["hermes", "-p", "architect", "skills", "list", "--enabled-only", "--source", "local"],
        "exit_code": skill_load.returncode,
        "log": str(skill_load_log),
        "log_sha256": sha256_file(skill_load_log),
        "visible_in_profile": "state-anchor" in (skill_load.stdout + skill_load.stderr) and "enabled" in (skill_load.stdout + skill_load.stderr),
    }
    skill_load_evidence["passed"] = skill_load_evidence["exit_code"] == 0 and skill_load_evidence["visible_in_profile"] and sweep["skill_frontmatter_valid"] and sweep["linked_scripts_exist"]

    report = {
        "schema_version": 1,
        "goal_id": "01a18a11-cd5b-7a13-9195-665802b0efc7",
        "collected_at": datetime.now(UTC).isoformat(),
        "hermes_task_source_commit": hermes_source,
        "checks": checks,
        "runtime_probe": runtime,
        "runtime_dispatch_observation": dispatch_observation,
        "runtime_policy": policy,
        "worker_guidance": workers,
        "sweep": sweep,
        "disposable_dispatcher": dispatcher,
        "architect_skill_load": skill_load_evidence,
        "branches": branches,
        "provider_calls": {"count": 0, "cost_usd": None, "cost_status": "not_applicable_no_live_calls"},
        "production_boundary": {
            "deploy_or_restart": False,
            "external_messages": False,
            "production_database_mutations": False,
            "active_gateway_loaded_revision": "unverified",
            "active_local_dispatcher_tick_owner": "not proven; root dispatch_in_gateway is false and no runtime restart was performed",
        },
        "native_goal_gate_api_supported": False,
        "native_goal_gate_api_reason": "The available Codex goal tools expose create, get and status update only; no gate registration operation is available.",
        "gate_self_test": {
            "passed": not gate_self_test_errors,
            "errors": gate_self_test_errors,
            "cases": ["valid evidence accepted", "tampered test count and log digest rejected"],
        },
    }
    report["status"] = "passed_with_documented_build_and_repository_baselines"
    errors = validate_report(report, check_current=True)
    report["validation_errors"] = errors
    if errors:
        report["status"] = "failed"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"receipt={receipt_path}")
    print(f"status={report['status']}")
    for error in errors:
        print(f"FAIL {error}")
    return report


def validate_check(check: dict, check_current: bool) -> list[str]:
    errors = []
    name = check.get("name", "unknown")
    log = Path(check.get("log", ""))
    if not log.is_file():
        return [f"{name}: log missing"]
    output = log.read_text(encoding="utf-8-sig")
    if sha256_file(log) != check.get("log_sha256"):
        errors.append(f"{name}: log digest changed")
    if check.get("exit_code") != check.get("expected_exit_code"):
        errors.append(f"{name}: unexpected exit code")
    if check.get("expected_counts") is not None and parse_counts(output) != check["expected_counts"]:
        errors.append(f"{name}: test count summary differs from expected")
    if check.get("diagnostic_signature") and check["diagnostic_signature"] not in output:
        errors.append(f"{name}: required diagnostic signature missing")
    if check.get("expected_empty_json"):
        try:
            actual_empty_json = json.loads(output) == []
        except json.JSONDecodeError:
            actual_empty_json = False
        if not check.get("empty_json") or not actual_empty_json:
            errors.append(f"{name}: expected an empty JSON findings list")
    errors.extend(validate_check_source(check))
    if check_current:
        errors.extend(validate_current_check_source(check))
    return errors


def validate_check_source(check: dict) -> list[str]:
    errors = []
    name = check.get("name", "unknown")
    if check.get("baseline_files_unchanged") is False:
        errors.append(f"{name}: baseline build diagnostic touched an implicated file")
    before = check.get("source_before", {})
    after = check.get("source_after", {})
    if before.get("sha256") != after.get("sha256"):
        errors.append(f"{name}: source changed while tests ran")
    if before.get("checkout_revision") != after.get("checkout_revision"):
        errors.append(f"{name}: checkout revision changed while tests ran")
    if check.get("extra_input_hashes_before") != check.get("extra_input_hashes_after"):
        errors.append(f"{name}: extra test input changed while the command ran")
    for input_path, expected_hash in check.get("extra_input_hashes_after", {}).items():
        path = Path(input_path)
        if not path.is_file() or sha256_file(path) != expected_hash:
            errors.append(f"{name}: extra test input missing or digest changed: {input_path}")
    if after.get("missing_paths"):
        errors.append(f"{name}: source paths missing: {after['missing_paths']}")
    for relative, expected_hash in check.get("baseline_file_hashes", {}).items():
        path = Path(check["cwd"]) / relative
        if not path.is_file() or sha256_file(path) != expected_hash:
            errors.append(f"{name}: baseline file hash changed: {relative}")
    if check.get("passed") is not True:
        errors.append(f"{name}: collected gate did not pass")
    return errors


def validate_current_check_source(check: dict) -> list[str]:
    errors = []
    name = check.get("name", "unknown")
    after = check.get("source_after", {})
    repo = Path(after.get("repo", ""))
    revision = after.get("source_revision", "")
    try:
        current = source_snapshot(repo, revision)
    except (OSError, RuntimeError, ValueError) as exc:
        return [f"{name}: cannot refresh source hash: {exc}"]
    if current["sha256"] != after.get("sha256"):
        errors.append(f"{name}: current source hash differs from tested source")
    tested_checkout = after.get("checkout_revision", "")
    if run_bounded(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", tested_checkout, "HEAD"]
    ).returncode != 0:
        errors.append(f"{name}: tested checkout revision is not reachable from current branch")
    return errors


def validate_report_header(report: dict) -> list[str]:
    errors = []
    if report.get("schema_version") != 1:
        errors.append("unsupported receipt schema")
    if report.get("goal_id") != "01a18a11-cd5b-7a13-9195-665802b0efc7":
        errors.append("goal ID mismatch")
    if report.get("status") != "passed_with_documented_build_and_repository_baselines":
        errors.append("receipt status is not a recognized final gate result")
    if report.get("validation_errors"):
        errors.append("receipt contains saved gate validation errors")
    return errors


def validate_runtime_receipt(report: dict, check_current: bool) -> list[str]:
    errors = []
    if not report.get("runtime_probe", {}).get("passed"):
        errors.append("infra-truth probe did not pass")
    runtime_probe = report.get("runtime_probe", {})
    runtime_log = Path(runtime_probe.get("log", ""))
    if not runtime_log.is_file() or sha256_file(runtime_log) != runtime_probe.get("log_sha256"):
        errors.append("infra-truth log missing or digest changed")
    if check_current:
        dispatch_observation = report.get("runtime_dispatch_observation", {})
        dispatch_path = Path(dispatch_observation.get("path", ""))
        if not dispatch_path.is_file() or sha256_file(dispatch_path) != dispatch_observation.get("sha256"):
            errors.append("read only runtime dispatcher observation missing or digest changed")
        else:
            observation = dispatch_observation.get("observation", {})
            if not observation.get("loaded_revision", "").startswith("unverified;"):
                errors.append("runtime loaded revision is not reported with its verification limit")
            if not observation.get("active_dispatch_tick_owner", "").startswith("not proven"):
                errors.append("runtime dispatcher ownership claim exceeds the observed evidence")
    return errors


def validate_policy_evidence(report: dict) -> list[str]:
    errors = []
    for field in ("runtime_policy", "worker_guidance", "sweep", "disposable_dispatcher", "architect_skill_load"):
        if not report.get(field):
            errors.append(f"missing {field} evidence")
    if not report.get("runtime_policy", {}).get("all_auto_decompose_false"):
        errors.append("auto_decompose policy is not uniformly false")
    if not report.get("runtime_policy", {}).get("dispatch_policy_preserved"):
        errors.append("dispatcher settings differ from the recorded intentional policy")
    if not report.get("worker_guidance", {}).get("all_six_copies_preserve_review_and_custom_content"):
        errors.append("worker skill copies failed preservation checks")
    if not report.get("worker_guidance", {}).get("receipt_script_matches"):
        errors.append("active AICT receipt sync script differs from canonical source")
    if not report.get("worker_guidance", {}).get("receipt_script_matches_prior_receipt"):
        errors.append("AICT receipt sync script differs from the recorded implementation")
    if not report.get("worker_guidance", {}).get("canonical_skill_matches_prior_propagation_receipt"):
        errors.append("canonical worker skill differs from the recorded implementation")
    if not all(item.get("matches_prior_propagation_receipt") for item in report.get("worker_guidance", {}).get("installed_copies", [])):
        errors.append("an installed worker skill differs from its saved propagation hash")
    return errors


def validate_skill_and_sweep_evidence(report: dict) -> list[str]:
    errors = []
    if not report.get("sweep", {}).get("skill_frontmatter_valid") or not report.get("sweep", {}).get("linked_scripts_exist"):
        errors.append("sweep skill frontmatter or linked scripts invalid")
    if not report.get("disposable_dispatcher", {}).get("passed"):
        errors.append("disposable dispatcher ownership proof failed")
    if not report.get("architect_skill_load", {}).get("passed"):
        errors.append("architect profile did not load the specialized sweep skill")
    skill_load = report.get("architect_skill_load", {})
    skill_log = Path(skill_load.get("log", ""))
    if not skill_log.is_file() or sha256_file(skill_log) != skill_load.get("log_sha256"):
        errors.append("architect skill load log missing or digest changed")
    return errors


def validate_live_source_hashes(report: dict) -> list[str]:
    errors = []
    try:
        if runtime_policy() != report.get("runtime_policy"):
            errors.append("runtime config values or hashes changed after collection")
        if worker_guidance_evidence() != report.get("worker_guidance"):
            errors.append("worker skill files or active receipt script changed after collection")
        current_sweep = sweep_evidence()
        saved_sweep = report.get("sweep", {})
        if current_sweep.get("hashes") != saved_sweep.get("hashes"):
            errors.append("sweep inputs or scripts changed after collection")
        if current_sweep.get("historical_packet_tree") != saved_sweep.get("historical_packet_tree"):
            errors.append("historical discriminator packets changed after collection")
    except (OSError, RuntimeError) as exc:
        errors.append(f"cannot refresh installed policy/skill hashes: {exc}")
    return errors


def validate_branch_evidence(report: dict, check_current: bool) -> list[str]:
    errors = []
    required_branches = {
        "jack_exec": "source_on_main",
        "vflow": "source_on_main",
        "ai_config_transfer": "receipt_implementation_on_main",
    }
    for name, key in required_branches.items():
        if not report.get("branches", {}).get(name, {}).get(key):
            errors.append(f"{name} source commit is not reachable from local main")
    hermes_branch = report.get("branches", {}).get("hermes", {})
    if not hermes_branch.get("source_on_target_branch") or hermes_branch.get("branch") != "fleet-py311-pin":
        errors.append("Hermes task commit is not on the recorded active target branch")
    integration = hermes_branch.get("default_main_integration", {})
    if not integration.get("complete"):
        conflicts = integration.get("preflight", {}).get("conflict_paths", [])
        errors.append(
            "Hermes task source is not integrated and tested on local main"
            + (f"; preflight conflicts: {', '.join(conflicts)}" if conflicts else "")
        )
    if check_current:
        errors.extend(validate_branch_snapshot(report))
    return errors


def validate_branch_snapshot(report: dict) -> list[str]:
    errors = []
    try:
        current_branches = branch_evidence(
            report.get("hermes_task_source_commit", ""),
            report.get("branches", {}).get("ai_config_transfer", {}).get("tested_revision", ""),
        )
    except (OSError, RuntimeError, IndexError, ValueError) as exc:
        return [f"cannot refresh branch reachability: {exc}"]
    branch_fields = {
        "hermes": ("branch", "source_on_target_branch", "source_on_default_main", "default_main_integration"),
        "jack_exec": ("source_on_main",),
        "vflow": ("source_on_main",),
        "ai_config_transfer": ("receipt_implementation_on_main",),
    }
    for name, keys in branch_fields.items():
        saved = report.get("branches", {}).get(name, {})
        if any(current_branches[name].get(key) != saved.get(key) for key in keys):
            errors.append(f"{name} branch reachability changed after collection")
    return errors


def validate_dispatcher_proof(report: dict) -> list[str]:
    errors = []
    dispatcher_record = report.get("disposable_dispatcher", {})
    proof_path = Path(dispatcher_record.get("path", ""))
    if not proof_path.is_file() or sha256_file(proof_path) != dispatcher_record.get("sha256"):
        errors.append("disposable dispatcher proof missing or digest changed")
    else:
        refreshed = parse_dispatcher_proof(proof_path, report.get("hermes_task_source_commit", ""))
        if refreshed.get("checks") != dispatcher_record.get("checks") or not refreshed.get("passed"):
            errors.append("disposable dispatcher proof no longer passes its process checks")
    return errors


def validate_provider_and_gate_selftest(report: dict) -> list[str]:
    errors = []
    if report.get("provider_calls", {}).get("count") != 0 or report.get("provider_calls", {}).get("cost_status") != "not_applicable_no_live_calls":
        errors.append("provider call boundary evidence is inconsistent")
    if not report.get("gate_self_test", {}).get("passed") or report.get("gate_self_test", {}).get("errors"):
        errors.append("final gate positive/negative self-test failed")
    return errors


def validate_report(report: dict, check_current: bool) -> list[str]:
    errors = validate_report_header(report)
    checks = report.get("checks", [])
    expected_names = {
        "hermes-focused", "hermes-disposable-dispatcher", "sweep-offline", "jack-exec",
        "aict-receipt-sync", "vflow-rendered-focused", "vflow-disposable-postgres",
        "vflow-eslint", "vflow-full-typecheck", "vflow-build-baseline-diagnostic",
        "hermes-repository-check", "hermes-changed-code-health",
    }
    actual_names = {check.get("name") for check in checks}
    if actual_names != expected_names:
        errors.append(f"required check set mismatch: {sorted(actual_names)}")
    for check in checks:
        errors.extend(validate_check(check, check_current))
    errors.extend(validate_runtime_receipt(report, check_current))
    errors.extend(validate_policy_evidence(report))
    errors.extend(validate_skill_and_sweep_evidence(report))
    errors.extend(validate_live_source_hashes(report))
    errors.extend(validate_branch_evidence(report, check_current))
    errors.extend(validate_dispatcher_proof(report))
    errors.extend(validate_provider_and_gate_selftest(report))
    return errors


def self_test() -> list[str]:
    errors = []
    with tempfile.TemporaryDirectory(prefix="luna-gate-selftest-") as directory:
        root = Path(directory)
        log = root / "good.log"
        log.write_text("1 passed, 0 skipped, 0 failed in 0.1s\n", encoding="utf-8")
        check = {
            "name": "self-test",
            "log": str(log),
            "log_sha256": sha256_file(log),
            "exit_code": 0,
            "expected_exit_code": 0,
            "expected_counts": {"passed": 1, "failed": 0, "skipped": 0},
            "source_before": {"sha256": "same", "repo": str(root), "source_revision": "unused", "missing_paths": []},
            "source_after": {"sha256": "same", "repo": str(root), "source_revision": "unused", "missing_paths": []},
            "passed": True,
        }
        good_errors = validate_check(check, False)
        if good_errors:
            errors.append(f"valid evidence rejected: {good_errors}")
        log.write_text("1 passed, 0 skipped, 1 failed in 0.1s\n", encoding="utf-8")
        bad_errors = validate_check(check, False)
        if not bad_errors:
            errors.append("deliberately invalid evidence was accepted")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collect", action="store_true", help="Run local acceptance checks and save a final receipt")
    parser.add_argument("--verify", type=Path, help="Verify a saved receipt against current sources and log hashes")
    parser.add_argument("--self-test", action="store_true", help="Prove valid evidence passes and invalid evidence fails")
    parser.add_argument("--evidence-dir", type=Path, help="Evidence directory used with --collect")
    parser.add_argument("--receipt", type=Path, help="Final receipt path used with --collect")
    args = parser.parse_args()

    if args.self_test:
        errors = self_test()
        if errors:
            for error in errors:
                print(f"FAIL {error}")
            return 1
        print("PASS self-test: valid evidence accepted; tampered count and digest rejected")
        return 0
    if args.collect:
        stamp = utc_stamp()
        evidence_dir = args.evidence_dir or (HERMES_ROOT / ".session/evidence" / f"luna-final-{stamp}")
        receipt = args.receipt or (evidence_dir / "final-receipt.json")
        report = run_collect(evidence_dir, receipt)
        return 0 if report["status"] != "failed" else 1
    if args.verify:
        report = json.loads(args.verify.read_text(encoding="utf-8-sig"))
        errors = validate_report(report, check_current=True)
        if errors:
            for error in errors:
                print(f"FAIL {error}")
            return 1
        print(f"PASS receipt {args.verify}: {report.get('status')}")
        return 0
    parser.error("choose --self-test, --collect, or --verify")


if __name__ == "__main__":
    raise SystemExit(main())
