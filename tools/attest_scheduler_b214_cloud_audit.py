from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ATTESTATION_VERSION = "B2_14_CLOUD_AUDIT_ATTESTATION_V1"
COLLECTOR_VERSION = "B2_13_CLOUD_LOGGING_READ_ONLY_COLLECTOR_V1"
AUDIT_VERSION = "B2_12_MULTI_CYCLE_LOCAL_AUDIT_V1"

MAX_INPUT_BYTES = 1_048_576
RUN_ID_MAX_LENGTH = 128

PROJECT_PATTERN = re.compile(r"^[a-z][a-z0-9-]{4,62}$")
REGION_PATTERN = re.compile(r"^[a-z]+-[a-z]+[0-9]+$")
RESOURCE_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,62}$")

SCOPE_FIELDS = (
    "project",
    "region",
    "service",
    "revision",
    "start_utc",
    "end_utc",
    "required_cycles",
    "limit",
    "excluded_manual_run_count",
)

AUDIT_FIELDS = (
    "audit_version",
    "ok",
    "gate",
    "required_cycles",
    "candidate_event_count",
    "eligible_cycle_count",
    "valid_cycle_count",
    "invalid_cycle_count",
    "retained_cycle_count",
    "retained_cycles",
    "anomaly_count",
    "duplicate_run_id_count",
    "excluded_manual_count",
    "strict_allowed_oui_non",
    "strict_usable_oui_non",
    "ticket_strict",
)

RETAINED_FIELDS = (
    "scheduler_run_id",
    "timestamp",
    "run_status",
    "duration_ms",
    "capture_count",
    "briefs_created",
    "movement_rows_total",
    "new_artifact_oui_non",
)

SECRET_KEY_FRAGMENTS = (
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "secret",
)

RAW_KEYS_FORBIDDEN = {
    "entries",
    "raw_entries",
    "raw_filter",
    "jsonpayload",
    "textpayload",
}


class AttestationError(Exception):
    def __init__(self, code: str, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise AttestationError(
            "ARGUMENT_ERROR",
            _sanitize_message(message),
            2,
        )


def _sanitize_message(message: str) -> str:
    compact = " ".join(str(message).split())

    for marker in SECRET_KEY_FRAGMENTS:
        compact = re.sub(
            rf"(?i){re.escape(marker)}\s*[:=]\s*\S+",
            f"{marker}=[REDACTED]",
            compact,
        )

    return compact[:300]


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _emit(payload: dict[str, Any]) -> None:
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )


def _require_dict(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AttestationError(
            "INVALID_FIELD_TYPE",
            f"{field} must be an object",
            1,
        )

    return value


def _require_list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise AttestationError(
            "INVALID_FIELD_TYPE",
            f"{field} must be an array",
            1,
        )

    return value


def _require_fields(
    value: dict[str, Any],
    fields: tuple[str, ...],
    prefix: str,
) -> None:
    missing = sorted(field for field in fields if field not in value)

    if missing:
        raise AttestationError(
            "MISSING_FIELDS",
            f"{prefix} missing fields: {','.join(missing)}",
            1,
        )


def _require_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise AttestationError(
            "INVALID_FIELD_TYPE",
            f"{field} must be boolean",
            1,
        )

    return value


def _require_non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AttestationError(
            "INVALID_NUMERIC_FIELD",
            f"{field} must be a non-negative integer",
            1,
        )

    return value


def _require_positive_int(
    value: Any,
    field: str,
    maximum: int,
) -> int:
    result = _require_non_negative_int(value, field)

    if result < 1 or result > maximum:
        raise AttestationError(
            "INVALID_NUMERIC_FIELD",
            f"{field} must be between 1 and {maximum}",
            1,
        )

    return result


def _require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AttestationError(
            "INVALID_FIELD_TYPE",
            f"{field} must be a non-empty string",
            1,
        )

    return value.strip()


def _parse_explicit_utc(value: Any, field: str) -> tuple[str, datetime]:
    text = _require_string(value, field)

    if not (text.endswith("Z") or text.endswith("+00:00")):
        raise AttestationError(
            "INVALID_UTC_TIMESTAMP",
            f"{field} must use explicit UTC",
            1,
        )

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AttestationError(
            "INVALID_UTC_TIMESTAMP",
            f"{field} is not a valid timestamp",
            1,
        ) from exc

    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise AttestationError(
            "INVALID_UTC_TIMESTAMP",
            f"{field} must use UTC",
            1,
        )

    return text, parsed


def _find_forbidden_key(value: Any, path: str = "$") -> str | None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).strip().lower()

            if normalized in RAW_KEYS_FORBIDDEN:
                return f"{path}.{key}"

            if any(fragment in normalized for fragment in SECRET_KEY_FRAGMENTS):
                return f"{path}.{key}"

            detected = _find_forbidden_key(nested, f"{path}.{key}")
            if detected:
                return detected

    if isinstance(value, list):
        for index, nested in enumerate(value):
            detected = _find_forbidden_key(nested, f"{path}[{index}]")
            if detected:
                return detected

    return None


