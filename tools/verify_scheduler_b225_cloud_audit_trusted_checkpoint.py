from __future__ import annotations

import argparse
import contextlib
import hashlib
import hmac
import json
import os
import pathlib
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from typing import Any, BinaryIO, Iterator, NoReturn, Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from verify_scheduler_b224_cloud_audit_verdict_validity_pin_validity_pin import (
    build_verdict_validity_pin_validity_pin_verdict,
)


TOOL_VERSION = "B2_25_CLOUD_AUDIT_TRUSTED_CHECKPOINT_TOOL_V1"
VERIFICATION_VERSION = "B2_25_CLOUD_AUDIT_TRUSTED_CHECKPOINT_VERIFICATION_V1"
SOURCE_VERIFICATION_VERSION = (
    "B2_24_CLOUD_AUDIT_VERDICT_VALIDITY_PIN_VALIDITY_PIN_VERIFICATION_V1"
)
CHECKPOINT_VERSION = "B2_25_CLOUD_AUDIT_TRUSTED_CHECKPOINT_V1"
STATE_VERSION = "B2_25_REPLAY_STATE_V1"
ROOT_POLICY_VERSION = "B2_25_ROOT_TRUST_POLICY_V1"
ZERO_HASH = "0" * 64

MAX_BYTES = {
    "verdict": 1_048_576,
    "signature": 65_536,
    "public_key": 4_096,
    "policy": 1_048_576,
    "policy_pin": 65_536,
    "policy_pin_validity": 65_536,
    "verdict_validity": 65_536,
    "verdict_validity_pin": 65_536,
    "verdict_validity_pin_validity": 65_536,
    "verdict_validity_pin_validity_pin": 65_536,
    "checkpoint": 65_536,
    "checkpoint_signature": 64,
    "root_public_key": 4_096,
    "root_trust_policy": 65_536,
    "replay_state": 65_536,
}

CHECKPOINT_FIELDS = {
    "attestation_id",
    "b224_result_canonical_sha256",
    "checkpoint_version",
    "environment_id",
    "issued_at_utc",
    "not_after_utc",
    "policy_id",
    "previous_checkpoint_canonical_sha256",
    "sequence",
}
STATE_FIELDS = {
    "environment_id",
    "last_attestation_id",
    "last_checkpoint_canonical_sha256",
    "last_sequence",
    "policy_id",
    "state_version",
}
ROOT_POLICY_FIELDS = {
    "authorized_root_keys",
    "environment_id",
    "policy_id",
    "root_policy_version",
}
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

USAGE = (
    "verify_scheduler_b225_cloud_audit_trusted_checkpoint.py "
    "--verdict FILE|- --signature FILE|- --public-key-file FILE|- "
    "--trust-policy FILE|- --policy-pin FILE|- --policy-pin-validity FILE|- "
    "--verdict-validity FILE|- --verdict-validity-pin FILE|- "
    "--verdict-validity-pin-validity FILE|- "
    "--verdict-validity-pin-validity-pin FILE|- --evaluation-time-utc UTC "
    "--signing-key-id ID --checkpoint FILE|- --checkpoint-signature FILE "
    "--root-public-key-file FILE --root-trust-policy FILE "
    "--root-signing-key-id ID --replay-state-file FILE --environment-id ID "
    "--policy-id ID [--lock-timeout-seconds N]"
)


class TrustedCheckpointFailure(Exception):
    def __init__(self, code: str, message: str, gate: str = "NO_GO", exit_code: int = 1):
        super().__init__(message)
        self.code = code
        self.message = message
        self.gate = gate
        self.exit_code = exit_code


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise TrustedCheckpointFailure("ARGUMENT_ERROR", "invalid command arguments", "INPUT_ERROR", 2)


