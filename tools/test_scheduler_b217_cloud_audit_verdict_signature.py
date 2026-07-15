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
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = pathlib.Path(__file__).resolve().parent
TOOL_PATH = ROOT / "sign_scheduler_b217_cloud_audit_verdict.py"
PRIVATE_KEY = bytes(range(32))
SIGNING_KEY_ID = "b217-local-signing-key"


def load_tool() -> Any:
    module_name = "sign_scheduler_b217_cloud_audit_verdict_tested"
    spec = importlib.util.spec_from_file_location(module_name, TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def public_key_bytes(private_key: bytes = PRIVATE_KEY) -> bytes:
    key = Ed25519PrivateKey.from_private_bytes(private_key)
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def valid_verdict() -> dict[str, Any]:
    return {
        "attestation_sha256": "1" * 64,
        "authenticated": True,
        "gate": "GO",
        "key_id": "b216-local-hmac-key",
        "ok": True,
        "source_canonical_sha256": "2" * 64,
        "verdict_canonical_sha256": "3" * 64,
        "verification_version": "B2_16_CLOUD_AUDIT_VERDICT_HMAC_VERIFICATION_V1",
    }


def write_json(path: pathlib.Path, value: Any, *, bom: bool = False) -> None:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + data)


def invoke(arguments: list[str], stdin_data: bytes = b"") -> tuple[int, dict[str, Any], str]:
    tool = load_tool()
    stdout = io.StringIO()
    code = tool.run_cli(arguments, io.BytesIO(stdin_data), stdout)
    raw = stdout.getvalue()
    lines = raw.splitlines()
    assert len(lines) == 1
    return code, json.loads(lines[0]), raw


def create_signed_pair(tmp_path: pathlib.Path) -> dict[str, Any]:
    verdict_path = tmp_path / "verdict.json"
    private_path = tmp_path / "private.key"
    public_path = tmp_path / "public.key"
    signature_path = tmp_path / "signature.json"
    verdict = valid_verdict()
    write_json(verdict_path, verdict)
    private_path.write_bytes(PRIVATE_KEY)
    public_path.write_bytes(public_key_bytes())
    code, envelope, _ = invoke(
        [
            "sign",
            "--verdict",
            str(verdict_path),
            "--private-key-file",
            str(private_path),
            "--signing-key-id",
            SIGNING_KEY_ID,
        ]
    )
    assert code == 0
    write_json(signature_path, envelope)
    return {
        "verdict": verdict,
        "verdict_path": verdict_path,
        "private_path": private_path,
        "public_path": public_path,
        "signature_path": signature_path,
        "envelope": envelope,
    }


def sign_arguments(verdict: pathlib.Path, private_key: pathlib.Path | str) -> list[str]:
    return [
        "sign",
        "--verdict",
        str(verdict),
        "--private-key-file",
        str(private_key),
        "--signing-key-id",
        SIGNING_KEY_ID,
    ]


def verify_arguments(
    verdict: pathlib.Path | str,
    signature: pathlib.Path | str,
    public_key: pathlib.Path | str,
) -> list[str]:
    return [
        "verify",
        "--verdict",
        str(verdict),
        "--signature",
        str(signature),
        "--public-key-file",
        str(public_key),
        "--signing-key-id",
        SIGNING_KEY_ID,
    ]


def test_help_is_one_json_line() -> None:
    code, result, raw = invoke(["--help"])
    assert code == 0
    assert result["ok"] is True
    assert "sign --verdict FILE|-" in result["usage"]
    assert "verify --verdict FILE|-" in result["usage"]
    assert raw.endswith("\n")


def test_valid_sign_file_inputs(tmp_path: pathlib.Path) -> None:
    pair = create_signed_pair(tmp_path)
    envelope = pair["envelope"]
    assert set(envelope) == load_tool().SIGNATURE_FIELDS
    assert envelope["algorithm"] == "Ed25519"
    assert envelope["signature_version"] == "B2_17_CLOUD_AUDIT_VERDICT_ED25519_V1"
    assert len(envelope["signature_hex"]) == 128


