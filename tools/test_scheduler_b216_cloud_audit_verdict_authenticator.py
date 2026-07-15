from __future__ import annotations

import ast
import copy
import hashlib
import hmac
import importlib.util
import io
import json
import pathlib
import sys
from types import ModuleType
from typing import Any

import pytest


TOOLS_DIRECTORY = pathlib.Path(__file__).resolve().parent
TOOL_PATH = (
    TOOLS_DIRECTORY
    / "authenticate_scheduler_b216_cloud_audit_verdict.py"
)

AUTHENTICATOR_VERSION = (
    "B2_16_CLOUD_AUDIT_VERDICT_HMAC_AUTHENTICATOR_V1"
)
ENVELOPE_VERSION = "B2_16_CLOUD_AUDIT_VERDICT_HMAC_V1"
VERIFICATION_VERSION = (
    "B2_16_CLOUD_AUDIT_VERDICT_HMAC_VERIFICATION_V1"
)

ENVELOPE_FIELDS = {
    "algorithm",
    "attestation_sha256",
    "attestation_version",
    "envelope_version",
    "key_id",
    "mac_hex",
    "source_canonical_sha256",
    "verdict_canonical_sha256",
    "verifier_version",
}

VERIFICATION_FIELDS = {
    "authenticated",
    "attestation_sha256",
    "gate",
    "key_id",
    "ok",
    "source_canonical_sha256",
    "verdict_canonical_sha256",
    "verification_version",
}


class BinaryStdin:
    def __init__(self, data: bytes) -> None:
        self.buffer = io.BytesIO(data)


@pytest.fixture
def tool_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "b216_authenticator_under_test",
        TOOL_PATH,
    )

    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def valid_verdict() -> dict[str, Any]:
    return {
        "attestation_sha256": "1" * 64,
        "attestation_version": (
            "B2_14_CLOUD_AUDIT_ATTESTATION_V1"
        ),
        "gate": "GO",
        "ok": True,
        "source_canonical_sha256": "0" * 64,
        "verified": True,
        "verifier_version": (
            "B2_15_CLOUD_AUDIT_ATTESTATION_VERIFIER_V1"
        ),
    }


@pytest.fixture
def secret_key() -> bytes:
    return b"B216-local-test-secret-key-material"


def write_json(
    path: pathlib.Path,
    value: Any,
    *,
    bom: bool = False,
    pretty: bool = False,
) -> None:
    text = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=not pretty,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
    )

    path.write_text(
        text,
        encoding="utf-8-sig" if bom else "utf-8",
    )


def invoke_tool(
    module: ModuleType,
    arguments: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    stdin_bytes: bytes = b"",
) -> tuple[int, dict[str, Any], str]:
    monkeypatch.setattr(
        sys,
        "stdin",
        BinaryStdin(stdin_bytes),
    )

    exit_code = module.main(arguments)
    captured = capsys.readouterr()

    assert captured.err == ""

    lines = [
        line
        for line in captured.out.splitlines()
        if line.strip()
    ]

    assert len(lines) == 1

    payload = json.loads(lines[0])
    assert isinstance(payload, dict)

    return exit_code, payload, lines[0]


def create_valid_files(
    tmp_path: pathlib.Path,
    module: ModuleType,
    verdict: dict[str, Any],
    key: bytes,
    key_id: str = "local-key-01",
) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    verdict_path = tmp_path / "verdict.json"
    envelope_path = tmp_path / "envelope.json"
    key_path = tmp_path / "secret.key"

    write_json(verdict_path, verdict)
    key_path.write_bytes(key)

    envelope = module.build_envelope(
        copy.deepcopy(verdict),
        key,
        key_id,
    )
    write_json(envelope_path, envelope)

    return verdict_path, envelope_path, key_path


