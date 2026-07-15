from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import re
import sys
from pathlib import Path
from typing import Any, NoReturn, Sequence


AUTHENTICATOR_VERSION = (
    "B2_16_CLOUD_AUDIT_VERDICT_HMAC_AUTHENTICATOR_V1"
)
ENVELOPE_VERSION = "B2_16_CLOUD_AUDIT_VERDICT_HMAC_V1"
VERIFICATION_VERSION = (
    "B2_16_CLOUD_AUDIT_VERDICT_HMAC_VERIFICATION_V1"
)
VERIFIER_VERSION = (
    "B2_15_CLOUD_AUDIT_ATTESTATION_VERIFIER_V1"
)
ATTESTATION_VERSION = "B2_14_CLOUD_AUDIT_ATTESTATION_V1"

ALGORITHM = "HMAC-SHA-256"
DOMAIN = "GPI_B2_16_CLOUD_AUDIT_VERDICT_HMAC_V1"

VERDICT_MAX_BYTES = 1_048_576
ENVELOPE_MAX_BYTES = 65_536
KEY_MIN_BYTES = 32
KEY_MAX_BYTES = 4_096

KEY_ID_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
)
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")

VERDICT_FIELDS = {
    "attestation_sha256",
    "attestation_version",
    "gate",
    "ok",
    "source_canonical_sha256",
    "verified",
    "verifier_version",
}

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


class ToolError(Exception):
    def __init__(
        self,
        exit_code: int,
        error_code: str,
        error_message: str,
        gate: str,
    ) -> None:
        super().__init__(error_message)
        self.exit_code = exit_code
        self.error_code = error_code
        self.error_message = error_message
        self.gate = gate


class InputError(ToolError):
    def __init__(
        self,
        error_code: str,
        error_message: str,
    ) -> None:
        super().__init__(
            2,
            error_code,
            error_message,
            "INPUT_ERROR",
        )


class VerdictContractError(ToolError):
    def __init__(self) -> None:
        super().__init__(
            1,
            "VERDICT_CONTRACT_INVALID",
            "verdict is not a verified B2.15 GO document",
            "NO_GO",
        )


class AuthenticationFailure(ToolError):
    def __init__(self) -> None:
        super().__init__(
            1,
            "AUTHENTICATION_FAILED",
            "authenticated envelope verification failed",
            "NO_GO",
        )


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise InputError(
            "ARGUMENT_ERROR",
            "invalid command line",
        )


class DuplicateKeyError(ValueError):
    pass


def _reject_json_constant(value: str) -> NoReturn:
    raise ValueError(f"unsupported JSON constant: {value}")


def _unique_object(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}

    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(
                f"duplicate JSON key: {key}"
            )

        result[key] = value

    return result


def emit_json(payload: dict[str, Any]) -> None:
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    )


def emit_error(error: ToolError) -> None:
    emit_json(
        {
            "authenticator_version": AUTHENTICATOR_VERSION,
            "error_code": error.error_code,
            "error_message": error.error_message,
            "gate": error.gate,
            "ok": False,
        }
    )


def emit_help() -> None:
    emit_json(
        {
            "authenticator_version": AUTHENTICATOR_VERSION,
            "ok": True,
            "usage": (
                "authenticate_scheduler_b216_cloud_audit_verdict.py "
                "seal --verdict FILE|- --key-file FILE|- --key-id ID "
                "| verify --verdict FILE|- --envelope FILE|- "
                "--key-file FILE|- --key-id ID"
            ),
        }
    )


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_limited_bytes(
    location: str,
    label: str,
    maximum_bytes: int,
) -> bytes:
    if location == "-":
        try:
            data = sys.stdin.buffer.read(
                maximum_bytes + 1
            )
        except Exception as exc:
            raise InputError(
                "STDIN_READ_ERROR",
                f"{label} could not be read from stdin",
            ) from exc
    else:
        try:
            data = Path(location).read_bytes()
        except OSError as exc:
            raise InputError(
                "FILE_READ_ERROR",
                f"{label} file could not be read",
            ) from exc

    if len(data) > maximum_bytes:
        raise InputError(
            "INPUT_TOO_LARGE",
            f"{label} exceeds the size limit",
        )

    return data


