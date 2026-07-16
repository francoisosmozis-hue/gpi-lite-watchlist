from __future__ import annotations

import argparse
import hashlib
import hmac
import json
from pathlib import Path
import re
import sys
from typing import Any, NoReturn, Sequence

from verify_scheduler_b218_cloud_audit_verdict_trust import build_trust_verdict


TOOL_VERSION = "B2_19_CLOUD_AUDIT_TRUST_POLICY_PIN_TOOL_V1"
VERIFICATION_VERSION = "B2_19_CLOUD_AUDIT_TRUST_POLICY_PIN_VERIFICATION_V1"
SOURCE_VERIFICATION_VERSION = "B2_18_CLOUD_AUDIT_VERDICT_TRUST_VERIFICATION_V1"
PIN_VERSION = "B2_19_CLOUD_AUDIT_TRUST_POLICY_PIN_V1"

VERDICT_MAX_BYTES = 1_048_576
SIGNATURE_MAX_BYTES = 65_536
PUBLIC_KEY_MAX_BYTES = 65_536
POLICY_MAX_BYTES = 1_048_576
PIN_MAX_BYTES = 65_536

HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
KEY_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

PIN_FIELDS = {
    "pin_version",
    "policy_canonical_sha256",
}

B218_SUCCESS_FIELDS = {
    "attestation_sha256",
    "authorized",
    "gate",
    "ok",
    "policy_canonical_sha256",
    "policy_version",
    "public_key_sha256",
    "signature_valid",
    "signing_key_id",
    "source_canonical_sha256",
    "source_verification_version",
    "verdict_canonical_sha256",
    "verification_version",
}