def test_valid_verify_file_inputs(tmp_path: pathlib.Path) -> None:
    pair = create_signed_pair(tmp_path)
    code, result, _ = invoke(
        verify_arguments(pair["verdict_path"], pair["signature_path"], pair["public_path"])
    )
    assert code == 0
    assert result == {
        "attestation_sha256": "1" * 64,
        "gate": "GO",
        "ok": True,
        "public_key_sha256": hashlib.sha256(public_key_bytes()).hexdigest(),
        "signature_valid": True,
        "signing_key_id": SIGNING_KEY_ID,
        "source_canonical_sha256": "2" * 64,
        "verdict_canonical_sha256": load_tool().canonical_sha256(valid_verdict()),
        "verification_version": "B2_17_CLOUD_AUDIT_VERDICT_ED25519_VERIFICATION_V1",
    }


def test_verdict_stdin_is_supported_for_sign(tmp_path: pathlib.Path) -> None:
    private_path = tmp_path / "private.key"
    private_path.write_bytes(PRIVATE_KEY)
    data = json.dumps(valid_verdict()).encode("utf-8")
    args = sign_arguments(pathlib.Path("-"), private_path)
    code, result, _ = invoke(args, data)
    assert code == 0
    assert result["signature_version"] == "B2_17_CLOUD_AUDIT_VERDICT_ED25519_V1"


def test_private_key_stdin_is_supported(tmp_path: pathlib.Path) -> None:
    verdict_path = tmp_path / "verdict.json"
    write_json(verdict_path, valid_verdict())
    code, result, _ = invoke(sign_arguments(verdict_path, "-"), PRIVATE_KEY)
    assert code == 0
    assert result["public_key_sha256"] == hashlib.sha256(public_key_bytes()).hexdigest()


def test_verdict_stdin_is_supported_for_verify(tmp_path: pathlib.Path) -> None:
    pair = create_signed_pair(tmp_path)
    data = json.dumps(pair["verdict"]).encode("utf-8")
    code, result, _ = invoke(
        verify_arguments("-", pair["signature_path"], pair["public_path"]),
        data,
    )
    assert code == 0
    assert result["signature_valid"] is True


def test_signature_document_stdin_is_supported(tmp_path: pathlib.Path) -> None:
    pair = create_signed_pair(tmp_path)
    data = json.dumps(pair["envelope"]).encode("utf-8")
    code, result, _ = invoke(
        verify_arguments(pair["verdict_path"], "-", pair["public_path"]),
        data,
    )
    assert code == 0
    assert result["signature_valid"] is True


def test_public_key_stdin_is_supported(tmp_path: pathlib.Path) -> None:
    pair = create_signed_pair(tmp_path)
    code, result, _ = invoke(
        verify_arguments(pair["verdict_path"], pair["signature_path"], "-"),
        public_key_bytes(),
    )
    assert code == 0
    assert result["signature_valid"] is True


@pytest.mark.parametrize(
    "arguments",
    [
        ["sign", "--verdict", "-", "--private-key-file", "-", "--signing-key-id", SIGNING_KEY_ID],
        [
            "verify", "--verdict", "-", "--signature", "-",
            "--public-key-file", "public.key", "--signing-key-id", SIGNING_KEY_ID,
        ],
        [
            "verify", "--verdict", "verdict.json", "--signature", "-",
            "--public-key-file", "-", "--signing-key-id", SIGNING_KEY_ID,
        ],
    ],
)
def test_multiple_stdin_inputs_are_rejected(arguments: list[str]) -> None:
    code, result, _ = invoke(arguments)
    assert code == 2
    assert result["error_code"] == "ARGUMENT_ERROR"
    assert result["gate"] == "INPUT_ERROR"


