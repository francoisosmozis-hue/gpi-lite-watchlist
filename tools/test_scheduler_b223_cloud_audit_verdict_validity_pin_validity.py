from __future__ import annotations

import ast
import copy
import io
import json
import pathlib
from typing import Any

import pytest

import verify_scheduler_b223_cloud_audit_verdict_validity_pin_validity as tool


SUCCESS_FIELDS = {
    "attestation_sha256",
    "authorized",
    "evaluation_time_utc",
    "gate",
    "not_after_utc",
    "not_before_utc",
    "ok",
    "policy_canonical_sha256",
    "policy_pin_canonical_sha256",
    "policy_pin_validity_canonical_sha256",
    "policy_pin_validity_verified",
    "policy_pin_verified",
    "public_key_sha256",
    "signature_valid",
    "signing_key_id",
    "source_canonical_sha256",
    "source_verification_version",
    "verdict_canonical_sha256",
    "verdict_not_after_utc",
    "verdict_not_before_utc",
    "verdict_validity_canonical_sha256",
    "verdict_validity_pin_canonical_sha256",
    "verdict_validity_pin_not_after_utc",
    "verdict_validity_pin_not_before_utc",
    "verdict_validity_pin_validity_canonical_sha256",
    "verdict_validity_pin_validity_verified",
    "verdict_validity_pin_verified",
    "verdict_validity_verified",
    "verification_version",
}


def valid_source_result() -> dict[str, Any]:
    return {
        "attestation_sha256": "1" * 64,
        "authorized": True,
        "evaluation_time_utc": "2026-07-16T12:00:00Z",
        "gate": "GO",
        "not_after_utc": "2026-07-16T23:59:59Z",
        "not_before_utc": "2026-07-16T00:00:00Z",
        "ok": True,
        "policy_canonical_sha256": "2" * 64,
        "policy_pin_canonical_sha256": "3" * 64,
        "policy_pin_validity_canonical_sha256": "4" * 64,
        "policy_pin_validity_verified": True,
        "policy_pin_verified": True,
        "public_key_sha256": "5" * 64,
        "signature_valid": True,
        "signing_key_id": "b223-test-key",
        "source_canonical_sha256": "6" * 64,
        "source_verification_version": (
            "B2_21_CLOUD_AUDIT_VERDICT_VALIDITY_VERIFICATION_V1"
        ),
        "verdict_canonical_sha256": "7" * 64,
        "verdict_not_after_utc": "2026-07-16T18:00:00Z",
        "verdict_not_before_utc": "2026-07-16T06:00:00Z",
        "verdict_validity_canonical_sha256": "8" * 64,
        "verdict_validity_pin_canonical_sha256": "9" * 64,
        "verdict_validity_pin_verified": True,
        "verdict_validity_verified": True,
        "verification_version": (
            "B2_22_CLOUD_AUDIT_VERDICT_VALIDITY_PIN_VERIFICATION_V1"
        ),
    }


def valid_validity() -> dict[str, Any]:
    return {
        "not_after_utc": "2026-07-16T23:59:59Z",
        "not_before_utc": "2026-07-16T00:00:00Z",
        "validity_version": (
            "B2_23_CLOUD_AUDIT_VERDICT_VALIDITY_PIN_VALIDITY_V1"
        ),
        "verdict_validity_pin_canonical_sha256": "9" * 64,
    }


def build_call(
    validity: dict[str, Any] | None = None,
    evaluation: Any = "2026-07-16T12:00:00Z",
) -> tuple[Any, ...]:
    return (
        {},
        {},
        b"p" * 32,
        {},
        {},
        {},
        {},
        {},
        valid_validity() if validity is None else validity,
        evaluation,
        "b223-test-key",
    )


