from __future__ import annotations

import hashlib
import json
import pathlib
import re
import sys
from typing import Any, BinaryIO, Sequence, TextIO

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )
except Exception as dependency_exception:  # pragma: no cover - exercised by isolation test
    DEPENDENCY_ERROR: Exception | None = dependency_exception
    InvalidSignature = Exception  # type: ignore[assignment,misc]
    serialization = None  # type: ignore[assignment]
    Ed25519PrivateKey = None  # type: ignore[assignment]
    Ed25519PublicKey = None  # type: ignore[assignment]
else:
    DEPENDENCY_ERROR = None


TOOL_VERSION = "B2_17_CLOUD_AUDIT_VERDICT_ED25519_TOOL_V1"
SIGNATURE_VERSION = "B2_17_CLOUD_AUDIT_VERDICT_ED25519_V1"
VERIFICATION_VERSION = "B2_17_CLOUD_AUDIT_VERDICT_ED25519_VERIFICATION_V1"
SOURCE_VERIFICATION_VERSION = "B2_16_CLOUD_AUDIT_VERDICT_HMAC_VERIFICATION_V1"
ALGORITHM = "Ed25519"
DOMAIN = "GPI_B2_17_CLOUD_AUDIT_VERDICT_ED25519_V1"

VERDICT_MAX_BYTES = 1_048_576
SIGNATURE_MAX_BYTES = 65_536
PRIVATE_KEY_BYTES = 32
PUBLIC_KEY_BYTES = 32

HEX_64_PATTERN = re.compile(r"^[0-9a-f]{64}$")
HEX_128_PATTERN = re.compile(r"^[0-9a-f]{128}$")
KEY_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

VERDICT_FIELDS = {
    "attestation_sha256",
    "authenticated",
    "gate",
    "key_id",
    "ok",
    "source_canonical_sha256",
    "verdict_canonical_sha256",
    "verification_version",
}
SIGNATURE_FIELDS = {
    "algorithm",
    "attestation_sha256",
    "hmac_key_id",
    "public_key_sha256",
    "signature_hex",
    "signature_version",
    "signing_key_id",
    "source_canonical_sha256",
    "source_verification_version",
    "verdict_canonical_sha256",
}


