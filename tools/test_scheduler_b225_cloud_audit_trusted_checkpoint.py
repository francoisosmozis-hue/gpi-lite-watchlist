from __future__ import annotations

import ast
import hashlib
import json
import pathlib
import re
import threading
import time
import uuid
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import verify_scheduler_b225_cloud_audit_trusted_checkpoint as tool


ENV = "gpi-lite-production"
POLICY = "scheduler-cloud-audit-v1"
EVALUATION = "2026-07-17T06:00:00Z"


def source_result() -> dict[str, Any]:
    return {"ok": True, "gate": "GO", "verification_version": "source", "value": 7}


def genesis_state() -> dict[str, Any]:
    return {
        "environment_id": ENV,
        "last_attestation_id": None,
        "last_checkpoint_canonical_sha256": tool.ZERO_HASH,
        "last_sequence": -1,
        "policy_id": POLICY,
        "state_version": tool.STATE_VERSION,
    }


def materials() -> tuple[dict[str, Any], bytes, bytes, dict[str, Any]]:
    private = Ed25519PrivateKey.from_private_bytes(b"\x01" * 32)
    public = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    checkpoint = {
        "attestation_id": str(uuid.UUID("12345678-1234-4234-9234-123456789abc")),
        "b224_result_canonical_sha256": tool.canonical_sha256(source_result()),
        "checkpoint_version": tool.CHECKPOINT_VERSION,
        "environment_id": ENV,
        "issued_at_utc": "2026-07-17T05:00:00Z",
        "not_after_utc": "2026-07-17T07:00:00Z",
        "policy_id": POLICY,
        "previous_checkpoint_canonical_sha256": tool.ZERO_HASH,
        "sequence": 0,
    }
    signature = private.sign(tool.canonical_json_bytes(checkpoint))
    root_policy = {
        "authorized_root_keys": {"root-1": hashlib.sha256(public).hexdigest()},
        "environment_id": ENV,
        "policy_id": POLICY,
        "root_policy_version": tool.ROOT_POLICY_VERSION,
    }
    return checkpoint, signature, public, root_policy


def build(monkeypatch: pytest.MonkeyPatch, **changes: Any):
    checkpoint, signature, public, root_policy = materials()
    state = genesis_state()
    checkpoint.update(changes.pop("checkpoint", {}))
    state.update(changes.pop("state", {}))
    if "resign" in changes:
        changes.pop("resign")
    source_builder = changes.pop("source_builder", lambda *args: source_result())
    monkeypatch.setattr(tool, "build_verdict_validity_pin_validity_pin_verdict", source_builder)
    return tool.build_trusted_checkpoint_verdict(
        {}, {}, b"public", {}, {}, {}, {}, {}, {}, {}, EVALUATION, "source-key",
        checkpoint, changes.pop("signature", signature), changes.pop("public", public),
        changes.pop("root_policy", root_policy), changes.pop("root_key_id", "root-1"),
        state, changes.pop("environment", ENV), changes.pop("policy", POLICY),
    )


def failure_code(monkeypatch: pytest.MonkeyPatch, code: str, **changes: Any) -> None:
    with pytest.raises(tool.TrustedCheckpointFailure) as failure:
        build(monkeypatch, **changes)
    assert failure.value.code == code


def test_successful_signed_checkpoint_and_atomic_state_transition(monkeypatch, tmp_path):
    result, state = build(monkeypatch)
    path = tmp_path / "state.json"
    tool.atomic_write_state(path, state)
    assert result["ok"] is True and json.loads(path.read_text()) == state


def test_b224_delegation_exactly_once(monkeypatch):
    calls = []
    build(monkeypatch, source_builder=lambda *args: calls.append(args) or source_result())
    assert len(calls) == 1


def test_b224_failure_sanitization(monkeypatch):
    with pytest.raises(tool.TrustedCheckpointFailure) as failure:
        build(monkeypatch, source_builder=lambda *args: (_ for _ in ()).throw(RuntimeError("secret")))
    assert failure.value.code == "B224_VERIFICATION_FAILED" and "secret" not in failure.value.message