def install_delegate(
    monkeypatch: pytest.MonkeyPatch,
    *,
    result: dict[str, Any] | None = None,
    error: Exception | None = None,
) -> list[tuple[Any, ...]]:
    calls: list[tuple[Any, ...]] = []
    selected_result = valid_source_result() if result is None else result

    def delegate(*arguments: Any) -> dict[str, Any]:
        calls.append(arguments)
        if error is not None:
            raise error
        return copy.deepcopy(selected_result)

    monkeypatch.setattr(tool, "build_verdict_validity_pin_verdict", delegate)
    return calls


def build(
    monkeypatch: pytest.MonkeyPatch,
    validity: dict[str, Any] | None = None,
    evaluation: Any = "2026-07-16T12:00:00Z",
) -> dict[str, Any]:
    source = valid_source_result()
    source["evaluation_time_utc"] = evaluation
    install_delegate(monkeypatch, result=source)
    return tool.build_verdict_validity_pin_validity_verdict(
        *build_call(validity, evaluation)
    )


def parsed_output(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    captured = capsys.readouterr()
    assert captured.err == ""
    lines = captured.out.splitlines()
    assert len(lines) == 1
    value = json.loads(lines[0])
    assert isinstance(value, dict)
    return value


def write_json(path: pathlib.Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def cli_arguments(tmp_path: pathlib.Path) -> list[str]:
    paths = {
        "verdict": tmp_path / "verdict.json",
        "signature": tmp_path / "signature.json",
        "public": tmp_path / "public.key",
        "policy": tmp_path / "policy.json",
        "policy_pin": tmp_path / "policy-pin.json",
        "policy_pin_validity": tmp_path / "policy-pin-validity.json",
        "verdict_validity": tmp_path / "verdict-validity.json",
        "verdict_validity_pin": tmp_path / "verdict-validity-pin.json",
        "verdict_validity_pin_validity": (
            tmp_path / "verdict-validity-pin-validity.json"
        ),
    }
    for name, path in paths.items():
        if name == "public":
            path.write_bytes(b"p" * 32)
        elif name == "verdict_validity_pin_validity":
            write_json(path, valid_validity())
        else:
            write_json(path, {})
    return [
        "--verdict",
        str(paths["verdict"]),
        "--signature",
        str(paths["signature"]),
        "--public-key-file",
        str(paths["public"]),
        "--trust-policy",
        str(paths["policy"]),
        "--policy-pin",
        str(paths["policy_pin"]),
        "--policy-pin-validity",
        str(paths["policy_pin_validity"]),
        "--verdict-validity",
        str(paths["verdict_validity"]),
        "--verdict-validity-pin",
        str(paths["verdict_validity_pin"]),
        "--verdict-validity-pin-validity",
        str(paths["verdict_validity_pin_validity"]),
        "--evaluation-time-utc",
        "2026-07-16T12:00:00Z",
        "--signing-key-id",
        "b223-test-key",
    ]


def replace_option(arguments: list[str], option: str, value: str) -> list[str]:
    result = list(arguments)
    result[result.index(option) + 1] = value
    return result


def assert_failure(
    failure: pytest.ExceptionInfo[tool.VerdictValidityPinValidityFailure],
    code: str,
    exit_code: int,
) -> None:
    assert failure.value.code == code
    assert failure.value.exit_code == exit_code
    assert failure.value.gate == ("INPUT_ERROR" if exit_code == 2 else "NO_GO")


def tool_source() -> str:
    return pathlib.Path(tool.__file__).read_text(encoding="utf-8")


def tool_tree() -> ast.Module:
    return ast.parse(tool_source())


def call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def test_help_is_one_json_line(capsys: pytest.CaptureFixture[str]) -> None:
    assert tool.run_cli(["--help"], io.BytesIO()) == 0
    assert parsed_output(capsys) == {
        "ok": True,
        "usage": tool.USAGE,
        "verdict_validity_pin_validity_tool_version": tool.TOOL_VERSION,
    }


def test_valid_verdict_validity_pin_validity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = build(monkeypatch)
    assert set(result) == SUCCESS_FIELDS
    assert result["ok"] is True
    assert result["gate"] == "GO"
    assert result["verdict_validity_pin_validity_verified"] is True


def test_output_is_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    first = build(monkeypatch)
    second = build(monkeypatch)
    assert tool.canonical_json_bytes(first) == tool.canonical_json_bytes(second)


def test_semantically_equivalent_validity_is_stable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_document = valid_validity()
    second_document = dict(reversed(list(first_document.items())))
    first = build(monkeypatch, first_document)
    second = build(monkeypatch, second_document)
    assert tool.canonical_sha256(first_document) == tool.canonical_sha256(
        second_document
    )
    assert first == second


def test_lower_boundary_is_inclusive(monkeypatch: pytest.MonkeyPatch) -> None:
    assert build(monkeypatch, evaluation="2026-07-16T00:00:00Z")["gate"] == "GO"


def test_upper_boundary_is_inclusive(monkeypatch: pytest.MonkeyPatch) -> None:
    assert build(monkeypatch, evaluation="2026-07-16T23:59:59Z")["gate"] == "GO"


def test_evaluation_before_window_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(tool.VerdictValidityPinValidityFailure) as failure:
        build(monkeypatch, evaluation="2026-07-15T23:59:59Z")
    assert_failure(failure, "EVALUATION_BEFORE_VALIDITY_WINDOW", 1)


def test_evaluation_after_window_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(tool.VerdictValidityPinValidityFailure) as failure:
        build(monkeypatch, evaluation="2026-07-17T00:00:00Z")
    assert_failure(failure, "EVALUATION_AFTER_VALIDITY_WINDOW", 1)


def test_verdict_validity_pin_hash_mismatch_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = valid_validity()
    document["verdict_validity_pin_canonical_sha256"] = "a" * 64
    with pytest.raises(tool.VerdictValidityPinValidityFailure) as failure:
        build(monkeypatch, document)
    assert_failure(failure, "VERDICT_VALIDITY_PIN_VALIDITY_MISMATCH", 1)


@pytest.mark.parametrize(
    "case",
    [
        "tampered verdict validity pin",
        "tampered verdict validity",
        "tampered verdict",
        "wrong public key",
        "revoked signing key",
    ],
)
def test_b222_failures_are_sanitized(
    case: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_delegate(monkeypatch, error=ValueError(case))
    with pytest.raises(tool.VerdictValidityPinValidityFailure) as failure:
        tool.build_verdict_validity_pin_validity_verdict(*build_call())
    assert_failure(failure, "VERDICT_VALIDITY_PIN_VERIFICATION_FAILED", 1)
    assert case not in str(failure.value)


@pytest.mark.parametrize("change", ["extra", "missing"])
def test_invalid_b222_fields_are_rejected(
    change: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = valid_source_result()
    if change == "extra":
        source["extra"] = True
    else:
        del source["attestation_sha256"]
    install_delegate(monkeypatch, result=source)
    with pytest.raises(tool.VerdictValidityPinValidityFailure) as failure:
        tool.build_verdict_validity_pin_validity_verdict(*build_call())
    assert_failure(failure, "VERDICT_VALIDITY_PIN_VERIFICATION_FAILED", 1)


@pytest.mark.parametrize(
    "field,value",
    [
        ("ok", False),
        ("authorized", False),
        ("signature_valid", False),
        ("policy_pin_verified", False),
        ("policy_pin_validity_verified", False),
        ("verdict_validity_verified", False),
        ("verdict_validity_pin_verified", False),
        ("gate", "NO_GO"),
        ("verification_version", "WRONG"),
        ("source_verification_version", "WRONG"),
        ("evaluation_time_utc", "2026-07-16T12:00:01Z"),
        ("signing_key_id", "other-key"),
    ],
)
def test_invalid_b222_values_are_rejected(
    field: str,
    value: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = valid_source_result()
    source[field] = value
    install_delegate(monkeypatch, result=source)
    with pytest.raises(tool.VerdictValidityPinValidityFailure) as failure:
        tool.build_verdict_validity_pin_validity_verdict(*build_call())
    assert_failure(failure, "VERDICT_VALIDITY_PIN_VERIFICATION_FAILED", 1)


@pytest.mark.parametrize("change", ["extra", "missing"])
def test_extra_or_missing_validity_field_is_rejected(change: str) -> None:
    document = valid_validity()
    if change == "extra":
        document["extra"] = True
    else:
        del document["not_after_utc"]
    with pytest.raises(tool.VerdictValidityPinValidityFailure) as failure:
        tool.validate_validity_document(document)
    assert_failure(failure, "VERDICT_VALIDITY_PIN_VALIDITY_CONTRACT_INVALID", 2)


def test_invalid_validity_version_is_rejected() -> None:
    document = valid_validity()
    document["validity_version"] = "WRONG"
    with pytest.raises(tool.VerdictValidityPinValidityFailure) as failure:
        tool.validate_validity_document(document)
    assert_failure(failure, "VERDICT_VALIDITY_PIN_VALIDITY_CONTRACT_INVALID", 2)


@pytest.mark.parametrize("value", ["A" * 64, "a" * 63, "g" * 64, 7, None])
def test_invalid_verdict_validity_pin_hash_is_rejected(value: Any) -> None:
    document = valid_validity()
    document["verdict_validity_pin_canonical_sha256"] = value
    with pytest.raises(tool.VerdictValidityPinValidityFailure) as failure:
        tool.validate_validity_document(document)
    assert_failure(failure, "VERDICT_VALIDITY_PIN_VALIDITY_CONTRACT_INVALID", 2)


@pytest.mark.parametrize(
    "value",
    [
        "2026-07-16",
        "2026-07-16T00:00:00+00:00",
        "2026-07-16T00:00:00.000Z",
        "2026-02-30T00:00:00Z",
        None,
    ],
)
def test_invalid_not_before_utc_is_rejected(value: Any) -> None:
    document = valid_validity()
    document["not_before_utc"] = value
    with pytest.raises(tool.VerdictValidityPinValidityFailure):
        tool.validate_validity_document(document)


@pytest.mark.parametrize(
    "value",
    [
        "2026-07-16",
        "2026-07-16T23:59:59+00:00",
        "2026-07-16T23:59:59.000Z",
        "2026-13-16T00:00:00Z",
        None,
    ],
)
def test_invalid_not_after_utc_is_rejected(value: Any) -> None:
    document = valid_validity()
    document["not_after_utc"] = value
    with pytest.raises(tool.VerdictValidityPinValidityFailure):
        tool.validate_validity_document(document)


@pytest.mark.parametrize(
    "value",
    [
        "2026-07-16",
        "2026-07-16T12:00:00+00:00",
        "2026-07-16T12:00:00.000Z",
        "invalid",
        None,
    ],
)
def test_invalid_evaluation_utc_is_rejected(
    value: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_delegate(monkeypatch)
    with pytest.raises(tool.VerdictValidityPinValidityFailure) as failure:
        tool.build_verdict_validity_pin_validity_verdict(*build_call(evaluation=value))
    assert_failure(failure, "INVALID_EVALUATION_TIME", 2)


def test_reversed_window_is_rejected() -> None:
    document = valid_validity()
    document["not_before_utc"], document["not_after_utc"] = (
        document["not_after_utc"],
        document["not_before_utc"],
    )
    with pytest.raises(tool.VerdictValidityPinValidityFailure):
        tool.validate_validity_document(document)


def test_zero_length_window_is_rejected() -> None:
    document = valid_validity()
    document["not_after_utc"] = document["not_before_utc"]
    with pytest.raises(tool.VerdictValidityPinValidityFailure):
        tool.validate_validity_document(document)


def test_malformed_validity_json_is_rejected() -> None:
    with pytest.raises(tool.VerdictValidityPinValidityFailure) as failure:
        tool.parse_json_document(b"{", "verdict validity pin validity document")
    assert_failure(failure, "INVALID_JSON", 2)


def test_duplicate_validity_key_is_rejected() -> None:
    raw = b'{"validity_version":"a","validity_version":"b"}'
    with pytest.raises(tool.VerdictValidityPinValidityFailure) as failure:
        tool.parse_json_document(raw, "verdict validity pin validity document")
    assert_failure(failure, "INVALID_JSON", 2)


def test_non_finite_validity_json_is_rejected() -> None:
    with pytest.raises(tool.VerdictValidityPinValidityFailure) as failure:
        tool.parse_json_document(b'{"value":NaN}', "validity document")
    assert_failure(failure, "INVALID_JSON", 2)


def test_validity_json_array_is_rejected() -> None:
    with pytest.raises(tool.VerdictValidityPinValidityFailure) as failure:
        tool.parse_json_document(b"[]", "validity document")
    assert_failure(failure, "VERDICT_VALIDITY_PIN_VALIDITY_CONTRACT_INVALID", 2)


def test_utf8_bom_validity_is_supported() -> None:
    raw = b"\xef\xbb\xbf" + json.dumps(valid_validity()).encode()
    assert tool.parse_json_document(raw, "validity document") == valid_validity()


def test_oversized_validity_is_rejected(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "large.json"
    path.write_bytes(b"x" * (tool.VERDICT_VALIDITY_PIN_VALIDITY_MAX_BYTES + 1))
    with pytest.raises(tool.VerdictValidityPinValidityFailure) as failure:
        tool.read_bytes(
            str(path),
            tool.VERDICT_VALIDITY_PIN_VALIDITY_MAX_BYTES,
            io.BytesIO(),
            "verdict validity pin validity document",
        )
    assert_failure(failure, "INPUT_TOO_LARGE", 2)


@pytest.mark.parametrize(
    "option",
    [
        "--verdict",
        "--signature",
        "--public-key-file",
        "--trust-policy",
        "--policy-pin",
        "--policy-pin-validity",
        "--verdict-validity",
        "--verdict-validity-pin",
        "--verdict-validity-pin-validity",
    ],
)
def test_missing_file_is_rejected(
    option: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_delegate(monkeypatch)
    arguments = replace_option(cli_arguments(tmp_path), option, str(tmp_path / "missing"))
    assert tool.run_cli(arguments, io.BytesIO()) == 2
    assert parsed_output(capsys)["error_code"] == "READ_ERROR"


@pytest.mark.parametrize(
    "first,second",
    [
        ("--verdict", "--signature"),
        ("--public-key-file", "--trust-policy"),
        ("--policy-pin", "--policy-pin-validity"),
        ("--verdict-validity", "--verdict-validity-pin"),
        ("--verdict-validity-pin", "--verdict-validity-pin-validity"),
    ],
)
def test_multiple_stdin_inputs_are_rejected(
    first: str,
    second: str,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    arguments = replace_option(cli_arguments(tmp_path), first, "-")
    arguments = replace_option(arguments, second, "-")
    assert tool.run_cli(arguments, io.BytesIO()) == 2
    assert parsed_output(capsys)["error_code"] == "ARGUMENT_ERROR"


def test_validity_document_stdin_is_supported(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_delegate(monkeypatch)
    arguments = replace_option(
        cli_arguments(tmp_path),
        "--verdict-validity-pin-validity",
        "-",
    )
    raw = json.dumps(valid_validity()).encode()
    assert tool.run_cli(arguments, io.BytesIO(raw)) == 0
    assert parsed_output(capsys)["verdict_validity_pin_validity_verified"] is True


def test_valid_cli_file_inputs(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = install_delegate(monkeypatch)
    assert tool.run_cli(cli_arguments(tmp_path), io.BytesIO()) == 0
    assert parsed_output(capsys)["ok"] is True
    assert len(calls) == 1


def test_unknown_argument_has_no_traceback(capsys: pytest.CaptureFixture[str]) -> None:
    assert tool.run_cli(["--unknown"], io.BytesIO()) == 2
    captured = capsys.readouterr()
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err
    assert json.loads(captured.out)["error_code"] == "ARGUMENT_ERROR"


@pytest.mark.parametrize("arguments", [[], ["--help", "--verdict", "value"]])
def test_help_with_inputs_or_missing_arguments_is_rejected(
    arguments: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert tool.run_cli(arguments, io.BytesIO()) == 2
    assert parsed_output(capsys)["error_code"] == "ARGUMENT_ERROR"


def test_output_excludes_raw_inputs_and_sets_source_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = build(monkeypatch)
    forbidden = {
        "envelope",
        "pin",
        "policy",
        "policy_pin_validity",
        "public_key",
        "verdict",
        "verdict_validity",
        "verdict_validity_pin",
        "verdict_validity_pin_validity",
    }
    assert forbidden.isdisjoint(result)
    assert result["source_verification_version"] == (
        "B2_22_CLOUD_AUDIT_VERDICT_VALIDITY_PIN_VERIFICATION_V1"
    )


def test_b222_delegation_is_called_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = install_delegate(monkeypatch)
    tool.build_verdict_validity_pin_validity_verdict(*build_call())
    assert len(calls) == 1
    assert len(calls[0]) == 10


def test_static_contract_and_previous_components(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = tool_tree()
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    delegation_count = sum(
        isinstance(node.func, ast.Name)
        and node.func.id == "build_verdict_validity_pin_verdict"
        for node in calls
    )
    assert delegation_count == 1
    compare_calls = [
        node
        for node in calls
        if isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "hmac"
        and node.func.attr == "compare_digest"
    ]
    assert len(compare_calls) == 1
    options = sorted(
        {
            node.args[0].value
            for node in calls
            if isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        }
    )
    assert options == sorted(
        [
            "--evaluation-time-utc",
            "--help",
            "--policy-pin",
            "--policy-pin-validity",
            "--public-key-file",
            "--signature",
            "--signing-key-id",
            "--trust-policy",
            "--verdict",
            "--verdict-validity",
            "--verdict-validity-pin",
            "--verdict-validity-pin-validity",
        ]
    )
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not (
        {"aiohttp", "boto3", "google", "http", "requests", "socket", "subprocess", "urllib"}
        & imported
    )
    write_calls: list[ast.Call] = []
    for node in calls:
        name = call_name(node)
        if name in {"touch", "write_bytes", "write_text"}:
            write_calls.append(node)
    assert write_calls == []
    source = tool_source()
    for version in ("b216", "b217", "b218", "b219", "b220", "b221"):
        assert f"verify_scheduler_{version}_" not in source
    expected_import = (
        "from verify_scheduler_b222_cloud_audit_verdict_validity_pin import (\n"
        "    build_verdict_validity_pin_verdict,\n"
        ")"
    )
    assert source.count(expected_import) == 1
    assert ".now(" not in source
    assert ".utcnow(" not in source
    result = build(monkeypatch)
    assert set(result) == SUCCESS_FIELDS
    assert tool.VALIDITY_FIELDS == {
        "not_after_utc",
        "not_before_utc",
        "validity_version",
        "verdict_validity_pin_canonical_sha256",
    }
    assert tool.VERDICT_VALIDITY_PIN_VALIDITY_MAX_BYTES == 65_536
    assert tool.VALIDITY_VERSION == (
        "B2_23_CLOUD_AUDIT_VERDICT_VALIDITY_PIN_VALIDITY_V1"
    )
    test_tree = ast.parse(pathlib.Path(__file__).read_text(encoding="utf-8"))
    test_functions = [
        node
        for node in test_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    ]
    assert len(test_functions) == 35