def test_help_is_one_json_line(
    tool_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, payload, _ = invoke_tool(
        tool_module,
        ["--help"],
        monkeypatch,
        capsys,
    )

    assert code == 0
    assert payload["ok"] is True
    assert (
        payload["authenticator_version"]
        == AUTHENTICATOR_VERSION
    )
    assert "seal" in payload["usage"]
    assert "verify" in payload["usage"]


def test_valid_seal_file_inputs(
    tmp_path: pathlib.Path,
    tool_module: ModuleType,
    valid_verdict: dict[str, Any],
    secret_key: bytes,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    verdict_path = tmp_path / "verdict.json"
    key_path = tmp_path / "key.bin"

    write_json(verdict_path, valid_verdict)
    key_path.write_bytes(secret_key)

    code, payload, _ = invoke_tool(
        tool_module,
        [
            "seal",
            "--verdict",
            str(verdict_path),
            "--key-file",
            str(key_path),
            "--key-id",
            "local-key-01",
        ],
        monkeypatch,
        capsys,
    )

    assert code == 0
    assert set(payload) == ENVELOPE_FIELDS
    assert payload["algorithm"] == "HMAC-SHA-256"
    assert payload["envelope_version"] == ENVELOPE_VERSION
    assert payload["key_id"] == "local-key-01"
    assert len(payload["mac_hex"]) == 64


def test_valid_verify_file_inputs(
    tmp_path: pathlib.Path,
    tool_module: ModuleType,
    valid_verdict: dict[str, Any],
    secret_key: bytes,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    verdict_path, envelope_path, key_path = (
        create_valid_files(
            tmp_path,
            tool_module,
            valid_verdict,
            secret_key,
        )
    )

    code, payload, _ = invoke_tool(
        tool_module,
        [
            "verify",
            "--verdict",
            str(verdict_path),
            "--envelope",
            str(envelope_path),
            "--key-file",
            str(key_path),
            "--key-id",
            "local-key-01",
        ],
        monkeypatch,
        capsys,
    )

    assert code == 0
    assert set(payload) == VERIFICATION_FIELDS
    assert payload["gate"] == "GO"
    assert payload["ok"] is True
    assert payload["authenticated"] is True
    assert payload["verification_version"] == VERIFICATION_VERSION


def test_verdict_stdin_is_supported(
    tmp_path: pathlib.Path,
    tool_module: ModuleType,
    valid_verdict: dict[str, Any],
    secret_key: bytes,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    key_path = tmp_path / "key.bin"
    key_path.write_bytes(secret_key)

    verdict_bytes = json.dumps(
        valid_verdict,
        separators=(",", ":"),
    ).encode("utf-8")

    code, payload, _ = invoke_tool(
        tool_module,
        [
            "seal",
            "--verdict",
            "-",
            "--key-file",
            str(key_path),
            "--key-id",
            "stdin-verdict",
        ],
        monkeypatch,
        capsys,
        stdin_bytes=verdict_bytes,
    )

    assert code == 0
    assert payload["key_id"] == "stdin-verdict"


def test_key_stdin_is_supported(
    tmp_path: pathlib.Path,
    tool_module: ModuleType,
    valid_verdict: dict[str, Any],
    secret_key: bytes,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    verdict_path = tmp_path / "verdict.json"
    write_json(verdict_path, valid_verdict)

    code, payload, _ = invoke_tool(
        tool_module,
        [
            "seal",
            "--verdict",
            str(verdict_path),
            "--key-file",
            "-",
            "--key-id",
            "stdin-key",
        ],
        monkeypatch,
        capsys,
        stdin_bytes=secret_key,
    )

    assert code == 0
    assert payload["key_id"] == "stdin-key"


def test_envelope_stdin_is_supported(
    tmp_path: pathlib.Path,
    tool_module: ModuleType,
    valid_verdict: dict[str, Any],
    secret_key: bytes,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    verdict_path, envelope_path, key_path = (
        create_valid_files(
            tmp_path,
            tool_module,
            valid_verdict,
            secret_key,
        )
    )

    envelope_bytes = envelope_path.read_bytes()

    code, payload, _ = invoke_tool(
        tool_module,
        [
            "verify",
            "--verdict",
            str(verdict_path),
            "--envelope",
            "-",
            "--key-file",
            str(key_path),
            "--key-id",
            "local-key-01",
        ],
        monkeypatch,
        capsys,
        stdin_bytes=envelope_bytes,
    )

    assert code == 0
    assert payload["authenticated"] is True


@pytest.mark.parametrize(
    "arguments",
    [
        [
            "seal",
            "--verdict",
            "-",
            "--key-file",
            "-",
            "--key-id",
            "key-01",
        ],
        [
            "verify",
            "--verdict",
            "-",
            "--envelope",
            "-",
            "--key-file",
            "key.bin",
            "--key-id",
            "key-01",
        ],
        [
            "verify",
            "--verdict",
            "verdict.json",
            "--envelope",
            "-",
            "--key-file",
            "-",
            "--key-id",
            "key-01",
        ],
    ],
)
def test_multiple_stdin_inputs_are_rejected(
    arguments: list[str],
    tool_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, payload, _ = invoke_tool(
        tool_module,
        arguments,
        monkeypatch,
        capsys,
    )

    assert code == 2
    assert payload["gate"] == "INPUT_ERROR"
    assert payload["error_code"] == "MULTIPLE_STDIN_INPUTS"


def test_utf8_bom_documents_are_supported(
    tmp_path: pathlib.Path,
    tool_module: ModuleType,
    valid_verdict: dict[str, Any],
    secret_key: bytes,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    verdict_path = tmp_path / "verdict-bom.json"
    envelope_path = tmp_path / "envelope-bom.json"
    key_path = tmp_path / "key.bin"

    write_json(verdict_path, valid_verdict, bom=True)
    key_path.write_bytes(secret_key)

    envelope = tool_module.build_envelope(
        valid_verdict,
        secret_key,
        "bom-key",
    )
    write_json(envelope_path, envelope, bom=True)

    code, payload, _ = invoke_tool(
        tool_module,
        [
            "verify",
            "--verdict",
            str(verdict_path),
            "--envelope",
            str(envelope_path),
            "--key-file",
            str(key_path),
            "--key-id",
            "bom-key",
        ],
        monkeypatch,
        capsys,
    )

    assert code == 0
    assert payload["authenticated"] is True


def test_semantically_equivalent_json_has_same_envelope(
    tmp_path: pathlib.Path,
    tool_module: ModuleType,
    valid_verdict: dict[str, Any],
    secret_key: bytes,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    compact_path = tmp_path / "compact.json"
    pretty_path = tmp_path / "pretty.json"
    key_path = tmp_path / "key.bin"

    write_json(compact_path, valid_verdict)
    write_json(pretty_path, valid_verdict, pretty=True)
    key_path.write_bytes(secret_key)

    arguments = [
        "--key-file",
        str(key_path),
        "--key-id",
        "semantic-key",
    ]

    first_code, _, first_raw = invoke_tool(
        tool_module,
        [
            "seal",
            "--verdict",
            str(compact_path),
            *arguments,
        ],
        monkeypatch,
        capsys,
    )

    second_code, _, second_raw = invoke_tool(
        tool_module,
        [
            "seal",
            "--verdict",
            str(pretty_path),
            *arguments,
        ],
        monkeypatch,
        capsys,
    )

    assert first_code == 0
    assert second_code == 0
    assert first_raw == second_raw


def test_envelope_output_is_deterministic(
    tmp_path: pathlib.Path,
    tool_module: ModuleType,
    valid_verdict: dict[str, Any],
    secret_key: bytes,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    verdict_path = tmp_path / "verdict.json"
    key_path = tmp_path / "key.bin"

    write_json(verdict_path, valid_verdict)
    key_path.write_bytes(secret_key)

    arguments = [
        "seal",
        "--verdict",
        str(verdict_path),
        "--key-file",
        str(key_path),
        "--key-id",
        "deterministic-key",
    ]

    first_code, _, first_raw = invoke_tool(
        tool_module,
        arguments,
        monkeypatch,
        capsys,
    )

    second_code, _, second_raw = invoke_tool(
        tool_module,
        arguments,
        monkeypatch,
        capsys,
    )

    assert first_code == 0
    assert second_code == 0
    assert first_raw == second_raw


def test_wrong_key_is_rejected(
    tmp_path: pathlib.Path,
    tool_module: ModuleType,
    valid_verdict: dict[str, Any],
    secret_key: bytes,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    verdict_path, envelope_path, _ = create_valid_files(
        tmp_path,
        tool_module,
        valid_verdict,
        secret_key,
    )

    wrong_key_path = tmp_path / "wrong.key"
    wrong_key_path.write_bytes(b"W" * 32)

    code, payload, _ = invoke_tool(
        tool_module,
        [
            "verify",
            "--verdict",
            str(verdict_path),
            "--envelope",
            str(envelope_path),
            "--key-file",
            str(wrong_key_path),
            "--key-id",
            "local-key-01",
        ],
        monkeypatch,
        capsys,
    )

    assert code == 1
    assert payload["gate"] == "NO_GO"
    assert payload["error_code"] == "AUTHENTICATION_FAILED"


def test_key_id_mismatch_is_rejected(
    tmp_path: pathlib.Path,
    tool_module: ModuleType,
    valid_verdict: dict[str, Any],
    secret_key: bytes,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    verdict_path, envelope_path, key_path = (
        create_valid_files(
            tmp_path,
            tool_module,
            valid_verdict,
            secret_key,
        )
    )

    code, payload, _ = invoke_tool(
        tool_module,
        [
            "verify",
            "--verdict",
            str(verdict_path),
            "--envelope",
            str(envelope_path),
            "--key-file",
            str(key_path),
            "--key-id",
            "different-key-id",
        ],
        monkeypatch,
        capsys,
    )

    assert code == 1
    assert payload["error_code"] == "AUTHENTICATION_FAILED"


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("algorithm", "HMAC-SHA-512"),
        ("attestation_sha256", "2" * 64),
        ("attestation_version", "TAMPERED"),
        ("envelope_version", "TAMPERED"),
        ("key_id", "tampered-key"),
        ("mac_hex", "f" * 64),
        ("source_canonical_sha256", "3" * 64),
        ("verdict_canonical_sha256", "4" * 64),
        ("verifier_version", "TAMPERED"),
    ],
)
def test_tampered_envelope_fields_are_rejected(
    field: str,
    replacement: str,
    tmp_path: pathlib.Path,
    tool_module: ModuleType,
    valid_verdict: dict[str, Any],
    secret_key: bytes,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    verdict_path, _, key_path = create_valid_files(
        tmp_path,
        tool_module,
        valid_verdict,
        secret_key,
    )

    envelope = tool_module.build_envelope(
        valid_verdict,
        secret_key,
        "local-key-01",
    )
    envelope[field] = replacement

    envelope_path = tmp_path / f"tampered-{field}.json"
    write_json(envelope_path, envelope)

    code, payload, _ = invoke_tool(
        tool_module,
        [
            "verify",
            "--verdict",
            str(verdict_path),
            "--envelope",
            str(envelope_path),
            "--key-file",
            str(key_path),
            "--key-id",
            "local-key-01",
        ],
        monkeypatch,
        capsys,
    )

    assert code == 1
    assert payload["error_code"] == "AUTHENTICATION_FAILED"


@pytest.mark.parametrize(
    "change",
    ["extra", "missing"],
)
def test_extra_or_missing_envelope_field_is_rejected(
    change: str,
    tmp_path: pathlib.Path,
    tool_module: ModuleType,
    valid_verdict: dict[str, Any],
    secret_key: bytes,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    verdict_path, _, key_path = create_valid_files(
        tmp_path,
        tool_module,
        valid_verdict,
        secret_key,
    )

    envelope = tool_module.build_envelope(
        valid_verdict,
        secret_key,
        "local-key-01",
    )

    if change == "extra":
        envelope["unexpected"] = True
    else:
        envelope.pop("algorithm")

    envelope_path = tmp_path / f"{change}.json"
    write_json(envelope_path, envelope)

    code, payload, _ = invoke_tool(
        tool_module,
        [
            "verify",
            "--verdict",
            str(verdict_path),
            "--envelope",
            str(envelope_path),
            "--key-file",
            str(key_path),
            "--key-id",
            "local-key-01",
        ],
        monkeypatch,
        capsys,
    )

    assert code == 1
    assert payload["error_code"] == "AUTHENTICATION_FAILED"


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("verifier_version", "TAMPERED"),
        ("attestation_version", "TAMPERED"),
        ("gate", "NO_GO"),
        ("ok", False),
        ("verified", False),
        ("source_canonical_sha256", "ABC"),
        ("attestation_sha256", "F" * 64),
    ],
)
def test_invalid_b215_verdict_is_rejected(
    field: str,
    replacement: Any,
    tmp_path: pathlib.Path,
    tool_module: ModuleType,
    valid_verdict: dict[str, Any],
    secret_key: bytes,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    verdict = copy.deepcopy(valid_verdict)
    verdict[field] = replacement

    verdict_path = tmp_path / f"invalid-{field}.json"
    key_path = tmp_path / "key.bin"

    write_json(verdict_path, verdict)
    key_path.write_bytes(secret_key)

    code, payload, _ = invoke_tool(
        tool_module,
        [
            "seal",
            "--verdict",
            str(verdict_path),
            "--key-file",
            str(key_path),
            "--key-id",
            "local-key-01",
        ],
        monkeypatch,
        capsys,
    )

    assert code == 1
    assert payload["gate"] == "NO_GO"
    assert payload["error_code"] == "VERDICT_CONTRACT_INVALID"


@pytest.mark.parametrize(
    "change",
    ["extra", "missing"],
)
def test_extra_or_missing_verdict_field_is_rejected(
    change: str,
    tmp_path: pathlib.Path,
    tool_module: ModuleType,
    valid_verdict: dict[str, Any],
    secret_key: bytes,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    verdict = copy.deepcopy(valid_verdict)

    if change == "extra":
        verdict["unexpected"] = True
    else:
        verdict.pop("verified")

    verdict_path = tmp_path / f"{change}.json"
    key_path = tmp_path / "key.bin"

    write_json(verdict_path, verdict)
    key_path.write_bytes(secret_key)

    code, payload, _ = invoke_tool(
        tool_module,
        [
            "seal",
            "--verdict",
            str(verdict_path),
            "--key-file",
            str(key_path),
            "--key-id",
            "local-key-01",
        ],
        monkeypatch,
        capsys,
    )

    assert code == 1
    assert payload["error_code"] == "VERDICT_CONTRACT_INVALID"


def test_tampered_valid_verdict_is_not_authenticated(
    tmp_path: pathlib.Path,
    tool_module: ModuleType,
    valid_verdict: dict[str, Any],
    secret_key: bytes,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    original = copy.deepcopy(valid_verdict)
    envelope = tool_module.build_envelope(
        original,
        secret_key,
        "local-key-01",
    )

    tampered = copy.deepcopy(valid_verdict)
    tampered["source_canonical_sha256"] = "2" * 64

    verdict_path = tmp_path / "tampered-verdict.json"
    envelope_path = tmp_path / "original-envelope.json"
    key_path = tmp_path / "key.bin"

    write_json(verdict_path, tampered)
    write_json(envelope_path, envelope)
    key_path.write_bytes(secret_key)

    code, payload, _ = invoke_tool(
        tool_module,
        [
            "verify",
            "--verdict",
            str(verdict_path),
            "--envelope",
            str(envelope_path),
            "--key-file",
            str(key_path),
            "--key-id",
            "local-key-01",
        ],
        monkeypatch,
        capsys,
    )

    assert code == 1
    assert payload["error_code"] == "AUTHENTICATION_FAILED"


@pytest.mark.parametrize(
    "target",
    ["verdict", "envelope"],
)
def test_malformed_json_is_rejected(
    target: str,
    tmp_path: pathlib.Path,
    tool_module: ModuleType,
    valid_verdict: dict[str, Any],
    secret_key: bytes,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    verdict_path, envelope_path, key_path = (
        create_valid_files(
            tmp_path,
            tool_module,
            valid_verdict,
            secret_key,
        )
    )

    if target == "verdict":
        verdict_path.write_text("{", encoding="utf-8")
    else:
        envelope_path.write_text("{", encoding="utf-8")

    code, payload, _ = invoke_tool(
        tool_module,
        [
            "verify",
            "--verdict",
            str(verdict_path),
            "--envelope",
            str(envelope_path),
            "--key-file",
            str(key_path),
            "--key-id",
            "local-key-01",
        ],
        monkeypatch,
        capsys,
    )

    assert code == 2
    assert payload["gate"] == "INPUT_ERROR"
    assert payload["error_code"] == "INVALID_JSON"


@pytest.mark.parametrize(
    "target",
    ["verdict", "envelope"],
)
def test_json_array_is_rejected(
    target: str,
    tmp_path: pathlib.Path,
    tool_module: ModuleType,
    valid_verdict: dict[str, Any],
    secret_key: bytes,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    verdict_path, envelope_path, key_path = (
        create_valid_files(
            tmp_path,
            tool_module,
            valid_verdict,
            secret_key,
        )
    )

    path = (
        verdict_path
        if target == "verdict"
        else envelope_path
    )
    write_json(path, [])

    code, payload, _ = invoke_tool(
        tool_module,
        [
            "verify",
            "--verdict",
            str(verdict_path),
            "--envelope",
            str(envelope_path),
            "--key-file",
            str(key_path),
            "--key-id",
            "local-key-01",
        ],
        monkeypatch,
        capsys,
    )

    assert code == 2
    assert payload["error_code"] == "JSON_OBJECT_REQUIRED"


def test_duplicate_json_key_is_rejected(
    tmp_path: pathlib.Path,
    tool_module: ModuleType,
    secret_key: bytes,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    verdict_path = tmp_path / "duplicate.json"
    key_path = tmp_path / "key.bin"

    verdict_path.write_text(
        '{"gate":"GO","gate":"GO"}',
        encoding="utf-8",
    )
    key_path.write_bytes(secret_key)

    code, payload, _ = invoke_tool(
        tool_module,
        [
            "seal",
            "--verdict",
            str(verdict_path),
            "--key-file",
            str(key_path),
            "--key-id",
            "local-key-01",
        ],
        monkeypatch,
        capsys,
    )

    assert code == 2
    assert payload["error_code"] == "INVALID_JSON"


def test_non_finite_json_constant_is_rejected(
    tmp_path: pathlib.Path,
    tool_module: ModuleType,
    secret_key: bytes,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    verdict_path = tmp_path / "nan.json"
    key_path = tmp_path / "key.bin"

    verdict_path.write_text(
        '{"value":NaN}',
        encoding="utf-8",
    )
    key_path.write_bytes(secret_key)

    code, payload, _ = invoke_tool(
        tool_module,
        [
            "seal",
            "--verdict",
            str(verdict_path),
            "--key-file",
            str(key_path),
            "--key-id",
            "local-key-01",
        ],
        monkeypatch,
        capsys,
    )

    assert code == 2
    assert payload["error_code"] == "INVALID_JSON"


@pytest.mark.parametrize(
    "target",
    ["verdict", "envelope", "key"],
)
def test_missing_file_is_rejected(
    target: str,
    tmp_path: pathlib.Path,
    tool_module: ModuleType,
    valid_verdict: dict[str, Any],
    secret_key: bytes,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    verdict_path, envelope_path, key_path = (
        create_valid_files(
            tmp_path,
            tool_module,
            valid_verdict,
            secret_key,
        )
    )

    missing = tmp_path / "missing"

    if target == "verdict":
        verdict_path = missing
    elif target == "envelope":
        envelope_path = missing
    else:
        key_path = missing

    code, payload, _ = invoke_tool(
        tool_module,
        [
            "verify",
            "--verdict",
            str(verdict_path),
            "--envelope",
            str(envelope_path),
            "--key-file",
            str(key_path),
            "--key-id",
            "local-key-01",
        ],
        monkeypatch,
        capsys,
    )

    assert code == 2
    assert payload["error_code"] == "FILE_READ_ERROR"


@pytest.mark.parametrize(
    "target",
    ["verdict", "envelope", "key"],
)
def test_oversized_input_is_rejected(
    target: str,
    tmp_path: pathlib.Path,
    tool_module: ModuleType,
    valid_verdict: dict[str, Any],
    secret_key: bytes,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    verdict_path, envelope_path, key_path = (
        create_valid_files(
            tmp_path,
            tool_module,
            valid_verdict,
            secret_key,
        )
    )

    if target == "verdict":
        verdict_path.write_bytes(b" " * 1_048_577)
    elif target == "envelope":
        envelope_path.write_bytes(b" " * 65_537)
    else:
        key_path.write_bytes(b"K" * 4_097)

    code, payload, _ = invoke_tool(
        tool_module,
        [
            "verify",
            "--verdict",
            str(verdict_path),
            "--envelope",
            str(envelope_path),
            "--key-file",
            str(key_path),
            "--key-id",
            "local-key-01",
        ],
        monkeypatch,
        capsys,
    )

    assert code == 2
    assert payload["error_code"] == "INPUT_TOO_LARGE"


def test_short_key_is_rejected(
    tmp_path: pathlib.Path,
    tool_module: ModuleType,
    valid_verdict: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    verdict_path = tmp_path / "verdict.json"
    key_path = tmp_path / "short.key"

    write_json(verdict_path, valid_verdict)
    key_path.write_bytes(b"K" * 31)

    code, payload, _ = invoke_tool(
        tool_module,
        [
            "seal",
            "--verdict",
            str(verdict_path),
            "--key-file",
            str(key_path),
            "--key-id",
            "local-key-01",
        ],
        monkeypatch,
        capsys,
    )

    assert code == 2
    assert payload["error_code"] == "KEY_TOO_SHORT"


@pytest.mark.parametrize(
    "key_size",
    [32, 4096],
)
def test_key_boundary_sizes_are_accepted(
    key_size: int,
    tmp_path: pathlib.Path,
    tool_module: ModuleType,
    valid_verdict: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    verdict_path = tmp_path / "verdict.json"
    key_path = tmp_path / f"key-{key_size}.bin"

    write_json(verdict_path, valid_verdict)
    key_path.write_bytes(b"K" * key_size)

    code, payload, _ = invoke_tool(
        tool_module,
        [
            "seal",
            "--verdict",
            str(verdict_path),
            "--key-file",
            str(key_path),
            "--key-id",
            f"boundary-{key_size}",
        ],
        monkeypatch,
        capsys,
    )

    assert code == 0
    assert len(payload["mac_hex"]) == 64


def test_key_whitespace_is_significant(
    tmp_path: pathlib.Path,
    tool_module: ModuleType,
    valid_verdict: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    key_without_newline = b"K" * 32
    key_with_newline = key_without_newline + b"\n"

    verdict_path = tmp_path / "verdict.json"
    envelope_path = tmp_path / "envelope.json"
    key_path = tmp_path / "key-newline.bin"

    write_json(verdict_path, valid_verdict)

    envelope = tool_module.build_envelope(
        valid_verdict,
        key_without_newline,
        "newline-key",
    )
    write_json(envelope_path, envelope)
    key_path.write_bytes(key_with_newline)

    code, payload, _ = invoke_tool(
        tool_module,
        [
            "verify",
            "--verdict",
            str(verdict_path),
            "--envelope",
            str(envelope_path),
            "--key-file",
            str(key_path),
            "--key-id",
            "newline-key",
        ],
        monkeypatch,
        capsys,
    )

    assert code == 1
    assert payload["error_code"] == "AUTHENTICATION_FAILED"


@pytest.mark.parametrize(
    "key_id",
    [
        "",
        "-leading",
        "space key",
        "x" * 65,
    ],
)
def test_invalid_key_id_is_rejected(
    key_id: str,
    tmp_path: pathlib.Path,
    tool_module: ModuleType,
    valid_verdict: dict[str, Any],
    secret_key: bytes,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    verdict_path = tmp_path / "verdict.json"
    key_path = tmp_path / "key.bin"

    write_json(verdict_path, valid_verdict)
    key_path.write_bytes(secret_key)

    code, payload, _ = invoke_tool(
        tool_module,
        [
            "seal",
            "--verdict",
            str(verdict_path),
            "--key-file",
            str(key_path),
            f"--key-id={key_id}",
        ],
        monkeypatch,
        capsys,
    )

    assert code == 2
    assert payload["error_code"] == "INVALID_KEY_ID"


def test_missing_verify_envelope_is_rejected(
    tool_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, payload, _ = invoke_tool(
        tool_module,
        [
            "verify",
            "--verdict",
            "verdict.json",
            "--key-file",
            "key.bin",
            "--key-id",
            "local-key-01",
        ],
        monkeypatch,
        capsys,
    )

    assert code == 2
    assert payload["error_code"] == "ARGUMENT_ERROR"


def test_unknown_argument_has_no_traceback(
    tool_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, payload, raw = invoke_tool(
        tool_module,
        [
            "seal",
            "--verdict",
            "verdict.json",
            "--key-file",
            "key.bin",
            "--key-id",
            "local-key-01",
            "--unknown",
            "value",
        ],
        monkeypatch,
        capsys,
    )

    assert code == 2
    assert payload["error_code"] == "ARGUMENT_ERROR"
    assert "Traceback" not in raw


def test_secret_key_is_never_emitted(
    tmp_path: pathlib.Path,
    tool_module: ModuleType,
    valid_verdict: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = b"UNIQUE-B216-SECRET-KEY-MATERIAL-0001"

    verdict_path = tmp_path / "verdict.json"
    key_path = tmp_path / "key.bin"

    write_json(verdict_path, valid_verdict)
    key_path.write_bytes(secret)

    code, payload, raw = invoke_tool(
        tool_module,
        [
            "seal",
            "--verdict",
            str(verdict_path),
            "--key-file",
            str(key_path),
            "--key-id",
            "secret-output-test",
        ],
        monkeypatch,
        capsys,
    )

    assert code == 0
    assert secret.decode("ascii") not in raw
    assert "secret" not in payload
    assert "key" not in payload


def test_domain_and_key_id_are_bound_to_mac(
    tool_module: ModuleType,
    valid_verdict: dict[str, Any],
    secret_key: bytes,
) -> None:
    first = tool_module.compute_mac_hex(
        valid_verdict,
        secret_key,
        "key-one",
    )
    second = tool_module.compute_mac_hex(
        valid_verdict,
        secret_key,
        "key-two",
    )

    alternate_payload = (
        tool_module.build_protected_payload(
            valid_verdict,
            "key-one",
        )
    )
    alternate_payload["domain"] = "OTHER_DOMAIN"

    alternate = hmac.new(
        secret_key,
        tool_module.canonical_json_bytes(
            alternate_payload
        ),
        hashlib.sha256,
    ).hexdigest()

    assert first != second
    assert first != alternate


def test_static_contract_uses_compare_digest() -> None:
    source = TOOL_PATH.read_text(encoding="utf-8-sig")
    tree = ast.parse(source)

    compare_digest_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "hmac"
        and node.func.attr == "compare_digest"
    ]

    assert len(compare_digest_calls) == 1
    assert "GPI_B2_16_CLOUD_AUDIT_VERDICT_HMAC_V1" in source


def test_static_contract_has_no_network_or_subprocess() -> None:
    source = TOOL_PATH.read_text(encoding="utf-8-sig")
    tree = ast.parse(source)

    imported_roots = set()

    for node in tree.body:
        if isinstance(node, ast.Import):
            imported_roots.update(
                alias.name.split(".")[0]
                for alias in node.names
            )

        if isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])

    assert "subprocess" not in imported_roots
    assert "requests" not in imported_roots
    assert "httpx" not in imported_roots
    assert "urllib" not in imported_roots
    assert "socket" not in imported_roots
    assert "secrets" not in imported_roots
    assert "os" not in imported_roots


def test_static_contract_has_no_file_writes() -> None:
    source = TOOL_PATH.read_text(encoding="utf-8-sig")
    tree = ast.parse(source)

    forbidden_calls = {
        "open",
        "write",
        "write_text",
        "write_bytes",
        "unlink",
        "rename",
        "replace",
    }

    detected = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        else:
            continue

        if name in forbidden_calls:
            detected.append(name)

    assert detected == []


def test_previous_components_are_not_referenced() -> None:
    source = TOOL_PATH.read_text(encoding="utf-8-sig")

    assert "api_main.py" not in source
    assert "audit_scheduler_b212_cycles.py" not in source
    assert "check_scheduler_b213_cloud_cycles.py" not in source
    assert "attest_scheduler_b214_cloud_audit.py" not in source
    assert "verify_scheduler_b215_cloud_audit_attestation.py" not in source
    assert "/scheduler/run-due-h5" not in source
    assert "gcloud logging read" not in source