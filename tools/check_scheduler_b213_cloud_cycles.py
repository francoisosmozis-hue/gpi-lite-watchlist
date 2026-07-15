#!/usr/bin/env python3
"""Read-only Cloud Logging collector delegating cycle validation to B2.12."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence
import os
import shutil


COLLECTOR_VERSION = "B2_13_CLOUD_LOGGING_READ_ONLY_COLLECTOR_V1"
B212_AUDITOR_NAME = "audit_scheduler_b212_cycles.py"

EVENT_NAME = "gpi_h5_scheduler_cycle"
LOG_STREAM = "run.googleapis.com%2Fstdout"

PROJECT_PATTERN = re.compile(r"^[a-z][a-z0-9-]{4,62}$")
REGION_PATTERN = re.compile(r"^[a-z]+-[a-z]+[0-9]+$")
SERVICE_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
REVISION_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,62}$")

USAGE = (
    "check_scheduler_b213_cloud_cycles.py "
    "--project PROJECT --region REGION --service SERVICE "
    "--revision REVISION --start-utc UTC --end-utc UTC "
    "[--required-cycles N] [--exclude-run-id RUN_ID] "
    "[--limit N] [--timeout-seconds N]"
)



# B2_13_WINDOWS_GCLOUD_RESOLUTION_V1
def _resolve_gcloud_executable():
    """Resolve the Cloud SDK launcher while keeping shell=False."""
    launcher_names = (
        ("gcloud.cmd", "gcloud.exe", "gcloud")
        if os.name == "nt"
        else ("gcloud", "gcloud.cmd", "gcloud.exe")
    )

    for launcher_name in launcher_names:
        resolved = shutil.which(launcher_name)
        if resolved:
            return resolved

    candidates = []

    cloud_sdk_root = os.environ.get("CLOUDSDK_ROOT_DIR")
    if cloud_sdk_root:
        candidates.append(
            os.path.join(cloud_sdk_root, "bin", "gcloud.cmd")
        )

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(
            os.path.join(
                local_app_data,
                "Google",
                "Cloud SDK",
                "google-cloud-sdk",
                "bin",
                "gcloud.cmd",
            )
        )

    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate

    return None


class ArgumentError(Exception):
    """Safe command-line error."""


class ValidationError(Exception):
    """Safe validated-input error."""


class CollectionError(Exception):
    """Safe Cloud Logging collection error."""

    def __init__(self, code: str, message: str, exit_code: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code


class DelegationError(Exception):
    """Safe B2.12 delegation error."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class SafeArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that never writes usage or errors to stderr."""

    def error(self, message: str) -> None:
        raise ArgumentError("invalid command-line arguments")


def emit_json(payload: dict[str, Any]) -> None:
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def emit_error(
    *,
    error_code: str,
    error_message: str,
    gate: str,
    extra: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "collector_version": COLLECTOR_VERSION,
        "error_code": error_code,
        "error_message": error_message,
        "gate": gate,
        "ok": False,
    }

    if extra:
        payload.update(extra)

    emit_json(payload)


def build_parser() -> SafeArgumentParser:
    parser = SafeArgumentParser(add_help=False)

    parser.add_argument("--project", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--service", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--start-utc", required=True)
    parser.add_argument("--end-utc", required=True)

    parser.add_argument("--required-cycles", type=int, default=7)
    parser.add_argument("--exclude-run-id", action="append", default=[])
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--timeout-seconds", type=int, default=120)

    return parser


def parse_utc(value: str, field_name: str) -> dt.datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name} must be an explicit UTC timestamp")

    candidate = value.strip()
    prepared = candidate

    if candidate.endswith("Z"):
        prepared = candidate[:-1] + "+00:00"

    try:
        parsed = dt.datetime.fromisoformat(prepared)
    except ValueError as exc:
        raise ValidationError(
            f"{field_name} must be a valid ISO-8601 UTC timestamp"
        ) from exc

    if parsed.tzinfo is None:
        raise ValidationError(f"{field_name} must include an explicit UTC zone")

    offset = parsed.utcoffset()

    if offset != dt.timedelta(0):
        raise ValidationError(f"{field_name} must use the UTC timezone")

    return parsed.astimezone(dt.timezone.utc)


