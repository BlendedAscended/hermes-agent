from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import urllib.error

import pytest


SCRIPTS = Path.home() / ".hermes/profiles/architect/skills/state-anchor-discriminator-sweep/scripts"
sys.path.insert(0, str(SCRIPTS))
import disc1_aggregate as aggregate_script
import disc1_contract as contract
import disc1_runner as runner


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, raw: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return _hash(path)


def _fixture_run(tmp_path: Path, *, low_score=False):
    run_dir = tmp_path / "disc1-offline-fixture"
    run_dir.mkdir(parents=True)
    anchor = {"code": "OH", "state": "Ohio", "veldon_vertical": "manufacturing"}
    projects_root = tmp_path / "agents"
    registry = []
    labels = {
        "M-INTAKE": "Single URL intake parser", "M-SEGMENT": "Admin audience segment query",
        "M-DRAFT": "Forge capability draft generation", "M-HITL": "Jack HITL card outbox target",
        "M-ESCALATE": "Expired assignment escalation", "M-RECEIPT": "Jack execution receipt verifier",
    }
    for module_id, (plane, relative_path, symbol) in contract.MODULE_SOURCES.items():
        source = projects_root / plane / relative_path
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(f"def {symbol}(): pass\n")
        registry.append({"id": module_id, "label": labels[module_id], "plane": plane,
            "implemented": True, "source_path": str(source.resolve()), "symbol": symbol,
            "source_sha256": _hash(source)})
    lane = [{"model": "deepseek/deepseek-v4.1-flash", "reasoning": {"enabled": False}},
            {"model": "z-ai/glm-5.3-flash", "reasoning": None}]
    prompt = "offline prompt body"
    anchors_path = tmp_path / "anchors.json"
    anchors_path.write_text(json.dumps({"states": [anchor]}))
    preset_path = tmp_path / "jev-presets.json"
    preset_path.write_bytes(b"offline-preset")
    source_registry = [{"name": name, "source": source_url} for name, source_url in [
        ("Krabat AI", "https://krabat.ai"), ("Lindy AI", "https://www.lindy.ai"),
        ("Gumloop", "https://www.gumloop.com"), ("Relevance AI", "https://relevanceai.com"),
        ("Clay", "https://www.clay.com")]]
    manifest = contract.build_manifest(run_id=run_dir.name, states=["OH"],
        urls=[contract.URLS[-1]], anchors={"OH": anchor}, prompt="offline template",
        anchors_path=anchors_path, preset_path=preset_path, preset_bytes=b"offline-preset",
        projects_root=projects_root, lane=lane, gate_model="typesafe/jev-1.14",
        module_registry=registry, source_registry=source_registry)
    (run_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2))
    doc_path = run_dir / "fetch-cache" / "www-clay-com.json"
    grounded_doc = {"url": "https://www.clay.com", "title": "Clay", "description": "Fixture",
        "extracted_chars": 800, "price_points_detected": [], "customer_logos_detected": [],
        "markdown_excerpt": "Grounded test input"}
    doc_hash = _write(doc_path, json.dumps(grounded_doc).encode())
    score = 0.01 if low_score else 0.91
    synthesis = {
        "synthesized_product_name": "Forge Intake Fixture",
        "_synthesized_by": lane[0]["model"],
        "multi_source_fusion_formula": [
            "Krabat AI: 60 second intake for Ohio manufacturing",
            "Lindy AI: approval workflow",
            "Gumloop: task telemetry",
            "Relevance AI: specialist handoff",
            "Ohio State Enterprise Anchor: Ohio manufacturing suppliers",
        ],
        "devils_advocate_critique": {
            "why_single_source_copy_fails": "One site does not cover the Ohio workflow.",
            "competitor_weakness_exploited": "The source lacks a state specific workflow.",
            "customer_churn_latency_fix": "A cached response stays under three seconds.",
        },
        "six_step_autopilot_flow": [{"step": i, "name": f"Step {i}", "module_id": "M-INTAKE",
            "latency_budget_ms": 1000} for i in range(1, 7)],
        "target_buyer_segments": [{"segment": "Ohio manufacturers", "state_ecosystem_tie": "Ohio",
            "offer_price_usd": "$10"}],
        "sample_cold_email": {"subject": "Ohio workflow", "body": f"Book {contract.BOOKING_URL}"},
    }
    verdict = {"model": "typesafe/jev-1.14", "via": "decisions",
        "answers": {"multi_source_synthesis": {"noul": score},
            "customer_latency_safe": {"noul": 0.75},
            "veldon_wiring_disposition": {"choice": "WIRE_EXISTING_MODULES"}}, "usage": {}}
    synth_request = {"headers": {"Content-Type": "application/json"}, "body": {
        "model": lane[0]["model"], "provider": {"data_collection": "deny"},
        "messages": [{"role": "system", "content": "fixture"}, {"role": "user", "content": prompt}]}}
    gate_request = {"headers": {"Content-Type": "application/json"}, "body": {
        "model": "typesafe/jev-1.14",
        "state": json.dumps({"synthesis": synthesis, "state_anchor": anchor})[:100000], "questions": {}}}
    raw_synthesis = dict(synthesis)
    raw_synthesis.pop("_synthesized_by")
    evidence = []
    for idx, model, request, response in (
        (1, lane[0]["model"], synth_request,
         {"model": lane[0]["model"], "choices": [{"message": {"content": json.dumps(raw_synthesis)}}]}),
        (2, "typesafe/jev-1.14", gate_request, verdict),
    ):
        request_path = run_dir / "evidence" / f"request-{idx}.json"
        response_path = run_dir / "evidence" / f"response-{idx}.body"
        request_hash = _write(request_path, json.dumps(request).encode())
        response_hash = _write(response_path, json.dumps(response).encode())
        evidence.append({"request_id": f"request-{idx}", "model": model, "status": 200,
            "request_path": str(request_path.resolve()), "request_sha256": request_hash,
            "response_path": str(response_path.resolve()), "response_sha256": response_hash})
    packet = {
        "run_id": run_dir.name, "manifest_sha256": manifest["manifest_sha256"],
        "input_sha256": manifest["input_sha256"], "prompt_template_sha256": manifest["prompt_sha256"],
        "prompt_sha256": contract.sha256_bytes(prompt.encode()),
        "grounded_doc_path": str(doc_path.resolve()), "grounded_doc_sha256": doc_hash,
        "attempt_no": 1, "previous_packet_sha256": None,
        "target_url": contract.URLS[-1], "state_anchor": anchor,
        "task": "t_cc55223a DISC-1",
        "synthesis_and_devils_advocate": synthesis,
        "grounded_extraction": {k: grounded_doc[k] for k in
            ("title", "description", "extracted_chars", "price_points_detected", "customer_logos_detected")},
        "jev_discriminator_verdict": verdict,
        "critique_gate": {"multi_source_synthesis": score, "customer_latency_safe": 0.75,
            "veldon_wiring_disposition": "WIRE_EXISTING_MODULES", "passes": score >= 0.70},
        "attempt_evidence": evidence, "timestamp": "2026-10-29T00:00:00Z",
    }
    packet_path = runner.packet_path(contract.URLS[-1], "OH", run_dir, 1)
    packet_path.parent.mkdir()
    packet_path.write_text(json.dumps(packet, indent=2))
    audit = [
        {"run_id": run_dir.name, "request_id": item["request_id"], "model": item["model"],
         "status": item["status"], "cost": None, "kind": "gate" if idx == 2 else "synthesis"}
        for idx, item in enumerate(evidence, 1)
    ]
    (run_dir / "_audit_usage.jsonl").write_text("".join(json.dumps(row) + "\n" for row in audit))
    return run_dir, manifest, packet, packet_path


