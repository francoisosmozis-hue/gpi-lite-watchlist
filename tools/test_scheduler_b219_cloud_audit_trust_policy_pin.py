from __future__ import annotations

import ast
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
from typing import Any

import pytest


TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import verify_scheduler_b219_cloud_audit_trust_policy_pin as tool


POLICY_HASH = "a" * 64


def source_result() -> dict[str, Any]:
    return {
        "attestation_sha256": "1" * 64,
        "authorized": True,
        "gate": "GO",
        "ok": True,
        "policy_canonical_sha256": POLICY_HASH,
        "policy_version": "B2_18_CLOUD_AUDIT_SIGNING_KEY_TRUST_POLICY_V1",
        "public_key_sha256": "2" * 64,
        "signature_valid": True,
        "signing_key_id": "b219-test-key",
        "source_canonical_sha256": "3" * 64,
        "source_verification_version": (
            "B2_17_CLOUD_AUDIT_VERDICT_ED25519_VERIFICATION_V1"
        ),
        "verdict_canonical_sha256": "4" * 64,
        "verification_version": (
            "B2_18_CLOUD_AUDIT_VERDICT_TRUST_VERIFICATION_V1"
        ),
    }


def valid_pin() -> dict[str, Any]:
    return {
        "pin_version": "B2_19_CLOUD_AUDIT_TRUST_POLICY_PIN_V1",
        "policy_canonical_sha256": POLICY_HASH,
    }


def build(monkeypatch: pytest.MonkeyPatch, result: Any = None) -> dict[str, Any]:
    if result is None:
        result = source_result()
    monkeypatch.setattr(tool, "build_trust_verdict", lambda *args: result)
    return tool.build_policy_pin_verdict(
        {"verdict": True},
        {"signature": True},
        b"p" * 32,
        {"policy": True},
        valid_pin(),
        "b219-test-key",
    )


def capture_run(
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
    stdin: bytes = b"",
) -> tuple[int, dict[str, Any]]:
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    try:
        code = tool.run_cli(arguments, io.BytesIO(stdin))
    except tool.PolicyPinFailure as failure:
        tool.emit_failure(failure)
        code = failure.exit_code
    payload = json.loads(output.getvalue())
    return code, payload


def write_inputs(tmp_path: Path) -> dict[str, str]:
    files = {
        "verdict": tmp_path / "verdict.json",
        "signature": tmp_path / "signature.json",
        "public": tmp_path / "public.key",
        "policy": tmp_path / "policy.json",
        "pin": tmp_path / "pin.json",
    }
    files["verdict"].write_text('{"verdict":true}', encoding="utf-8")
    files["signature"].write_text('{"signature":true}', encoding="utf-8")
    files["public"].write_bytes(b"p" * 32)
    files["policy"].write_text('{"policy":true}', encoding="utf-8")
    files["pin"].write_text(json.dumps(valid_pin()), encoding="utf-8")
    return {name: str(path) for name, path in files.items()}


def cli_arguments(files: dict[str, str]) -> list[str]:
    return [
        "--verdict",
        files["verdict"],
        "--signature",
        files["signature"],
        "--public-key-file",
        files["public"],
        "--trust-policy",
        files["policy"],
        "--policy-pin",
        files["pin"],
        "--signing-key-id",
        "b219-test-key",
    ]


def test_help_is_one_json_line(monkeypatch: pytest.MonkeyPatch) -> None:
    code, payload = capture_run(monkeypatch, ["--help"])
    assert code == 0
    assert payload["ok"] is True
    assert payload["policy_pin_tool_version"] == tool.TOOL_VERSION


def test_valid_policy_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    result = build(monkeypatch)
    assert result["ok"] is True
    assert result["gate"] == "GO"
    assert result["policy_pin_verified"] is True


def test_output_is_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    first = build(monkeypatch)
    second = build(monkeypatch)
    assert tool.canonical_json_bytes(first) == tool.canonical_json_bytes(second)


def test_semantically_equivalent_pin_is_stable(monkeypatch: pytest.MonkeyPatch) -> None:
    first = valid_pin()
    second = {
        "policy_canonical_sha256": POLICY_HASH,
        "pin_version": tool.PIN_VERSION,
    }
    assert tool.canonical_sha256(first) == tool.canonical_sha256(second)
    monkeypatch.setattr(tool, "build_trust_verdict", lambda *args: source_result())
    a = tool.build_policy_pin_verdict({}, {}, b"p" * 32, {}, first, "b219-test-key")
    b = tool.build_policy_pin_verdict({}, {}, b"p" * 32, {}, second, "b219-test-key")
    assert a == b


def test_policy_pin_mismatch_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool, "build_trust_verdict", lambda *args: source_result())
    pin = valid_pin()
    pin["policy_canonical_sha256"] = "b" * 64
    with pytest.raises(tool.PolicyPinFailure) as captured:
        tool.build_policy_pin_verdict({}, {}, b"p" * 32, {}, pin, "b219-test-key")
    assert captured.value.error_code == "POLICY_PIN_MISMATCH"
    assert captured.value.exit_code == 1