def test_utf8_bom_json_documents_are_supported(tmp_path: pathlib.Path) -> None:
    verdict_path = tmp_path / "verdict.json"
    private_path = tmp_path / "private.key"
    public_path = tmp_path / "public.key"
    signature_path = tmp_path / "signature.json"
    write_json(verdict_path, valid_verdict(), bom=True)
    private_path.write_bytes(PRIVATE_KEY)
    public_path.write_bytes(public_key_bytes())
    code, envelope, _ = invoke(sign_arguments(verdict_path, private_path))
    assert code == 0
    write_json(signature_path, envelope, bom=True)
    code, result, _ = invoke(verify_arguments(verdict_path, signature_path, public_path))
    assert code == 0
    assert result["signature_valid"] is True


def test_semantically_equivalent_json_has_same_signature(tmp_path: pathlib.Path) -> None:
    private_path = tmp_path / "private.key"
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    private_path.write_bytes(PRIVATE_KEY)
    verdict = valid_verdict()
    first_path.write_text(json.dumps(verdict, sort_keys=True), encoding="utf-8")
    second_path.write_text(json.dumps(dict(reversed(list(verdict.items()))), indent=3), encoding="utf-8")
    first = invoke(sign_arguments(first_path, private_path))[1]
    second = invoke(sign_arguments(second_path, private_path))[1]
    assert first == second


def test_signature_output_is_deterministic(tmp_path: pathlib.Path) -> None:
    pair = create_signed_pair(tmp_path)
    first = invoke(sign_arguments(pair["verdict_path"], pair["private_path"]))
    second = invoke(sign_arguments(pair["verdict_path"], pair["private_path"]))
    assert first == second


def test_wrong_public_key_is_rejected(tmp_path: pathlib.Path) -> None:
    pair = create_signed_pair(tmp_path)
    pair["public_path"].write_bytes(public_key_bytes(bytes(range(1, 33))))
    code, result, _ = invoke(
        verify_arguments(pair["verdict_path"], pair["signature_path"], pair["public_path"])
    )
    assert code == 1
    assert result["error_code"] == "SIGNATURE_INVALID"


def test_signing_key_id_mismatch_is_rejected(tmp_path: pathlib.Path) -> None:
    pair = create_signed_pair(tmp_path)
    arguments = verify_arguments(pair["verdict_path"], pair["signature_path"], pair["public_path"])
    arguments[-1] = "another-signing-key"
    code, result, _ = invoke(arguments)
    assert code == 1
    assert result["error_code"] == "SIGNATURE_INVALID"


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("algorithm", "Ed448"),
        ("attestation_sha256", "4" * 64),
        ("hmac_key_id", "other-hmac-key"),
        ("public_key_sha256", "5" * 64),
        ("signature_hex", "f" * 128),
        ("signature_version", "TAMPERED"),
        ("signing_key_id", "other-signing-key"),
        ("source_canonical_sha256", "6" * 64),
        ("source_verification_version", "TAMPERED"),
        ("verdict_canonical_sha256", "7" * 64),
    ],
)
def test_tampered_signature_fields_are_rejected(
    tmp_path: pathlib.Path,
    field: str,
    replacement: str,
) -> None:
    pair = create_signed_pair(tmp_path)
    envelope = copy.deepcopy(pair["envelope"])
    envelope[field] = replacement
    write_json(pair["signature_path"], envelope)
    code, result, _ = invoke(
        verify_arguments(pair["verdict_path"], pair["signature_path"], pair["public_path"])
    )
    assert code == 1
    assert result["error_code"] == "SIGNATURE_INVALID"