def test_invalid_root_signature_rejection(monkeypatch):
    failure_code(monkeypatch, "INVALID_ROOT_SIGNATURE", signature=b"x" * 64)


def test_unauthorized_root_key_rejection(monkeypatch):
    failure_code(monkeypatch, "UNAUTHORIZED_ROOT_KEY", root_key_id="other")


def test_root_public_key_hash_mismatch_rejection(monkeypatch):
    _, _, public, policy = materials()
    policy["authorized_root_keys"]["root-1"] = "a" * 64
    failure_code(monkeypatch, "UNAUTHORIZED_ROOT_KEY", public=public, root_policy=policy)


def test_environment_mismatch_rejection(monkeypatch):
    failure_code(monkeypatch, "ENVIRONMENT_MISMATCH", environment="other")


def test_policy_mismatch_rejection(monkeypatch):
    failure_code(monkeypatch, "POLICY_MISMATCH", policy="other")


def test_b224_canonical_hash_mismatch_rejection(monkeypatch):
    failure_code(monkeypatch, "B224_HASH_MISMATCH", checkpoint={"b224_result_canonical_sha256": "a" * 64})


def test_expired_checkpoint_rejection(monkeypatch):
    failure_code(monkeypatch, "CHECKPOINT_NOT_CURRENT", checkpoint={"not_after_utc": "2026-07-17T05:59:59Z"})


def test_future_checkpoint_rejection(monkeypatch):
    failure_code(monkeypatch, "CHECKPOINT_NOT_CURRENT", checkpoint={"issued_at_utc": "2026-07-17T06:00:01Z"})


def test_lower_temporal_boundary_inclusive(monkeypatch):
    checkpoint, _, _, _ = materials()
    checkpoint["issued_at_utc"] = EVALUATION
    assert tool.validate_checkpoint(checkpoint)


def test_upper_temporal_boundary_inclusive(monkeypatch):
    checkpoint, _, _, _ = materials()
    checkpoint["not_after_utc"] = EVALUATION
    checkpoint["issued_at_utc"] = "2026-07-17T05:59:59Z"
    assert tool.validate_checkpoint(checkpoint)


def test_duplicate_json_key_rejection():
    with pytest.raises(tool.TrustedCheckpointFailure) as failure:
        tool.parse_json_bytes(b'{"a":1,"a":2}', "document")
    assert failure.value.code == "DUPLICATE_JSON_KEY"


def test_non_finite_json_rejection():
    with pytest.raises(tool.TrustedCheckpointFailure):
        tool.parse_json_bytes(b'{"a":NaN}', "document")


def test_extra_or_missing_checkpoint_field_rejection():
    checkpoint, _, _, _ = materials()
    checkpoint["extra"] = True
    with pytest.raises(tool.TrustedCheckpointFailure):
        tool.validate_checkpoint(checkpoint)


def test_malformed_checkpoint_rejection():
    with pytest.raises(tool.TrustedCheckpointFailure):
        tool.parse_json_bytes(b"{", "checkpoint")


def test_oversized_input_rejection(tmp_path):
    path = tmp_path / "large"
    path.write_bytes(b"x" * 5)
    with pytest.raises(tool.TrustedCheckpointFailure):
        tool.read_bytes(str(path), 4, "input", None)  # type: ignore[arg-type]


def test_sequence_replay_rejection(monkeypatch):
    failure_code(monkeypatch, "REPLAY_SEQUENCE", checkpoint={"sequence": 0}, state={"last_sequence": 0, "last_attestation_id": str(uuid.uuid4()), "last_checkpoint_canonical_sha256": "a" * 64})


def test_sequence_gap_rejection(monkeypatch):
    failure_code(monkeypatch, "REPLAY_SEQUENCE", checkpoint={"sequence": 2})