@pytest.mark.parametrize(
    "condition",
    ["tampered policy", "tampered verdict", "tampered signature", "wrong key", "revoked key"],
)
def test_b218_rejections_are_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    condition: str,
) -> None:
    def reject(*args: Any) -> Any:
        raise RuntimeError(condition)

    monkeypatch.setattr(tool, "build_trust_verdict", reject)
    with pytest.raises(tool.PolicyPinFailure) as captured:
        tool.build_policy_pin_verdict({}, {}, b"p" * 32, {}, valid_pin(), "key")
    assert captured.value.error_code == "TRUST_VERIFICATION_FAILED"
    assert condition not in captured.value.message


@pytest.mark.parametrize("change", ["extra", "missing"])
def test_invalid_b218_fields_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    result = source_result()
    if change == "extra":
        result["extra"] = True
    else:
        result.pop("policy_version")
    with pytest.raises(tool.PolicyPinFailure) as captured:
        build(monkeypatch, result)
    assert captured.value.error_code == "TRUST_VERIFICATION_FAILED"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ok", False),
        ("authorized", False),
        ("signature_valid", False),
        ("gate", "NO_GO"),
        ("verification_version", "WRONG"),
    ],
)
def test_invalid_b218_success_values_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: Any,
) -> None:
    result = source_result()
    result[field] = value
    with pytest.raises(tool.PolicyPinFailure):
        build(monkeypatch, result)


@pytest.mark.parametrize("value", ["A" * 64, "a" * 63, "g" * 64, None])
def test_invalid_b218_policy_hash_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    value: Any,
) -> None:
    result = source_result()
    result["policy_canonical_sha256"] = value
    with pytest.raises(tool.PolicyPinFailure) as captured:
        build(monkeypatch, result)
    assert captured.value.error_code == "TRUST_VERIFICATION_FAILED"


@pytest.mark.parametrize("change", ["extra", "missing"])
def test_extra_or_missing_pin_field_is_rejected(change: str) -> None:
    pin = valid_pin()
    if change == "extra":
        pin["extra"] = True
    else:
        pin.pop("pin_version")
    with pytest.raises(tool.PolicyPinFailure) as captured:
        tool.validate_policy_pin(pin)
    assert captured.value.error_code == "PIN_CONTRACT_INVALID"


def test_invalid_pin_version_is_rejected() -> None:
    pin = valid_pin()
    pin["pin_version"] = "WRONG"
    with pytest.raises(tool.PolicyPinFailure):
        tool.validate_policy_pin(pin)


@pytest.mark.parametrize("value", ["A" * 64, "a" * 63, "g" * 64, 123, None])
def test_invalid_pin_hash_is_rejected(value: Any) -> None:
    pin = valid_pin()
    pin["policy_canonical_sha256"] = value
    with pytest.raises(tool.PolicyPinFailure):
        tool.validate_policy_pin(pin)


def test_malformed_pin_json_is_rejected() -> None:
    with pytest.raises(tool.PolicyPinFailure) as captured:
        tool.parse_json_document(b"{", "policy pin")
    assert captured.value.error_code == "INVALID_JSON"


def test_duplicate_pin_key_is_rejected() -> None:
    raw = (
        '{"pin_version":"%s","pin_version":"%s",'
        '"policy_canonical_sha256":"%s"}'
    ) % (tool.PIN_VERSION, tool.PIN_VERSION, POLICY_HASH)
    with pytest.raises(tool.PolicyPinFailure):
        tool.parse_json_document(raw.encode(), "policy pin")


def test_non_finite_pin_json_is_rejected() -> None:
    with pytest.raises(tool.PolicyPinFailure):
        tool.parse_json_document(b'{"value":NaN}', "policy pin")


def test_pin_json_array_is_rejected() -> None:
    with pytest.raises(tool.PolicyPinFailure):
        tool.parse_json_document(b"[]", "policy pin")


def test_utf8_bom_pin_is_supported() -> None:
    raw = b"\xef\xbb\xbf" + json.dumps(valid_pin()).encode("utf-8")
    assert tool.parse_json_document(raw, "policy pin") == valid_pin()


@pytest.mark.parametrize("label", ["verdict", "signature", "public", "policy", "pin"])
def test_missing_input_file_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    label: str,
) -> None:
    files = write_inputs(tmp_path)
    files[label] = str(tmp_path / "missing")
    code, payload = capture_run(monkeypatch, cli_arguments(files))
    assert code == 2
    assert payload["error_code"] == "INPUT_ERROR"


