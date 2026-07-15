from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from attest_scheduler_b214_cloud_audit import build_attestation


VERIFIER_VERSION = "B2_15_CLOUD_AUDIT_ATTESTATION_VERIFIER_V1"
ATTESTATION_VERSION = "B2_14_CLOUD_AUDIT_ATTESTATION_V1"
MAX_DOCUMENT_BYTES = 1_048_576

EXIT_VERIFIED = 0
EXIT_NOT_VERIFIED = 1
EXIT_INPUT_ERROR = 2


class InputError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class VerificationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise InputError(
            "ARGUMENT_ERROR",
            "invalid command line",
        )


def _emit(payload: dict[str, Any]) -> None:
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _help_payload() -> dict[str, Any]:
    return {
        "ok": True,
        "usage": (
            "verify_scheduler_b215_cloud_audit_attestation.py "
            "--source FILE|- --attestation FILE|-"
        ),
        "verifier_version": VERIFIER_VERSION,
    }


def _input_error_payload(error: InputError) -> dict[str, Any]:
    return {
        "error_code": error.code,
        "error_message": error.message,
        "gate": "INPUT_ERROR",
        "ok": False,
        "verified": False,
        "verifier_version": VERIFIER_VERSION,
    }


def _verification_error_payload(
    error: VerificationError,
) -> dict[str, Any]:
    return {
        "error_code": error.code,
        "error_message": error.message,
        "gate": "NO_GO",
        "ok": False,
        "verified": False,
        "verifier_version": VERIFIER_VERSION,
    }


def _build_parser() -> JsonArgumentParser:
    parser = JsonArgumentParser(add_help=False)
    parser.add_argument("--source", required=True)
    parser.add_argument("--attestation", required=True)
    return parser


def _read_file(locator: str, label: str) -> str:
    path = Path(locator)

    if not path.exists():
        raise InputError(
            "FILE_NOT_FOUND",
            f"{label} file is unavailable",
        )

    if not path.is_file():
        raise InputError(
            "FILE_NOT_REGULAR",
            f"{label} path is not a regular file",
        )

    try:
        size = path.stat().st_size
    except OSError as exc:
        raise InputError(
            "FILE_READ_ERROR",
            f"{label} file is unreadable",
        ) from exc

    if size > MAX_DOCUMENT_BYTES:
        raise InputError(
            "INPUT_TOO_LARGE",
            f"{label} document exceeds the size limit",
        )

    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise InputError(
            "FILE_READ_ERROR",
            f"{label} file is unreadable",
        ) from exc

    if len(raw) > MAX_DOCUMENT_BYTES:
        raise InputError(
            "INPUT_TOO_LARGE",
            f"{label} document exceeds the size limit",
        )

    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InputError(
            "INVALID_UTF8",
            f"{label} document is not valid UTF-8",
        ) from exc


def _read_stdin(label: str) -> str:
    try:
        text = sys.stdin.read(MAX_DOCUMENT_BYTES + 1)
    except (OSError, UnicodeError) as exc:
        raise InputError(
            "STDIN_READ_ERROR",
            f"{label} stdin is unreadable",
        ) from exc

    encoded_size = len(text.encode("utf-8"))

    if encoded_size > MAX_DOCUMENT_BYTES:
        raise InputError(
            "INPUT_TOO_LARGE",
            f"{label} document exceeds the size limit",
        )

    return text


def _parse_document(text: str, label: str) -> dict[str, Any]:
    if not text.strip():
        raise InputError(
            "EMPTY_INPUT",
            f"{label} document is empty",
        )

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InputError(
            "INVALID_JSON",
            f"{label} document is not valid JSON",
        ) from exc

    if not isinstance(payload, dict):
        raise InputError(
            "JSON_OBJECT_REQUIRED",
            f"{label} document must be a JSON object",
        )

    return payload


def _read_document(locator: str, label: str) -> dict[str, Any]:
    if locator == "-":
        text = _read_stdin(label)
    else:
        text = _read_file(locator, label)

    return _parse_document(text, label)


def verify_pair(
    source_document: dict[str, Any],
    provided_attestation: dict[str, Any],
) -> dict[str, Any]:
    try:
        expected_attestation = build_attestation(
            copy.deepcopy(source_document)
        )
    except Exception as exc:
        raise VerificationError(
            "SOURCE_CONTRACT_INVALID",
            "source document does not satisfy the B2.14 contract",
        ) from exc

    if expected_attestation != provided_attestation:
        raise VerificationError(
            "ATTESTATION_MISMATCH",
            "attestation does not match the source document",
        )

    return {
        "attestation_sha256": expected_attestation[
            "attestation_sha256"
        ],
        "attestation_version": expected_attestation[
            "attestation_version"
        ],
        "gate": "GO",
        "ok": True,
        "source_canonical_sha256": expected_attestation[
            "source_canonical_sha256"
        ],
        "verified": True,
        "verifier_version": VERIFIER_VERSION,
    }


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)

    if "-h" in arguments or "--help" in arguments:
        _emit(_help_payload())
        return EXIT_VERIFIED

    try:
        parsed = _build_parser().parse_args(arguments)

        if parsed.source == "-" and parsed.attestation == "-":
            raise InputError(
                "MULTIPLE_STDIN_INPUTS",
                "source and attestation cannot both use stdin",
            )

        source_document = _read_document(
            parsed.source,
            "source",
        )
        provided_attestation = _read_document(
            parsed.attestation,
            "attestation",
        )

        result = verify_pair(
            source_document,
            provided_attestation,
        )
    except InputError as exc:
        _emit(_input_error_payload(exc))
        return EXIT_INPUT_ERROR
    except VerificationError as exc:
        _emit(_verification_error_payload(exc))
        return EXIT_NOT_VERIFIED
    except Exception:
        _emit(
            _input_error_payload(
                InputError(
                    "INTERNAL_VERIFICATION_ERROR",
                    "local verification failed",
                )
            )
        )
        return EXIT_INPUT_ERROR

    _emit(result)
    return EXIT_VERIFIED


if __name__ == "__main__":
    raise SystemExit(main())
