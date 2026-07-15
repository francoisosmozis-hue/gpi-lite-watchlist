"""Authorize a B2.17 Ed25519 verification against an external trust policy."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
from typing import Any, NoReturn, Sequence

from sign_scheduler_b217_cloud_audit_verdict import verify_signature_envelope


TRUST_TOOL_VERSION = "B2_18_CLOUD_AUDIT_VERDICT_TRUST_TOOL_V1"
TRUST_VERIFICATION_VERSION = "B2_18_CLOUD_AUDIT_VERDICT_TRUST_VERIFICATION_V1"
POLICY_VERSION = "B2_18_CLOUD_AUDIT_SIGNING_KEY_TRUST_POLICY_V1"
B217_VERIFICATION_VERSION = "B2_17_CLOUD_AUDIT_VERDICT_ED25519_VERIFICATION_V1"

USAGE = (
    "verify_scheduler_b218_cloud_audit_verdict_trust.py "
    "--verdict FILE|- --signature FILE|- --public-key-file FILE|- "
    "--trust-policy FILE|- --signing-key-id ID"
)

VERDICT_MAX_BYTES = 1_048_576
SIGNATURE_MAX_BYTES = 1_048_576
PUBLIC_KEY_MAX_BYTES = 65_536
POLICY_MAX_BYTES = 1_048_576
POLICY_MAX_ENTRIES = 256

KEY_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

POLICY_FIELDS = {"policy_version", "trusted_signing_keys"}
POLICY_ENTRY_FIELDS = {"public_key_sha256", "signing_key_id", "status"}
POLICY_STATUSES = {"ACTIVE", "REVOKED"}

B217_SUCCESS_FIELDS = {
    "attestation_sha256",
    "gate",
    "ok",
    "public_key_sha256",
    "signature_valid",
    "signing_key_id",
    "source_canonical_sha256",
    "verdict_canonical_sha256",
    "verification_version",
}


class DuplicateKeyError(ValueError):
    """Raised when a JSON object contains a duplicate member."""


class TrustToolFailure(Exception):
    """A deterministic user-facing failure."""

    def __init__(
        self,
        error_code: str,
        error_message: str,
        gate: str,
        exit_code: int,
    ) -> None:
        super().__init__(error_message)
        self.error_code = error_code
        self.error_message = error_message
        self.gate = gate
        self.exit_code = exit_code


class InputFailure(TrustToolFailure):
    """Input or command-line failure."""

    def __init__(self, error_code: str, error_message: str) -> None:
        super().__init__(error_code, error_message, "INPUT_ERROR", 2)


class NoGoFailure(TrustToolFailure):
    """Cryptographic or trust-policy rejection."""

    def __init__(self, error_code: str, error_message: str) -> None:
        super().__init__(error_code, error_message, "NO_GO", 1)


class SafeArgumentParser(argparse.ArgumentParser):
    """Argument parser that never writes usage or exits by itself."""

    def error(self, message: str) -> NoReturn:
        raise InputFailure("ARGUMENT_ERROR", "invalid command line")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(key)
        result[key] = value
    return result


def _reject_constant(value: str) -> NoReturn:
    raise ValueError("non-finite JSON constant")


def emit_json(payload: dict[str, Any]) -> None:
    print(
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    )


def emit_help() -> None:
    emit_json(
        {
            "ok": True,
            "trust_tool_version": TRUST_TOOL_VERSION,
            "usage": USAGE,
        }
    )


def emit_failure(failure: TrustToolFailure) -> None:
    emit_json(
        {
            "error_code": failure.error_code,
            "error_message": failure.error_message,
            "gate": failure.gate,
            "ok": False,
            "trust_tool_version": TRUST_TOOL_VERSION,
        }
    )


def canonical_json_bytes(value: Any) -> bytes:
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise InputFailure(
            "POLICY_CONTRACT_INVALID",
            "trust policy cannot be canonicalized",
        ) from exc
    return rendered.encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _read_stdin(maximum_bytes: int, label: str) -> bytes:
    stream = getattr(sys.stdin, "buffer", sys.stdin)
    data = stream.read(maximum_bytes + 1)
    if isinstance(data, str):
        data = data.encode("utf-8")
    if len(data) > maximum_bytes:
        raise InputFailure("INPUT_TOO_LARGE", f"{label} input is too large")
    return bytes(data)


def _read_file(location: str, maximum_bytes: int, label: str) -> bytes:
    path = pathlib.Path(location)
    try:
        if not path.is_file():
            raise InputFailure("READ_ERROR", f"{label} file cannot be read")
        if path.stat().st_size > maximum_bytes:
            raise InputFailure("INPUT_TOO_LARGE", f"{label} input is too large")
        data = path.read_bytes()
    except TrustToolFailure:
        raise
    except OSError as exc:
        raise InputFailure("READ_ERROR", f"{label} file cannot be read") from exc
    if len(data) > maximum_bytes:
        raise InputFailure("INPUT_TOO_LARGE", f"{label} input is too large")
    return data


def read_bytes(location: str, maximum_bytes: int, label: str) -> bytes:
    if location == "-":
        return _read_stdin(maximum_bytes, label)
    return _read_file(location, maximum_bytes, label)


def parse_json_document(raw: bytes, label: str) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InputFailure("INVALID_JSON", f"{label} document is not valid JSON") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (DuplicateKeyError, json.JSONDecodeError, ValueError) as exc:
        raise InputFailure("INVALID_JSON", f"{label} document is not valid JSON") from exc
    if not isinstance(value, dict):
        raise InputFailure("INVALID_JSON", f"{label} document must be a JSON object")
    return value


def validate_signing_key_id(value: Any) -> str:
    if not isinstance(value, str) or KEY_ID_PATTERN.fullmatch(value) is None:
        raise InputFailure(
            "POLICY_CONTRACT_INVALID",
            "trust policy contains an invalid signing key identifier",
        )
    return value


def validate_policy(policy: dict[str, Any]) -> list[dict[str, str]]:
    if set(policy) != POLICY_FIELDS:
        raise InputFailure(
            "POLICY_CONTRACT_INVALID",
            "trust policy fields are invalid",
        )
    if policy.get("policy_version") != POLICY_VERSION:
        raise InputFailure(
            "POLICY_CONTRACT_INVALID",
            "trust policy version is invalid",
        )
    entries = policy.get("trusted_signing_keys")
    if not isinstance(entries, list):
        raise InputFailure(
            "POLICY_CONTRACT_INVALID",
            "trusted signing keys must be a list",
        )
    if len(entries) > POLICY_MAX_ENTRIES:
        raise InputFailure(
            "POLICY_CONTRACT_INVALID",
            "trust policy contains too many entries",
        )
    validated: list[dict[str, str]] = []
    seen_key_ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != POLICY_ENTRY_FIELDS:
            raise InputFailure(
                "POLICY_CONTRACT_INVALID",
                "trust policy entry fields are invalid",
            )
        key_id = validate_signing_key_id(entry.get("signing_key_id"))
        fingerprint = entry.get("public_key_sha256")
        status = entry.get("status")
        if not isinstance(fingerprint, str) or SHA256_PATTERN.fullmatch(fingerprint) is None:
            raise InputFailure(
                "POLICY_CONTRACT_INVALID",
                "trust policy contains an invalid public key fingerprint",
            )
        if status not in POLICY_STATUSES:
            raise InputFailure(
                "POLICY_CONTRACT_INVALID",
                "trust policy contains an invalid key status",
            )
        if key_id in seen_key_ids:
            raise InputFailure(
                "POLICY_CONTRACT_INVALID",
                "trust policy contains a duplicate signing key identifier",
            )
        seen_key_ids.add(key_id)
        validated.append(
            {
                "public_key_sha256": fingerprint,
                "signing_key_id": key_id,
                "status": status,
            }
        )
    return validated


def validate_b217_success(payload: Any, signing_key_id: str) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != B217_SUCCESS_FIELDS:
        raise NoGoFailure(
            "SIGNATURE_INVALID",
            "B2.17 signature verification did not return a valid GO verdict",
        )
    required_values = {
        "gate": "GO",
        "ok": True,
        "signature_valid": True,
        "signing_key_id": signing_key_id,
        "verification_version": B217_VERIFICATION_VERSION,
    }
    for name, expected in required_values.items():
        if payload.get(name) != expected:
            raise NoGoFailure(
                "SIGNATURE_INVALID",
                "B2.17 signature verification did not return a valid GO verdict",
            )
    for name in (
        "attestation_sha256",
        "public_key_sha256",
        "source_canonical_sha256",
        "verdict_canonical_sha256",
    ):
        value = payload.get(name)
        if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
            raise NoGoFailure(
                "SIGNATURE_INVALID",
                "B2.17 signature verification did not return a valid GO verdict",
            )
    return payload


def delegate_b217_verification(
    verdict: dict[str, Any],
    envelope: dict[str, Any],
    raw_public: bytes,
    signing_key_id: str,
) -> dict[str, Any]:
    try:
        result = verify_signature_envelope(
            verdict,
            envelope,
            raw_public,
            signing_key_id,
        )
    except Exception as exc:
        exception_name = type(exc).__name__
        if exception_name == "SignatureFailure":
            raise NoGoFailure(
                "SIGNATURE_INVALID",
                "B2.17 signature verification failed",
            ) from exc
        if exception_name == "ContractFailure":
            raise NoGoFailure(
                "VERDICT_CONTRACT_INVALID",
                "B2.17 verdict or signature contract is invalid",
            ) from exc
        if exception_name == "InputFailure":
            raise InputFailure(
                "READ_ERROR",
                "B2.17 public key input is invalid",
            ) from exc
        raise
    return validate_b217_success(result, signing_key_id)


def authorize_signing_key(
    b217_result: dict[str, Any],
    policy_entries: list[dict[str, str]],
) -> None:
    signing_key_id = b217_result["signing_key_id"]
    matches = [
        entry
        for entry in policy_entries
        if entry["signing_key_id"] == signing_key_id
    ]
    if not matches:
        raise NoGoFailure(
            "SIGNING_KEY_NOT_TRUSTED",
            "signing key is not authorized by the trust policy",
        )
    entry = matches[0]
    if entry["status"] == "REVOKED":
        raise NoGoFailure(
            "SIGNING_KEY_REVOKED",
            "signing key is revoked by the trust policy",
        )
    if entry["public_key_sha256"] != b217_result["public_key_sha256"]:
        raise NoGoFailure(
            "PUBLIC_KEY_NOT_TRUSTED",
            "public key fingerprint is not authorized by the trust policy",
        )


def build_trust_verdict(
    verdict: dict[str, Any],
    envelope: dict[str, Any],
    raw_public: bytes,
    policy: dict[str, Any],
    signing_key_id: str,
) -> dict[str, Any]:
    entries = validate_policy(policy)
    b217_result = delegate_b217_verification(
        verdict,
        envelope,
        raw_public,
        signing_key_id,
    )
    authorize_signing_key(b217_result, entries)
    return {
        "attestation_sha256": b217_result["attestation_sha256"],
        "authorized": True,
        "gate": "GO",
        "ok": True,
        "policy_canonical_sha256": canonical_sha256(policy),
        "policy_version": POLICY_VERSION,
        "public_key_sha256": b217_result["public_key_sha256"],
        "signature_valid": True,
        "signing_key_id": b217_result["signing_key_id"],
        "source_canonical_sha256": b217_result["source_canonical_sha256"],
        "source_verification_version": b217_result["verification_version"],
        "verdict_canonical_sha256": b217_result["verdict_canonical_sha256"],
        "verification_version": TRUST_VERIFICATION_VERSION,
    }


def parse_arguments(arguments: Sequence[str]) -> argparse.Namespace:
    parser = SafeArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--help", action="store_true")
    parser.add_argument("--verdict")
    parser.add_argument("--signature")
    parser.add_argument("--public-key-file")
    parser.add_argument("--trust-policy")
    parser.add_argument("--signing-key-id")
    parsed = parser.parse_args(list(arguments))
    if parsed.help:
        if list(arguments) != ["--help"]:
            raise InputFailure("ARGUMENT_ERROR", "invalid command line")
        return parsed
    required = (
        parsed.verdict,
        parsed.signature,
        parsed.public_key_file,
        parsed.trust_policy,
        parsed.signing_key_id,
    )
    if any(value is None for value in required):
        raise InputFailure("ARGUMENT_ERROR", "invalid command line")
    if KEY_ID_PATTERN.fullmatch(parsed.signing_key_id) is None:
        raise InputFailure("ARGUMENT_ERROR", "invalid command line")
    stdin_inputs = sum(
        value == "-"
        for value in (
            parsed.verdict,
            parsed.signature,
            parsed.public_key_file,
            parsed.trust_policy,
        )
    )
    if stdin_inputs > 1:
        raise InputFailure(
            "ARGUMENT_ERROR",
            "at most one input may be read from stdin",
        )
    return parsed


def run_cli(arguments: Sequence[str] | None = None) -> int:
    selected = list(sys.argv[1:] if arguments is None else arguments)
    try:
        parsed = parse_arguments(selected)
        if parsed.help:
            emit_help()
            return 0
        verdict = parse_json_document(
            read_bytes(parsed.verdict, VERDICT_MAX_BYTES, "verdict"),
            "verdict",
        )
        envelope = parse_json_document(
            read_bytes(parsed.signature, SIGNATURE_MAX_BYTES, "signature"),
            "signature",
        )
        raw_public = read_bytes(
            parsed.public_key_file,
            PUBLIC_KEY_MAX_BYTES,
            "public key",
        )
        policy = parse_json_document(
            read_bytes(parsed.trust_policy, POLICY_MAX_BYTES, "trust policy"),
            "trust policy",
        )
        result = build_trust_verdict(
            verdict,
            envelope,
            raw_public,
            policy,
            parsed.signing_key_id,
        )
    except TrustToolFailure as failure:
        emit_failure(failure)
        return failure.exit_code
    emit_json(result)
    return 0


def main() -> None:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