def format_utc(value: dt.datetime) -> str:
    timespec = "microseconds" if value.microsecond else "seconds"
    return value.isoformat(timespec=timespec).replace("+00:00", "Z")


def validate_identifier(
    *,
    value: str,
    pattern: re.Pattern[str],
    field_name: str,
) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValidationError(f"{field_name} has an invalid format")

    return value


def validate_run_ids(values: Sequence[str]) -> list[str]:
    validated: set[str] = set()

    for value in values:
        if not isinstance(value, str):
            raise ValidationError("exclude-run-id must be text")

        candidate = value.strip()

        if not candidate or len(candidate) > 128:
            raise ValidationError("exclude-run-id has an invalid length")

        if any(ord(character) < 32 for character in candidate):
            raise ValidationError("exclude-run-id contains control characters")

        validated.add(candidate)

    return sorted(validated)


def validate_arguments(
    args: argparse.Namespace,
    *,
    now_utc: dt.datetime | None = None,
) -> dict[str, Any]:
    project = validate_identifier(
        value=args.project,
        pattern=PROJECT_PATTERN,
        field_name="project",
    )
    region = validate_identifier(
        value=args.region,
        pattern=REGION_PATTERN,
        field_name="region",
    )
    service = validate_identifier(
        value=args.service,
        pattern=SERVICE_PATTERN,
        field_name="service",
    )
    revision = validate_identifier(
        value=args.revision,
        pattern=REVISION_PATTERN,
        field_name="revision",
    )

    if not 1 <= args.required_cycles <= 100:
        raise ValidationError("required-cycles must be between 1 and 100")

    if not 1 <= args.limit <= 1000:
        raise ValidationError("limit must be between 1 and 1000")

    if not 1 <= args.timeout_seconds <= 900:
        raise ValidationError("timeout-seconds must be between 1 and 900")

    start_utc = parse_utc(args.start_utc, "start-utc")
    end_utc = parse_utc(args.end_utc, "end-utc")

    if start_utc >= end_utc:
        raise ValidationError("start-utc must be earlier than end-utc")

    reference_now = now_utc or dt.datetime.now(dt.timezone.utc)

    if reference_now.tzinfo is None:
        reference_now = reference_now.replace(tzinfo=dt.timezone.utc)

    reference_now = reference_now.astimezone(dt.timezone.utc)

    if end_utc > reference_now:
        raise ValidationError("end-utc cannot be in the future")

    excluded_run_ids = validate_run_ids(args.exclude_run_id)

    return {
        "project": project,
        "region": region,
        "service": service,
        "revision": revision,
        "start_utc": format_utc(start_utc),
        "end_utc": format_utc(end_utc),
        "required_cycles": args.required_cycles,
        "excluded_run_ids": excluded_run_ids,
        "limit": args.limit,
        "timeout_seconds": args.timeout_seconds,
    }


def build_logging_filter(config: dict[str, Any]) -> str:
    return (
        'resource.type="cloud_run_revision" '
        f'AND resource.labels.location="{config["region"]}" '
        f'AND resource.labels.service_name="{config["service"]}" '
        f'AND resource.labels.revision_name="{config["revision"]}" '
        f'AND logName:"{LOG_STREAM}" '
        f'AND timestamp>="{config["start_utc"]}" '
        f'AND timestamp<="{config["end_utc"]}" '
        f'AND (jsonPayload.event="{EVENT_NAME}" '
        f'OR textPayload:"{EVENT_NAME}")'
    )


def build_gcloud_command(config: dict[str, Any]) -> list[str]:
    logging_filter = build_logging_filter(config)

    return [
        "gcloud",
        "logging",
        "read",
        logging_filter,
        "--project",
        config["project"],
        "--format=json",
        "--order=asc",
        "--limit",
        str(config["limit"]),
    ]


