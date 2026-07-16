from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import re
import sys
from typing import Any, BinaryIO, NoReturn, Sequence

from verify_scheduler_b219_cloud_audit_trust_policy_pin import build_policy_pin_verdict


TOOL_VERSION = "B2_20_CLOUD_AUDIT_TRUST_POLICY_PIN_VALIDITY_TOOL_V1"
VALIDITY_VERSION = "B2_20_CLOUD_AUDIT_TRUST_POLICY_PIN_VALIDITY_V1"
VERIFICATION_VERSION = "B2_20_CLOUD_AUDIT_TRUST_POLICY_PIN_VALIDITY_VERIFICATION_V1"
SOURCE_VERIFICATION_VERSION = "B2_19_CLOUD_AUDIT_TRUST_POLICY_PIN_VERIFICATION_V1"

VERDICT_MAX_BYTES = 1_048_576
SIGNATURE_MAX_BYTES = 65_536
PUBLIC_KEY_MAX_BYTES = 4_096
POLICY_MAX_BYTES = 1_048_576
PIN_MAX_BYTES = 65_536
VALIDITY_MAX_BYTES = 65_536

HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
UTC_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
VALIDITY_FIELDS = {
    "not_after_utc",
    "not_before_utc",
    "policy_pin_canonical_sha256",
    "validity_version",
}
B219_SUCCESS_FIELDS = {
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
USAGE = (
    "verify_scheduler_b220_cloud_audit_trust_policy_pin_validity.py "
    "--verdict FILE|- --signature FILE|- --public-key-file FILE|- "
    "--trust-policy FILE|- --policy-pin FILE|- --policy-pin-validity FILE|- "
    "--evaluation-time-utc UTC --signing-key-id ID"
)


class ValidityFailure(Exception):
    def __init__(self, code: str, message: str, gate: str, exit_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.gate = gate
        self.exit_code = exit_code


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ValidityFailure("ARGUMENT_ERROR", "invalid command arguments", "INPUT_ERROR", 2)


def _reject_duplicate_key(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValidityFailure("INVALID_JSON", "validity document contains duplicate keys", "INPUT_ERROR", 2)
        result[key] = value
    return result


def _reject_non_finite(value: str) -> NoReturn:
    raise ValidityFailure("INVALID_JSON", "validity document contains a non-finite number", "INPUT_ERROR", 2)


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
        raise ValidityFailure("VALIDITY_CONTRACT_INVALID", "validity document is not canonical JSON", "INPUT_ERROR", 2) from exc
    return rendered.encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def read_bytes(source: str, maximum_bytes: int, stdin_buffer: BinaryIO, label: str) -> bytes:
    if source == "-":
        try:
            data = stdin_buffer.read(maximum_bytes + 1)
        except Exception as exc:
            raise ValidityFailure("READ_ERROR", f"unable to read {label}", "INPUT_ERROR", 2) from exc
    else:
        path = pathlib.Path(source)
        try:
            if not path.is_file():
                raise OSError("not a file")
            if path.stat().st_size > maximum_bytes:
                raise ValidityFailure("INPUT_TOO_LARGE", f"{label} exceeds its size limit", "INPUT_ERROR", 2)
            data = path.read_bytes()
        except ValidityFailure:
            raise
        except OSError as exc:
            raise ValidityFailure("READ_ERROR", f"unable to read {label}", "INPUT_ERROR", 2) from exc
    if len(data) > maximum_bytes:
        raise ValidityFailure("INPUT_TOO_LARGE", f"{label} exceeds its size limit", "INPUT_ERROR", 2)
    return data


def parse_json_document(raw: bytes, label: str) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8-sig")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_key,
            parse_constant=_reject_non_finite,
        )
    except ValidityFailure:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidityFailure("INVALID_JSON", f"{label} is not valid JSON", "INPUT_ERROR", 2) from exc
    if not isinstance(value, dict):
        raise ValidityFailure("INVALID_JSON", f"{label} must be a JSON object", "INPUT_ERROR", 2)
    return value


def parse_utc(value: Any, error_code: str, label: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_PATTERN.fullmatch(value) is None:
        raise ValidityFailure(error_code, f"{label} is not a strict UTC timestamp", "INPUT_ERROR", 2)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ValidityFailure(error_code, f"{label} is not a valid UTC timestamp", "INPUT_ERROR", 2) from exc
    return parsed.replace(tzinfo=dt.timezone.utc)


def validate_validity_document(value: dict[str, Any]) -> tuple[dt.datetime, dt.datetime]:
    if set(value) != VALIDITY_FIELDS:
        raise ValidityFailure("VALIDITY_CONTRACT_INVALID", "validity document fields are invalid", "INPUT_ERROR", 2)
    if value.get("validity_version") != VALIDITY_VERSION:
        raise ValidityFailure("VALIDITY_CONTRACT_INVALID", "validity document version is invalid", "INPUT_ERROR", 2)
    policy_pin_hash = value.get("policy_pin_canonical_sha256")
    if not isinstance(policy_pin_hash, str) or HASH_PATTERN.fullmatch(policy_pin_hash) is None:
        raise ValidityFailure("VALIDITY_CONTRACT_INVALID", "policy pin hash is invalid", "INPUT_ERROR", 2)
    not_before = parse_utc(value.get("not_before_utc"), "VALIDITY_CONTRACT_INVALID", "not_before_utc")
    not_after = parse_utc(value.get("not_after_utc"), "VALIDITY_CONTRACT_INVALID", "not_after_utc")
    if not_before >= not_after:
        raise ValidityFailure("VALIDITY_CONTRACT_INVALID", "validity window must have positive duration", "INPUT_ERROR", 2)
    return not_before, not_after


def validate_b219_success(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != B219_SUCCESS_FIELDS:
        raise ValidityFailure("TRUST_POLICY_PIN_VERIFICATION_FAILED", "B2.19 verification returned an invalid contract", "NO_GO", 1)
    if value.get("ok") is not True or value.get("gate") != "GO":
        raise ValidityFailure("TRUST_POLICY_PIN_VERIFICATION_FAILED", "B2.19 verification did not return GO", "NO_GO", 1)
    if value.get("authorized") is not True or value.get("signature_valid") is not True:
        raise ValidityFailure("TRUST_POLICY_PIN_VERIFICATION_FAILED", "B2.19 trust verification is incomplete", "NO_GO", 1)
    if value.get("policy_pin_verified") is not True:
        raise ValidityFailure("TRUST_POLICY_PIN_VERIFICATION_FAILED", "B2.19 policy pin is not verified", "NO_GO", 1)
    if value.get("verification_version") != SOURCE_VERIFICATION_VERSION:
        raise ValidityFailure("TRUST_POLICY_PIN_VERIFICATION_FAILED", "B2.19 verification version is invalid", "NO_GO", 1)
    pin_hash = value.get("policy_pin_canonical_sha256")
    if not isinstance(pin_hash, str) or HASH_PATTERN.fullmatch(pin_hash) is None:
        raise ValidityFailure("TRUST_POLICY_PIN_VERIFICATION_FAILED", "B2.19 policy pin hash is invalid", "NO_GO", 1)
    return value


def build_policy_pin_validity_verdict(
    verdict: dict[str, Any],
    envelope: dict[str, Any],
    raw_public: bytes,
    policy: dict[str, Any],
    pin: dict[str, Any],
    validity: dict[str, Any],
    evaluation_time_utc: str,
    signing_key_id: str,
) -> dict[str, Any]:
    not_before, not_after = validate_validity_document(validity)
    evaluation_time = parse_utc(evaluation_time_utc, "INVALID_EVALUATION_TIME", "evaluation_time_utc")
    try:
        source_result = build_policy_pin_verdict(
            verdict,
            envelope,
            raw_public,
            policy,
            pin,
            signing_key_id,
        )
    except Exception as exc:
        raise ValidityFailure("TRUST_POLICY_PIN_VERIFICATION_FAILED", "B2.19 trust policy pin verification failed", "NO_GO", 1) from exc
    trusted = validate_b219_success(source_result)
    if trusted["policy_pin_canonical_sha256"] != validity["policy_pin_canonical_sha256"]:
        raise ValidityFailure("POLICY_PIN_VALIDITY_MISMATCH", "policy pin does not match the external validity document", "NO_GO", 1)
    if evaluation_time < not_before:
        raise ValidityFailure("EVALUATION_BEFORE_VALIDITY_WINDOW", "evaluation time is before the policy pin validity window", "NO_GO", 1)
    if evaluation_time > not_after:
        raise ValidityFailure("EVALUATION_AFTER_VALIDITY_WINDOW", "evaluation time is after the policy pin validity window", "NO_GO", 1)
    return {
        "attestation_sha256": trusted["attestation_sha256"],
        "authorized": True,
        "evaluation_time_utc": evaluation_time_utc,
        "gate": "GO",
        "not_after_utc": validity["not_after_utc"],
        "not_before_utc": validity["not_before_utc"],
        "ok": True,
        "policy_canonical_sha256": trusted["policy_canonical_sha256"],
        "policy_pin_canonical_sha256": trusted["policy_pin_canonical_sha256"],
        "policy_pin_validity_canonical_sha256": canonical_sha256(validity),
        "policy_pin_validity_verified": True,
        "policy_pin_verified": True,
        "public_key_sha256": trusted["public_key_sha256"],
        "signature_valid": True,
        "signing_key_id": trusted["signing_key_id"],
        "source_canonical_sha256": trusted["source_canonical_sha256"],
        "source_verification_version": SOURCE_VERIFICATION_VERSION,
        "verdict_canonical_sha256": trusted["verdict_canonical_sha256"],
        "verification_version": VERIFICATION_VERSION,
    }


def emit_json(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False))


def emit_failure(failure: ValidityFailure) -> None:
    emit_json(
        {
            "error_code": failure.code,
            "error_message": failure.message,
            "gate": failure.gate,
            "ok": False,
            "validity_tool_version": TOOL_VERSION,
        }
    )


def emit_help() -> None:
    emit_json({"ok": True, "usage": USAGE, "validity_tool_version": TOOL_VERSION})


def parse_arguments(arguments: Sequence[str]) -> argparse.Namespace:
    parser = SafeArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--help", action="store_true")
    parser.add_argument("--verdict")
    parser.add_argument("--signature")
    parser.add_argument("--public-key-file")
    parser.add_argument("--trust-policy")
    parser.add_argument("--policy-pin")
    parser.add_argument("--policy-pin-validity")
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
        )
        supplied = [getattr(parsed, name) for name in input_names]
        if parsed.help:
            if any(value is not None for value in supplied) or parsed.evaluation_time_utc is not None or parsed.signing_key_id is not None:
                raise ValidityFailure("ARGUMENT_ERROR", "help cannot be combined with inputs", "INPUT_ERROR", 2)
            emit_help()
            return 0
        if any(value is None for value in supplied) or parsed.evaluation_time_utc is None or parsed.signing_key_id is None:
            raise ValidityFailure("ARGUMENT_ERROR", "all verification inputs are required", "INPUT_ERROR", 2)
        if sum(value == "-" for value in supplied) > 1:
            raise ValidityFailure("ARGUMENT_ERROR", "at most one file input may use stdin", "INPUT_ERROR", 2)
        verdict = parse_json_document(read_bytes(parsed.verdict, VERDICT_MAX_BYTES, selected_stdin, "verdict document"), "verdict document")
        envelope = parse_json_document(read_bytes(parsed.signature, SIGNATURE_MAX_BYTES, selected_stdin, "signature document"), "signature document")
        raw_public = read_bytes(parsed.public_key_file, PUBLIC_KEY_MAX_BYTES, selected_stdin, "public key")
        policy = parse_json_document(read_bytes(parsed.trust_policy, POLICY_MAX_BYTES, selected_stdin, "trust policy document"), "trust policy document")
        pin = parse_json_document(read_bytes(parsed.policy_pin, PIN_MAX_BYTES, selected_stdin, "policy pin document"), "policy pin document")
        validity = parse_json_document(read_bytes(parsed.policy_pin_validity, VALIDITY_MAX_BYTES, selected_stdin, "policy pin validity document"), "policy pin validity document")
        result = build_policy_pin_validity_verdict(
            verdict,
            envelope,
            raw_public,
            policy,
            pin,
            validity,
            parsed.evaluation_time_utc,
            parsed.signing_key_id,
        )
        emit_json(result)
        return 0
    except ValidityFailure as failure:
        emit_failure(failure)
        return failure.exit_code
    except Exception:
        emit_failure(ValidityFailure("VALIDITY_CONTRACT_INVALID", "local validity verification failed", "INPUT_ERROR", 2))
        return 2


def main() -> NoReturn:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
