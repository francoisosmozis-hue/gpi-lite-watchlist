from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


AUDIT_VERSION = "B2_12_MULTI_CYCLE_LOCAL_AUDIT_V1"
EVENT_NAME = "gpi_h5_scheduler_cycle"
MESSAGE_NAME = "GPI_H5_SCHEDULER_AUDIT"
OBSERVABILITY_VERSION = (
    "GPI_H5_SCHEDULER_STRUCTURED_LOG_V0_9_31B2_11"
)
SCHEDULER_VERSION = "SCHEDULER_RUN_DUE_H5_V0_9_31B2_10"
P31_READER_VERSION = (
    "p31_reuse_b2_official_reader_v0_9_31b2_9_fix1"
)

DEFAULT_REQUIRED_CYCLES = 7
MAX_ANOMALY_DETAILS = 20

REQUIRED_FIELDS = {
    "severity",
    "message",
    "event",
    "observability_version",
    "scheduler_run_id",
    "scheduler_version",
    "run_status",
    "h5_run_ok",
    "stability_ok",
    "duration_ms",
    "stage_ok",
    "stage_error_count",
    "latest_course_uid_consistent_oui_non",
    "p31_non_recursive_guard_preserved_oui_non",
    "p31_reader_version_expected",
    "strict_allowed_oui_non",
    "strict_usable_oui_non",
    "ticket_strict",
    "new_artifact_oui_non",
    "capture_count",
    "briefs_created",
    "movement_rows_total",
}

REQUIRED_STAGES = {
    "plan",
    "url_resolver",
    "capture",
    "brief",
    "status_after",
}

NUMERIC_FIELDS = (
    "duration_ms",
    "capture_count",
    "briefs_created",
    "movement_rows_total",
)


class InputContractError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class AuditArgumentParser(argparse.ArgumentParser):
    def error(self, message: str):
        raise InputContractError(
            "ARGUMENT_ERROR",
            message,
        )


def positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(
            "must be an integer"
        ) from exc

    if parsed < 1:
        raise argparse.ArgumentTypeError(
            "must be greater than zero"
        )

    return parsed


def emit_json(payload: dict) -> None:
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def flatten_json_items(value):
    if isinstance(value, list):
        for item in value:
            yield from flatten_json_items(item)
        return

    if isinstance(value, dict):
        yield value
        return

    raise InputContractError(
        "INVALID_TOP_LEVEL_TYPE",
        "JSON input must contain objects or arrays of objects",
    )


def read_input_text(input_value: str) -> str:
    if input_value == "-":
        value = sys.stdin.read()
    else:
        path = Path(input_value)

        try:
            value = path.read_text(
                encoding="utf-8-sig"
            )
        except OSError as exc:
            raise InputContractError(
                "INPUT_FILE_ERROR",
                str(exc),
            ) from exc

    value = value.lstrip("\ufeff")

    if not value.strip():
        raise InputContractError(
            "EMPTY_INPUT",
            "input is empty",
        )

    return value


def parse_input_items(text: str):
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        items = []
        non_empty_lines = [
            line
            for line in text.splitlines()
            if line.strip()
        ]

        if not non_empty_lines:
            raise InputContractError(
                "EMPTY_INPUT",
                "input is empty",
            )

        for line_number, line in enumerate(
            non_empty_lines,
            start=1,
        ):
            try:
                line_value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise InputContractError(
                    "INVALID_JSON_LINE",
                    (
                        f"line {line_number}: "
                        f"{exc.msg}"
                    ),
                ) from exc

            items.extend(
                flatten_json_items(line_value)
            )

        return items, "json_lines"

    items = list(flatten_json_items(parsed))

    input_format = (
        "json_array"
        if isinstance(parsed, list)
        else "json_object"
    )

    return items, input_format


def extract_event(item: dict, source_index: int):
    event = None
    timestamp = ""

    if item.get("event") == EVENT_NAME:
        event = item
    else:
        json_payload = item.get("jsonPayload")

        if (
            isinstance(json_payload, dict)
            and json_payload.get("event")
            == EVENT_NAME
        ):
            event = json_payload
            timestamp = str(
                item.get("timestamp", "") or ""
            )

        if event is None:
            text_payload = item.get("textPayload")

            if isinstance(text_payload, str):
                try:
                    parsed_text = json.loads(
                        text_payload
                    )
                except json.JSONDecodeError:
                    parsed_text = None

                if (
                    isinstance(parsed_text, dict)
                    and parsed_text.get("event")
                    == EVENT_NAME
                ):
                    event = parsed_text
                    timestamp = str(
                        item.get("timestamp", "")
                        or ""
                    )

    if event is None:
        return None

    return {
        "source_index": source_index,
        "timestamp": timestamp,
        "event": event,
        "run_id": str(
            event.get("scheduler_run_id", "")
            or ""
        ).strip(),
    }


def is_non_negative_integer(value) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
    )


