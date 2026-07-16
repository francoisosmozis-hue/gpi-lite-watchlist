from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import pathlib
import re
import sys
from typing import Any, BinaryIO, NoReturn, Sequence

from verify_scheduler_b222_cloud_audit_verdict_validity_pin import (
    build_verdict_validity_pin_verdict,
)


TOOL_VERSION = "B2_23_CLOUD_AUDIT_VERDICT_VALIDITY_PIN_VALIDITY_TOOL_V1"
VALIDITY_VERSION = "B2_23_CLOUD_AUDIT_VERDICT_VALIDITY_PIN_VALIDITY_V1"
VERIFICATION_VERSION = (
    "B2_23_CLOUD_AUDIT_VERDICT_VALIDITY_PIN_VALIDITY_VERIFICATION_V1"
)
SOURCE_VERIFICATION_VERSION = (
    "B2_22_CLOUD_AUDIT_VERDICT_VALIDITY_PIN_VERIFICATION_V1"
)
SOURCE_SOURCE_VERIFICATION_VERSION = (
    "B2_21_CLOUD_AUDIT_VERDICT_VALIDITY_VERIFICATION_V1"
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

HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
UTC_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
)
VALIDITY_FIELDS = {
    "not_after_utc",
    "not_before_utc",
    "validity_version",
    "verdict_validity_pin_canonical_sha256",
}
B222_SUCCESS_FIELDS = {
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
}
TIME_OUTPUT_FIELDS = {
    "evaluation_time_utc",
    "not_after_utc",
    "not_before_utc",
    "verdict_not_after_utc",
    "verdict_not_before_utc",
}
USAGE = (
    "verify_scheduler_b223_cloud_audit_verdict_validity_pin_validity.py "
    "--verdict FILE|- --signature FILE|- --public-key-file FILE|- "
    "--trust-policy FILE|- --policy-pin FILE|- --policy-pin-validity FILE|- "
    "--verdict-validity FILE|- --verdict-validity-pin FILE|- "
    "--verdict-validity-pin-validity FILE|- --evaluation-time-utc UTC "
    "--signing-key-id ID"
)