def test_oversized_pin_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "pin.json"
    path.write_bytes(b"x" * (tool.PIN_MAX_BYTES + 1))
    with pytest.raises(tool.PolicyPinFailure) as captured:
        tool.read_bytes(str(path), "policy pin", tool.PIN_MAX_BYTES, io.BytesIO())
    assert captured.value.exit_code == 2


@pytest.mark.parametrize(
    "fields",
    [
        ("verdict", "signature"),
        ("verdict", "public"),
        ("verdict", "policy"),
        ("verdict", "pin"),
    ],
)
def test_multiple_stdin_inputs_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fields: tuple[str, str],
) -> None:
    files = write_inputs(tmp_path)
    files[fields[0]] = "-"
    files[fields[1]] = "-"
    code, payload = capture_run(monkeypatch, cli_arguments(files))
    assert code == 2
    assert payload["error_code"] == "INPUT_ERROR"


def test_policy_pin_stdin_is_supported(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    files = write_inputs(tmp_path)
    files["pin"] = "-"
    monkeypatch.setattr(tool, "build_trust_verdict", lambda *args: source_result())
    code, payload = capture_run(
        monkeypatch,
        cli_arguments(files),
        json.dumps(valid_pin()).encode(),
    )
    assert code == 0
    assert payload["policy_pin_verified"] is True


def test_valid_cli_file_inputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    files = write_inputs(tmp_path)
    monkeypatch.setattr(tool, "build_trust_verdict", lambda *args: source_result())
    code, payload = capture_run(monkeypatch, cli_arguments(files))
    assert code == 0
    assert payload["ok"] is True


def test_unknown_argument_has_no_traceback(monkeypatch: pytest.MonkeyPatch) -> None:
    code, payload = capture_run(monkeypatch, ["--unknown"])
    assert code == 2
    assert payload["error_code"] == "INPUT_ERROR"
    assert "traceback" not in json.dumps(payload).lower()


def test_help_with_inputs_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    code, payload = capture_run(monkeypatch, ["--help", "--verdict", "x"])
    assert code == 2
    assert payload["gate"] == "INPUT_ERROR"


def test_missing_required_argument_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    code, payload = capture_run(monkeypatch, [])
    assert code == 2
    assert payload["error_code"] == "INPUT_ERROR"


def test_output_excludes_raw_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    result = build(monkeypatch)
    rendered = json.dumps(result)
    assert "verdict" not in result
    assert "signature" not in result
    assert "raw_public" not in rendered
    assert '"policy": true' not in rendered.lower()


def test_source_verification_version_is_b218(monkeypatch: pytest.MonkeyPatch) -> None:
    result = build(monkeypatch)
    assert result["source_verification_version"] == tool.SOURCE_VERIFICATION_VERSION


def test_b218_delegation_is_called_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def delegate(*args: Any) -> dict[str, Any]:
        calls.append(args)
        result = source_result()
        result["signing_key_id"] = args[-1]
        return result

    monkeypatch.setattr(tool, "build_trust_verdict", delegate)
    tool.build_policy_pin_verdict({}, {}, b"p" * 32, {}, valid_pin(), "key")
    assert len(calls) == 1


def test_static_contract_uses_compare_digest_once() -> None:
    source = Path(tool.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "compare_digest"
    ]
    assert len(calls) == 1


def test_static_contract_has_no_network_or_subprocess() -> None:
    source = Path(tool.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {"requests", "urllib", "socket", "subprocess", "http", "google.cloud"}
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add(node.module or "")
    assert not any(any(name == root or name.startswith(root + ".") for root in forbidden) for name in imports)


def test_static_contract_has_no_file_writes() -> None:
    source = Path(tool.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr in {"write_text", "write_bytes"}:
            forbidden.append(node.lineno)
    assert forbidden == []


def test_previous_components_are_not_referenced() -> None:
    source = Path(tool.__file__).read_text(encoding="utf-8")
    assert "sign_scheduler_b217" not in source
    assert "authenticate_scheduler_b216" not in source
    assert "verify_scheduler_b215" not in source
    assert source.count("from verify_scheduler_b218_cloud_audit_verdict_trust import build_trust_verdict") == 1


def test_argument_options_are_exact() -> None:
    source = Path(tool.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    options = sorted(
        {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.startswith("--")
        }
    )
    assert options == [
        "--help",
        "--policy-pin",
        "--public-key-file",
        "--signature",
        "--signing-key-id",
        "--trust-policy",
        "--verdict",
    ]


def test_success_output_fields_are_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    result = build(monkeypatch)
    assert set(result) == {
        "attestation_sha256",
        "authorized",
        "gate",
        "ok",
        "policy_canonical_sha256",
        "policy_pin_canonical_sha256",
        "policy_pin_verified",
        "public_key_sha256",
        "signature_valid",
        "signing_key_id",
        "source_canonical_sha256",
        "source_verification_version",
        "verdict_canonical_sha256",
        "verification_version",
    }


def test_test_suite_has_exactly_35_functions() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    ]
    assert len(functions) == 35