def read_json_document(
    location: str,
    label: str,
    maximum_bytes: int,
) -> dict[str, Any]:
    raw = _read_limited_bytes(
        location,
        label,
        maximum_bytes,
    )

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InputError(
            "INVALID_UTF8",
            f"{label} is not valid UTF-8",
        ) from exc

    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (
        json.JSONDecodeError,
        DuplicateKeyError,
        ValueError,
    ) as exc:
        raise InputError(
            "INVALID_JSON",
            f"{label} is not valid JSON",
        ) from exc

    if not isinstance(value, dict):
        raise InputError(
            "JSON_OBJECT_REQUIRED",
            f"{label} must be a JSON object",
        )

    return value


def read_secret_key(location: str) -> bytes:
    key = _read_limited_bytes(
        location,
        "secret key",
        KEY_MAX_BYTES,
    )

    if len(key) < KEY_MIN_BYTES:
        raise InputError(
            "KEY_TOO_SHORT",
            "secret key is shorter than 32 bytes",
        )

    return key


def validate_key_id(key_id: str) -> None:
    if not KEY_ID_PATTERN.fullmatch(key_id):
        raise InputError(
            "INVALID_KEY_ID",
            "key identifier is invalid",
        )


def validate_verdict(
    verdict: dict[str, Any],
) -> None:
    if set(verdict) != VERDICT_FIELDS:
        raise VerdictContractError()

    if verdict.get("verifier_version") != VERIFIER_VERSION:
        raise VerdictContractError()

    if (
        verdict.get("attestation_version")
        != ATTESTATION_VERSION
    ):
        raise VerdictContractError()

    if verdict.get("gate") != "GO":
        raise VerdictContractError()

    if verdict.get("ok") is not True:
        raise VerdictContractError()

    if verdict.get("verified") is not True:
        raise VerdictContractError()

    source_hash = verdict.get(
        "source_canonical_sha256"
    )
    attestation_hash = verdict.get(
        "attestation_sha256"
    )

    if (
        not isinstance(source_hash, str)
        or not HASH_PATTERN.fullmatch(source_hash)
    ):
        raise VerdictContractError()

    if (
        not isinstance(attestation_hash, str)
        or not HASH_PATTERN.fullmatch(attestation_hash)
    ):
        raise VerdictContractError()


def build_protected_payload(
    verdict: dict[str, Any],
    key_id: str,
) -> dict[str, Any]:
    return {
        "algorithm": ALGORITHM,
        "domain": DOMAIN,
        "envelope_version": ENVELOPE_VERSION,
        "key_id": key_id,
        "verdict": verdict,
    }


def compute_mac_hex(
    verdict: dict[str, Any],
    key: bytes,
    key_id: str,
) -> str:
    protected_payload = build_protected_payload(
        verdict,
        key_id,
    )
    protected_bytes = canonical_json_bytes(
        protected_payload
    )

    return hmac.new(
        key,
        protected_bytes,
        hashlib.sha256,
    ).hexdigest()


def build_envelope(
    verdict: dict[str, Any],
    key: bytes,
    key_id: str,
) -> dict[str, Any]:
    validate_verdict(verdict)
    validate_key_id(key_id)

    verdict_bytes = canonical_json_bytes(verdict)
    verdict_hash = sha256_hex(verdict_bytes)
    mac_hex = compute_mac_hex(
        verdict,
        key,
        key_id,
    )

    envelope = {
        "algorithm": ALGORITHM,
        "attestation_sha256": verdict[
            "attestation_sha256"
        ],
        "attestation_version": verdict[
            "attestation_version"
        ],
        "envelope_version": ENVELOPE_VERSION,
        "key_id": key_id,
        "mac_hex": mac_hex,
        "source_canonical_sha256": verdict[
            "source_canonical_sha256"
        ],
        "verdict_canonical_sha256": verdict_hash,
        "verifier_version": verdict[
            "verifier_version"
        ],
    }

    if set(envelope) != ENVELOPE_FIELDS:
        raise RuntimeError(
            "internal envelope contract mismatch"
        )

    return envelope