def collect_log_entries(config: dict[str, Any]) -> list[Any]:
    command = build_gcloud_command(config)

    try:
        completed = subprocess.run(
            command,
            executable=_resolve_gcloud_executable(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=config["timeout_seconds"],
            shell=False,
            check=False,
        )
    except FileNotFoundError as exc:
        raise CollectionError(
            "GCLOUD_NOT_FOUND",
            "gcloud logging read is unavailable",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise CollectionError(
            "GCLOUD_TIMEOUT",
            "gcloud logging read timed out",
        ) from exc
    except OSError as exc:
        raise CollectionError(
            "GCLOUD_EXECUTION_ERROR",
            "gcloud logging read could not be executed",
        ) from exc

    if completed.returncode != 0:
        raise CollectionError(
            "GCLOUD_READ_FAILED",
            "gcloud logging read returned a non-zero exit code",
            exit_code=int(completed.returncode),
        )

    try:
        payload = json.loads(completed.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise CollectionError(
            "GCLOUD_INVALID_JSON",
            "gcloud logging read returned invalid JSON",
        ) from exc

    if not isinstance(payload, list):
        raise CollectionError(
            "GCLOUD_INVALID_PAYLOAD",
            "gcloud logging read must return a JSON array",
        )

    return payload


def build_auditor_command(config: dict[str, Any]) -> list[str]:
    auditor_path = Path(__file__).resolve().with_name(B212_AUDITOR_NAME)

    if not auditor_path.is_file():
        raise DelegationError(
            "B212_AUDITOR_NOT_FOUND",
            "the delegated B2.12 auditor is unavailable",
        )

    command = [
        sys.executable,
        str(auditor_path),
        "--input",
        "-",
        "--required-cycles",
        str(config["required_cycles"]),
    ]

    for run_id in config["excluded_run_ids"]:
        command.extend(["--exclude-run-id", run_id])

    return command



# B2_13_PROMOTED_SEVERITY_NORMALIZATION_V1
def _restore_promoted_severity(entries: list[Any]) -> list[Any]:
    """Restore Cloud Logging's promoted severity without mutating raw entries."""
    normalized_entries: list[Any] = []

    for entry in entries:
        if not isinstance(entry, dict):
            normalized_entries.append(entry)
            continue

        normalized_entry = dict(entry)
        promoted_severity = normalized_entry.get("severity")

        if not isinstance(promoted_severity, str) or not promoted_severity.strip():
            normalized_entries.append(normalized_entry)
            continue

        promoted_severity = promoted_severity.strip()
        json_payload = normalized_entry.get("jsonPayload")

        if isinstance(json_payload, dict):
            normalized_payload = dict(json_payload)

            if (
                normalized_payload.get("event") == EVENT_NAME
                and "severity" not in normalized_payload
            ):
                normalized_payload["severity"] = promoted_severity

            normalized_entry["jsonPayload"] = normalized_payload
            normalized_entries.append(normalized_entry)
            continue

        text_payload = normalized_entry.get("textPayload")

        if isinstance(text_payload, str):
            try:
                parsed_text_payload = json.loads(text_payload)
            except json.JSONDecodeError:
                parsed_text_payload = None

            if (
                isinstance(parsed_text_payload, dict)
                and parsed_text_payload.get("event") == EVENT_NAME
                and "severity" not in parsed_text_payload
            ):
                parsed_text_payload["severity"] = promoted_severity
                normalized_entry["textPayload"] = json.dumps(
                    parsed_text_payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )

        normalized_entries.append(normalized_entry)

    return normalized_entries


def delegate_to_b212(
    entries: list[Any],
    config: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    command = build_auditor_command(config)
    input_text = json.dumps(
        entries,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    try:
        completed = subprocess.run(
            command,
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=config["timeout_seconds"],
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise DelegationError(
            "B212_AUDITOR_TIMEOUT",
            "the delegated B2.12 auditor timed out",
        ) from exc
    except OSError as exc:
        raise DelegationError(
            "B212_AUDITOR_EXECUTION_ERROR",
            "the delegated B2.12 auditor could not be executed",
        ) from exc

    if completed.returncode not in {0, 1, 2}:
        raise DelegationError(
            "B212_AUDITOR_EXIT_CODE",
            "the delegated B2.12 auditor returned an unexpected exit code",
        )

    output_lines = [
        line.strip()
        for line in completed.stdout.splitlines()
        if line.strip()
    ]

    if len(output_lines) != 1:
        raise DelegationError(
            "B212_AUDITOR_OUTPUT_LINES",
            "the delegated B2.12 auditor must return one JSON line",
        )

    try:
        audit_summary = json.loads(output_lines[0])
    except json.JSONDecodeError as exc:
        raise DelegationError(
            "B212_AUDITOR_INVALID_JSON",
            "the delegated B2.12 auditor returned invalid JSON",
        ) from exc

    if not isinstance(audit_summary, dict):
        raise DelegationError(
            "B212_AUDITOR_INVALID_PAYLOAD",
            "the delegated B2.12 auditor must return a JSON object",
        )

    expected_gate = {
        0: "GO",
        1: "NO_GO",
        2: "INPUT_ERROR",
    }[completed.returncode]

    if audit_summary.get("gate") != expected_gate:
        raise DelegationError(
            "B212_AUDITOR_GATE_MISMATCH",
            "the delegated B2.12 audit gate does not match its exit code",
        )

    return int(completed.returncode), audit_summary


def build_success_payload(
    *,
    config: dict[str, Any],
    entry_count: int,
    audit_exit_code: int,
    audit_summary: dict[str, Any],
) -> dict[str, Any]:
    return {
        "audit": audit_summary,
        "collector_version": COLLECTOR_VERSION,
        "gate": audit_summary["gate"],
        "gcloud_entry_count": entry_count,
        "ok": audit_exit_code == 0,
        "scope": {
            "end_utc": config["end_utc"],
            "excluded_manual_run_count": len(config["excluded_run_ids"]),
            "limit": config["limit"],
            "project": config["project"],
            "region": config["region"],
            "required_cycles": config["required_cycles"],
            "revision": config["revision"],
            "service": config["service"],
            "start_utc": config["start_utc"],
        },
    }


def run(
    argv: Sequence[str] | None = None,
    *,
    now_utc: dt.datetime | None = None,
) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)

    if "-h" in arguments or "--help" in arguments:
        emit_json(
            {
                "collector_version": COLLECTOR_VERSION,
                "ok": True,
                "usage": USAGE,
            }
        )
        return 0

    try:
        parser = build_parser()
        parsed = parser.parse_args(arguments)
        config = validate_arguments(parsed, now_utc=now_utc)

        entries = collect_log_entries(config)
        audit_exit_code, audit_summary = delegate_to_b212(_restore_promoted_severity(entries), config)

        payload = build_success_payload(
            config=config,
            entry_count=len(entries),
            audit_exit_code=audit_exit_code,
            audit_summary=audit_summary,
        )

        emit_json(payload)
        return audit_exit_code

    except (ArgumentError, ValidationError):
        emit_error(
            error_code="ARGUMENT_ERROR",
            error_message="invalid or unsafe command-line arguments",
            gate="INPUT_ERROR",
        )
        return 2

    except CollectionError as exc:
        extra: dict[str, Any] = {}

        if exc.exit_code is not None:
            extra["gcloud_exit_code"] = exc.exit_code

        emit_error(
            error_code=exc.code,
            error_message=exc.message,
            gate="COLLECTION_ERROR",
            extra=extra,
        )
        return 3

    except DelegationError as exc:
        emit_error(
            error_code=exc.code,
            error_message=exc.message,
            gate="COLLECTION_ERROR",
        )
        return 3

    except Exception:
        emit_error(
            error_code="UNEXPECTED_LOCAL_ERROR",
            error_message="the local collector failed safely",
            gate="COLLECTION_ERROR",
        )
        return 3


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()