def _read_input(input_path: str | None) -> dict[str, Any]:
    try:
        if input_path in (None, "-"):
            text = sys.stdin.read(MAX_INPUT_BYTES + 1)
        else:
            path = Path(input_path)

            if not path.is_file():
                raise AttestationError(
                    "INPUT_FILE_NOT_FOUND",
                    "input file is unavailable",
                    2,
                )

            if path.stat().st_size > MAX_INPUT_BYTES:
                raise AttestationError(
                    "INPUT_TOO_LARGE",
                    "input exceeds the size limit",
                    2,
                )

            text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise AttestationError(
            "INPUT_READ_ERROR",
            _sanitize_message(str(exc)),
            2,
        ) from exc

    if len(text.encode("utf-8")) > MAX_INPUT_BYTES:
        raise AttestationError(
            "INPUT_TOO_LARGE",
            "input exceeds the size limit",
            2,
        )

    text = text.lstrip("\ufeff")

    if not text.strip():
        raise AttestationError(
            "EMPTY_INPUT",
            "input is empty",
            2,
        )

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AttestationError(
            "INVALID_JSON",
            f"invalid JSON at line {exc.lineno}",
            2,
        ) from exc

    if not isinstance(payload, dict):
        raise AttestationError(
            "INVALID_JSON_ROOT",
            "input root must be an object",
            2,
        )

    return payload


def _validate_scope(scope_value: Any) -> dict[str, Any]:
    scope = _require_dict(scope_value, "scope")
    _require_fields(scope, SCOPE_FIELDS, "scope")

    project = _require_string(scope["project"], "scope.project")
    region = _require_string(scope["region"], "scope.region")
    service = _require_string(scope["service"], "scope.service")
    revision = _require_string(scope["revision"], "scope.revision")

    if not PROJECT_PATTERN.fullmatch(project):
        raise AttestationError(
            "INVALID_SCOPE",
            "scope.project is invalid",
            1,
        )

    if not REGION_PATTERN.fullmatch(region):
        raise AttestationError(
            "INVALID_SCOPE",
            "scope.region is invalid",
            1,
        )

    if not RESOURCE_PATTERN.fullmatch(service):
        raise AttestationError(
            "INVALID_SCOPE",
            "scope.service is invalid",
            1,
        )

    if not RESOURCE_PATTERN.fullmatch(revision):
        raise AttestationError(
            "INVALID_SCOPE",
            "scope.revision is invalid",
            1,
        )

    start_text, start_time = _parse_explicit_utc(
        scope["start_utc"],
        "scope.start_utc",
    )
    end_text, end_time = _parse_explicit_utc(
        scope["end_utc"],
        "scope.end_utc",
    )

    if start_time >= end_time:
        raise AttestationError(
            "INVALID_SCOPE",
            "scope.start_utc must precede scope.end_utc",
            1,
        )

    required_cycles = _require_positive_int(
        scope["required_cycles"],
        "scope.required_cycles",
        100,
    )
    limit = _require_positive_int(scope["limit"], "scope.limit", 1000)
    excluded_count = _require_non_negative_int(
        scope["excluded_manual_run_count"],
        "scope.excluded_manual_run_count",
    )

    return {
        "end_utc": end_text,
        "excluded_manual_run_count": excluded_count,
        "limit": limit,
        "project": project,
        "region": region,
        "required_cycles": required_cycles,
        "revision": revision,
        "service": service,
        "start_utc": start_text,
    }