def validate_event(event: dict):
    errors = []

    actual_fields = set(event)
    missing_fields = sorted(
        REQUIRED_FIELDS - actual_fields
    )
    extra_fields = sorted(
        actual_fields - REQUIRED_FIELDS
    )

    errors.extend(
        f"MISSING_FIELD:{field}"
        for field in missing_fields
    )
    errors.extend(
        f"UNEXPECTED_FIELD:{field}"
        for field in extra_fields
    )

    if event.get("severity") != "INFO":
        errors.append("INVALID_SEVERITY")

    if event.get("message") != MESSAGE_NAME:
        errors.append("INVALID_MESSAGE")

    if event.get("event") != EVENT_NAME:
        errors.append("INVALID_EVENT")

    if (
        event.get("observability_version")
        != OBSERVABILITY_VERSION
    ):
        errors.append(
            "INVALID_OBSERVABILITY_VERSION"
        )

    if (
        event.get("scheduler_version")
        != SCHEDULER_VERSION
    ):
        errors.append("INVALID_SCHEDULER_VERSION")

    run_id = event.get("scheduler_run_id")

    if (
        not isinstance(run_id, str)
        or not run_id.strip()
    ):
        errors.append("INVALID_RUN_ID")

    run_status = event.get("run_status")

    if (
        not isinstance(run_status, str)
        or not run_status.strip()
    ):
        errors.append("INVALID_RUN_STATUS")
    elif run_status == "H5_RUN_ERROR":
        errors.append("FORBIDDEN_RUN_STATUS")

    if event.get("h5_run_ok") is not True:
        errors.append("H5_RUN_NOT_OK")

    if event.get("stability_ok") is not True:
        errors.append("STABILITY_NOT_OK")

    stage_ok = event.get("stage_ok")

    if not isinstance(stage_ok, dict):
        errors.append("INVALID_STAGE_OBJECT")
    else:
        actual_stages = set(stage_ok)

        if actual_stages != REQUIRED_STAGES:
            errors.append("INVALID_STAGE_KEYS")

        for stage_name in sorted(
            REQUIRED_STAGES
        ):
            if stage_ok.get(stage_name) is not True:
                errors.append(
                    f"STAGE_NOT_OK:{stage_name}"
                )

    if event.get("stage_error_count") != 0:
        errors.append(
            "INVALID_STAGE_ERROR_COUNT"
        )

    if (
        event.get(
            "latest_course_uid_consistent_oui_non"
        )
        not in {"oui", "ND"}
    ):
        errors.append(
            "INVALID_LATEST_CONSISTENCY"
        )

    if (
        event.get(
            "p31_non_recursive_guard_preserved_oui_non"
        )
        != "oui"
    ):
        errors.append("INVALID_P31_GUARD")

    if (
        event.get("p31_reader_version_expected")
        != P31_READER_VERSION
    ):
        errors.append("INVALID_P31_READER")

    if (
        event.get("new_artifact_oui_non")
        not in {"oui", "non"}
    ):
        errors.append("INVALID_NEW_ARTIFACT")

    if (
        event.get("strict_allowed_oui_non")
        != "non"
    ):
        errors.append("STRICT_ALLOWED_OPEN")

    if (
        event.get("strict_usable_oui_non")
        != "non"
    ):
        errors.append("STRICT_USABLE_OPEN")

    if event.get("ticket_strict") != "NO_BET":
        errors.append("INVALID_STRICT_TICKET")

    for field_name in NUMERIC_FIELDS:
        if not is_non_negative_integer(
            event.get(field_name)
        ):
            errors.append(
                f"INVALID_METRIC:{field_name}"
            )

    return sorted(set(errors))


def record_sort_key(record: dict):
    return (
        record["timestamp"],
        record["source_index"],
        record["run_id"],
    )


def safe_retained_cycle(record: dict):
    event = record["event"]

    return {
        "briefs_created": event.get(
            "briefs_created"
        ),
        "capture_count": event.get(
            "capture_count"
        ),
        "duration_ms": event.get(
            "duration_ms"
        ),
        "movement_rows_total": event.get(
            "movement_rows_total"
        ),
        "new_artifact_oui_non": event.get(
            "new_artifact_oui_non"
        ),
        "run_status": event.get("run_status"),
        "scheduler_run_id": record["run_id"],
        "timestamp": record["timestamp"],
    }


