from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import pathlib
import re
import sys
from typing import Any, BinaryIO, NoReturn, Sequence

from verify_scheduler_b220_cloud_audit_trust_policy_pin_validity import (
    build_policy_pin_validity_verdict,
)


TOOL_VERSION = "B2_21_CLOUD_AUDIT_VERDICT_VALIDITY_TOOL_V1"
VERIFICATION_VERSION = "B2_21_CLOUD_AUDIT_VERDICT_VALIDITY_VERIFICATION_V1"
SOURCE_VERIFICATION_VERSION = "B2_20_CLOUD_AUDIT_TRUST_POLICY_PIN_VALIDITY_VERIFICATION_V1"
VALIDITY_VERSION = "B2_21_CLOUD_AUDIT_VERDICT_VALIDITY_V1"

VERDICT_MAX_BYTES = 1024 * 1024
SIGNATURE_MAX_BYTES = 65536
PUBLIC_KEY_MAX_BYTES = 4096
POLICY_MAX_BYTES = 1024 * 1024
PIN_MAX_BYTES = 65536
POLICY_PIN_VALIDITY_MAX_BYTES = 65536
VERDICT_VALIDITY_MAX_BYTES = 65536

HASH_PATTERN = re.compile(r"[0-9a-f]{64}")
UTC_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")

VALIDITY_FIELDS = {
    "not_after_utc",
    "not_before_utc",
    "validity_version",
    "verdict_canonical_sha256",
}

B220_SUCCESS_FIELDS = {
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
    "verification_version",
}

USAGE = (
    "verify_scheduler_b221_cloud_audit_verdict_validity.py "
    "--verdict FILE|- --signature FILE|- --public-key-file FILE|- "
    "--trust-policy FILE|- --policy-pin FILE|- --policy-pin-validity FILE|- "
    "--verdict-validity FILE|- --evaluation-time-utc UTC --signing-key-id ID"
)