def _validate_retained_cycles(
    retained_value: Any,
    required_cycles: int,
) -> tuple[list[str], list[str]]:
    retained = _require_list(retained_value, "audit.retained_cycles")

    if len(retained) != required_cycles:
        raise AttestationError(
            "INVALID_RETAINED_COUNT",
            "retained cycle array length is inconsistent",
            1,
        )

    run_ids: list[str] = []
    timestamps: list[str] = []
    parsed_timestamps: list[datetime] = []

    for index, raw_cycle in enumerate(retained):
        prefix = f"audit.retained_cycles[{index}]"
        cycle = _require_dict(raw_cycle, prefix)
        _require_fields(cycle, RETAINED_FIELDS, prefix)

        run_id = _require_string(
            cycle["scheduler_run_id"],
            f"{prefix}.scheduler_run_id",
        )

        if len(run_id) > RUN_ID_MAX_LENGTH:
            raise AttestationError(
                "INVALID_RUN_ID",
                f"{prefix}.scheduler_run_id is too long",
                1,
            )

        timestamp_text, timestamp = _parse_explicit_utc(
            cycle["timestamp"],
            f"{prefix}.timestamp",
        )

        run_status = _require_string(
            cycle["run_status"],
            f"{prefix}.run_status",
        )

        if run_status == "H5_RUN_ERROR":
            raise AttestationError(
                "INVALID_RUN_STATUS",
                f"{prefix}.run_status is forbidden",
                1,
            )

        for field in (
            "duration_ms",
            "capture_count",
            "briefs_created",
            "movement_rows_total",
        ):
            _require_non_negative_int(
                cycle[field],
                f"{prefix}.{field}",
            )

        artifact = _require_string(
            cycle["new_artifact_oui_non"],
            f"{prefix}.new_artifact_oui_non",
        )

        if artifact not in {"oui", "non"}:
            raise AttestationError(
                "INVALID_ARTIFACT_FLAG",
                f"{prefix}.new_artifact_oui_non is invalid",
                1,
            )

        run_ids.append(run_id)
        timestamps.append(timestamp_text)
        parsed_timestamps.append(timestamp)

    if len(set(run_ids)) != len(run_ids):
        raise AttestationError(
            "DUPLICATE_RETAINED_RUN_ID",
            "retained scheduler run IDs must be unique",
            1,
        )

    if any(
        current <= previous
        for previous, current in zip(
            parsed_timestamps,
            parsed_timestamps[1:],
        )
    ):
        raise AttestationError(
            "INVALID_RETAINED_ORDER",
            "retained cycles must be chronological",
            1,
        )

    return run_ids, timestamps


def _validate_audit(
    audit_value: Any,
    scope: dict[str, Any],
) -> dict[str, Any]:
    audit = _require_dict(audit_value, "audit")
    _require_fields(audit, AUDIT_FIELDS, "audit")

    if audit["audit_version"] != AUDIT_VERSION:
        raise AttestationError(
            "AUDIT_VERSION_MISMATCH",
            "audit version is unsupported",
            1,
        )

    if not _require_bool(audit["ok"], "audit.ok"):
        raise AttestationError(
            "AUDIT_NO_GO",
            "audit.ok must be true",
            1,
        )

    if audit["gate"] != "GO":
        raise AttestationError(
            "AUDIT_NO_GO",
            "audit.gate must be GO",
            1,
        )

    numeric_fields = (
        "required_cycles",
        "candidate_event_count",
        "eligible_cycle_count",
        "valid_cycle_count",
        "invalid_cycle_count",
        "retained_cycle_count",
        "anomaly_count",
        "duplicate_run_id_count",
        "excluded_manual_count",
    )

    counts = {
        field: _require_non_negative_int(
            audit[field],
            f"audit.{field}",
        )
        for field in numeric_fields
    }

    required_cycles = counts["required_cycles"]

    if required_cycles != scope["required_cycles"]:
        raise AttestationError(
            "REQUIRED_CYCLES_MISMATCH",
            "scope and audit required cycle counts differ",
            1,
        )

    if counts["eligible_cycle_count"] > counts["candidate_event_count"]:
        raise AttestationError(
            "INVALID_COUNTS",
            "eligible cycles exceed candidate cycles",
            1,
        )

    if counts["valid_cycle_count"] != counts["eligible_cycle_count"]:
        raise AttestationError(
            "INVALID_COUNTS",
            "all eligible cycles must be valid",
            1,
        )

    if counts["valid_cycle_count"] < required_cycles:
        raise AttestationError(
            "INSUFFICIENT_VALID_CYCLES",
            "valid cycle count is insufficient",
            1,
        )

    if counts["invalid_cycle_count"] != 0:
        raise AttestationError(
            "INVALID_COUNTS",
            "invalid cycle count must be zero",
            1,
        )

    if counts["retained_cycle_count"] != required_cycles:
        raise AttestationError(
            "INVALID_RETAINED_COUNT",
            "retained cycle count must equal required cycles",
            1,
        )

    if counts["anomaly_count"] != 0:
        raise AttestationError(
            "AUDIT_ANOMALIES_PRESENT",
            "anomaly count must be zero",
            1,
        )

    if counts["duplicate_run_id_count"] != 0:
        raise AttestationError(
            "DUPLICATE_RUN_IDS",
            "duplicate run ID count must be zero",
            1,
        )

    strict_allowed = _require_string(
        audit["strict_allowed_oui_non"],
        "audit.strict_allowed_oui_non",
    )
    strict_usable = _require_string(
        audit["strict_usable_oui_non"],
        "audit.strict_usable_oui_non",
    )
    ticket_strict = _require_string(
        audit["ticket_strict"],
        "audit.ticket_strict",
    )

    if strict_allowed != "non":
        raise AttestationError(
            "INVALID_STRICT_CONTRACT",
            "strict_allowed_oui_non must be non",
            1,
        )

    if strict_usable != "non":
        raise AttestationError(
            "INVALID_STRICT_CONTRACT",
            "strict_usable_oui_non must be non",
            1,
        )

    if ticket_strict != "NO_BET":
        raise AttestationError(
            "INVALID_STRICT_CONTRACT",
            "ticket_strict must be NO_BET",
            1,
        )

    run_ids, timestamps = _validate_retained_cycles(
        audit["retained_cycles"],
        required_cycles,
    )

    return {
        "counts": counts,
        "retained_run_ids": run_ids,
        "retained_timestamps": timestamps,
        "strict_allowed_oui_non": strict_allowed,
        "strict_usable_oui_non": strict_usable,
        "ticket_strict": ticket_strict,
    }