def audit_items(
    items,
    input_format: str,
    required_cycles: int,
    excluded_run_ids,
):
    excluded_run_ids = set(excluded_run_ids)

    candidates = []
    unrelated_count = 0

    for source_index, item in enumerate(items):
        record = extract_event(
            item,
            source_index,
        )

        if record is None:
            unrelated_count += 1
            continue

        candidates.append(record)

    excluded_records = []
    eligible_records = []

    for record in candidates:
        if (
            record["run_id"]
            and record["run_id"]
            in excluded_run_ids
        ):
            excluded_records.append(record)
            continue

        eligible_records.append(record)

    run_id_counts = {}

    for record in eligible_records:
        run_id = record["run_id"]

        if not run_id:
            continue

        run_id_counts[run_id] = (
            run_id_counts.get(run_id, 0) + 1
        )

    duplicate_run_ids = sorted(
        run_id
        for run_id, count in run_id_counts.items()
        if count > 1
    )

    valid_records = []
    invalid_records = []

    for record in eligible_records:
        errors = validate_event(record["event"])

        if errors:
            invalid_records.append(
                {
                    "record": record,
                    "errors": errors,
                }
            )
        else:
            valid_records.append(record)

    valid_records = sorted(
        valid_records,
        key=record_sort_key,
    )

    retained_records = valid_records[
        -required_cycles:
    ]

    insufficient_cycles = (
        len(valid_records) < required_cycles
    )

    anomaly_details = []

    for invalid in invalid_records:
        anomaly_details.append(
            {
                "codes": invalid["errors"],
                "scheduler_run_id": (
                    invalid["record"]["run_id"]
                    or "<missing>"
                ),
                "source_index": invalid[
                    "record"
                ]["source_index"],
            }
        )

    for run_id in duplicate_run_ids:
        anomaly_details.append(
            {
                "codes": ["DUPLICATE_RUN_ID"],
                "scheduler_run_id": run_id,
                "source_index": None,
            }
        )

    if insufficient_cycles:
        anomaly_details.append(
            {
                "codes": ["INSUFFICIENT_CYCLES"],
                "scheduler_run_id": None,
                "source_index": None,
            }
        )

    anomaly_count = len(anomaly_details)
    truncated_count = max(
        0,
        anomaly_count - MAX_ANOMALY_DETAILS,
    )

    gate_ok = (
        not invalid_records
        and not duplicate_run_ids
        and not insufficient_cycles
    )

    return {
        "anomalies": anomaly_details[
            :MAX_ANOMALY_DETAILS
        ],
        "anomalies_truncated_count": (
            truncated_count
        ),
        "anomaly_count": anomaly_count,
        "audit_version": AUDIT_VERSION,
        "candidate_event_count": len(
            candidates
        ),
        "duplicate_run_id_count": len(
            duplicate_run_ids
        ),
        "duplicate_run_ids": duplicate_run_ids[
            :MAX_ANOMALY_DETAILS
        ],
        "eligible_cycle_count": len(
            eligible_records
        ),
        "excluded_manual_count": len(
            excluded_records
        ),
        "gate": "GO" if gate_ok else "NO_GO",
        "input_format": input_format,
        "input_item_count": len(items),
        "invalid_cycle_count": len(
            invalid_records
        ),
        "ok": gate_ok,
        "required_cycles": required_cycles,
        "retained_cycle_count": len(
            retained_records
        ),
        "retained_cycles": [
            safe_retained_cycle(record)
            for record in retained_records
        ],
        "strict_allowed_oui_non": "non",
        "strict_usable_oui_non": "non",
        "ticket_strict": "NO_BET",
        "unrelated_entries_ignored": (
            unrelated_count
        ),
        "valid_cycle_count": len(
            valid_records
        ),
    }


def build_parser():
    parser = AuditArgumentParser(
        add_help=False,
        prog="audit_scheduler_b212_cycles.py",
    )

    parser.add_argument(
        "--help",
        action="store_true",
        dest="show_help",
    )
    parser.add_argument(
        "--input",
        default="-",
    )
    parser.add_argument(
        "--required-cycles",
        type=positive_integer,
        default=DEFAULT_REQUIRED_CYCLES,
    )
    parser.add_argument(
        "--exclude-run-id",
        action="append",
        default=[],
    )

    return parser


def main() -> int:
    try:
        parser = build_parser()
        args = parser.parse_args()

        if args.show_help:
            emit_json(
                {
                    "audit_version": AUDIT_VERSION,
                    "ok": True,
                    "usage": (
                        "audit_scheduler_b212_cycles.py "
                        "[--input FILE|-] "
                        "[--required-cycles N] "
                        "[--exclude-run-id RUN_ID]"
                    ),
                }
            )
            return 0

        input_text = read_input_text(args.input)
        items, input_format = parse_input_items(
            input_text
        )

        result = audit_items(
            items=items,
            input_format=input_format,
            required_cycles=args.required_cycles,
            excluded_run_ids=(
                args.exclude_run_id
            ),
        )

        emit_json(result)

        return 0 if result["ok"] else 1

    except InputContractError as exc:
        emit_json(
            {
                "audit_version": AUDIT_VERSION,
                "error_code": exc.code,
                "error_message": str(exc),
                "gate": "INPUT_ERROR",
                "ok": False,
            }
        )
        return 2

    except Exception as exc:
        emit_json(
            {
                "audit_version": AUDIT_VERSION,
                "error_code": "INTERNAL_ERROR",
                "error_message": str(exc),
                "gate": "INPUT_ERROR",
                "ok": False,
            }
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
