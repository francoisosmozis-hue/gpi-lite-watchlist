import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "audit_scheduler_b212_cycles.py"

OBSERVABILITY_VERSION = (
    "GPI_H5_SCHEDULER_STRUCTURED_LOG_V0_9_31B2_11"
)
SCHEDULER_VERSION = "SCHEDULER_RUN_DUE_H5_V0_9_31B2_10"
P31_READER_VERSION = (
    "p31_reuse_b2_official_reader_v0_9_31b2_9_fix1"
)


def make_event(index, **overrides):
    event = {
        "severity": "INFO",
        "message": "GPI_H5_SCHEDULER_AUDIT",
        "event": "gpi_h5_scheduler_cycle",
        "observability_version": (
            OBSERVABILITY_VERSION
        ),
        "scheduler_run_id": f"B210-TEST-{index:03d}",
        "scheduler_version": SCHEDULER_VERSION,
        "run_status": "H5_RUN_CAPTURED_AND_BRIEFED",
        "h5_run_ok": True,
        "stability_ok": True,
        "duration_ms": 1000 + index,
        "stage_ok": {
            "plan": True,
            "url_resolver": True,
            "capture": True,
            "brief": True,
            "status_after": True,
        },
        "stage_error_count": 0,
        "latest_course_uid_consistent_oui_non": "oui",
        "p31_non_recursive_guard_preserved_oui_non": "oui",
        "p31_reader_version_expected": (
            P31_READER_VERSION
        ),
        "strict_allowed_oui_non": "non",
        "strict_usable_oui_non": "non",
        "ticket_strict": "NO_BET",
        "new_artifact_oui_non": "non",
        "capture_count": index,
        "briefs_created": index,
        "movement_rows_total": index * 2,
    }

    event.update(overrides)
    return event


def run_tool(
    payload=None,
    arguments=None,
    raw_text=None,
):
    if arguments is None:
        arguments = []

    if raw_text is None:
        raw_text = json.dumps(
            payload,
            ensure_ascii=False,
        )

    completed = subprocess.run(
        [
            sys.executable,
            str(TOOL),
            *arguments,
        ],
        cwd=ROOT,
        input=raw_text,
        capture_output=True,
        text=True,
        check=False,
    )

    stdout_lines = [
        line
        for line in completed.stdout.splitlines()
        if line.strip()
    ]

    assert len(stdout_lines) == 1
    assert completed.stderr == ""

    return (
        completed,
        json.loads(stdout_lines[0]),
    )


def anomaly_codes(result):
    return {
        code
        for anomaly in result["anomalies"]
        for code in anomaly["codes"]
    }


def test_valid_seven_direct_events_go():
    completed, result = run_tool(
        [make_event(index) for index in range(7)]
    )

    assert completed.returncode == 0
    assert result["ok"] is True
    assert result["gate"] == "GO"
    assert result["valid_cycle_count"] == 7
    assert result["retained_cycle_count"] == 7
    assert result["ticket_strict"] == "NO_BET"


def test_cloud_json_payload_is_supported():
    entries = [
        {
            "timestamp": "2026-07-14T21:00:00Z",
            "jsonPayload": make_event(1),
        }
    ]

    completed, result = run_tool(
        entries,
        arguments=[
            "--required-cycles",
            "1",
        ],
    )

    assert completed.returncode == 0
    assert result["input_format"] == "json_array"
    assert result["valid_cycle_count"] == 1


def test_cloud_text_payload_json_lines_supported():
    entries = [
        {
            "timestamp": (
                f"2026-07-14T21:0{index}:00Z"
            ),
            "textPayload": json.dumps(
                make_event(index)
            ),
        }
        for index in range(2)
    ]

    raw_text = "\n".join(
        json.dumps(entry)
        for entry in entries
    )

    completed, result = run_tool(
        raw_text=raw_text,
        arguments=[
            "--required-cycles",
            "2",
        ],
    )

    assert completed.returncode == 0
    assert result["input_format"] == "json_lines"
    assert result["valid_cycle_count"] == 2


def test_unrelated_entries_are_ignored():
    payload = [
        {"textPayload": "startup"},
        {"jsonPayload": {"event": "other_event"}},
        make_event(1),
    ]

    completed, result = run_tool(
        payload,
        arguments=[
            "--required-cycles",
            "1",
        ],
    )

    assert completed.returncode == 0
    assert result["unrelated_entries_ignored"] == 2


def test_manual_run_ids_are_excluded_explicitly():
    manual = make_event(
        1,
        scheduler_run_id="MANUAL-RUN",
    )
    automatic = make_event(2)

    completed, result = run_tool(
        [manual, automatic],
        arguments=[
            "--required-cycles",
            "1",
            "--exclude-run-id",
            "MANUAL-RUN",
        ],
    )

    assert completed.returncode == 0
    assert result["excluded_manual_count"] == 1
    assert result["eligible_cycle_count"] == 1