class PolicyPinFailure(Exception):
    def __init__(
        self,
        error_code: str,
        message: str,
        gate: str,
        exit_code: int,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.gate = gate
        self.exit_code = exit_code


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise PolicyPinFailure(
            "INPUT_ERROR",
            "invalid command line",
            "INPUT_ERROR",
            2,
        )


def canonical_json_bytes(value: Any) -> bytes:
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise PolicyPinFailure(
            "PIN_CONTRACT_INVALID",
            "policy pin cannot be represented as canonical JSON",
            "INPUT_ERROR",
            2,
        ) from exc
    return rendered.encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PolicyPinFailure(
                "INVALID_JSON",
                "JSON document contains a duplicate object key",
                "INPUT_ERROR",
                2,
            )
        result[key] = value
    return result


def _reject_constant(value: str) -> NoReturn:
    raise PolicyPinFailure(
        "INVALID_JSON",
        "JSON document contains a non-finite number",
        "INPUT_ERROR",
        2,
    )


def parse_json_document(raw: bytes, label: str) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise PolicyPinFailure(
            "INVALID_JSON",
            f"{label} document is not valid UTF-8 JSON",
            "INPUT_ERROR",
            2,
        ) from exc

    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except PolicyPinFailure:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise PolicyPinFailure(
            "INVALID_JSON",
            f"{label} document is not valid JSON",
            "INPUT_ERROR",
            2,
        ) from exc

    if not isinstance(value, dict):
        raise PolicyPinFailure(
            "INVALID_JSON",
            f"{label} document must be a JSON object",
            "INPUT_ERROR",
            2,
        )
    return value


def read_bytes(
    location: str,
    label: str,
    maximum_bytes: int,
    stdin_buffer: Any,
) -> bytes:
    try:
        if location == "-":
            data = stdin_buffer.read(maximum_bytes + 1)
        else:
            path = Path(location)
            if not path.is_file():
                raise OSError("input file does not exist")
            with path.open("rb") as handle:
                data = handle.read(maximum_bytes + 1)
    except OSError as exc:
        raise PolicyPinFailure(
            "INPUT_ERROR",
            f"cannot read {label} input",
            "INPUT_ERROR",
            2,
        ) from exc

    if not isinstance(data, bytes):
        data = bytes(data)
    if len(data) > maximum_bytes:
        raise PolicyPinFailure(
            "INPUT_ERROR",
            f"{label} input exceeds the maximum size",
            "INPUT_ERROR",
            2,
        )
    return data


def validate_policy_pin(pin: dict[str, Any]) -> None:
    if set(pin) != PIN_FIELDS:
        raise PolicyPinFailure(
            "PIN_CONTRACT_INVALID",
            "policy pin fields do not match the required contract",
            "INPUT_ERROR",
            2,
        )
    if pin.get("pin_version") != PIN_VERSION:
        raise PolicyPinFailure(
            "PIN_CONTRACT_INVALID",
            "policy pin version is invalid",
            "INPUT_ERROR",
            2,
        )
    expected_hash = pin.get("policy_canonical_sha256")
    if not isinstance(expected_hash, str) or HASH_PATTERN.fullmatch(expected_hash) is None:
        raise PolicyPinFailure(
            "PIN_CONTRACT_INVALID",
            "policy pin hash is invalid",
            "INPUT_ERROR",
            2,
        )


def validate_b218_success(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict) or set(result) != B218_SUCCESS_FIELDS:
        raise PolicyPinFailure(
            "TRUST_VERIFICATION_FAILED",
            "B2.18 trust verification returned an invalid result",
            "NO_GO",
            1,
        )
    if (
        result.get("ok") is not True
        or result.get("authorized") is not True
        or result.get("signature_valid") is not True
        or result.get("gate") != "GO"
        or result.get("verification_version") != SOURCE_VERIFICATION_VERSION
    ):
        raise PolicyPinFailure(
            "TRUST_VERIFICATION_FAILED",
            "B2.18 trust verification did not return GO",
            "NO_GO",
            1,
        )
    policy_hash = result.get("policy_canonical_sha256")
    if not isinstance(policy_hash, str) or HASH_PATTERN.fullmatch(policy_hash) is None:
        raise PolicyPinFailure(
            "TRUST_VERIFICATION_FAILED",
            "B2.18 trust verification returned an invalid policy hash",
            "NO_GO",
            1,
        )
    for field in [
        "attestation_sha256",
        "public_key_sha256",
        "source_canonical_sha256",
        "verdict_canonical_sha256",
    ]:
        value = result.get(field)
        if not isinstance(value, str) or HASH_PATTERN.fullmatch(value) is None:
            raise PolicyPinFailure(
                "TRUST_VERIFICATION_FAILED",
                "B2.18 trust verification returned an invalid hash field",
                "NO_GO",
                1,
            )
    signing_key_id = result.get("signing_key_id")
    if (
        not isinstance(signing_key_id, str)
        or KEY_ID_PATTERN.fullmatch(signing_key_id) is None
    ):
        raise PolicyPinFailure(
            "TRUST_VERIFICATION_FAILED",
            "B2.18 trust verification returned an invalid signing key id",
            "NO_GO",
            1,
        )
    if result.get("policy_version") != "B2_18_CLOUD_AUDIT_SIGNING_KEY_TRUST_POLICY_V1":
        raise PolicyPinFailure(
            "TRUST_VERIFICATION_FAILED",
            "B2.18 trust verification returned an invalid policy version",
            "NO_GO",
            1,
        )
    if (
        result.get("source_verification_version")
        != "B2_17_CLOUD_AUDIT_VERDICT_ED25519_VERIFICATION_V1"
    ):
        raise PolicyPinFailure(
            "TRUST_VERIFICATION_FAILED",
            "B2.18 trust verification returned an invalid source version",
            "NO_GO",
            1,
        )
    return result


def build_policy_pin_verdict(
    verdict: dict[str, Any],
    envelope: dict[str, Any],
    raw_public: bytes,
    policy: dict[str, Any],
    pin: dict[str, Any],
    signing_key_id: str,
) -> dict[str, Any]:
    validate_policy_pin(pin)

    try:
        source_result = build_trust_verdict(
            verdict,
            envelope,
            raw_public,
            policy,
            signing_key_id,
        )
    except Exception as exc:
        raise PolicyPinFailure(
            "TRUST_VERIFICATION_FAILED",
            "B2.18 trust verification failed",
            "NO_GO",
            1,
        ) from exc

    trusted = validate_b218_success(source_result)
    if trusted["signing_key_id"] != signing_key_id:
        raise PolicyPinFailure(
            "TRUST_VERIFICATION_FAILED",
            "B2.18 trust verification returned a different signing key id",
            "NO_GO",
            1,
        )
    actual_hash = trusted["policy_canonical_sha256"]
    expected_hash = pin["policy_canonical_sha256"]

    if not hmac.compare_digest(actual_hash, expected_hash):
        raise PolicyPinFailure(
            "POLICY_PIN_MISMATCH",
            "trust policy does not match the external policy pin",
            "NO_GO",
            1,
        )

    return {
        "attestation_sha256": trusted["attestation_sha256"],
        "authorized": True,
        "gate": "GO",
        "ok": True,
        "policy_canonical_sha256": actual_hash,
        "policy_pin_canonical_sha256": canonical_sha256(pin),
        "policy_pin_verified": True,
        "public_key_sha256": trusted["public_key_sha256"],
        "signature_valid": True,
        "signing_key_id": trusted["signing_key_id"],
        "source_canonical_sha256": trusted["source_canonical_sha256"],
        "source_verification_version": SOURCE_VERIFICATION_VERSION,
        "verdict_canonical_sha256": trusted["verdict_canonical_sha256"],
        "verification_version": VERIFICATION_VERSION,
    }


def emit_json(payload: dict[str, Any]) -> None:
    rendered = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    sys.stdout.write(rendered + "\n")


def emit_failure(failure: PolicyPinFailure) -> None:
    emit_json(
        {
            "error_code": failure.error_code,
            "error_message": failure.message,
            "gate": failure.gate,
            "ok": False,
            "policy_pin_tool_version": TOOL_VERSION,
        }
    )


def emit_help() -> None:
    emit_json(
        {
            "ok": True,
            "policy_pin_tool_version": TOOL_VERSION,
            "usage": (
                "verify_scheduler_b219_cloud_audit_trust_policy_pin.py "
                "--verdict FILE|- --signature FILE|- --public-key-file FILE|- "
                "--trust-policy FILE|- --policy-pin FILE|- --signing-key-id ID"
            ),
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
    parser.add_argument("--signing-key-id")
    return parser.parse_args(list(arguments))


def run_cli(
    arguments: Sequence[str] | None = None,
    stdin_buffer: Any = None,
) -> int:
    args = parse_arguments(sys.argv[1:] if arguments is None else arguments)

    if args.help:
        other_values = [
            args.verdict,
            args.signature,
            args.public_key_file,
            args.trust_policy,
            args.policy_pin,
            args.signing_key_id,
        ]
        if any(value is not None for value in other_values):
            raise PolicyPinFailure(
                "INPUT_ERROR",
                "help cannot be combined with verification inputs",
                "INPUT_ERROR",
                2,
            )
        emit_help()
        return 0

    required = {
        "verdict": args.verdict,
        "signature": args.signature,
        "public key": args.public_key_file,
        "trust policy": args.trust_policy,
        "policy pin": args.policy_pin,
        "signing key id": args.signing_key_id,
    }
    if any(value is None for value in required.values()):
        raise PolicyPinFailure(
            "INPUT_ERROR",
            "required command-line input is missing",
            "INPUT_ERROR",
            2,
        )

    file_inputs = [
        args.verdict,
        args.signature,
        args.public_key_file,
        args.trust_policy,
        args.policy_pin,
    ]
    if sum(value == "-" for value in file_inputs) > 1:
        raise PolicyPinFailure(
            "INPUT_ERROR",
            "at most one input may use stdin",
            "INPUT_ERROR",
            2,
        )

    if stdin_buffer is None:
        stdin_buffer = sys.stdin.buffer

    verdict = parse_json_document(
        read_bytes(args.verdict, "verdict", VERDICT_MAX_BYTES, stdin_buffer),
        "verdict",
    )
    envelope = parse_json_document(
        read_bytes(args.signature, "signature", SIGNATURE_MAX_BYTES, stdin_buffer),
        "signature",
    )
    raw_public = read_bytes(
        args.public_key_file,
        "public key",
        PUBLIC_KEY_MAX_BYTES,
        stdin_buffer,
    )
    policy = parse_json_document(
        read_bytes(args.trust_policy, "trust policy", POLICY_MAX_BYTES, stdin_buffer),
        "trust policy",
    )
    pin = parse_json_document(
        read_bytes(args.policy_pin, "policy pin", PIN_MAX_BYTES, stdin_buffer),
        "policy pin",
    )

    result = build_policy_pin_verdict(
        verdict,
        envelope,
        raw_public,
        policy,
        pin,
        args.signing_key_id,
    )
    emit_json(result)
    return 0


def main() -> int:
    try:
        return run_cli()
    except PolicyPinFailure as failure:
        emit_failure(failure)
        return failure.exit_code
    except Exception:
        emit_failure(
            PolicyPinFailure(
                "INPUT_ERROR",
                "unexpected local verification failure",
                "INPUT_ERROR",
                2,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