def test_runner_import_does_not_load_key_or_driver():
    assert not hasattr(runner, "KEY")
    assert not hasattr(runner, "VD")
    assert runner.load_key(Path("/missing/.env"), environ={}) == ""


def test_runtime_module_registry_is_built_from_implemented_sources_only():
    registry = runner.module_registry()
    ids = {item["id"] for item in registry}
    assert ids == {"M-INTAKE", "M-SEGMENT", "M-DRAFT", "M-HITL", "M-ESCALATE", "M-RECEIPT"}
    assert not ids.intersection({"M-WATERFALL", "M-VERIFY", "M-BOOK", "M-TELEMETRY"})
    assert all(Path(item["source_path"]).is_file() and item["implemented"] for item in registry)


def test_primary_failure_then_glm_success_keeps_every_sanitized_attempt(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    seen = []

    def transport(request, _timeout):
        body = json.loads(request.data)
        seen.append(body)
        if body["model"] == "deepseek/deepseek-v4.1-flash":
            return 402, b'{"error":{"message":"fixture unavailable"}}', {"Retry-After": "0"}
        response = {"model": body["model"], "choices": [{"message": {"content": "{\"ok\":true}"}}],
                    "usage": {"cost": 0.002}}
        return 200, json.dumps(response).encode(), {"Content-Type": "application/json"}

    result, evidence = runner.synthesise(
        {"url": "https://www.clay.com", "title": "Clay", "description": "",
         "price_points_detected": [], "customer_logos_detected": [], "markdown_excerpt": "source"},
        {"code": "OH", "state": "Ohio", "veldon_vertical": "manufacturing"}, "fixture-oh",
        run_dir=run_dir, run_id="fixture-run", primitives=[], module_registry=[],
        transport=transport, key="offline-secret")
    assert result["_synthesized_by"] == "z-ai/glm-5.3-flash"
    assert [body["model"] for body in seen] == ["deepseek/deepseek-v4.1-flash"] * 4 + ["z-ai/glm-5.3-flash"]
    assert all(body["provider"]["data_collection"] == "deny" for body in seen)
    assert "reasoning" not in seen[-1]
    assert seen[0]["reasoning"] == {"enabled": False}
    assert len(evidence) == 5
    request_files = list((run_dir / "evidence" / "requests").glob("*.json"))
    assert len(request_files) == 5
    assert all("offline-secret" not in p.read_text() and "Authorization" not in p.read_text()
               for p in request_files)
    assert len({p.name for p in request_files}) == 5


def test_all_synthesis_lanes_fail_with_immutable_evidence_and_no_success(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    calls = []

    def transport(request, _timeout):
        model = json.loads(request.data)["model"]
        calls.append(model)
        return 429, b'{"error":{"message":"offline fixture"}}', {}

    result, evidence = runner.synthesise(
        {"url": "https://www.clay.com", "title": "Clay", "description": "",
         "price_points_detected": [], "customer_logos_detected": [], "markdown_excerpt": "source"},
        {"code": "OH", "state": "Ohio", "veldon_vertical": "manufacturing"}, "all-fail-oh",
        run_dir=run_dir, run_id="fixture-run", primitives=[], module_registry=[],
        transport=transport, key="offline-secret")
    assert result["error"] == "all internal cheap rungs failed"
    assert calls.count("deepseek/deepseek-v4.1-flash") == 4
    assert calls.count("z-ai/glm-5.3-flash") == 4
    assert len(evidence) == 8
    assert len(list((run_dir / "evidence" / "responses").glob("*.body"))) == 8


def test_packet_validator_accepts_only_manifest_bound_complete_packet(tmp_path):
    run_dir, manifest, packet, _ = _fixture_run(tmp_path)
    validated = contract.validate_packet(packet, manifest, run_dir=run_dir,
        projects_root=manifest["projects_root"])
    assert validated["passes"] is True
    assert validated["source_count"] == 4
    assert validated["modules"] == ["M-INTAKE"] * 6

    stale = dict(packet, manifest_sha256="0" * 64)
    with pytest.raises(contract.ContractError, match="manifest"):
        contract.validate_packet(stale, manifest, run_dir=run_dir,
            projects_root=manifest["projects_root"])


def test_manifest_rejects_unsafe_run_id_and_fallback_policy(tmp_path):
    _run_dir, manifest, _, _ = _fixture_run(tmp_path)
    unsafe = copy.deepcopy(manifest)
    unsafe["run_id"] = "../escape"
    unsafe["manifest_sha256"] = contract.manifest_digest(unsafe)
    with pytest.raises(contract.ContractError, match="run_id"):
        contract.validate_manifest(unsafe, projects_root=manifest["projects_root"])

    wrong_lane = copy.deepcopy(manifest)
    wrong_lane["lane_policy"]["fallback"] = "any available provider"
    wrong_lane["manifest_sha256"] = contract.manifest_digest(wrong_lane)
    with pytest.raises(contract.ContractError, match="provider policy"):
        contract.validate_manifest(wrong_lane, projects_root=manifest["projects_root"])

    incomplete_registry = copy.deepcopy(manifest)
    incomplete_registry["module_registry"] = incomplete_registry["module_registry"][:-1]
    incomplete_registry["module_registry_sha256"] = contract.sha256_bytes(
        contract.canonical_json(incomplete_registry["module_registry"]))
    incomplete_registry["manifest_sha256"] = contract.manifest_digest(incomplete_registry)
    with pytest.raises(contract.ContractError, match="complete implemented capability set"):
        contract.validate_manifest(incomplete_registry, projects_root=manifest["projects_root"])


def test_packet_validator_rejects_duplicate_anchor_claim_and_incomplete_flow_step(tmp_path):
    run_dir, manifest, packet, _ = _fixture_run(tmp_path)
    packet["synthesis_and_devils_advocate"]["multi_source_fusion_formula"].append(
        "Ohio State Anchor: duplicate anchor claim")
    with pytest.raises(contract.ContractError, match="four distinct source identities"):
        contract.validate_packet(packet, manifest, run_dir=run_dir,
            projects_root=manifest["projects_root"])

    run_dir, manifest, packet, _ = _fixture_run(tmp_path / "flow")
    packet["synthesis_and_devils_advocate"]["six_step_autopilot_flow"][0].pop("name")
    with pytest.raises(contract.ContractError, match="ordered and numbered"):
        contract.validate_packet(packet, manifest, run_dir=run_dir,
            projects_root=manifest["projects_root"])


def test_packet_validator_rejects_duplicate_provider_attempt_ids(tmp_path):
    run_dir, manifest, packet, _ = _fixture_run(tmp_path)
    packet["attempt_evidence"][1]["request_id"] = packet["attempt_evidence"][0]["request_id"]
    with pytest.raises(contract.ContractError, match="request IDs must be unique"):
        contract.validate_packet(packet, manifest, run_dir=run_dir,
            projects_root=manifest["projects_root"])


def test_accepted_packet_restart_is_skipped_without_another_request(tmp_path):
    run_dir, manifest, _, _ = _fixture_run(tmp_path)
    result = runner.run_one(contract.URLS[-1], "OH", manifest["state_anchors"]["OH"]["data"],
        {}, "repair", run_dir=run_dir, run_id=manifest["run_id"], manifest=manifest,
        vd=object(), primitives=[], module_registry_data=manifest["module_registry"],
        fetch_evidence=[], transport=lambda *_a: pytest.fail("accepted packet made an extra request"))
    assert result["accepted"] is True
    assert result["skipped"] == "accepted packet matches run manifest"
    assert len(list((run_dir / "packets").glob("*.json"))) == 1


@pytest.mark.parametrize("mutation, expected", [
    ("weak_fusion", "four distinct source identities"),
    ("invented_source", "outside the manifest source registry"),
    ("missing_link", "cal.com/sandeepx"),
    ("invented_module", "unknown or unimplemented module"),
    ("fallback_gate", "served gate model"),
    ("fallback_synthesis", "outside the approved lane"),
])
def test_packet_validator_rejects_known_false_successes(tmp_path, mutation, expected):
    run_dir, manifest, packet, _ = _fixture_run(tmp_path)
    mutations = {
        "weak_fusion": (
            ("synthesis_and_devils_advocate", "multi_source_fusion_formula"),
            ["Clay: one source", "Clay: repeated source", "Ohio State Enterprise Anchor: Ohio manufacturing"],
        ),
        "invented_source": (
            ("synthesis_and_devils_advocate", "multi_source_fusion_formula", 0),
            "UnknownVendor: invented source",
        ),
        "missing_link": (
            ("synthesis_and_devils_advocate", "sample_cold_email", "body"),
            "No booking link",
        ),
        "invented_module": (
            ("synthesis_and_devils_advocate", "six_step_autopilot_flow", 0, "module_id"),
            "M-WATERFALL",
        ),
        "fallback_gate": (("jev_discriminator_verdict", "model"), "openrouter/free"),
        "fallback_synthesis": (("synthesis_and_devils_advocate", "_synthesized_by"), "openrouter/free"),
    }
    path, value = mutations[mutation]
    target = packet
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(contract.ContractError, match=expected):
        contract.validate_packet(packet, manifest, run_dir=run_dir,
            projects_root=manifest["projects_root"])


def test_aggregator_rejects_saved_passes_true_below_score_floor_and_cost_is_unknown(tmp_path):
    run_dir, manifest, packet, packet_path = _fixture_run(tmp_path, low_score=True)
    packet["critique_gate"]["passes"] = True
    packet_path.write_text(json.dumps(packet))
    ledger, code = aggregate_script.aggregate(run_dir, projects_root=manifest["projects_root"])
    assert code == 2
    assert ledger["status"] == "failed"
    assert ledger["coverage"]["failing"] == []
    assert ledger["coverage"]["invalid_pairs"]
    assert ledger["cost_audit"]["unknown_cost_attempts"] == 2
    assert ledger["cost_audit"]["complete_total_usd"] is None


def test_aggregator_passes_valid_small_run_without_fabricating_missing_cost(tmp_path):
    run_dir, _, _, _ = _fixture_run(tmp_path)
    manifest = json.loads((run_dir / "run_manifest.json").read_text())
    ledger, code = aggregate_script.aggregate(run_dir, projects_root=manifest["projects_root"])
    assert code == 0
    assert ledger["status"] == "accepted"
    assert ledger["coverage"]["expected_packets"] == 1
    assert ledger["cost_audit"]["known_cost_subtotal_usd"] == 0
    assert ledger["cost_audit"]["unknown_cost_attempts"] == 2
    assert ledger["cost_audit"]["complete_total_usd"] is None
    saved_hash = _hash(run_dir / "SUMMARY_LEDGER.json")
    assert aggregate_script.aggregate(run_dir, projects_root=manifest["projects_root"])[1] == 2
    assert _hash(run_dir / "SUMMARY_LEDGER.json") == saved_hash


def test_aggregator_rejects_missing_stale_and_unexpected_pairs(tmp_path):
    run_dir, _, packet, packet_path = _fixture_run(tmp_path / "missing")
    packet_path.unlink()
    manifest = json.loads((run_dir / "run_manifest.json").read_text())
    missing_ledger, code = aggregate_script.aggregate(run_dir, projects_root=manifest["projects_root"])
    assert code == 2 and missing_ledger["coverage"]["missing"]

    run_dir, _, packet, packet_path = _fixture_run(tmp_path / "stale")
    packet["state_anchor"]["veldon_vertical"] = "stale"
    packet_path.write_text(json.dumps(packet))
    manifest = json.loads((run_dir / "run_manifest.json").read_text())
    stale_ledger, code = aggregate_script.aggregate(run_dir, projects_root=manifest["projects_root"])
    assert code == 2 and stale_ledger["coverage"]["invalid_pairs"]

    run_dir, _, packet, packet_path = _fixture_run(tmp_path / "unexpected")
    other = dict(packet, target_url="https://krabat.ai")
    unexpected_path = run_dir / "packets" / "unexpected-a001.json"
    unexpected_path.write_text(json.dumps(other))
    manifest = json.loads((run_dir / "run_manifest.json").read_text())
    unexpected_ledger, code = aggregate_script.aggregate(run_dir, projects_root=manifest["projects_root"])
    assert code == 2 and any("unexpected packet" in x for x in unexpected_ledger["errors"])


def test_jev_strict_lane_records_http_error_without_authorization_or_fallback(tmp_path, monkeypatch):
    path = Path.home() / ".hermes/scripts/jev-decide.py"
    spec = importlib.util.spec_from_file_location("jev_decide_offline_test", path)
    jev = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(jev)
    monkeypatch.setattr(jev, "api_key", lambda: "offline-secret")
    monkeypatch.setattr(jev, "_fallback_chat_decide", lambda *_a, **_k: pytest.fail("strict gate fell back"))
    body = b'{"error":"offline fixture"}'
    error = urllib.error.HTTPError("https://fixture", 402, "payment", {}, io.BytesIO(body))
    monkeypatch.setattr(jev.urllib.request, "urlopen", lambda *_a, **_k: (_ for _ in ()).throw(error))
    evidence_dir = tmp_path / "jev-evidence"
    with pytest.raises(urllib.error.HTTPError):
        jev.decide("fixture", {}, "typesafe/jev-1.14", 1,
                   allow_fallback=False, evidence_dir=str(evidence_dir))
    request = next(evidence_dir.glob("*-request.json"))
    response = next(evidence_dir.glob("*-response.body"))
    meta = next(evidence_dir.glob("*-attempt.json"))
    assert "offline-secret" not in request.read_text()
    assert "Authorization" not in request.read_text()
    assert response.read_bytes() == body
    assert json.loads(meta.read_text())["status"] == 402
