from __future__ import annotations

import ast
import importlib.util
import io
import json
import pathlib
import sys
from typing import Any

import pytest


HERE = pathlib.Path(__file__).resolve().parent
TOOL_PATH = HERE / "verify_scheduler_b221_cloud_audit_verdict_validity.py"

SPEC = importlib.util.spec_from_file_location("b221_verdict_validity_tool", TOOL_PATH)
assert SPEC is not None and SPEC.loader is not None
tool = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = tool
SPEC.loader.exec_module(tool)


def source_result(evaluation: str = "2026-07-16T12:00:00Z") -> dict[str, Any]:
    return {
        "attestation_sha256": "1" * 64,
        "authorized": True,
        "evaluation_time_utc": evaluation,
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
        "signing_key_id": "key",
        "source_canonical_sha256": "6" * 64,
        "source_verification_version": "B2_19_CLOUD_AUDIT_TRUST_POLICY_PIN_VERIFICATION_V1",
        "verdict_canonical_sha256": "a" * 64,
        "verification_version": "B2_20_CLOUD_AUDIT_TRUST_POLICY_PIN_VALIDITY_VERIFICATION_V1",
    }


def valid_validity() -> dict[str, Any]:
    return {
        "not_after_utc": "2026-07-16T23:00:00Z",
        "not_before_utc": "2026-07-16T01:00:00Z",
        "validity_version": "B2_21_CLOUD_AUDIT_VERDICT_VALIDITY_V1",
        "verdict_canonical_sha256": "a" * 64,
    }


def install_delegate(
    monkeypatch: pytest.MonkeyPatch,
    result: Any = None,
    error: Exception | None = None,
) -> list[tuple[Any, ...]]:
    calls: list[tuple[Any, ...]] = []

    def delegate(*args: Any) -> dict[str, Any]:
        calls.append(args)
        if error is not None:
            raise error
        return source_result(args[-2]) if result is None else result

    monkeypatch.setattr(tool, "build_policy_pin_validity_verdict", delegate)
    return calls


def build(
    monkeypatch: pytest.MonkeyPatch,
    validity: dict[str, Any] | None = None,
    evaluation: str = "2026-07-16T12:00:00Z",
) -> dict[str, Any]:
    install_delegate(monkeypatch)
    return tool.build_verdict_validity_verdict(
        {},
        {},
        b"p" * 32,
        {},
        {},
        {},
        valid_validity() if validity is None else validity,
        evaluation,
        "key",
    )


def write_inputs(tmp_path: pathlib.Path) -> dict[str, pathlib.Path]:
    values = {
        "verdict": {},
        "signature": {},
        "policy": {},
        "pin": {},
        "policy_validity": {},
        "verdict_validity": valid_validity(),
    }
    paths: dict[str, pathlib.Path] = {}
    for name, value in values.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        paths[name] = path
    public = tmp_path / "public.key"
    public.write_bytes(b"p" * 32)
    paths["public"] = public
    return paths


def cli_arguments(paths: dict[str, pathlib.Path]) -> list[str]:
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
        str(paths["pin"]),
        "--policy-pin-validity",
        str(paths["policy_validity"]),
        "--verdict-validity",
        str(paths["verdict_validity"]),
        "--evaluation-time-utc",
        "2026-07-16T12:00:00Z",
        "--signing-key-id",
        "key",
    ]