class InputFailure(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ContractFailure(Exception):
    pass


class SignatureFailure(Exception):
    pass


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def emit_json(value: dict[str, Any], stdout: TextIO) -> None:
    print(canonical_json_bytes(value).decode("utf-8"), file=stdout, flush=True)


def emit_help(stdout: TextIO) -> None:
    emit_json(
        {
            "ok": True,
            "signature_tool_version": TOOL_VERSION,
            "usage": (
                "sign_scheduler_b217_cloud_audit_verdict.py "
                "sign --verdict FILE|- --private-key-file FILE|- --signing-key-id ID | "
                "verify --verdict FILE|- --signature FILE|- --public-key-file FILE|- "
                "--signing-key-id ID"
            ),
        },
        stdout,
    )


def emit_failure(
    code: str,
    message: str,
    gate: str,
    stdout: TextIO,
) -> None:
    emit_json(
        {
            "error_code": code,
            "error_message": message,
            "gate": gate,
            "ok": False,
            "signature_tool_version": TOOL_VERSION,
        },
        stdout,
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InputFailure("DUPLICATE_JSON_KEY", "JSON document contains a duplicate key")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> None:
    raise InputFailure("NON_FINITE_JSON", "JSON document contains a non-finite value")


def _read_bytes(
    location: str,
    maximum_bytes: int,
    label: str,
    stdin: BinaryIO,
) -> bytes:
    try:
        if location == "-":
            data = stdin.read(maximum_bytes + 1)
        else:
            data = pathlib.Path(location).read_bytes()
    except OSError as exc:
        raise InputFailure("INPUT_READ_ERROR", f"{label} input cannot be read") from exc
    if not isinstance(data, bytes):
        raise InputFailure("INPUT_READ_ERROR", f"{label} input is not binary")
    if len(data) > maximum_bytes:
        raise InputFailure("INPUT_TOO_LARGE", f"{label} input exceeds the size limit")
    return data


def read_json_document(
    location: str,
    maximum_bytes: int,
    label: str,
    stdin: BinaryIO,
) -> dict[str, Any]:
    data = _read_bytes(location, maximum_bytes, label, stdin)
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InputFailure("INVALID_UTF8", f"{label} document is not valid UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_non_finite,
        )
    except InputFailure:
        raise
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise InputFailure("INVALID_JSON", f"{label} document is not valid JSON") from exc
    if not isinstance(value, dict):
        raise InputFailure("INVALID_JSON_OBJECT", f"{label} document must be a JSON object")
    return value


def read_raw_key(
    location: str,
    expected_bytes: int,
    error_code: str,
    label: str,
    stdin: BinaryIO,
) -> bytes:
    try:
        data = _read_bytes(location, expected_bytes, label, stdin)
    except InputFailure as exc:
        if exc.code == "INPUT_TOO_LARGE":
            raise InputFailure(
                error_code,
                f"{label} must contain exactly {expected_bytes} raw bytes",
            ) from exc
        raise
    if len(data) != expected_bytes:
        raise InputFailure(error_code, f"{label} must contain exactly {expected_bytes} raw bytes")
    return data


def validate_key_id(value: Any, label: str = "signing key identifier") -> str:
    if not isinstance(value, str) or KEY_ID_PATTERN.fullmatch(value) is None:
        raise InputFailure("INVALID_SIGNING_KEY_ID", f"{label} is invalid")
    return value


def validate_verdict(verdict: dict[str, Any]) -> None:
    if set(verdict) != VERDICT_FIELDS:
        raise ContractFailure("verdict is not a verified B2.16 GO document")
    if verdict.get("verification_version") != SOURCE_VERIFICATION_VERSION:
        raise ContractFailure("verdict is not a verified B2.16 GO document")
    if verdict.get("gate") != "GO":
        raise ContractFailure("verdict is not a verified B2.16 GO document")
    if verdict.get("ok") is not True or verdict.get("authenticated") is not True:
        raise ContractFailure("verdict is not a verified B2.16 GO document")
    if not isinstance(verdict.get("key_id"), str):
        raise ContractFailure("verdict is not a verified B2.16 GO document")
    if KEY_ID_PATTERN.fullmatch(verdict["key_id"]) is None:
        raise ContractFailure("verdict is not a verified B2.16 GO document")
    for field in (
        "attestation_sha256",
        "source_canonical_sha256",
        "verdict_canonical_sha256",
    ):
        value = verdict.get(field)
        if not isinstance(value, str) or HEX_64_PATTERN.fullmatch(value) is None:
            raise ContractFailure("verdict is not a verified B2.16 GO document")


def ensure_dependency() -> None:
    if DEPENDENCY_ERROR is not None:
        raise InputFailure(
            "DEPENDENCY_UNAVAILABLE",
            "Ed25519 dependency is unavailable",
        )


def load_private_key(raw_key: bytes) -> Any:
    ensure_dependency()
    try:
        return Ed25519PrivateKey.from_private_bytes(raw_key)
    except (TypeError, ValueError) as exc:
        raise InputFailure("INVALID_PRIVATE_KEY", "private key is invalid") from exc


def load_public_key(raw_key: bytes) -> Any:
    ensure_dependency()
    try:
        return Ed25519PublicKey.from_public_bytes(raw_key)
    except (TypeError, ValueError) as exc:
        raise InputFailure("INVALID_PUBLIC_KEY", "public key is invalid") from exc


def raw_public_key(public_key: Any) -> bytes:
    return public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def public_key_sha256(raw_key: bytes) -> str:
    return hashlib.sha256(raw_key).hexdigest()


def build_protected_payload(
    verdict: dict[str, Any],
    fingerprint: str,
    signing_key_id: str,
) -> dict[str, Any]:
    return {
        "algorithm": ALGORITHM,
        "domain": DOMAIN,
        "public_key_sha256": fingerprint,
        "signature_version": SIGNATURE_VERSION,
        "signing_key_id": signing_key_id,
        "verdict": verdict,
    }


def build_signature_envelope(
    verdict: dict[str, Any],
    raw_private_key: bytes,
    signing_key_id: str,
) -> dict[str, Any]:
    validate_verdict(verdict)
    validated_key_id = validate_key_id(signing_key_id)
    private_key = load_private_key(raw_private_key)
    public_raw = raw_public_key(private_key.public_key())
    fingerprint = public_key_sha256(public_raw)
    payload = build_protected_payload(verdict, fingerprint, validated_key_id)
    signature = private_key.sign(canonical_json_bytes(payload))
    return {
        "algorithm": ALGORITHM,
        "attestation_sha256": verdict["attestation_sha256"],
        "hmac_key_id": verdict["key_id"],
        "public_key_sha256": fingerprint,
        "signature_hex": signature.hex(),
        "signature_version": SIGNATURE_VERSION,
        "signing_key_id": validated_key_id,
        "source_canonical_sha256": verdict["source_canonical_sha256"],
        "source_verification_version": verdict["verification_version"],
        "verdict_canonical_sha256": canonical_sha256(verdict),
    }


def validate_signature_envelope(
    verdict: dict[str, Any],
    envelope: dict[str, Any],
    raw_public: bytes,
    signing_key_id: str,
) -> tuple[Any, str, bytes]:
    validate_verdict(verdict)
    validated_key_id = validate_key_id(signing_key_id)
    public_key = load_public_key(raw_public)
    fingerprint = public_key_sha256(raw_public)
    expected = {
        "algorithm": ALGORITHM,
        "attestation_sha256": verdict["attestation_sha256"],
        "hmac_key_id": verdict["key_id"],
        "public_key_sha256": fingerprint,
        "signature_version": SIGNATURE_VERSION,
        "signing_key_id": validated_key_id,
        "source_canonical_sha256": verdict["source_canonical_sha256"],
        "source_verification_version": verdict["verification_version"],
        "verdict_canonical_sha256": canonical_sha256(verdict),
    }
    if set(envelope) != SIGNATURE_FIELDS:
        raise SignatureFailure("signature envelope verification failed")
    for field, expected_value in expected.items():
        if envelope.get(field) != expected_value:
            raise SignatureFailure("signature envelope verification failed")
    signature_hex = envelope.get("signature_hex")
    if not isinstance(signature_hex, str) or HEX_128_PATTERN.fullmatch(signature_hex) is None:
        raise SignatureFailure("signature envelope verification failed")
    return public_key, fingerprint, bytes.fromhex(signature_hex)


def verify_signature_envelope(
    verdict: dict[str, Any],
    envelope: dict[str, Any],
    raw_public: bytes,
    signing_key_id: str,
) -> dict[str, Any]:
    public_key, fingerprint, signature = validate_signature_envelope(
        verdict,
        envelope,
        raw_public,
        signing_key_id,
    )
    payload = build_protected_payload(verdict, fingerprint, signing_key_id)
    try:
        public_key.verify(signature, canonical_json_bytes(payload))
    except InvalidSignature as exc:
        raise SignatureFailure("signature envelope verification failed") from exc
    return {
        "attestation_sha256": verdict["attestation_sha256"],
        "gate": "GO",
        "ok": True,
        "public_key_sha256": fingerprint,
        "signature_valid": True,
        "signing_key_id": signing_key_id,
        "source_canonical_sha256": verdict["source_canonical_sha256"],
        "verdict_canonical_sha256": canonical_sha256(verdict),
        "verification_version": VERIFICATION_VERSION,
    }


def parse_arguments(arguments: Sequence[str]) -> dict[str, str]:
    argv = list(arguments)
    if argv in (["--help"], ["-h"]):
        return {"mode": "help"}
    if not argv or argv[0] not in {"sign", "verify"}:
        raise InputFailure("ARGUMENT_ERROR", "invalid command line")
    mode = argv[0]
    allowed = {
        "sign": {"--verdict", "--private-key-file", "--signing-key-id"},
        "verify": {
            "--verdict",
            "--signature",
            "--public-key-file",
            "--signing-key-id",
        },
    }[mode]
    values: dict[str, str] = {"mode": mode}
    index = 1
    while index < len(argv):
        option = argv[index]
        if option not in allowed or option in values or index + 1 >= len(argv):
            raise InputFailure("ARGUMENT_ERROR", "invalid command line")
        values[option] = argv[index + 1]
        index += 2
    if set(values) != allowed | {"mode"}:
        raise InputFailure("ARGUMENT_ERROR", "invalid command line")
    stdin_count = sum(1 for option in allowed if values[option] == "-")
    if stdin_count > 1:
        raise InputFailure("ARGUMENT_ERROR", "at most one input may use stdin")
    return values


def run_cli(
    arguments: Sequence[str],
    stdin: BinaryIO,
    stdout: TextIO,
) -> int:
    try:
        parsed = parse_arguments(arguments)
        if parsed["mode"] == "help":
            emit_help(stdout)
            return 0
        ensure_dependency()
        signing_key_id = validate_key_id(parsed["--signing-key-id"])
        verdict = read_json_document(
            parsed["--verdict"],
            VERDICT_MAX_BYTES,
            "verdict",
            stdin,
        )
        validate_verdict(verdict)
        if parsed["mode"] == "sign":
            raw_private = read_raw_key(
                parsed["--private-key-file"],
                PRIVATE_KEY_BYTES,
                "INVALID_PRIVATE_KEY",
                "private key",
                stdin,
            )
            result = build_signature_envelope(verdict, raw_private, signing_key_id)
        else:
            envelope = read_json_document(
                parsed["--signature"],
                SIGNATURE_MAX_BYTES,
                "signature",
                stdin,
            )
            raw_public = read_raw_key(
                parsed["--public-key-file"],
                PUBLIC_KEY_BYTES,
                "INVALID_PUBLIC_KEY",
                "public key",
                stdin,
            )
            result = verify_signature_envelope(
                verdict,
                envelope,
                raw_public,
                signing_key_id,
            )
        emit_json(result, stdout)
        return 0
    except ContractFailure:
        emit_failure(
            "VERDICT_CONTRACT_INVALID",
            "verdict is not a verified B2.16 GO document",
            "NO_GO",
            stdout,
        )
        return 1
    except SignatureFailure:
        emit_failure(
            "SIGNATURE_INVALID",
            "signature envelope verification failed",
            "NO_GO",
            stdout,
        )
        return 1
    except InputFailure as exc:
        emit_failure(exc.code, exc.message, "INPUT_ERROR", stdout)
        return 2
    except Exception:
        emit_failure(
            "INTERNAL_ERROR",
            "local signature operation failed",
            "INPUT_ERROR",
            stdout,
        )
        return 2


def main(arguments: Sequence[str] | None = None) -> int:
    selected_arguments = sys.argv[1:] if arguments is None else list(arguments)
    return run_cli(selected_arguments, sys.stdin.buffer, sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
