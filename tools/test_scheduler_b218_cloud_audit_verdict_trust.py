from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import io
import json
import pathlib
import sys
from typing import Any

import pytest


TOOLS = pathlib.Path(__file__).resolve().parent
TOOL_PATH = TOOLS / "verify_scheduler_b218_cloud_audit_verdict_trust.py"
B217_PATH = TOOLS / "sign_scheduler_b217_cloud_audit_verdict.py"
KEY_ID = "b218-local-trusted-signer"
PUBLIC_KEY = b"P" * 32
PUBLIC_KEY_SHA256 = hashlib.sha256(PUBLIC_KEY).hexdigest()
HEX_A = "a" * 64
HEX_B = "b" * 64
HEX_C = "c" * 64


class BinaryStdin:
    def __init__(self, data: bytes) -> None:
        self.buffer = io.BytesIO(data)


class SignatureFailure(Exception):
    pass


class ContractFailure(Exception):
    pass


class InputFailure(Exception):
    pass


def load_tool() -> Any:
    tools_string = str(TOOLS)
    if tools_string not in sys.path:
        sys.path.insert(0, tools_string)
    spec = importlib.util.spec_from_file_location("b218_trust_tool", TOOL_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load B2.18 tool")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def tool() -> Any:
    return load_tool()


def valid_verdict() -> dict[str, Any]:
    return {
        "attestation_sha256": HEX_A,
        "authenticated": True,
        "gate": "GO",
        "key_id": "b216-local-key",
        "ok": True,
        "source_canonical_sha256": HEX_B,
        "verdict_canonical_sha256": HEX_C,
        "verification_version": "B2_16_CLOUD_AUDIT_VERDICT_HMAC_VERIFICATION_V1",
    }


def valid_signature() -> dict[str, Any]:
    return {
        "algorithm": "Ed25519",
        "attestation_sha256": HEX_A,
        "hmac_key_id": "b216-local-key",
        "public_key_sha256": PUBLIC_KEY_SHA256,
        "signature_hex": "d" * 128,
        "signature_version": "B2_17_CLOUD_AUDIT_VERDICT_ED25519_V1",
        "signing_key_id": KEY_ID,
        "source_canonical_sha256": HEX_B,
        "source_verification_version": "B2_16_CLOUD_AUDIT_VERDICT_HMAC_VERIFICATION_V1",
        "verdict_canonical_sha256": HEX_C,
    }


def valid_policy(
    status: str = "ACTIVE",
    key_id: str = KEY_ID,
    fingerprint: str = PUBLIC_KEY_SHA256,
) -> dict[str, Any]:
    return {
        "policy_version": "B2_18_CLOUD_AUDIT_SIGNING_KEY_TRUST_POLICY_V1",
        "trusted_signing_keys": [
            {
                "public_key_sha256": fingerprint,
                "signing_key_id": key_id,
                "status": status,
            }
        ],
    }


def valid_b217_result() -> dict[str, Any]:
    return {
        "attestation_sha256": HEX_A,
        "gate": "GO",
        "ok": True,
        "public_key_sha256": PUBLIC_KEY_SHA256,
        "signature_valid": True,
        "signing_key_id": KEY_ID,
        "source_canonical_sha256": HEX_B,
        "verdict_canonical_sha256": HEX_C,
        "verification_version": "B2_17_CLOUD_AUDIT_VERDICT_ED25519_VERIFICATION_V1",
    }


def write_json(path: pathlib.Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def create_inputs(
    tmp_path: pathlib.Path,
    policy: dict[str, Any] | None = None,
) -> dict[str, pathlib.Path]:
    paths = {
        "verdict": tmp_path / "verdict.json",
        "signature": tmp_path / "signature.json",
        "public": tmp_path / "public.key",
        "policy": tmp_path / "policy.json",
    }
    write_json(paths["verdict"], valid_verdict())
    write_json(paths["signature"], valid_signature())
    paths["public"].write_bytes(PUBLIC_KEY)
    write_json(paths["policy"], valid_policy() if policy is None else policy)
    return paths


def arguments(paths: dict[str, pathlib.Path]) -> list[str]:
    return [
        "--verdict",
        str(paths["verdict"]),
        "--signature",
        str(paths["signature"]),
        "--public-key-file",
        str(paths["public"]),
        "--trust-policy",
        str(paths["policy"]),
        "--signing-key-id",
        KEY_ID,
    ]


def install_success(monkeypatch: pytest.MonkeyPatch, tool: Any) -> list[tuple[Any, ...]]:
    calls: list[tuple[Any, ...]] = []

    def fake_verify(*values: Any) -> dict[str, Any]:
        calls.append(values)
        return valid_b217_result()

    monkeypatch.setattr(tool, "verify_signature_envelope", fake_verify)
    return calls


def invoke(
    tool: Any,
    args: list[str],
    capsys: pytest.CaptureFixture[str],
) -> tuple[int, dict[str, Any], str]:
    code = tool.run_cli(args)
    output = capsys.readouterr().out.strip()
    return code, json.loads(output), output


def test_help_is_one_json_line(tool: Any, capsys: pytest.CaptureFixture[str]) -> None:
    code, payload, output = invoke(tool, ["--help"], capsys)
    assert code == 0
    assert len(output.splitlines()) == 1
    assert payload["ok"] is True
    assert payload["trust_tool_version"] == tool.TRUST_TOOL_VERSION


def test_valid_trusted_signature(tool: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    calls = install_success(monkeypatch, tool)
    paths = create_inputs(tmp_path)
    code, payload, _ = invoke(tool, arguments(paths), capsys)
    assert code == 0
    assert payload["gate"] == "GO"
    assert payload["authorized"] is True
    assert payload["public_key_sha256"] == PUBLIC_KEY_SHA256
    assert len(calls) == 1


def test_success_fields_are_exact(tool: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    install_success(monkeypatch, tool)
    paths = create_inputs(tmp_path)
    code, payload, _ = invoke(tool, arguments(paths), capsys)
    assert code == 0
    assert set(payload) == {
        "attestation_sha256",
        "authorized",
        "gate",
        "ok",
        "policy_canonical_sha256",
        "policy_version",
        "public_key_sha256",
        "signature_valid",
        "signing_key_id",
        "source_canonical_sha256",
        "source_verification_version",
        "verdict_canonical_sha256",
        "verification_version",
    }


def test_deterministic_output(tool: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    install_success(monkeypatch, tool)
    paths = create_inputs(tmp_path)
    first = invoke(tool, arguments(paths), capsys)[2]
    second = invoke(tool, arguments(paths), capsys)[2]
    assert first == second


def test_semantically_equivalent_policy_is_stable(tool: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    install_success(monkeypatch, tool)
    paths = create_inputs(tmp_path)
    first = invoke(tool, arguments(paths), capsys)[1]
    paths["policy"].write_text(
        json.dumps(valid_policy(), indent=4, ensure_ascii=False),
        encoding="utf-8",
    )
    second = invoke(tool, arguments(paths), capsys)[1]
    assert first["policy_canonical_sha256"] == second["policy_canonical_sha256"]


def test_unknown_signing_key_is_rejected(tool: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    install_success(monkeypatch, tool)
    paths = create_inputs(tmp_path, valid_policy(key_id="other-key"))
    code, payload, _ = invoke(tool, arguments(paths), capsys)
    assert code == 1
    assert payload["error_code"] == "SIGNING_KEY_NOT_TRUSTED"


def test_revoked_signing_key_is_rejected(tool: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    install_success(monkeypatch, tool)
    paths = create_inputs(tmp_path, valid_policy(status="REVOKED"))
    code, payload, _ = invoke(tool, arguments(paths), capsys)
    assert code == 1
    assert payload["error_code"] == "SIGNING_KEY_REVOKED"


def test_public_key_fingerprint_mismatch_is_rejected(tool: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    install_success(monkeypatch, tool)
    paths = create_inputs(tmp_path, valid_policy(fingerprint="0" * 64))
    code, payload, _ = invoke(tool, arguments(paths), capsys)
    assert code == 1
    assert payload["error_code"] == "PUBLIC_KEY_NOT_TRUSTED"


@pytest.mark.parametrize("case", ["wrong_public_key", "tampered_verdict", "tampered_signature"])
def test_delegated_signature_failures_are_rejected(tool: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], case: str) -> None:
    def reject(*values: Any) -> None:
        raise SignatureFailure(case)

    monkeypatch.setattr(tool, "verify_signature_envelope", reject)
    paths = create_inputs(tmp_path)
    code, payload, _ = invoke(tool, arguments(paths), capsys)
    assert code == 1
    assert payload["error_code"] == "SIGNATURE_INVALID"


def test_verdict_contract_failure_is_rejected(tool: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(tool, "verify_signature_envelope", lambda *args: (_ for _ in ()).throw(ContractFailure()))
    paths = create_inputs(tmp_path)
    code, payload, _ = invoke(tool, arguments(paths), capsys)
    assert code == 1
    assert payload["error_code"] == "VERDICT_CONTRACT_INVALID"


def test_public_key_input_failure_is_rejected(tool: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(tool, "verify_signature_envelope", lambda *args: (_ for _ in ()).throw(InputFailure()))
    paths = create_inputs(tmp_path)
    code, payload, _ = invoke(tool, arguments(paths), capsys)
    assert code == 2
    assert payload["error_code"] == "READ_ERROR"


@pytest.mark.parametrize("document", ["verdict", "signature", "policy"])
def test_malformed_json_is_rejected(tool: Any, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], document: str) -> None:
    paths = create_inputs(tmp_path)
    paths[document].write_text("{not-json", encoding="utf-8")
    code, payload, _ = invoke(tool, arguments(paths), capsys)
    assert code == 2
    assert payload["error_code"] == "INVALID_JSON"


@pytest.mark.parametrize("document", ["verdict", "signature", "policy"])
def test_json_array_is_rejected(tool: Any, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], document: str) -> None:
    paths = create_inputs(tmp_path)
    paths[document].write_text("[]", encoding="utf-8")
    code, payload, _ = invoke(tool, arguments(paths), capsys)
    assert code == 2
    assert payload["error_code"] == "INVALID_JSON"


def test_duplicate_json_key_is_rejected(tool: Any, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    paths = create_inputs(tmp_path)
    paths["policy"].write_text('{"policy_version":"A","policy_version":"B","trusted_signing_keys":[]}', encoding="utf-8")
    code, payload, _ = invoke(tool, arguments(paths), capsys)
    assert code == 2
    assert payload["error_code"] == "INVALID_JSON"


def test_non_finite_json_is_rejected(tool: Any, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    paths = create_inputs(tmp_path)
    paths["policy"].write_text('{"policy_version":NaN,"trusted_signing_keys":[]}', encoding="utf-8")
    code, payload, _ = invoke(tool, arguments(paths), capsys)
    assert code == 2
    assert payload["error_code"] == "INVALID_JSON"


@pytest.mark.parametrize("change", ["extra", "missing"])
def test_extra_or_missing_policy_field_is_rejected(tool: Any, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], change: str) -> None:
    policy = valid_policy()
    if change == "extra":
        policy["extra"] = True
    else:
        policy.pop("policy_version")
    paths = create_inputs(tmp_path, policy)
    code, payload, _ = invoke(tool, arguments(paths), capsys)
    assert code == 2
    assert payload["error_code"] == "POLICY_CONTRACT_INVALID"


@pytest.mark.parametrize("change", ["extra", "missing"])
def test_extra_or_missing_policy_entry_field_is_rejected(tool: Any, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], change: str) -> None:
    policy = valid_policy()
    entry = policy["trusted_signing_keys"][0]
    if change == "extra":
        entry["extra"] = True
    else:
        entry.pop("status")
    paths = create_inputs(tmp_path, policy)
    code, payload, _ = invoke(tool, arguments(paths), capsys)
    assert code == 2
    assert payload["error_code"] == "POLICY_CONTRACT_INVALID"


def test_invalid_policy_version_is_rejected(tool: Any, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    policy = valid_policy()
    policy["policy_version"] = "TAMPERED"
    paths = create_inputs(tmp_path, policy)
    code, payload, _ = invoke(tool, arguments(paths), capsys)
    assert code == 2
    assert payload["error_code"] == "POLICY_CONTRACT_INVALID"


@pytest.mark.parametrize("status", ["active", "DISABLED", "", None])
def test_invalid_policy_status_is_rejected(tool: Any, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], status: Any) -> None:
    policy = valid_policy()
    policy["trusted_signing_keys"][0]["status"] = status
    paths = create_inputs(tmp_path, policy)
    code, payload, _ = invoke(tool, arguments(paths), capsys)
    assert code == 2
    assert payload["error_code"] == "POLICY_CONTRACT_INVALID"


@pytest.mark.parametrize("fingerprint", ["A" * 64, "0" * 63, "g" * 64, 7])
def test_invalid_public_key_hash_is_rejected(tool: Any, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], fingerprint: Any) -> None:
    policy = valid_policy()
    policy["trusted_signing_keys"][0]["public_key_sha256"] = fingerprint
    paths = create_inputs(tmp_path, policy)
    code, payload, _ = invoke(tool, arguments(paths), capsys)
    assert code == 2
    assert payload["error_code"] == "POLICY_CONTRACT_INVALID"


@pytest.mark.parametrize("key_id", ["", "-leading", "space key", "x" * 65, 7])
def test_invalid_policy_signing_key_id_is_rejected(tool: Any, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], key_id: Any) -> None:
    policy = valid_policy()
    policy["trusted_signing_keys"][0]["signing_key_id"] = key_id
    paths = create_inputs(tmp_path, policy)
    code, payload, _ = invoke(tool, arguments(paths), capsys)
    assert code == 2
    assert payload["error_code"] == "POLICY_CONTRACT_INVALID"


def test_duplicate_signing_key_id_is_rejected(tool: Any, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    policy = valid_policy()
    policy["trusted_signing_keys"].append(copy.deepcopy(policy["trusted_signing_keys"][0]))
    paths = create_inputs(tmp_path, policy)
    code, payload, _ = invoke(tool, arguments(paths), capsys)
    assert code == 2
    assert payload["error_code"] == "POLICY_CONTRACT_INVALID"


def test_empty_policy_authorizes_nothing(tool: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    install_success(monkeypatch, tool)
    policy = {"policy_version": tool.POLICY_VERSION, "trusted_signing_keys": []}
    paths = create_inputs(tmp_path, policy)
    code, payload, _ = invoke(tool, arguments(paths), capsys)
    assert code == 1
    assert payload["error_code"] == "SIGNING_KEY_NOT_TRUSTED"


def test_policy_entry_limit_is_enforced(tool: Any, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    entries = []
    for index in range(tool.POLICY_MAX_ENTRIES + 1):
        entries.append({"public_key_sha256": f"{index:064x}", "signing_key_id": f"key-{index}", "status": "ACTIVE"})
    policy = {"policy_version": tool.POLICY_VERSION, "trusted_signing_keys": entries}
    paths = create_inputs(tmp_path, policy)
    code, payload, _ = invoke(tool, arguments(paths), capsys)
    assert code == 2
    assert payload["error_code"] == "POLICY_CONTRACT_INVALID"


@pytest.mark.parametrize("document", ["verdict", "signature", "policy", "public"])
def test_oversized_input_is_rejected(tool: Any, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], document: str) -> None:
    paths = create_inputs(tmp_path)
    limits = {"verdict": tool.VERDICT_MAX_BYTES, "signature": tool.SIGNATURE_MAX_BYTES, "policy": tool.POLICY_MAX_BYTES, "public": tool.PUBLIC_KEY_MAX_BYTES}
    paths[document].write_bytes(b"x" * (limits[document] + 1))
    code, payload, _ = invoke(tool, arguments(paths), capsys)
    assert code == 2
    assert payload["error_code"] == "INPUT_TOO_LARGE"


@pytest.mark.parametrize("document", ["verdict", "signature", "policy", "public"])
def test_missing_file_is_rejected(tool: Any, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], document: str) -> None:
    paths = create_inputs(tmp_path)
    paths[document].unlink()
    code, payload, _ = invoke(tool, arguments(paths), capsys)
    assert code == 2
    assert payload["error_code"] == "READ_ERROR"


def test_utf8_bom_policy_is_supported(tool: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    install_success(monkeypatch, tool)
    paths = create_inputs(tmp_path)
    raw = json.dumps(valid_policy(), sort_keys=True).encode("utf-8")
    paths["policy"].write_bytes(b"\xef\xbb\xbf" + raw)
    code, payload, _ = invoke(tool, arguments(paths), capsys)
    assert code == 0
    assert payload["authorized"] is True


def test_policy_stdin_is_supported(tool: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    install_success(monkeypatch, tool)
    paths = create_inputs(tmp_path)
    monkeypatch.setattr(sys, "stdin", BinaryStdin(json.dumps(valid_policy()).encode("utf-8")))
    args = arguments(paths)
    args[args.index(str(paths["policy"]))] = "-"
    code, payload, _ = invoke(tool, args, capsys)
    assert code == 0
    assert payload["authorized"] is True


def test_multiple_stdin_inputs_are_rejected(tool: Any, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    paths = create_inputs(tmp_path)
    args = arguments(paths)
    args[args.index(str(paths["verdict"]))] = "-"
    args[args.index(str(paths["signature"]))] = "-"
    code, payload, _ = invoke(tool, args, capsys)
    assert code == 2
    assert payload["error_code"] == "ARGUMENT_ERROR"


def test_unknown_argument_has_no_traceback(tool: Any, capsys: pytest.CaptureFixture[str]) -> None:
    code, payload, output = invoke(tool, ["--unknown"], capsys)
    assert code == 2
    assert payload["error_code"] == "ARGUMENT_ERROR"
    assert "Traceback" not in output


def test_output_excludes_raw_policy_and_keys(tool: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    install_success(monkeypatch, tool)
    policy = valid_policy()
    policy["trusted_signing_keys"][0]["marker"] = "secret-policy-marker"
    policy["trusted_signing_keys"][0].pop("marker")
    paths = create_inputs(tmp_path, policy)
    code, _, output = invoke(tool, arguments(paths), capsys)
    assert code == 0
    assert PUBLIC_KEY.decode("ascii") not in output
    assert "trusted_signing_keys" not in output


def test_b217_verifier_is_called_once() -> None:
    tree = ast.parse(TOOL_PATH.read_text(encoding="utf-8"))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "verify_signature_envelope"]
    assert len(calls) == 1


def test_static_contract_has_no_network_or_subprocess() -> None:
    tree = ast.parse(TOOL_PATH.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported.intersection({"http", "requests", "socket", "subprocess", "urllib"})


def test_static_contract_has_no_direct_file_writes() -> None:
    tree = ast.parse(TOOL_PATH.read_text(encoding="utf-8"))
    forbidden = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr in {"write_bytes", "write_text", "unlink", "rename", "replace"}:
            forbidden.append(node.func.attr)
        if isinstance(node.func, ast.Name) and node.func.id == "open" and len(node.args) >= 2:
            mode = node.args[1]
            if isinstance(mode, ast.Constant) and any(marker in str(mode.value) for marker in "wax+"):
                forbidden.append("open")
    assert forbidden == []


def test_previous_components_are_not_referenced_for_writes() -> None:
    source = TOOL_PATH.read_text(encoding="utf-8")
    assert "api/main.py" not in source
    for version in ("b212", "b213", "b214", "b215", "b216"):
        assert version not in source.lower()
    tree = ast.parse(source)
    imported = [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module == "sign_scheduler_b217_cloud_audit_verdict"]
    assert len(imported) == 1
    assert [alias.name for alias in imported[0].names] == ["verify_signature_envelope"]