class VerdictValidityPinValidityFailure(Exception):
    def __init__(self, code: str, message: str, gate: str, exit_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.gate = gate
        self.exit_code = exit_code


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise VerdictValidityPinValidityFailure(
            "ARGUMENT_ERROR",
            "invalid command arguments",
            "INPUT_ERROR",
            2,
        )


def _reject_duplicate_key(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise VerdictValidityPinValidityFailure(
                "INVALID_JSON",
                "verdict validity pin validity document contains duplicate keys",
                "INPUT_ERROR",
                2,
            )
        result[key] = value
    return result


def _reject_non_finite(value: str) -> NoReturn:
    raise VerdictValidityPinValidityFailure(
        "INVALID_JSON",
        "verdict validity pin validity document contains a non-finite number",
        "INPUT_ERROR",
        2,
    )


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise VerdictValidityPinValidityFailure(
            "VERDICT_VALIDITY_PIN_VALIDITY_CONTRACT_INVALID",
            "verdict validity pin validity document cannot be canonicalized",
            "INPUT_ERROR",
            2,
        ) from exc


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def parse_json_document(raw: bytes, label: str) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8-sig")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_key,
            parse_constant=_reject_non_finite,
        )
    except VerdictValidityPinValidityFailure:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerdictValidityPinValidityFailure(
            "INVALID_JSON",
            f"{label} is not valid JSON",
            "INPUT_ERROR",
            2,
        ) from exc
    if not isinstance(value, dict):
        raise VerdictValidityPinValidityFailure(
            "VERDICT_VALIDITY_PIN_VALIDITY_CONTRACT_INVALID",
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
        raise VerdictValidityPinValidityFailure(
            "READ_ERROR",
            f"cannot read {label}",
            "INPUT_ERROR",
            2,
        ) from exc
    if len(raw) > maximum_bytes:
        raise VerdictValidityPinValidityFailure(
            "INPUT_TOO_LARGE",
            f"{label} exceeds the maximum size",
            "INPUT_ERROR",
            2,
        )
    return raw


def parse_utc(value: Any, error_code: str, label: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_PATTERN.fullmatch(value) is None:
        raise VerdictValidityPinValidityFailure(
            error_code,
            f"{label} is not a strict UTC timestamp",
            "INPUT_ERROR",
            2,
        )
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise VerdictValidityPinValidityFailure(
            error_code,
            f"{label} is not a valid UTC timestamp",
            "INPUT_ERROR",
            2,
        ) from exc
    return parsed.replace(tzinfo=dt.timezone.utc)


def validate_validity_document(
    value: dict[str, Any],
) -> tuple[str, dt.datetime, dt.datetime]:
    if set(value) != VALIDITY_FIELDS:
        raise VerdictValidityPinValidityFailure(
            "VERDICT_VALIDITY_PIN_VALIDITY_CONTRACT_INVALID",
            "verdict validity pin validity document fields are invalid",
            "INPUT_ERROR",
            2,
        )
    if value.get("validity_version") != VALIDITY_VERSION:
        raise VerdictValidityPinValidityFailure(
            "VERDICT_VALIDITY_PIN_VALIDITY_CONTRACT_INVALID",
            "verdict validity pin validity document version is invalid",
            "INPUT_ERROR",
            2,
        )
    pinned_hash = value.get("verdict_validity_pin_canonical_sha256")
    if not isinstance(pinned_hash, str) or HASH_PATTERN.fullmatch(pinned_hash) is None:
        raise VerdictValidityPinValidityFailure(
            "VERDICT_VALIDITY_PIN_VALIDITY_CONTRACT_INVALID",
            "verdict validity pin hash is invalid",
            "INPUT_ERROR",
            2,
        )
    not_before = parse_utc(
        value.get("not_before_utc"),
        "VERDICT_VALIDITY_PIN_VALIDITY_CONTRACT_INVALID",
        "not_before_utc",
    )
    not_after = parse_utc(
        value.get("not_after_utc"),
        "VERDICT_VALIDITY_PIN_VALIDITY_CONTRACT_INVALID",
        "not_after_utc",
    )
    if not_before >= not_after:
        raise VerdictValidityPinValidityFailure(
            "VERDICT_VALIDITY_PIN_VALIDITY_CONTRACT_INVALID",
            "verdict validity pin validity window must have positive duration",
            "INPUT_ERROR",
            2,
        )
    canonical_json_bytes(value)
    return pinned_hash, not_before, not_after


def validate_source_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != B222_SUCCESS_FIELDS:
        raise VerdictValidityPinValidityFailure(
            "VERDICT_VALIDITY_PIN_VERIFICATION_FAILED",
            "B2.22 verdict validity pin verification returned invalid fields",
            "NO_GO",
            1,
        )
    required_true = (
        "authorized",
        "ok",
        "policy_pin_validity_verified",
        "policy_pin_verified",
        "signature_valid",
        "verdict_validity_pin_verified",
        "verdict_validity_verified",
    )
    if any(value.get(field) is not True for field in required_true):
        raise VerdictValidityPinValidityFailure(
            "VERDICT_VALIDITY_PIN_VERIFICATION_FAILED",
            "B2.22 verdict validity pin verification did not authorize the verdict",
            "NO_GO",
            1,
        )
    if value.get("gate") != "GO":
        raise VerdictValidityPinValidityFailure(
            "VERDICT_VALIDITY_PIN_VERIFICATION_FAILED",
            "B2.22 verdict validity pin gate is not GO",
            "NO_GO",
            1,
        )
    if value.get("verification_version") != SOURCE_VERIFICATION_VERSION:
        raise VerdictValidityPinValidityFailure(
            "VERDICT_VALIDITY_PIN_VERIFICATION_FAILED",
            "B2.22 verification version is invalid",
            "NO_GO",
            1,
        )
    if value.get("source_verification_version") != SOURCE_SOURCE_VERIFICATION_VERSION:
        raise VerdictValidityPinValidityFailure(
            "VERDICT_VALIDITY_PIN_VERIFICATION_FAILED",
            "B2.22 source verification version is invalid",
            "NO_GO",
            1,
        )
    for field in HASH_OUTPUT_FIELDS:
        field_value = value.get(field)
        if not isinstance(field_value, str) or HASH_PATTERN.fullmatch(field_value) is None:
            raise VerdictValidityPinValidityFailure(
                "VERDICT_VALIDITY_PIN_VERIFICATION_FAILED",
                f"B2.22 {field} is invalid",
                "NO_GO",
                1,
            )
    for field in TIME_OUTPUT_FIELDS:
        if not isinstance(value.get(field), str) or not value[field]:
            raise VerdictValidityPinValidityFailure(
                "VERDICT_VALIDITY_PIN_VERIFICATION_FAILED",
                f"B2.22 {field} is invalid",
                "NO_GO",
                1,
            )
    signing_key_id = value.get("signing_key_id")
    if not isinstance(signing_key_id, str) or not signing_key_id:
        raise VerdictValidityPinValidityFailure(
            "VERDICT_VALIDITY_PIN_VERIFICATION_FAILED",
            "B2.22 signing key identifier is invalid",
            "NO_GO",
            1,
        )
    return value


def build_verdict_validity_pin_validity_verdict(
    verdict: dict[str, Any],
    envelope: dict[str, Any],
    raw_public: bytes,
    policy: dict[str, Any],
    pin: dict[str, Any],
    policy_pin_validity: dict[str, Any],
    verdict_validity: dict[str, Any],
    verdict_validity_pin: dict[str, Any],
    verdict_validity_pin_validity: dict[str, Any],
    evaluation_time_utc: str,
    signing_key_id: str,
) -> dict[str, Any]:
    pinned_hash, not_before, not_after = validate_validity_document(
        verdict_validity_pin_validity
    )
    evaluation_time = parse_utc(
        evaluation_time_utc,
        "INVALID_EVALUATION_TIME",
        "evaluation_time_utc",
    )
    try:
        source_result = build_verdict_validity_pin_verdict(
            verdict,
            envelope,
            raw_public,
            policy,
            pin,
            policy_pin_validity,
            verdict_validity,
            verdict_validity_pin,
            evaluation_time_utc,
            signing_key_id,
        )
    except Exception as exc:
        raise VerdictValidityPinValidityFailure(
            "VERDICT_VALIDITY_PIN_VERIFICATION_FAILED",
            "B2.22 verdict validity pin verification failed",
            "NO_GO",
            1,
        ) from exc
    trusted = validate_source_result(source_result)
    if trusted["evaluation_time_utc"] != evaluation_time_utc:
        raise VerdictValidityPinValidityFailure(
            "VERDICT_VALIDITY_PIN_VERIFICATION_FAILED",
            "B2.22 evaluation time does not match the requested evaluation time",
            "NO_GO",
            1,
        )
    if trusted["signing_key_id"] != signing_key_id:
        raise VerdictValidityPinValidityFailure(
            "VERDICT_VALIDITY_PIN_VERIFICATION_FAILED",
            "B2.22 signing key identifier does not match the requested identifier",
            "NO_GO",
            1,
        )
    if not hmac.compare_digest(
        trusted["verdict_validity_pin_canonical_sha256"],
        pinned_hash,
    ):
        raise VerdictValidityPinValidityFailure(
            "VERDICT_VALIDITY_PIN_VALIDITY_MISMATCH",
            "verified verdict validity pin does not match the external validity document",
            "NO_GO",
            1,
        )
    if evaluation_time < not_before:
        raise VerdictValidityPinValidityFailure(
            "EVALUATION_BEFORE_VALIDITY_WINDOW",
            "evaluation time is before the verdict validity pin validity window",
            "NO_GO",
            1,
        )
    if evaluation_time > not_after:
        raise VerdictValidityPinValidityFailure(
            "EVALUATION_AFTER_VALIDITY_WINDOW",
            "evaluation time is after the verdict validity pin validity window",
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
        "verdict_validity_pin_not_after_utc": verdict_validity_pin_validity[
            "not_after_utc"
        ],
        "verdict_validity_pin_not_before_utc": verdict_validity_pin_validity[
            "not_before_utc"
        ],
        "verdict_validity_pin_validity_canonical_sha256": canonical_sha256(
            verdict_validity_pin_validity
        ),
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


def emit_failure(failure: VerdictValidityPinValidityFailure) -> None:
    emit_json(
        {
            "error_code": failure.code,
            "error_message": failure.message,
            "gate": failure.gate,
            "ok": False,
            "verdict_validity_pin_validity_tool_version": TOOL_VERSION,
        }
    )


def emit_help() -> None:
    emit_json(
        {
            "ok": True,
            "usage": USAGE,
            "verdict_validity_pin_validity_tool_version": TOOL_VERSION,
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
        )
        supplied = [getattr(parsed, name) for name in input_names]
        if parsed.help:
            if (
                any(value is not None for value in supplied)
                or parsed.evaluation_time_utc is not None
                or parsed.signing_key_id is not None
            ):
                raise VerdictValidityPinValidityFailure(
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
            raise VerdictValidityPinValidityFailure(
                "ARGUMENT_ERROR",
                "all verification inputs are required",
                "INPUT_ERROR",
                2,
            )
        if supplied.count("-") > 1:
            raise VerdictValidityPinValidityFailure(
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
        result = build_verdict_validity_pin_validity_verdict(
            verdict,
            envelope,
            raw_public,
            policy,
            pin,
            policy_pin_validity,
            verdict_validity,
            verdict_validity_pin,
            verdict_validity_pin_validity,
            parsed.evaluation_time_utc,
            parsed.signing_key_id,
        )
        emit_json(result)
        return 0
    except VerdictValidityPinValidityFailure as failure:
        emit_failure(failure)
        return failure.exit_code
    except Exception:
        failure = VerdictValidityPinValidityFailure(
            "VERDICT_VALIDITY_PIN_VALIDITY_CONTRACT_INVALID",
            "local verdict validity pin validity verification failed",
            "INPUT_ERROR",
            2,
        )
        emit_failure(failure)
        return failure.exit_code


if __name__ == "__main__":
    raise SystemExit(run_cli())