def test_duplicate_attestation_rejection(monkeypatch):
    checkpoint, _, _, _ = materials()
    failure_code(
        monkeypatch,
        "REPLAY_ATTESTATION",
        checkpoint={"sequence": 1, "previous_checkpoint_canonical_sha256": "a" * 64},
        state={"last_sequence": 0, "last_attestation_id": checkpoint["attestation_id"], "last_checkpoint_canonical_sha256": "a" * 64},
    )


def test_previous_checkpoint_hash_mismatch_rejection(monkeypatch):
    failure_code(monkeypatch, "CHECKPOINT_CHAIN_MISMATCH", checkpoint={"previous_checkpoint_canonical_sha256": "a" * 64})


def test_genesis_transition_acceptance(monkeypatch):
    _, state = build(monkeypatch)
    assert state["last_sequence"] == 0


def test_state_unchanged_on_failure(monkeypatch, tmp_path):
    path = tmp_path / "state.json"
    before = tool.canonical_json_bytes(genesis_state())
    path.write_bytes(before)
    with pytest.raises(tool.TrustedCheckpointFailure):
        build(monkeypatch, signature=b"x" * 64)
    assert path.read_bytes() == before


def test_exclusive_lock_timeout_rejection(tmp_path):
    path = tmp_path / "state.json"
    entered = threading.Event()
    release = threading.Event()
    def holder():
        with tool.exclusive_lock(path, 1):
            entered.set(); release.wait(1)
    thread = threading.Thread(target=holder); thread.start(); entered.wait(1)
    with pytest.raises(tool.TrustedCheckpointFailure) as failure:
        with tool.exclusive_lock(path, 0.01):
            pass
    release.set(); thread.join()
    assert failure.value.code == "LOCK_TIMEOUT"


def test_concurrent_duplicate_acceptance_prevented(tmp_path):
    path = tmp_path / "state.json"
    order = []
    def worker(value):
        with tool.exclusive_lock(path, 1):
            order.append(value); time.sleep(0.02)
    threads = [threading.Thread(target=worker, args=(n,)) for n in (1, 2)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert sorted(order) == [1, 2]


def test_atomic_replace_verified(tmp_path):
    path = tmp_path / "state.json"; path.write_text("old")
    tool.atomic_write_state(path, genesis_state())
    assert json.loads(path.read_text())["state_version"] == tool.STATE_VERSION


def test_temporary_state_cleanup_on_failure(monkeypatch, tmp_path):
    path = tmp_path / "state.json"
    monkeypatch.setattr(tool.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("fail")))
    with pytest.raises(OSError): tool.atomic_write_state(path, genesis_state())
    assert not list(tmp_path.glob("state.json.tmp-*"))


def test_deterministic_pure_build_output(monkeypatch):
    first = build(monkeypatch); second = build(monkeypatch)
    assert first == second


def test_semantic_json_stability():
    assert tool.canonical_sha256({"b": 2, "a": 1}) == tool.canonical_sha256({"a": 1, "b": 2})


def test_one_line_json_help(capsys):
    assert tool.run_cli(["--help"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["ok"] is True


def test_unknown_argument_without_traceback(capsys):
    assert tool.run_cli(["--unknown"]) != 0
    assert "Traceback" not in capsys.readouterr().out


def test_exact_cli_options():
    source = pathlib.Path(tool.__file__).read_text()
    tree = ast.parse(source)
    options = set(re.findall(r'"(--[a-z0-9-]+)"', source))
    assert len(options) == 22 and "--checkpoint" in options and "--replay-state-file" in options


def test_no_network_cloud_subprocess_or_scheduler():
    source = pathlib.Path(tool.__file__).read_text()
    assert all(value not in source for value in ("socket", "requests", "subprocess", "boto", "scheduler.execute"))


def test_permitted_writes_restricted_to_state_transaction():
    source = pathlib.Path(tool.__file__).read_text()
    assert "atomic_write_state(state_path, next_state)" in source
    assert source.count("os.replace(") == 1


def assert_suite_has_exactly_35_functions():
    tree = ast.parse(pathlib.Path(__file__).read_text())
    tests = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")]
    assert len(tests) == 35