def _reject_duplicate_key(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise TrustedCheckpointFailure("DUPLICATE_JSON_KEY", "duplicate JSON key")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> NoReturn:
    raise TrustedCheckpointFailure("NON_FINITE_JSON", "non-finite JSON value")


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TrustedCheckpointFailure("INVALID_JSON_VALUE", "invalid JSON value") from exc


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def parse_json_bytes(raw: bytes, label: str) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8-sig")
        value = json.loads(
            text, object_pairs_hook=_reject_duplicate_key, parse_constant=_reject_non_finite
        )
    except TrustedCheckpointFailure:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TrustedCheckpointFailure("INVALID_JSON", f"invalid {label} JSON") from exc
    if not isinstance(value, dict):
        raise TrustedCheckpointFailure("INVALID_JSON_TYPE", f"{label} must be a JSON object")
    return value


def parse_utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not UTC_RE.fullmatch(value):
        raise TrustedCheckpointFailure("INVALID_TIME", f"invalid {label}")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise TrustedCheckpointFailure("INVALID_TIME", f"invalid {label}") from exc
    return parsed


def _valid_identifier(value: Any) -> bool:
    return isinstance(value, str) and 1 <= len(value.encode("utf-8")) <= 128


def validate_checkpoint(value: dict[str, Any]) -> dict[str, Any]:
    if set(value) != CHECKPOINT_FIELDS:
        raise TrustedCheckpointFailure("INVALID_CHECKPOINT_FIELDS", "invalid checkpoint fields")
    if value["checkpoint_version"] != CHECKPOINT_VERSION:
        raise TrustedCheckpointFailure("INVALID_CHECKPOINT_VERSION", "invalid checkpoint version")
    if not isinstance(value["attestation_id"], str) or not UUID4_RE.fullmatch(value["attestation_id"]):
        raise TrustedCheckpointFailure("INVALID_ATTESTATION_ID", "invalid attestation identifier")
    sequence = value["sequence"]
    if isinstance(sequence, bool) or not isinstance(sequence, int) or not 0 <= sequence <= 2**63 - 1:
        raise TrustedCheckpointFailure("INVALID_SEQUENCE", "invalid checkpoint sequence")
    for field in ("environment_id", "policy_id"):
        if not _valid_identifier(value[field]):
            raise TrustedCheckpointFailure("INVALID_IDENTIFIER", f"invalid {field}")
    for field in ("b224_result_canonical_sha256", "previous_checkpoint_canonical_sha256"):
        if not isinstance(value[field], str) or not HASH_RE.fullmatch(value[field]):
            raise TrustedCheckpointFailure("INVALID_HASH", f"invalid {field}")
    issued = parse_utc(value["issued_at_utc"], "issued_at_utc")
    expires = parse_utc(value["not_after_utc"], "not_after_utc")
    if issued >= expires:
        raise TrustedCheckpointFailure("INVALID_TIME_WINDOW", "invalid checkpoint time window")
    return value


def validate_state(value: dict[str, Any]) -> dict[str, Any]:
    if set(value) != STATE_FIELDS or value.get("state_version") != STATE_VERSION:
        raise TrustedCheckpointFailure("INVALID_REPLAY_STATE", "invalid replay state")
    if not _valid_identifier(value["environment_id"]) or not _valid_identifier(value["policy_id"]):
        raise TrustedCheckpointFailure("INVALID_REPLAY_STATE", "invalid replay state identifiers")
    sequence = value["last_sequence"]
    if isinstance(sequence, bool) or not isinstance(sequence, int) or not -1 <= sequence <= 2**63 - 1:
        raise TrustedCheckpointFailure("INVALID_REPLAY_STATE", "invalid replay state sequence")
    attestation = value["last_attestation_id"]
    digest = value["last_checkpoint_canonical_sha256"]
    if not isinstance(digest, str) or not HASH_RE.fullmatch(digest):
        raise TrustedCheckpointFailure("INVALID_REPLAY_STATE", "invalid replay state hash")
    if sequence == -1:
        if attestation is not None or digest != ZERO_HASH:
            raise TrustedCheckpointFailure("INVALID_GENESIS_STATE", "invalid genesis replay state")
    elif not isinstance(attestation, str) or not UUID4_RE.fullmatch(attestation):
        raise TrustedCheckpointFailure("INVALID_REPLAY_STATE", "invalid replay state attestation")
    return value


def validate_root_policy(value: dict[str, Any]) -> dict[str, Any]:
    if set(value) != ROOT_POLICY_FIELDS or value.get("root_policy_version") != ROOT_POLICY_VERSION:
        raise TrustedCheckpointFailure("INVALID_ROOT_POLICY", "invalid root trust policy")
    if not _valid_identifier(value["environment_id"]) or not _valid_identifier(value["policy_id"]):
        raise TrustedCheckpointFailure("INVALID_ROOT_POLICY", "invalid root policy identifiers")
    keys = value["authorized_root_keys"]
    if not isinstance(keys, dict) or not 1 <= len(keys) <= 64:
        raise TrustedCheckpointFailure("INVALID_ROOT_POLICY", "invalid authorized root keys")
    for key_id, digest in keys.items():
        if not _valid_identifier(key_id) or not isinstance(digest, str) or not HASH_RE.fullmatch(digest):
            raise TrustedCheckpointFailure("INVALID_ROOT_POLICY", "invalid authorized root key")
    return value


def build_trusted_checkpoint_verdict(
    verdict: dict[str, Any], envelope: dict[str, Any], raw_public: bytes,
    policy: dict[str, Any], pin: dict[str, Any], policy_pin_validity: dict[str, Any],
    verdict_validity: dict[str, Any], verdict_validity_pin: dict[str, Any],
    verdict_validity_pin_validity: dict[str, Any],
    verdict_validity_pin_validity_pin: dict[str, Any], evaluation_time_utc: str,
    signing_key_id: str, checkpoint: dict[str, Any], checkpoint_signature: bytes,
    root_public_key: bytes, root_trust_policy: dict[str, Any], root_signing_key_id: str,
    replay_state: dict[str, Any], environment_id: str, policy_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    checkpoint = validate_checkpoint(checkpoint)
    replay_state = validate_state(replay_state)
    root_trust_policy = validate_root_policy(root_trust_policy)
    evaluation = parse_utc(evaluation_time_utc, "evaluation_time_utc")
    if not _valid_identifier(environment_id) or not _valid_identifier(policy_id):
        raise TrustedCheckpointFailure("INVALID_IDENTIFIER", "invalid requested identifiers")
    try:
        source_result = build_verdict_validity_pin_validity_pin_verdict(
            verdict, envelope, raw_public, policy, pin, policy_pin_validity,
            verdict_validity, verdict_validity_pin, verdict_validity_pin_validity,
            verdict_validity_pin_validity_pin, evaluation_time_utc, signing_key_id,
        )
    except Exception as exc:
        raise TrustedCheckpointFailure("B224_VERIFICATION_FAILED", "B2.24 verification failed") from exc
    if not isinstance(source_result, dict) or source_result.get("ok") is not True:
        raise TrustedCheckpointFailure("B224_VERIFICATION_FAILED", "B2.24 verification failed")
    source_hash = canonical_sha256(source_result)
    if not hmac.compare_digest(checkpoint["b224_result_canonical_sha256"], source_hash):
        raise TrustedCheckpointFailure("B224_HASH_MISMATCH", "B2.24 result hash mismatch")
    if any(
        value != environment_id
        for value in (checkpoint["environment_id"], replay_state["environment_id"], root_trust_policy["environment_id"])
    ):
        raise TrustedCheckpointFailure("ENVIRONMENT_MISMATCH", "environment mismatch")
    if any(
        value != policy_id
        for value in (checkpoint["policy_id"], replay_state["policy_id"], root_trust_policy["policy_id"])
    ):
        raise TrustedCheckpointFailure("POLICY_MISMATCH", "policy mismatch")
    issued = parse_utc(checkpoint["issued_at_utc"], "issued_at_utc")
    expires = parse_utc(checkpoint["not_after_utc"], "not_after_utc")
    if not issued <= evaluation <= expires:
        raise TrustedCheckpointFailure("CHECKPOINT_NOT_CURRENT", "checkpoint is not current")
    if checkpoint["sequence"] != replay_state["last_sequence"] + 1:
        raise TrustedCheckpointFailure("REPLAY_SEQUENCE", "checkpoint sequence is not next")
    if checkpoint["attestation_id"] == replay_state["last_attestation_id"]:
        raise TrustedCheckpointFailure("REPLAY_ATTESTATION", "attestation was already accepted")
    if not hmac.compare_digest(
        checkpoint["previous_checkpoint_canonical_sha256"],
        replay_state["last_checkpoint_canonical_sha256"],
    ):
        raise TrustedCheckpointFailure("CHECKPOINT_CHAIN_MISMATCH", "checkpoint chain mismatch")
    root_digest = hashlib.sha256(root_public_key).hexdigest()
    expected_root_digest = root_trust_policy["authorized_root_keys"].get(root_signing_key_id)
    if expected_root_digest is None or not hmac.compare_digest(expected_root_digest, root_digest):
        raise TrustedCheckpointFailure("UNAUTHORIZED_ROOT_KEY", "root key is not authorized")
    if len(checkpoint_signature) != 64:
        raise TrustedCheckpointFailure("INVALID_ROOT_SIGNATURE", "invalid root signature")
    try:
        loaded = serialization.load_pem_public_key(root_public_key)
        if not isinstance(loaded, Ed25519PublicKey):
            raise TypeError("not Ed25519")
        loaded.verify(checkpoint_signature, canonical_json_bytes(checkpoint))
    except (ValueError, TypeError, InvalidSignature) as exc:
        raise TrustedCheckpointFailure("INVALID_ROOT_SIGNATURE", "invalid root signature") from exc
    checkpoint_hash = canonical_sha256(checkpoint)
    next_state = {
        "environment_id": environment_id,
        "last_attestation_id": checkpoint["attestation_id"],
        "last_checkpoint_canonical_sha256": checkpoint_hash,
        "last_sequence": checkpoint["sequence"],
        "policy_id": policy_id,
        "state_version": STATE_VERSION,
    }
    result = {
        "attestation_id": checkpoint["attestation_id"], "authorized": True,
        "b224_result_canonical_sha256": source_hash,
        "checkpoint_canonical_sha256": checkpoint_hash,
        "checkpoint_not_after_utc": checkpoint["not_after_utc"],
        "checkpoint_signature_valid": True, "environment_id": environment_id,
        "evaluation_time_utc": evaluation_time_utc, "gate": "GO",
        "next_replay_state_canonical_sha256": canonical_sha256(next_state), "ok": True,
        "policy_id": policy_id,
        "previous_checkpoint_canonical_sha256": checkpoint["previous_checkpoint_canonical_sha256"],
        "replay_detected": False, "replay_state_updated": True,
        "root_policy_canonical_sha256": canonical_sha256(root_trust_policy),
        "root_public_key_sha256": root_digest, "root_signing_key_id": root_signing_key_id,
        "sequence": checkpoint["sequence"],
        "source_verification_version": SOURCE_VERIFICATION_VERSION,
        "verification_version": VERIFICATION_VERSION,
    }
    return result, next_state


def read_bytes(path_value: str, maximum: int, label: str, stdin: BinaryIO) -> bytes:
    if path_value == "-":
        raw = stdin.read(maximum + 1)
    else:
        try:
            with pathlib.Path(path_value).open("rb") as stream:
                raw = stream.read(maximum + 1)
        except OSError as exc:
            raise TrustedCheckpointFailure("FILE_READ_ERROR", f"unable to read {label}") from exc
    if len(raw) > maximum:
        raise TrustedCheckpointFailure("INPUT_TOO_LARGE", f"{label} is too large")
    return raw


@contextlib.contextmanager
def exclusive_lock(path: pathlib.Path, timeout: float) -> Iterator[None]:
    lock_path = pathlib.Path(str(path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    stream = lock_path.open("a+b")
    stream.seek(0, os.SEEK_END)
    if stream.tell() == 0:
        stream.write(b"0")
        stream.flush()
    deadline = time.monotonic() + timeout
    acquired = False
    try:
        while not acquired:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except (OSError, IOError):
                if time.monotonic() >= deadline:
                    raise TrustedCheckpointFailure("LOCK_TIMEOUT", "replay state lock timeout")
                time.sleep(0.02)
        yield
    finally:
        if acquired:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


def atomic_write_state(path: pathlib.Path, value: dict[str, Any]) -> None:
    data = canonical_json_bytes(value)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def parse_arguments(argv: Sequence[str]) -> argparse.Namespace:
    parser = SafeArgumentParser(add_help=False, allow_abbrev=False)
    for option in (
        "--verdict", "--signature", "--public-key-file", "--trust-policy", "--policy-pin",
        "--policy-pin-validity", "--verdict-validity", "--verdict-validity-pin",
        "--verdict-validity-pin-validity", "--verdict-validity-pin-validity-pin",
        "--evaluation-time-utc", "--signing-key-id", "--checkpoint",
        "--checkpoint-signature", "--root-public-key-file", "--root-trust-policy",
        "--root-signing-key-id", "--replay-state-file", "--environment-id", "--policy-id",
    ):
        parser.add_argument(option)
    parser.add_argument("--lock-timeout-seconds", type=float, default=10.0)
    parser.add_argument("--help", action="store_true")
    args = parser.parse_args(list(argv))
    if args.help:
        if len(argv) != 1:
            parser.error("help must be used alone")
        return args
    if any(getattr(args, key) is None for key in vars(args) if key not in {"help"}):
        parser.error("missing arguments")
    if args.lock_timeout_seconds < 0:
        parser.error("invalid lock timeout")
    stdin_values = [getattr(args, key) for key in vars(args) if isinstance(getattr(args, key), str)]
    if stdin_values.count("-") > 1 or args.replay_state_file == "-":
        parser.error("invalid stdin usage")
    return args


def emit_json(value: dict[str, Any]) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


def run_cli(argv: Sequence[str], stdin: BinaryIO = sys.stdin.buffer) -> int:
    try:
        args = parse_arguments(argv)
        if args.help:
            emit_json({"ok": True, "trusted_checkpoint_tool_version": TOOL_VERSION, "usage": USAGE})
            return 0
        names = {
            "verdict": "verdict", "signature": "signature", "public_key_file": "public_key",
            "trust_policy": "policy", "policy_pin": "policy_pin",
            "policy_pin_validity": "policy_pin_validity", "verdict_validity": "verdict_validity",
            "verdict_validity_pin": "verdict_validity_pin",
            "verdict_validity_pin_validity": "verdict_validity_pin_validity",
            "verdict_validity_pin_validity_pin": "verdict_validity_pin_validity_pin",
            "checkpoint": "checkpoint", "checkpoint_signature": "checkpoint_signature",
            "root_public_key_file": "root_public_key", "root_trust_policy": "root_trust_policy",
        }
        raw = {key: read_bytes(getattr(args, key), MAX_BYTES[label], label, stdin) for key, label in names.items()}
        state_path = pathlib.Path(args.replay_state_file)
        with exclusive_lock(state_path, args.lock_timeout_seconds):
            state_raw = read_bytes(str(state_path), MAX_BYTES["replay_state"], "replay_state", stdin)
            result, next_state = build_trusted_checkpoint_verdict(
                parse_json_bytes(raw["verdict"], "verdict"), parse_json_bytes(raw["signature"], "signature"),
                raw["public_key_file"], parse_json_bytes(raw["trust_policy"], "trust_policy"),
                parse_json_bytes(raw["policy_pin"], "policy_pin"),
                parse_json_bytes(raw["policy_pin_validity"], "policy_pin_validity"),
                parse_json_bytes(raw["verdict_validity"], "verdict_validity"),
                parse_json_bytes(raw["verdict_validity_pin"], "verdict_validity_pin"),
                parse_json_bytes(raw["verdict_validity_pin_validity"], "verdict_validity_pin_validity"),
                parse_json_bytes(raw["verdict_validity_pin_validity_pin"], "verdict_validity_pin_validity_pin"),
                args.evaluation_time_utc, args.signing_key_id,
                parse_json_bytes(raw["checkpoint"], "checkpoint"), raw["checkpoint_signature"],
                raw["root_public_key_file"], parse_json_bytes(raw["root_trust_policy"], "root_trust_policy"),
                args.root_signing_key_id, parse_json_bytes(state_raw, "replay_state"),
                args.environment_id, args.policy_id,
            )
            atomic_write_state(state_path, next_state)
        emit_json(result)
        return 0
    except TrustedCheckpointFailure as failure:
        emit_json({"code": failure.code, "gate": failure.gate, "message": failure.message, "ok": False})
        return failure.exit_code
    except Exception:
        emit_json({"code": "INTERNAL_ERROR", "gate": "NO_GO", "message": "internal verification error", "ok": False})
        return 1


if __name__ == "__main__":
    raise SystemExit(run_cli(sys.argv[1:]))