def test_duplicate_run_ids_are_rejected():
    first = make_event(
        1,
        scheduler_run_id="DUPLICATE",
    )
    second = make_event(
        2,
        scheduler_run_id="DUPLICATE",
    )

    completed, result = run_tool(
        [first, second],
        arguments=[
            "--required-cycles",
            "1",
        ],
    )

    assert completed.returncode == 1
    assert result["gate"] == "NO_GO"
    assert result["duplicate_run_ids"] == [
        "DUPLICATE"
    ]


def test_insufficient_cycles_are_rejected():
    completed, result = run_tool(
        [make_event(index) for index in range(6)]
    )

    assert completed.returncode == 1
    assert "INSUFFICIENT_CYCLES" in anomaly_codes(
        result
    )


def test_open_strict_cycle_is_rejected():
    invalid = make_event(
        1,
        strict_allowed_oui_non="oui",
    )

    completed, result = run_tool(
        [invalid],
        arguments=[
            "--required-cycles",
            "1",
        ],
    )

    assert completed.returncode == 1
    assert "STRICT_ALLOWED_OPEN" in anomaly_codes(
        result
    )


def test_invalid_stage_contract_is_rejected():
    invalid = make_event(1)
    invalid["stage_ok"]["capture"] = False

    completed, result = run_tool(
        [invalid],
        arguments=[
            "--required-cycles",
            "1",
        ],
    )

    assert completed.returncode == 1
    assert "STAGE_NOT_OK:capture" in anomaly_codes(
        result
    )


@pytest.mark.parametrize(
    ("field_name", "invalid_value", "expected_code"),
    [
        (
            "p31_reader_version_expected",
            "WRONG_READER",
            "INVALID_P31_READER",
        ),
        (
            "new_artifact_oui_non",
            "ND",
            "INVALID_NEW_ARTIFACT",
        ),
    ],
)
def test_reader_and_artifact_contracts(
    field_name,
    invalid_value,
    expected_code,
):
    invalid = make_event(
        1,
        **{field_name: invalid_value},
    )

    completed, result = run_tool(
        [invalid],
        arguments=[
            "--required-cycles",
            "1",
        ],
    )

    assert completed.returncode == 1
    assert expected_code in anomaly_codes(result)


def test_malformed_input_returns_code_two_without_traceback():
    completed, result = run_tool(
        raw_text="{not-json"
    )

    assert completed.returncode == 2
    assert result["gate"] == "INPUT_ERROR"
    assert result["error_code"] == "INVALID_JSON_LINE"
    assert "Traceback" not in completed.stdout


def test_output_is_deterministic():
    payload = [
        make_event(index)
        for index in range(7)
    ]

    first, _ = run_tool(payload)
    second, _ = run_tool(payload)

    assert first.stdout == second.stdout


def test_local_file_input_is_supported(tmp_path):
    input_path = tmp_path / "cycles.json"
    input_path.write_text(
        json.dumps([make_event(1)]),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(TOOL),
            "--input",
            str(input_path),
            "--required-cycles",
            "1",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stderr == ""

    result = json.loads(completed.stdout)
    assert result["valid_cycle_count"] == 1


def test_negative_numeric_metric_is_rejected():
    invalid = make_event(
        1,
        duration_ms=-1,
    )

    completed, result = run_tool(
        [invalid],
        arguments=[
            "--required-cycles",
            "1",
        ],
    )

    assert completed.returncode == 1
    assert (
        "INVALID_METRIC:duration_ms"
        in anomaly_codes(result)
    )


def test_unexpected_event_field_is_rejected():
    invalid = make_event(1)
    invalid["unexpected"] = "value"

    completed, result = run_tool(
        [invalid],
        arguments=[
            "--required-cycles",
            "1",
        ],
    )

    assert completed.returncode == 1
    assert (
        "UNEXPECTED_FIELD:unexpected"
        in anomaly_codes(result)
    )


def test_anomaly_details_are_bounded():
    invalid_events = [
        make_event(
            index,
            strict_usable_oui_non="oui",
        )
        for index in range(25)
    ]

    completed, result = run_tool(
        invalid_events,
        arguments=[
            "--required-cycles",
            "1",
        ],
    )

    assert completed.returncode == 1
    assert len(result["anomalies"]) == 20
    assert result["anomalies_truncated_count"] > 0


def test_tool_has_no_network_or_gcs_imports():
    source = TOOL.read_text(encoding="utf-8-sig")
    tree = ast.parse(source)

    imported_roots = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(
                alias.name.split(".")[0]
                for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported_roots.add(
                    node.module.split(".")[0]
                )

    assert imported_roots <= {
        "__future__",
        "argparse",
        "json",
        "pathlib",
        "sys",
    }

    assert "requests" not in source
    assert "urllib" not in source
    assert "google.cloud" not in source
    assert ".write_text(" not in source
    assert ".write_bytes(" not in source