def authenticate_envelope(
    verdict: dict[str, Any],
    envelope: dict[str, Any],
    key: bytes,
    key_id: str,
) -> dict[str, Any]:
    expected = build_envelope(
        verdict,
        key,
        key_id,
    )

    supplied_mac_value = envelope.get("mac_hex")
    supplied_mac = (
        supplied_mac_value
        if isinstance(supplied_mac_value, str)
        else ""
    )

    expected_mac = expected["mac_hex"]

    mac_matches = hmac.compare_digest(
        supplied_mac,
        expected_mac,
    )

    fields_match = set(envelope) == ENVELOPE_FIELDS

    metadata_matches = all(
        envelope.get(field) == expected[field]
        for field in ENVELOPE_FIELDS - {"mac_hex"}
    )

    if not (
        fields_match
        and metadata_matches
        and mac_matches
    ):
        raise AuthenticationFailure()

    result = {
        "authenticated": True,
        "attestation_sha256": verdict[
            "attestation_sha256"
        ],
        "gate": "GO",
        "key_id": key_id,
        "ok": True,
        "source_canonical_sha256": verdict[
            "source_canonical_sha256"
        ],
        "verdict_canonical_sha256": expected[
            "verdict_canonical_sha256"
        ],
        "verification_version": VERIFICATION_VERSION,
    }

    if set(result) != VERIFICATION_FIELDS:
        raise RuntimeError(
            "internal verification contract mismatch"
        )

    return result


def parse_arguments(
    argv: Sequence[str],
) -> argparse.Namespace:
    parser = SafeArgumentParser(
        add_help=False,
        allow_abbrev=False,
    )

    parser.add_argument(
        "mode",
        choices=("seal", "verify"),
    )
    parser.add_argument(
        "--verdict",
        required=True,
    )
    parser.add_argument(
        "--envelope",
    )
    parser.add_argument(
        "--key-file",
        required=True,
    )
    parser.add_argument(
        "--key-id",
        required=True,
    )

    args = parser.parse_args(list(argv))

    if args.mode == "seal":
        if args.envelope is not None:
            raise InputError(
                "ARGUMENT_ERROR",
                "seal mode does not accept an envelope",
            )
    else:
        if args.envelope is None:
            raise InputError(
                "ARGUMENT_ERROR",
                "verify mode requires an envelope",
            )

    validate_key_id(args.key_id)

    stdin_inputs = [
        value
        for value in (
            args.verdict,
            args.envelope,
            args.key_file,
        )
        if value == "-"
    ]

    if len(stdin_inputs) > 1:
        raise InputError(
            "MULTIPLE_STDIN_INPUTS",
            "at most one input may use stdin",
        )

    return args


def run_mode(args: argparse.Namespace) -> int:
    verdict = read_json_document(
        args.verdict,
        "verdict",
        VERDICT_MAX_BYTES,
    )
    key = read_secret_key(args.key_file)

    if args.mode == "seal":
        envelope = build_envelope(
            verdict,
            key,
            args.key_id,
        )
        emit_json(envelope)
        return 0

    supplied_envelope = read_json_document(
        args.envelope,
        "envelope",
        ENVELOPE_MAX_BYTES,
    )

    result = authenticate_envelope(
        verdict,
        supplied_envelope,
        key,
        args.key_id,
    )
    emit_json(result)
    return 0


def main(
    argv: Sequence[str] | None = None,
) -> int:
    arguments = (
        list(argv)
        if argv is not None
        else sys.argv[1:]
    )

    if arguments == ["--help"]:
        emit_help()
        return 0

    try:
        args = parse_arguments(arguments)
        return run_mode(args)
    except ToolError as exc:
        emit_error(exc)
        return exc.exit_code
    except Exception:
        emit_error(
            InputError(
                "INTERNAL_ERROR",
                "local authentication failed unexpectedly",
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())