@pytest.mark.parametrize("change", ["extra", "missing"])
def test_extra_or_missing_signature_field_is_rejected(
    tmp_path: pathlib.Path,
    change: str,
) -> None:
    pair = create_signed_pair(tmp_path)
    envelope = copy.deepcopy(pair["envelope"])
    if change == "extra":
        envelope["extra"] = True
    else:
        envelope.pop("algorithm")
    write_json(pair["signature_path"], envelope)
    code, result, _ = invoke(
        verify_arguments(pair["verdict_path"], pair["signature_path"], pair["public_path"])
    )
    assert code == 1
    assert result["error_code"] == "SIGNATURE_INVALID"


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("verification_version", "TAMPERED"),
        ("gate", "NO_GO"),
        ("ok", False),
        ("authenticated", False),
        ("key_id", "-invalid"),
        ("attestation_sha256", "A" * 64),
        ("source_canonical_sha256", "short"),
        ("verdict_canonical_sha256", "F" * 64),
    ],
)
def test_invalid_b216_verdict_is_rejected(
    tmp_path: pathlib.Path,
    field: str,
    replacement: Any,
) -> None:
    verdict = valid_verdict()
    verdict[field] = replacement
    verdict_path = tmp_path / "verdict.json"
    private_path = tmp_path / "private.key"
    write_json(verdict_path, verdict)
    private_path.write_bytes(PRIVATE_KEY)
    code, result, _ = invoke(sign_arguments(verdict_path, private_path))
    assert code == 1
    assert result["error_code"] == "VERDICT_CONTRACT_INVALID"


@pytest.mark.parametrize("change", ["extra", "missing"])
def test_extra_or_missing_verdict_field_is_rejected(
    tmp_path: pathlib.Path,
    change: str,
) -> None:
    verdict = valid_verdict()
    if change == "extra":
        verdict["extra"] = True
    else:
        verdict.pop("ok")
    verdict_path = tmp_path / "verdict.json"
    private_path = tmp_path / "private.key"
    write_json(verdict_path, verdict)
    private_path.write_bytes(PRIVATE_KEY)
    code, result, _ = invoke(sign_arguments(verdict_path, private_path))
    assert code == 1
    assert result["error_code"] == "VERDICT_CONTRACT_INVALID"


def test_tampered_valid_verdict_is_not_verified(tmp_path: pathlib.Path) -> None:
    pair = create_signed_pair(tmp_path)
    verdict = copy.deepcopy(pair["verdict"])
    verdict["source_canonical_sha256"] = "8" * 64
    write_json(pair["verdict_path"], verdict)
    code, result, _ = invoke(
        verify_arguments(pair["verdict_path"], pair["signature_path"], pair["public_path"])
    )
    assert code == 1
    assert result["error_code"] == "SIGNATURE_INVALID"


@pytest.mark.parametrize("target", ["verdict", "signature"])
def test_malformed_json_is_rejected(tmp_path: pathlib.Path, target: str) -> None:
    pair = create_signed_pair(tmp_path)
    if target == "verdict":
        pair["verdict_path"].write_bytes(b"{")
    else:
        pair["signature_path"].write_bytes(b"{")
    code, result, _ = invoke(
        verify_arguments(pair["verdict_path"], pair["signature_path"], pair["public_path"])
    )
    assert code == 2
    assert result["error_code"] == "INVALID_JSON"


@pytest.mark.parametrize("target", ["verdict", "signature"])
def test_json_array_is_rejected(tmp_path: pathlib.Path, target: str) -> None:
    pair = create_signed_pair(tmp_path)
    selected = pair["verdict_path"] if target == "verdict" else pair["signature_path"]
    selected.write_bytes(b"[]")
    code, result, _ = invoke(
        verify_arguments(pair["verdict_path"], pair["signature_path"], pair["public_path"])
    )
    assert code == 2
    assert result["error_code"] == "INVALID_JSON_OBJECT"


def test_duplicate_json_key_is_rejected(tmp_path: pathlib.Path) -> None:
    verdict_path = tmp_path / "verdict.json"
    private_path = tmp_path / "private.key"
    verdict_path.write_bytes(b'{"ok":true,"ok":true}')
    private_path.write_bytes(PRIVATE_KEY)
    code, result, _ = invoke(sign_arguments(verdict_path, private_path))
    assert code == 2
    assert result["error_code"] == "DUPLICATE_JSON_KEY"