class VerdictValidityFailure(Exception):
    def __init__(self, code: str, message: str, gate: str, exit_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.gate = gate
        self.exit_code = exit_code


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise VerdictValidityFailure("ARGUMENT_ERROR", "arguments are invalid", "INPUT_ERROR", 2)


def _reject_duplicate_key(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise VerdictValidityFailure("INVALID_JSON", "JSON document contains duplicate keys", "INPUT_ERROR", 2)
        result[key] = value
    return result


def _reject_non_finite(value: str) -> NoReturn:
    raise VerdictValidityFailure("INVALID_JSON", "JSON document contains a non-finite number", "INPUT_ERROR", 2)


def canonical_json_bytes(value: dict[str, Any]) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise VerdictValidityFailure(
            "VERDICT_VALIDITY_CONTRACT_INVALID",
            "verdict validity document is not canonical JSON",
            "INPUT_ERROR",
            2,
        ) from exc


def canonical_sha256(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def read_bytes(path_value: str, maximum: int, stdin_buffer: BinaryIO, label: str) -> bytes:
    try:
        if path_value == "-":
            raw = stdin_buffer.read(maximum + 1)
        else:
            with pathlib.Path(path_value).open("rb") as handle:
                raw = handle.read(maximum + 1)
    except OSError as exc:
        raise VerdictValidityFailure("READ_ERROR", f"{label} cannot be read", "INPUT_ERROR", 2) from exc
    if len(raw) > maximum:
        raise VerdictValidityFailure("INPUT_TOO_LARGE", f"{label} is too large", "INPUT_ERROR", 2)
    return raw


def parse_json_document(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8-sig"),
            object_pairs_hook=_reject_duplicate_key,
            parse_constant=_reject_non_finite,
        )
    except VerdictValidityFailure:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerdictValidityFailure("INVALID_JSON", f"{label} is not valid JSON", "INPUT_ERROR", 2) from exc
    if not isinstance(value, dict):
        raise VerdictValidityFailure("INVALID_JSON", f"{label} must be a JSON object", "INPUT_ERROR", 2)
    return value


def parse_utc(value: Any, code: str, message: str, gate: str, exit_code: int) -> dt.datetime:
    if not isinstance(value, str) or UTC_PATTERN.fullmatch(value) is None:
        raise VerdictValidityFailure(code, message, gate, exit_code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise VerdictValidityFailure(code, message, gate, exit_code) from exc
    return parsed.replace(tzinfo=dt.timezone.utc)


def validate_validity_document(value: dict[str, Any]) -> tuple[dt.datetime, dt.datetime]:
    if set(value) != VALIDITY_FIELDS:
        raise VerdictValidityFailure(
            "VERDICT_VALIDITY_CONTRACT_INVALID",
            "verdict validity document fields are invalid",
            "INPUT_ERROR",
            2,
        )
    if value.get("validity_version") != VALIDITY_VERSION:
        raise VerdictValidityFailure(
            "VERDICT_VALIDITY_CONTRACT_INVALID",
            "verdict validity document version is invalid",
            "INPUT_ERROR",
            2,
        )
    verdict_hash = value.get("verdict_canonical_sha256")
    if not isinstance(verdict_hash, str) or HASH_PATTERN.fullmatch(verdict_hash) is None:
        raise VerdictValidityFailure(
            "VERDICT_VALIDITY_CONTRACT_INVALID",
            "verdict hash is invalid",
            "INPUT_ERROR",
            2,
        )
    not_before = parse_utc(
        value.get("not_before_utc"),
        "VERDICT_VALIDITY_CONTRACT_INVALID",
        "not_before_utc is invalid",
        "INPUT_ERROR",
        2,
    )
    not_after = parse_utc(
        value.get("not_after_utc"),
        "VERDICT_VALIDITY_CONTRACT_INVALID",
        "not_after_utc is invalid",
        "INPUT_ERROR",
        2,
    )
    if not_before >= not_after:
        raise VerdictValidityFailure(
            "VERDICT_VALIDITY_CONTRACT_INVALID",
            "verdict validity window must have positive duration",
            "INPUT_ERROR",
            2,
        )
    return not_before, not_after


def validate_b220_success(value: Any, evaluation_time_utc: str) -> tuple[dict[str, Any], dt.datetime]:
    failure = VerdictValidityFailure(
        "POLICY_PIN_VALIDITY_VERIFICATION_FAILED",
        "B2.20 policy pin validity verification failed",
        "NO_GO",
        1,
    )
    if not isinstance(value, dict) or set(value) != B220_SUCCESS_FIELDS:
        raise failure
    if value.get("ok") is not True or value.get("gate") != "GO":
        raise failure
    if value.get("authorized") is not True or value.get("signature_valid") is not True:
        raise failure
    if value.get("policy_pin_verified") is not True:
        raise failure
    if value.get("policy_pin_validity_verified") is not True:
        raise failure
    if value.get("verification_version") != SOURCE_VERIFICATION_VERSION:
        raise failure
    if value.get("evaluation_time_utc") != evaluation_time_utc:
        raise failure
    verdict_hash = value.get("verdict_canonical_sha256")
    if not isinstance(verdict_hash, str) or HASH_PATTERN.fullmatch(verdict_hash) is None:
        raise failure
    evaluation = parse_utc(
        value.get("evaluation_time_utc"),
        failure.code,
        failure.message,
        failure.gate,
        failure.exit_code,
    )
    return value, evaluation


def build_verdict_validity_verdict(
    verdict: dict[str, Any],
    envelope: dict[str, Any],
    raw_public: bytes,
    policy: dict[str, Any],
    pin: dict[str, Any],
    policy_pin_validity: dict[str, Any],
    verdict_validity: dict[str, Any],
    evaluation_time_utc: str,
    signing_key_id: str,
) -> dict[str, Any]:
    verdict_not_before, verdict_not_after = validate_validity_document(verdict_validity)
    try:
        source_result = build_policy_pin_validity_verdict(
            verdict,
            envelope,
            raw_public,
            policy,
            pin,
            policy_pin_validity,
            evaluation_time_utc,
            signing_key_id,
        )
    except Exception as exc:
        raise VerdictValidityFailure(
            "POLICY_PIN_VALIDITY_VERIFICATION_FAILED",
            "B2.20 policy pin validity verification failed",
            "NO_GO",
            1,
        ) from exc
    trusted, evaluation_time = validate_b220_success(source_result, evaluation_time_utc)
    if trusted["verdict_canonical_sha256"] != verdict_validity["verdict_canonical_sha256"]:
        raise VerdictValidityFailure(
            "VERDICT_VALIDITY_MISMATCH",
            "verified verdict does not match the external validity document",
            "NO_GO",
            1,
        )
    if evaluation_time < verdict_not_before:
        raise VerdictValidityFailure(
            "EVALUATION_BEFORE_VERDICT_VALIDITY_WINDOW",
            "evaluation time is before the verdict validity window",
            "NO_GO",
            1,
        )
    if evaluation_time > verdict_not_after:
        raise VerdictValidityFailure(
            "EVALUATION_AFTER_VERDICT_VALIDITY_WINDOW",
            "evaluation time is after the verdict validity window",
            "NO_GO",
            1,
        )
    return {
        "attestation_sha256": trusted["attestation_sha256"],
        "authorized": True,
        "evaluation_time_utc": evaluation_time_utc,
        "gate": "GO",
        "not_after_utc": trusted["not_after_utc"],
        "not_before_utc": trusted["not_before_utc"],
        "ok": True,
        "policy_canonical_sha256": trusted["policy_canonical_sha256"],
        "policy_pin_canonical_sha256": trusted["policy_pin_canonical_sha256"],
        "policy_pin_validity_canonical_sha256": trusted["policy_pin_validity_canonical_sha256"],
        "policy_pin_validity_verified": True,
        "policy_pin_verified": True,
        "public_key_sha256": trusted["public_key_sha256"],
        "signature_valid": True,
        "signing_key_id": trusted["signing_key_id"],
        "source_canonical_sha256": trusted["source_canonical_sha256"],
        "source_verification_version": SOURCE_VERIFICATION_VERSION,
        "verdict_canonical_sha256": trusted["verdict_canonical_sha256"],
        "verdict_not_after_utc": verdict_validity["not_after_utc"],
        "verdict_not_before_utc": verdict_validity["not_before_utc"],
        "verdict_validity_canonical_sha256": canonical_sha256(verdict_validity),
        "verdict_validity_verified": True,
        "verification_version": VERIFICATION_VERSION,
    }


def emit_json(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False))


def emit_failure(failure: VerdictValidityFailure) -> None:
    emit_json(
        {
            "error_code": failure.code,
            "error_message": failure.message,
            "gate": failure.gate,
            "ok": False,
            "verdict_validity_tool_version": TOOL_VERSION,
        }
    )


def emit_help() -> None:
    emit_json({"ok": True, "usage": USAGE, "verdict_validity_tool_version": TOOL_VERSION})


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
    parser.add_argument("--evaluation-time-utc")
    parser.add_argument("--signing-key-id")
    return parser.parse_args(list(arguments))


def run_cli(arguments: Sequence[str] | None = None, stdin_buffer: BinaryIO | None = None) -> int:
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
        )
        supplied = [getattr(parsed, name) for name in input_names]
        if parsed.help:
            if any(value is not None for value in supplied) or parsed.evaluation_time_utc is not None or parsed.signing_key_id is not None:
                raise VerdictValidityFailure("ARGUMENT_ERROR", "help cannot be combined with inputs", "INPUT_ERROR", 2)
            emit_help()
            return 0
        if any(value is None for value in supplied) or parsed.evaluation_time_utc is None or parsed.signing_key_id is None:
            raise VerdictValidityFailure("ARGUMENT_ERROR", "all verification inputs are required", "INPUT_ERROR", 2)
        if sum(value == "-" for value in supplied) > 1:
            raise VerdictValidityFailure("ARGUMENT_ERROR", "at most one file input may use stdin", "INPUT_ERROR", 2)
        verdict = parse_json_document(read_bytes(parsed.verdict, VERDICT_MAX_BYTES, selected_stdin, "verdict document"), "verdict document")
        envelope = parse_json_document(read_bytes(parsed.signature, SIGNATURE_MAX_BYTES, selected_stdin, "signature document"), "signature document")
        raw_public = read_bytes(parsed.public_key_file, PUBLIC_KEY_MAX_BYTES, selected_stdin, "public key")
        policy = parse_json_document(read_bytes(parsed.trust_policy, POLICY_MAX_BYTES, selected_stdin, "trust policy document"), "trust policy document")
        pin = parse_json_document(read_bytes(parsed.policy_pin, PIN_MAX_BYTES, selected_stdin, "policy pin document"), "policy pin document")
        policy_pin_validity = parse_json_document(read_bytes(parsed.policy_pin_validity, POLICY_PIN_VALIDITY_MAX_BYTES, selected_stdin, "policy pin validity document"), "policy pin validity document")
        verdict_validity = parse_json_document(read_bytes(parsed.verdict_validity, VERDICT_VALIDITY_MAX_BYTES, selected_stdin, "verdict validity document"), "verdict validity document")
        result = build_verdict_validity_verdict(
            verdict,
            envelope,
            raw_public,
            policy,
            pin,
            policy_pin_validity,
            verdict_validity,
            parsed.evaluation_time_utc,
            parsed.signing_key_id,
        )
        emit_json(result)
        return 0
    except VerdictValidityFailure as failure:
        emit_failure(failure)
        return failure.exit_code
    except Exception:
        emit_failure(
            VerdictValidityFailure(
                "VERDICT_VALIDITY_CONTRACT_INVALID",
                "local verdict validity verification failed",
                "INPUT_ERROR",
                2,
            )
        )
        return 2


def main() -> NoReturn:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