def parsed_output(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    captured = capsys.readouterr()
    assert captured.err == ""
    lines = captured.out.splitlines()
    assert len(lines) == 1
    return json.loads(lines[0])


def test_help_is_one_json_line(capsys: pytest.CaptureFixture[str]) -> None:
    assert tool.run_cli(["--help"], io.BytesIO()) == 0
    result = parsed_output(capsys)
    assert result["ok"] is True
    assert result["verdict_validity_tool_version"] == tool.TOOL_VERSION


def test_valid_verdict_validity(monkeypatch: pytest.MonkeyPatch) -> None:
    result = build(monkeypatch)
    assert result["gate"] == "GO"
    assert result["verdict_validity_verified"] is True


def test_output_is_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    first = build(monkeypatch)
    second = build(monkeypatch)
    assert tool.canonical_json_bytes(first) == tool.canonical_json_bytes(second)


def test_semantically_equivalent_validity_is_stable(monkeypatch: pytest.MonkeyPatch) -> None:
    first = valid_validity()
    second = dict(reversed(list(first.items())))
    assert tool.canonical_sha256(first) == tool.canonical_sha256(second)
    assert build(monkeypatch, first) == build(monkeypatch, second)


def test_lower_boundary_is_inclusive(monkeypatch: pytest.MonkeyPatch) -> None:
    assert build(monkeypatch, evaluation="2026-07-16T01:00:00Z")["gate"] == "GO"


def test_upper_boundary_is_inclusive(monkeypatch: pytest.MonkeyPatch) -> None:
    assert build(monkeypatch, evaluation="2026-07-16T23:00:00Z")["gate"] == "GO"


def test_evaluation_before_window_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(tool.VerdictValidityFailure) as caught:
        build(monkeypatch, evaluation="2026-07-16T00:59:59Z")
    assert caught.value.code == "EVALUATION_BEFORE_VERDICT_VALIDITY_WINDOW"


def test_evaluation_after_window_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(tool.VerdictValidityFailure) as caught:
        build(monkeypatch, evaluation="2026-07-16T23:00:01Z")
    assert caught.value.code == "EVALUATION_AFTER_VERDICT_VALIDITY_WINDOW"


def test_verdict_hash_mismatch_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    validity = valid_validity()
    validity["verdict_canonical_sha256"] = "b" * 64
    with pytest.raises(tool.VerdictValidityFailure) as caught:
        build(monkeypatch, validity)
    assert caught.value.code == "VERDICT_VALIDITY_MISMATCH"


def test_b220_failures_are_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    install_delegate(monkeypatch, error=RuntimeError("secret"))
    with pytest.raises(tool.VerdictValidityFailure) as caught:
        tool.build_verdict_validity_verdict(
            {}, {}, b"p" * 32, {}, {}, {}, valid_validity(), "2026-07-16T12:00:00Z", "key"
        )
    assert caught.value.code == "POLICY_PIN_VALIDITY_VERIFICATION_FAILED"
    assert "secret" not in caught.value.message


@pytest.mark.parametrize("mode", ["extra", "missing"])
def test_invalid_b220_fields_are_rejected(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    result = source_result()
    if mode == "extra":
        result["extra"] = True
    else:
        result.pop("authorized")
    install_delegate(monkeypatch, result=result)
    with pytest.raises(tool.VerdictValidityFailure) as caught:
        tool.build_verdict_validity_verdict(
            {}, {}, b"p" * 32, {}, {}, {}, valid_validity(), "2026-07-16T12:00:00Z", "key"
        )
    assert caught.value.code == "POLICY_PIN_VALIDITY_VERIFICATION_FAILED"


@pytest.mark.parametrize(
    "field,value",
    [
        ("ok", False),
        ("authorized", False),
        ("signature_valid", False),
        ("policy_pin_verified", False),
        ("policy_pin_validity_verified", False),
        ("gate", "NO_GO"),
        ("verification_version", "WRONG"),
    ],
)
def test_invalid_b220_values_are_rejected(
    monkeypatch: pytest.MonkeyPatch, field: str, value: Any
) -> None:
    result = source_result()
    result[field] = value
    install_delegate(monkeypatch, result=result)
    with pytest.raises(tool.VerdictValidityFailure):
        tool.build_verdict_validity_verdict(
            {}, {}, b"p" * 32, {}, {}, {}, valid_validity(), "2026-07-16T12:00:00Z", "key"
        )


def test_b220_evaluation_time_mismatch_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    install_delegate(monkeypatch, result=source_result("2026-07-16T12:00:01Z"))
    with pytest.raises(tool.VerdictValidityFailure) as caught:
        tool.build_verdict_validity_verdict(
            {}, {}, b"p" * 32, {}, {}, {}, valid_validity(), "2026-07-16T12:00:00Z", "key"
        )
    assert caught.value.code == "POLICY_PIN_VALIDITY_VERIFICATION_FAILED"


@pytest.mark.parametrize("mode", ["extra", "missing"])
def test_extra_or_missing_validity_field_is_rejected(mode: str) -> None:
    value = valid_validity()
    if mode == "extra":
        value["extra"] = True
    else:
        value.pop("not_after_utc")
    with pytest.raises(tool.VerdictValidityFailure):
        tool.validate_validity_document(value)


def test_invalid_validity_version_is_rejected() -> None:
    value = valid_validity()
    value["validity_version"] = "WRONG"
    with pytest.raises(tool.VerdictValidityFailure):
        tool.validate_validity_document(value)


@pytest.mark.parametrize("value", ["A" * 64, "a" * 63, "g" * 64, 7, None])
def test_invalid_verdict_hash_is_rejected(value: Any) -> None:
    document = valid_validity()
    document["verdict_canonical_sha256"] = value
    with pytest.raises(tool.VerdictValidityFailure):
        tool.validate_validity_document(document)


@pytest.mark.parametrize(
    "value",
    ["2026-07-16", "2026-07-16T01:00:00+00:00", "2026-07-16T01:00:00.000Z", "bad", None],
)
def test_invalid_not_before_utc_is_rejected(value: Any) -> None:
    document = valid_validity()
    document["not_before_utc"] = value
    with pytest.raises(tool.VerdictValidityFailure):
        tool.validate_validity_document(document)


@pytest.mark.parametrize(
    "value",
    ["2026-07-16", "2026-07-16T23:00:00+00:00", "2026-07-16T23:00:00.000Z", "bad", None],
)
def test_invalid_not_after_utc_is_rejected(value: Any) -> None:
    document = valid_validity()
    document["not_after_utc"] = value
    with pytest.raises(tool.VerdictValidityFailure):
        tool.validate_validity_document(document)


def test_reversed_window_is_rejected() -> None:
    value = valid_validity()
    value["not_before_utc"], value["not_after_utc"] = value["not_after_utc"], value["not_before_utc"]
    with pytest.raises(tool.VerdictValidityFailure):
        tool.validate_validity_document(value)


def test_zero_length_window_is_rejected() -> None:
    value = valid_validity()
    value["not_after_utc"] = value["not_before_utc"]
    with pytest.raises(tool.VerdictValidityFailure):
        tool.validate_validity_document(value)


def test_malformed_validity_json_is_rejected() -> None:
    with pytest.raises(tool.VerdictValidityFailure) as caught:
        tool.parse_json_document(b"{", "verdict validity document")
    assert caught.value.code == "INVALID_JSON"


def test_duplicate_validity_key_is_rejected() -> None:
    with pytest.raises(tool.VerdictValidityFailure):
        tool.parse_json_document(b'{"validity_version":"x","validity_version":"y"}', "document")


def test_non_finite_validity_json_is_rejected() -> None:
    with pytest.raises(tool.VerdictValidityFailure):
        tool.parse_json_document(b'{"value":NaN}', "document")


def test_validity_json_array_is_rejected() -> None:
    with pytest.raises(tool.VerdictValidityFailure):
        tool.parse_json_document(b"[]", "document")


def test_utf8_bom_validity_is_supported() -> None:
    raw = b"\xef\xbb\xbf" + json.dumps(valid_validity()).encode()
    assert tool.parse_json_document(raw, "document") == valid_validity()


def test_oversized_validity_is_rejected(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "large.json"
    path.write_bytes(b"x" * (tool.VERDICT_VALIDITY_MAX_BYTES + 1))
    with pytest.raises(tool.VerdictValidityFailure) as caught:
        tool.read_bytes(str(path), tool.VERDICT_VALIDITY_MAX_BYTES, io.BytesIO(), "document")
    assert caught.value.code == "INPUT_TOO_LARGE"


@pytest.mark.parametrize(
    "name",
    ["verdict", "signature", "public", "policy", "pin", "policy_validity", "verdict_validity"],
)
def test_missing_file_is_rejected(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    name: str,
) -> None:
    paths = write_inputs(tmp_path)
    paths[name].unlink()
    assert tool.run_cli(cli_arguments(paths), io.BytesIO()) == 2
    assert parsed_output(capsys)["error_code"] == "READ_ERROR"


@pytest.mark.parametrize(
    "first,second",
    [("verdict", "signature"), ("public", "policy"), ("pin", "policy_validity"), ("policy_validity", "verdict_validity")],
)
def test_multiple_stdin_inputs_are_rejected(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    first: str,
    second: str,
) -> None:
    paths = write_inputs(tmp_path)
    arguments = cli_arguments(paths)
    option_by_name = {
        "verdict": "--verdict",
        "signature": "--signature",
        "public": "--public-key-file",
        "policy": "--trust-policy",
        "pin": "--policy-pin",
        "policy_validity": "--policy-pin-validity",
        "verdict_validity": "--verdict-validity",
    }
    for name in (first, second):
        option = option_by_name[name]
        arguments[arguments.index(option) + 1] = "-"
    assert tool.run_cli(arguments, io.BytesIO()) == 2
    assert parsed_output(capsys)["error_code"] == "ARGUMENT_ERROR"


def test_validity_document_stdin_is_supported(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = write_inputs(tmp_path)
    arguments = cli_arguments(paths)
    arguments[arguments.index("--verdict-validity") + 1] = "-"
    install_delegate(monkeypatch)
    raw = json.dumps(valid_validity()).encode()
    assert tool.run_cli(arguments, io.BytesIO(raw)) == 0
    assert parsed_output(capsys)["verdict_validity_verified"] is True


def test_valid_cli_file_inputs(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = write_inputs(tmp_path)
    install_delegate(monkeypatch)
    assert tool.run_cli(cli_arguments(paths), io.BytesIO()) == 0
    assert parsed_output(capsys)["gate"] == "GO"


def test_unknown_argument_has_no_traceback(capsys: pytest.CaptureFixture[str]) -> None:
    assert tool.run_cli(["--unknown"], io.BytesIO()) == 2
    result = parsed_output(capsys)
    assert result["error_code"] == "ARGUMENT_ERROR"


@pytest.mark.parametrize("arguments", [["--help", "--verdict", "x"], []])
def test_help_with_inputs_or_missing_arguments_is_rejected(
    capsys: pytest.CaptureFixture[str], arguments: list[str]
) -> None:
    assert tool.run_cli(arguments, io.BytesIO()) == 2
    assert parsed_output(capsys)["error_code"] == "ARGUMENT_ERROR"


def test_output_excludes_raw_inputs_and_sets_source_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = build(monkeypatch)
    assert not ({"verdict", "signature", "policy", "pin", "validity", "public_key"} & set(result))
    assert result["source_verification_version"] == tool.SOURCE_VERIFICATION_VERSION


def test_b220_delegation_is_called_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = install_delegate(monkeypatch)
    tool.build_verdict_validity_verdict(
        {}, {}, b"p" * 32, {}, {}, {}, valid_validity(), "2026-07-16T12:00:00Z", "key"
    )
    assert len(calls) == 1
    assert len(calls[0]) == 8


def test_static_contract_and_previous_components() -> None:
    source = TOOL_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = [
        alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "verify_scheduler_b220_cloud_audit_trust_policy_pin_validity"
        for alias in node.names
    ]
    assert imports == ["build_policy_pin_validity_verdict"]
    assert sum(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "build_policy_pin_validity_verdict"
        for node in ast.walk(tree)
    ) == 1
    options = sorted(
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_argument"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
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
        ]
    )
    forbidden = {"socket", "subprocess", "requests", "httpx", "urllib"}
    imported_roots = {
        alias.name.split(".", 1)[0]
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not (forbidden & imported_roots)
    assert "verify_scheduler_b217_" not in source
    assert "verify_scheduler_b218_" not in source
    assert "verify_scheduler_b219_" not in source
    test_tree = ast.parse(pathlib.Path(__file__).read_text(encoding="utf-8"))
    assert sum(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
        for node in ast.walk(test_tree)
    ) == 35