def test_non_finite_json_constant_is_rejected(tmp_path: pathlib.Path) -> None:
    verdict_path = tmp_path / "verdict.json"
    private_path = tmp_path / "private.key"
    verdict_path.write_bytes(b'{"value":NaN}')
    private_path.write_bytes(PRIVATE_KEY)
    code, result, _ = invoke(sign_arguments(verdict_path, private_path))
    assert code == 2
    assert result["error_code"] == "NON_FINITE_JSON"


@pytest.mark.parametrize("target", ["verdict", "private", "signature", "public"])
def test_missing_file_is_rejected(tmp_path: pathlib.Path, target: str) -> None:
    pair = create_signed_pair(tmp_path)
    paths = {
        "verdict": pair["verdict_path"],
        "private": pair["private_path"],
        "signature": pair["signature_path"],
        "public": pair["public_path"],
    }
    paths[target].unlink()
    if target == "private":
        arguments = sign_arguments(pair["verdict_path"], pair["private_path"])
    else:
        arguments = verify_arguments(
            pair["verdict_path"], pair["signature_path"], pair["public_path"]
        )
    code, result, _ = invoke(arguments)
    assert code == 2
    assert result["error_code"] == "INPUT_READ_ERROR"


@pytest.mark.parametrize("target", ["verdict", "signature"])
def test_oversized_json_input_is_rejected(tmp_path: pathlib.Path, target: str) -> None:
    pair = create_signed_pair(tmp_path)
    tool = load_tool()
    if target == "verdict":
        pair["verdict_path"].write_bytes(b" " * (tool.VERDICT_MAX_BYTES + 1))
    else:
        pair["signature_path"].write_bytes(b" " * (tool.SIGNATURE_MAX_BYTES + 1))
    code, result, _ = invoke(
        verify_arguments(pair["verdict_path"], pair["signature_path"], pair["public_path"])
    )
    assert code == 2
    assert result["error_code"] == "INPUT_TOO_LARGE"


@pytest.mark.parametrize("length", [0, 31, 33, 64])
def test_invalid_private_key_lengths_are_rejected(
    tmp_path: pathlib.Path,
    length: int,
) -> None:
    verdict_path = tmp_path / "verdict.json"
    private_path = tmp_path / "private.key"
    write_json(verdict_path, valid_verdict())
    private_path.write_bytes(b"k" * length)
    code, result, _ = invoke(sign_arguments(verdict_path, private_path))
    assert code == 2
    assert result["error_code"] == "INVALID_PRIVATE_KEY"


@pytest.mark.parametrize("length", [0, 31, 33, 64])
def test_invalid_public_key_lengths_are_rejected(
    tmp_path: pathlib.Path,
    length: int,
) -> None:
    pair = create_signed_pair(tmp_path)
    pair["public_path"].write_bytes(b"p" * length)
    code, result, _ = invoke(
        verify_arguments(pair["verdict_path"], pair["signature_path"], pair["public_path"])
    )
    assert code == 2
    assert result["error_code"] == "INVALID_PUBLIC_KEY"


@pytest.mark.parametrize("key_id", ["", "-leading", "space key", "x" * 65])
def test_invalid_signing_key_id_is_rejected(
    tmp_path: pathlib.Path,
    key_id: str,
) -> None:
    pair = create_signed_pair(tmp_path)
    arguments = sign_arguments(pair["verdict_path"], pair["private_path"])
    arguments[-1] = key_id
    code, result, _ = invoke(arguments)
    assert code == 2
    assert result["error_code"] == "INVALID_SIGNING_KEY_ID"