def build_attestation(source: dict[str, Any]) -> dict[str, Any]:
    forbidden_path = _find_forbidden_key(source)

    if forbidden_path:
        raise AttestationError(
            "FORBIDDEN_INPUT_FIELD",
            f"forbidden field detected at {forbidden_path}",
            1,
        )

    if source.get("collector_version") != COLLECTOR_VERSION:
        raise AttestationError(
            "COLLECTOR_VERSION_MISMATCH",
            "collector version is unsupported",
            1,
        )

    if not _require_bool(source.get("ok"), "ok"):
        raise AttestationError(
            "COLLECTOR_NO_GO",
            "collector ok must be true",
            1,
        )

    if source.get("gate") != "GO":
        raise AttestationError(
            "COLLECTOR_NO_GO",
            "collector gate must be GO",
            1,
        )

    gcloud_entry_count = _require_non_negative_int(
        source.get("gcloud_entry_count"),
        "gcloud_entry_count",
    )

    scope = _validate_scope(source.get("scope"))
    audit = _validate_audit(source.get("audit"), scope)
    counts = audit["counts"]

    if gcloud_entry_count < counts["candidate_event_count"]:
        raise AttestationError(
            "INVALID_COUNTS",
            "Cloud entry count is lower than candidate count",
            1,
        )

    source_hash = _sha256(source)

    attestation = {
        "attestation_version": ATTESTATION_VERSION,
        "counts": {
            "anomaly_count": counts["anomaly_count"],
            "candidate_event_count": counts["candidate_event_count"],
            "duplicate_run_id_count": counts["duplicate_run_id_count"],
            "eligible_cycle_count": counts["eligible_cycle_count"],
            "excluded_manual_count": counts["excluded_manual_count"],
            "gcloud_entry_count": gcloud_entry_count,
            "invalid_cycle_count": counts["invalid_cycle_count"],
            "required_cycles": counts["required_cycles"],
            "retained_cycle_count": counts["retained_cycle_count"],
            "valid_cycle_count": counts["valid_cycle_count"],
        },
        "gate": "GO",
        "ok": True,
        "retained_run_ids": audit["retained_run_ids"],
        "retained_time_bounds": {
            "first_utc": audit["retained_timestamps"][0],
            "last_utc": audit["retained_timestamps"][-1],
        },
        "scope": scope,
        "source_canonical_sha256": source_hash,
        "source_versions": {
            "audit": AUDIT_VERSION,
            "collector": COLLECTOR_VERSION,
        },
        "strict_contract": {
            "strict_allowed_oui_non": audit["strict_allowed_oui_non"],
            "strict_usable_oui_non": audit["strict_usable_oui_non"],
            "ticket_strict": audit["ticket_strict"],
        },
    }

    attestation["attestation_sha256"] = _sha256(attestation)
    return attestation


def _build_parser() -> JsonArgumentParser:
    parser = JsonArgumentParser(
        prog="attest_scheduler_b214_cloud_audit.py",
        add_help=False,
    )
    parser.add_argument("--input")
    parser.add_argument("--help", action="store_true")
    return parser


def _help_payload() -> dict[str, Any]:
    return {
        "attestation_version": ATTESTATION_VERSION,
        "ok": True,
        "usage": (
            "attest_scheduler_b214_cloud_audit.py "
            "[--input FILE|-]"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    try:
        args = _build_parser().parse_args(argv)

        if args.help:
            _emit(_help_payload())
            return 0

        source = _read_input(args.input)
        attestation = build_attestation(source)
        _emit(attestation)
        return 0
    except AttestationError as exc:
        _emit(
            {
                "attestation_version": ATTESTATION_VERSION,
                "error_code": exc.code,
                "error_message": _sanitize_message(exc.message),
                "gate": "NO_GO" if exc.exit_code == 1 else "INPUT_ERROR",
                "ok": False,
            }
        )
        return exc.exit_code
    except Exception:
        _emit(
            {
                "attestation_version": ATTESTATION_VERSION,
                "error_code": "INTERNAL_ERROR",
                "error_message": "unexpected local attestation error",
                "gate": "INPUT_ERROR",
                "ok": False,
            }
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
