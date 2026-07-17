from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import pathlib
import re
import sys
from typing import Any, BinaryIO, NoReturn, Sequence

from verify_scheduler_b223_cloud_audit_verdict_validity_pin_validity import (
    build_verdict_validity_pin_validity_verdict,
)


TOOL_VERSION = "B2_24_CLOUD_AUDIT_VERDICT_VALIDITY_PIN_VALIDITY_PIN_TOOL_V1"
PIN_VERSION = "B2_24_CLOUD_AUDIT_VERDICT_VALIDITY_PIN_VALIDITY_PIN_V1"
VERIFICATION_VERSION = "B2_24_CLOUD_AUDIT_VERDICT_VALIDITY_PIN_VALIDITY_PIN_VERIFICATION_V1"
SOURCE_VERIFICATION_VERSION = (
    "B2_23_CLOUD_AUDIT_VERDICT_VALIDITY_PIN_VALIDITY_VERIFICATION_V1"
)
SOURCE_SOURCE_VERIFICATION_VERSION = (
    "B2_22_CLOUD_AUDIT_VERDICT_VALIDITY_PIN_VERIFICATION_V1"
)

VERDICT_MAX_BYTES = 1_048_576
SIGNATURE_MAX_BYTES = 65_536
PUBLIC_KEY_MAX_BYTES = 4_096
POLICY_MAX_BYTES = 1_048_576
POLICY_PIN_MAX_BYTES = 65_536
POLICY_PIN_VALIDITY_MAX_BYTES = 65_536
VERDICT_VALIDITY_MAX_BYTES = 65_536
VERDICT_VALIDITY_PIN_MAX_BYTES = 65_536
VERDICT_VALIDITY_PIN_VALIDITY_MAX_BYTES = 65_536
VERDICT_VALIDITY_PIN_VALIDITY_PIN_MAX_BYTES = 65_536

HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
PIN_FIELDS = {
    "pin_version",
    "verdict_validity_pin_validity_canonical_sha256",
}
B223_SUCCESS_FIELDS = {
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
HASH_OUTPUT_FIELDS = {
    "attestation_sha256",
    "policy_canonical_sha256",
    "policy_pin_canonical_sha256",
    "policy_pin_validity_canonical_sha256",
    "public_key_sha256",
    "source_canonical_sha256",
    "verdict_canonical_sha256",
    "verdict_validity_canonical_sha256",
    "verdict_validity_pin_canonical_sha256",
    "verdict_validity_pin_validity_canonical_sha256",
}
TIME_OUTPUT_FIELDS = {
    "evaluation_time_utc",
    "not_after_utc",
    "not_before_utc",
    "verdict_not_after_utc",
    "verdict_not_before_utc",
    "verdict_validity_pin_not_after_utc",
    "verdict_validity_pin_not_before_utc",
}
USAGE = (
    "verify_scheduler_b224_cloud_audit_verdict_validity_pin_validity_pin.py "
    "--verdict FILE|- --signature FILE|- --public-key-file FILE|- "
    "--trust-policy FILE|- --policy-pin FILE|- --policy-pin-validity FILE|- "
    "--verdict-validity FILE|- --verdict-validity-pin FILE|- "
    "--verdict-validity-pin-validity FILE|- "
    "--verdict-validity-pin-validity-pin FILE|- "
    "--evaluation-time-utc UTC --signing-key-id ID"
)


class VerdictValidityPinValidityPinFailure(Exception):
    def __init__(self, code: str, message: str, gate: str, exit_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.gate = gate
        self.exit_code = exit_code


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise VerdictValidityPinValidityPinFailure(
            "ARGUMENT_ERROR",
            "invalid command arguments",
            "INPUT_ERROR",
            2,
        )


def _reject_duplicate_key(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise VerdictValidityPinValidityPinFailure(
                "INVALID_JSON",
                "verdict validity pin validity pin document contains duplicate keys",
                "INPUT_ERROR",
                2,
            )
        result[key] = value
    return result


def _reject_non_finite(value: str) -> NoReturn:
    raise VerdictValidityPinValidityPinFailure(
        "INVALID_JSON",
        "verdict validity pin validity pin document contains a non-finite number",
        "INPUT_ERROR",
        2,
    )


def canonical_json_bytes(value: dict[str, Any]) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise VerdictValidityPinValidityPinFailure(
            "VERDICT_VALIDITY_PIN_VALIDITY_PIN_CONTRACT_INVALID",
            "verdict validity pin validity pin document cannot be canonicalized",
            "INPUT_ERROR",
            2,
        ) from exc


def canonical_sha256(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def parse_json_document(raw: bytes, label: str) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8-sig")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_key,
            parse_constant=_reject_non_finite,
        )
    except VerdictValidityPinValidityPinFailure:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerdictValidityPinValidityPinFailure(
            "INVALID_JSON",
            f"{label} is not valid JSON",
            "INPUT_ERROR",
            2,
        ) from exc
    if not isinstance(value, dict):
        raise VerdictValidityPinValidityPinFailure(
            "VERDICT_VALIDITY_PIN_VALIDITY_PIN_CONTRACT_INVALID",
            f"{label} must be a JSON object",
            "INPUT_ERROR",
            2,
        )
    return value


def read_bytes(
    path: str,
    maximum_bytes: int,
    stdin_buffer: BinaryIO,
    label: str,
) -> bytes:
    try:
        if path == "-":
            raw = stdin_buffer.read(maximum_bytes + 1)
        else:
            with pathlib.Path(path).open("rb") as stream:
                raw = stream.read(maximum_bytes + 1)
    except (OSError, ValueError) as exc:
        raise VerdictValidityPinValidityPinFailure(
            "READ_ERROR",
            f"cannot read {label}",
            "INPUT_ERROR",
            2,
        ) from exc
    if len(raw) > maximum_bytes:
        raise VerdictValidityPinValidityPinFailure(
            "INPUT_TOO_LARGE",
            f"{label} exceeds the maximum size",
            "INPUT_ERROR",
            2,
        )
    return raw


def validate_pin_document(value: dict[str, Any]) -> str:
    if set(value) != PIN_FIELDS:
        raise VerdictValidityPinValidityPinFailure(
            "VERDICT_VALIDITY_PIN_VALIDITY_PIN_CONTRACT_INVALID",
            "verdict validity pin validity pin document fields are invalid",
            "INPUT_ERROR",
            2,
        )
    if value.get("pin_version") != PIN_VERSION:
        raise VerdictValidityPinValidityPinFailure(
            "VERDICT_VALIDITY_PIN_VALIDITY_PIN_CONTRACT_INVALID",
            "verdict validity pin validity pin document version is invalid",
            "INPUT_ERROR",
            2,
        )
    pinned_hash = value.get("verdict_validity_pin_validity_canonical_sha256")
    if not isinstance(pinned_hash, str) or HASH_PATTERN.fullmatch(pinned_hash) is None:
        raise VerdictValidityPinValidityPinFailure(
            "VERDICT_VALIDITY_PIN_VALIDITY_PIN_CONTRACT_INVALID",
            "verdict validity pin validity pin hash is invalid",
            "INPUT_ERROR",
            2,
        )
    canonical_json_bytes(value)
    return pinned_hash


def validate_source_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != B223_SUCCESS_FIELDS:
        raise VerdictValidityPinValidityPinFailure(
            "VERDICT_VALIDITY_PIN_VALIDITY_VERIFICATION_FAILED",
            "B2.23 verdict validity pin validity verification returned invalid fields",
            "NO_GO",
            1,
        )
    required_true = (
        "authorized",
        "ok",
        "policy_pin_validity_verified",
        "policy_pin_verified",
        "signature_valid",
        "verdict_validity_pin_validity_verified",
        "verdict_validity_pin_verified",
        "verdict_validity_verified",
    )
    if any(value.get(field) is not True for field in required_true):
        raise VerdictValidityPinValidityPinFailure(
            "VERDICT_VALIDITY_PIN_VALIDITY_VERIFICATION_FAILED",
            "B2.23 verdict validity pin validity verification did not authorize the verdict",
            "NO_GO",
            1,
        )
    if value.get("gate") != "GO":
        raise VerdictValidityPinValidityPinFailure(
            "VERDICT_VALIDITY_PIN_VALIDITY_VERIFICATION_FAILED",
            "B2.23 verdict validity pin validity gate is not GO",
            "NO_GO",
            1,
        )
    if value.get("verification_version") != SOURCE_VERIFICATION_VERSION:
        raise VerdictValidityPinValidityPinFailure(
            "VERDICT_VALIDITY_PIN_VALIDITY_VERIFICATION_FAILED",
            "B2.23 verification version is invalid",
            "NO_GO",
            1,
        )
    if value.get("source_verification_version") != SOURCE_SOURCE_VERIFICATION_VERSION:
        raise VerdictValidityPinValidityPinFailure(
            "VERDICT_VALIDITY_PIN_VALIDITY_VERIFICATION_FAILED",
            "B2.23 source verification version is invalid",
            "NO_GO",
            1,
        )
    for field in HASH_OUTPUT_FIELDS:
        field_value = value.get(field)
        if not isinstance(field_value, str) or HASH_PATTERN.fullmatch(field_value) is None:
            raise VerdictValidityPinValidityPinFailure(
                "VERDICT_VALIDITY_PIN_VALIDITY_VERIFICATION_FAILED",
                f"B2.23 {field} is invalid",
                "NO_GO",
                1,
            )
    for field in TIME_OUTPUT_FIELDS:
        if not isinstance(value.get(field), str) or not value[field]:
            raise VerdictValidityPinValidityPinFailure(
                "VERDICT_VALIDITY_PIN_VALIDITY_VERIFICATION_FAILED",
                f"B2.23 {field} is invalid",
                "NO_GO",
                1,
            )
    signing_key_id = value.get("signing_key_id")
    if not isinstance(signing_key_id, str) or not signing_key_id:
        raise VerdictValidityPinValidityPinFailure(
            "VERDICT_VALIDITY_PIN_VALIDITY_VERIFICATION_FAILED",
            "B2.23 signing key identifier is invalid",
            "NO_GO",
            1,
        )
    return value


def build_verdict_validity_pin_validity_pin_verdict(
    verdict: dict[str, Any],
    envelope: dict[str, Any],
    raw_public: bytes,
    policy: dict[str, Any],
    pin: dict[str, Any],
    policy_pin_validity: dict[str, Any],
    verdict_validity: dict[str, Any],
    verdict_validity_pin: dict[str, Any],
    verdict_validity_pin_validity: dict[str, Any],
    verdict_validity_pin_validity_pin: dict[str, Any],
    evaluation_time_utc: str,
    signing_key_id: str,
) -> dict[str, Any]:
    pinned_hash = validate_pin_document(verdict_validity_pin_validity_pin)
    try:
        source_result = build_verdict_validity_pin_validity_verdict(
            verdict,
            envelope,
            raw_public,
            policy,
            pin,
            policy_pin_validity,
            verdict_validity,
            verdict_validity_pin,
            verdict_validity_pin_validity,
            evaluation_time_utc,
            signing_key_id,
        )
    except Exception as exc:
        raise VerdictValidityPinValidityPinFailure(
            "VERDICT_VALIDITY_PIN_VALIDITY_VERIFICATION_FAILED",
            "B2.23 verdict validity pin validity verification failed",
            "NO_GO",
            1,
        ) from exc
    trusted = validate_source_result(source_result)
    if trusted["evaluation_time_utc"] != evaluation_time_utc:
        raise VerdictValidityPinValidityPinFailure(
            "VERDICT_VALIDITY_PIN_VALIDITY_VERIFICATION_FAILED",
            "B2.23 evaluation time does not match the requested evaluation time",
            "NO_GO",
            1,
        )
    if trusted["signing_key_id"] != signing_key_id:
        raise VerdictValidityPinValidityPinFailure(
            "VERDICT_VALIDITY_PIN_VALIDITY_VERIFICATION_FAILED",
            "B2.23 signing key identifier does not match the requested identifier",
            "NO_GO",
            1,
        )
    if not hmac.compare_digest(
        trusted["verdict_validity_pin_validity_canonical_sha256"],
        pinned_hash,
    ):
        raise VerdictValidityPinValidityPinFailure(
            "VERDICT_VALIDITY_PIN_VALIDITY_PIN_MISMATCH",
            "verified verdict validity pin validity does not match the external pin",
            "NO_GO",
            1,
        )
    return {
        "attestation_sha256": trusted["attestation_sha256"],
        "authorized": True,
        "evaluation_time_utc": trusted["evaluation_time_utc"],
        "gate": "GO",
        "not_after_utc": trusted["not_after_utc"],
        "not_before_utc": trusted["not_before_utc"],
        "ok": True,
        "policy_canonical_sha256": trusted["policy_canonical_sha256"],
        "policy_pin_canonical_sha256": trusted["policy_pin_canonical_sha256"],
        "policy_pin_validity_canonical_sha256": trusted[
            "policy_pin_validity_canonical_sha256"
        ],
        "policy_pin_validity_verified": True,
        "policy_pin_verified": True,
        "public_key_sha256": trusted["public_key_sha256"],
        "signature_valid": True,
        "signing_key_id": trusted["signing_key_id"],
        "source_canonical_sha256": trusted["source_canonical_sha256"],
        "source_verification_version": SOURCE_VERIFICATION_VERSION,
        "verdict_canonical_sha256": trusted["verdict_canonical_sha256"],
        "verdict_not_after_utc": trusted["verdict_not_after_utc"],
        "verdict_not_before_utc": trusted["verdict_not_before_utc"],
        "verdict_validity_canonical_sha256": trusted[
            "verdict_validity_canonical_sha256"
        ],
        "verdict_validity_pin_canonical_sha256": trusted[
            "verdict_validity_pin_canonical_sha256"
        ],
        "verdict_validity_pin_not_after_utc": trusted[
            "verdict_validity_pin_not_after_utc"
        ],
        "verdict_validity_pin_not_before_utc": trusted[
            "verdict_validity_pin_not_before_utc"
        ],
        "verdict_validity_pin_validity_canonical_sha256": trusted[
            "verdict_validity_pin_validity_canonical_sha256"
        ],
        "verdict_validity_pin_validity_pin_canonical_sha256": canonical_sha256(
            verdict_validity_pin_validity_pin
        ),
        "verdict_validity_pin_validity_pin_verified": True,
        "verdict_validity_pin_validity_verified": True,
        "verdict_validity_pin_verified": True,
        "verdict_validity_verified": True,
        "verification_version": VERIFICATION_VERSION,
    }


def emit_json(value: dict[str, Any]) -> None:
    print(
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )


def emit_failure(failure: VerdictValidityPinValidityPinFailure) -> None:
    emit_json(
        {
            "error_code": failure.code,
            "error_message": failure.message,
            "gate": failure.gate,
            "ok": False,
            "verdict_validity_pin_validity_pin_tool_version": TOOL_VERSION,
        }
    )


def emit_help() -> None:
    emit_json(
        {
            "ok": True,
            "usage": USAGE,
            "verdict_validity_pin_validity_pin_tool_version": TOOL_VERSION,
        }
    )


def parse_arguments(arguments: Sequence[str]) -> argparse.Namespace:
    parser = SafeArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--help", action="store_true")
    parser.add_argument("--verdict")
    parser.add_argument("--signature")
    parser.add_argument("--public-key-file")
    parser.add_argument("--trust-policy")
    parser.add_argument("--policy-pin")
    parser.add_argument("--policy-pin-validity")
    parser.add_argument("--verdict-validity")
    parser.add_argument("--verdict-validity-pin")
    parser.add_argument("--verdict-validity-pin-validity")
    parser.add_argument("--verdict-validity-pin-validity-pin")
    parser.add_argument("--evaluation-time-utc")
    parser.add_argument("--signing-key-id")
    return parser.parse_args(list(arguments))


def run_cli(
    arguments: Sequence[str] | None = None,
    stdin_buffer: BinaryIO | None = None,
) -> int:
    selected_arguments = list(sys.argv[1:] if arguments is None else arguments)
    selected_stdin = sys.stdin.buffer if stdin_buffer is None else stdin_buffer
    try:
        parsed = parse_arguments(selected_arguments)
        input_names = (
            "verdict",
            "signature",
            "public_key_file",
            "trust_policy",
            "policy_pin",
            "policy_pin_validity",
            "verdict_validity",
            "verdict_validity_pin",
            "verdict_validity_pin_validity",
            "verdict_validity_pin_validity_pin",
        )
        supplied = [getattr(parsed, name) for name in input_names]
        if parsed.help:
            if (
                any(value is not None for value in supplied)
                or parsed.evaluation_time_utc is not None
                or parsed.signing_key_id is not None
            ):
                raise VerdictValidityPinValidityPinFailure(
                    "ARGUMENT_ERROR",
                    "help cannot be combined with inputs",
                    "INPUT_ERROR",
                    2,
                )
            emit_help()
            return 0
        if (
            any(value is None for value in supplied)
            or parsed.evaluation_time_utc is None
            or parsed.signing_key_id is None
        ):
            raise VerdictValidityPinValidityPinFailure(
                "ARGUMENT_ERROR",
                "all verification inputs are required",
                "INPUT_ERROR",
                2,
            )
        if supplied.count("-") > 1:
            raise VerdictValidityPinValidityPinFailure(
                "ARGUMENT_ERROR",
                "at most one file input may use stdin",
                "INPUT_ERROR",
                2,
            )
        verdict = parse_json_document(
            read_bytes(parsed.verdict, VERDICT_MAX_BYTES, selected_stdin, "verdict"),
            "verdict",
        )
        envelope = parse_json_document(
            read_bytes(
                parsed.signature,
                SIGNATURE_MAX_BYTES,
                selected_stdin,
                "signature envelope",
            ),
            "signature envelope",
        )
        raw_public = read_bytes(
            parsed.public_key_file,
            PUBLIC_KEY_MAX_BYTES,
            selected_stdin,
            "public key",
        )
        policy = parse_json_document(
            read_bytes(
                parsed.trust_policy,
                POLICY_MAX_BYTES,
                selected_stdin,
                "trust policy",
            ),
            "trust policy",
        )
        pin = parse_json_document(
            read_bytes(
                parsed.policy_pin,
                POLICY_PIN_MAX_BYTES,
                selected_stdin,
                "policy pin document",
            ),
            "policy pin document",
        )
        policy_pin_validity = parse_json_document(
            read_bytes(
                parsed.policy_pin_validity,
                POLICY_PIN_VALIDITY_MAX_BYTES,
                selected_stdin,
                "policy pin validity document",
            ),
            "policy pin validity document",
        )
        verdict_validity = parse_json_document(
            read_bytes(
                parsed.verdict_validity,
                VERDICT_VALIDITY_MAX_BYTES,
                selected_stdin,
                "verdict validity document",
            ),
            "verdict validity document",
        )
        verdict_validity_pin = parse_json_document(
            read_bytes(
                parsed.verdict_validity_pin,
                VERDICT_VALIDITY_PIN_MAX_BYTES,
                selected_stdin,
                "verdict validity pin document",
            ),
            "verdict validity pin document",
        )
        verdict_validity_pin_validity = parse_json_document(
            read_bytes(
                parsed.verdict_validity_pin_validity,
                VERDICT_VALIDITY_PIN_VALIDITY_MAX_BYTES,
                selected_stdin,
                "verdict validity pin validity document",
            ),
            "verdict validity pin validity document",
        )
        verdict_validity_pin_validity_pin = parse_json_document(
            read_bytes(
                parsed.verdict_validity_pin_validity_pin,
                VERDICT_VALIDITY_PIN_VALIDITY_PIN_MAX_BYTES,
                selected_stdin,
                "verdict validity pin validity pin document",
            ),
            "verdict validity pin validity pin document",
        )
        result = build_verdict_validity_pin_validity_pin_verdict(
            verdict,
            envelope,
            raw_public,
            policy,
            pin,
            policy_pin_validity,
            verdict_validity,
            verdict_validity_pin,
            verdict_validity_pin_validity,
            verdict_validity_pin_validity_pin,
            parsed.evaluation_time_utc,
            parsed.signing_key_id,
        )
        emit_json(result)
        return 0
    except VerdictValidityPinValidityPinFailure as failure:
        emit_failure(failure)
        return failure.exit_code
    except Exception:
        failure = VerdictValidityPinValidityPinFailure(
            "VERDICT_VALIDITY_PIN_VALIDITY_PIN_CONTRACT_INVALID",
            "local verdict validity pin validity pin verification failed",
            "INPUT_ERROR",
            2,
        )
        emit_failure(failure)
        return failure.exit_code


if __name__ == "__main__":
    raise SystemExit(run_cli())