def test_missing_verify_signature_option_is_rejected(tmp_path: pathlib.Path) -> None:
    pair = create_signed_pair(tmp_path)
    arguments = [
        "verify",
        "--verdict", str(pair["verdict_path"]),
        "--public-key-file", str(pair["public_path"]),
        "--signing-key-id", SIGNING_KEY_ID,
    ]
    code, result, _ = invoke(arguments)
    assert code == 2
    assert result["error_code"] == "ARGUMENT_ERROR"


def test_unknown_argument_has_no_traceback(tmp_path: pathlib.Path) -> None:
    pair = create_signed_pair(tmp_path)
    arguments = sign_arguments(pair["verdict_path"], pair["private_path"]) + ["--unknown", "x"]
    code, result, raw = invoke(arguments)
    assert code == 2
    assert result["error_code"] == "ARGUMENT_ERROR"
    assert "Traceback" not in raw


def test_key_values_are_never_emitted(tmp_path: pathlib.Path) -> None:
    pair = create_signed_pair(tmp_path)
    sign_raw = invoke(sign_arguments(pair["verdict_path"], pair["private_path"]))[2]
    verify_raw = invoke(
        verify_arguments(pair["verdict_path"], pair["signature_path"], pair["public_path"])
    )[2]
    assert PRIVATE_KEY.hex() not in sign_raw
    assert public_key_bytes().hex() not in sign_raw
    assert public_key_bytes().hex() not in verify_raw
    assert "verdict" not in json.loads(sign_raw)


def test_public_key_fingerprint_is_bound(tmp_path: pathlib.Path) -> None:
    pair = create_signed_pair(tmp_path)
    expected = hashlib.sha256(public_key_bytes()).hexdigest()
    assert pair["envelope"]["public_key_sha256"] == expected
    envelope = copy.deepcopy(pair["envelope"])
    envelope["public_key_sha256"] = "0" * 64
    write_json(pair["signature_path"], envelope)
    code, result, _ = invoke(
        verify_arguments(pair["verdict_path"], pair["signature_path"], pair["public_path"])
    )
    assert code == 1
    assert result["error_code"] == "SIGNATURE_INVALID"


def test_dependency_unavailable_is_sanitized(tmp_path: pathlib.Path) -> None:
    verdict_path = tmp_path / "verdict.json"
    private_path = tmp_path / "private.key"
    write_json(verdict_path, valid_verdict())
    private_path.write_bytes(PRIVATE_KEY)
    tool = load_tool()
    tool.DEPENDENCY_ERROR = RuntimeError("sensitive dependency details")
    stdout = io.StringIO()
    code = tool.run_cli(sign_arguments(verdict_path, private_path), io.BytesIO(), stdout)
    result = json.loads(stdout.getvalue())
    assert code == 2
    assert result["error_code"] == "DEPENDENCY_UNAVAILABLE"
    assert "sensitive" not in stdout.getvalue()
    assert "Traceback" not in stdout.getvalue()


def test_static_contract_has_no_network_subprocess_or_file_writes() -> None:
    source = TOOL_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_imports = {"socket", "requests", "urllib", "http", "subprocess"}
    imported: set[str] = set()
    forbidden_write_calls: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"write_bytes", "write_text", "touch", "mkdir", "unlink", "rename", "replace"}:
                forbidden_write_calls.append(node.func.attr)
    assert not (imported & forbidden_imports)
    assert forbidden_write_calls == []
    assert "generate_private_key" not in source


def test_previous_components_are_not_referenced_for_modification() -> None:
    source = TOOL_PATH.read_text(encoding="utf-8")
    forbidden = [
        "api_main.py",
        "audit_scheduler_b212_cycles.py",
        "check_scheduler_b213_cloud_cycles.py",
        "attest_scheduler_b214_cloud_audit.py",
        "verify_scheduler_b215_cloud_audit_attestation.py",
        "authenticate_scheduler_b216_cloud_audit_verdict.py",
    ]
    assert all(name not in source for name in forbidden)
