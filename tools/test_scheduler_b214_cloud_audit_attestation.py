from __future__ import annotations

import ast
import copy
import hashlib
import io
import json
import runpy
from pathlib import Path
from typing import Any

import pytest

TOOL_PATH = Path(__file__).with_name(
    "attest_scheduler_b214_cloud_audit.py"
)


def load_tool() -> dict[str, Any]:
    return runpy.run_path(str(TOOL_PATH))


@pytest.fixture
def valid_payload() -> dict[str, Any]:
    retained = []

    for index in range(7):
        minute = 10 + index * 3
        retained.append(
            {
                "briefs_created": 15,
                "capture_count": 0,
                "duration_ms": 90_000 + index,
                "movement_rows_total": 193,
                "new_artifact_oui_non": "non",
                "run_status": "H5_RUN_BRIEF_REPLAYED_POST_START_EXCLUDED",
                "scheduler_run_id": f"B214-LOCAL-{index + 1:04d}",
                "timestamp": f"2026-07-14T21:{minute:02d}:00Z",
            }
        )

    return {
        "audit": {
            "anomalies": [],
            "anomaly_count": 0,
            "audit_version": "B2_12_MULTI_CYCLE_LOCAL_AUDIT_V1",
            "candidate_event_count": 8,
            "duplicate_run_id_count": 0,
            "duplicate_run_ids": [],
            "eligible_cycle_count": 8,
            "excluded_manual_count": 0,
            "gate": "GO",
            "invalid_cycle_count": 0,
            "ok": True,
            "required_cycles": 7,
            "retained_cycle_count": 7,
            "retained_cycles": retained,
            "strict_allowed_oui_non": "non",
            "strict_usable_oui_non": "non",
            "ticket_strict": "NO_BET",
            "valid_cycle_count": 8,
        },
        "collector_version": "B2_13_CLOUD_LOGGING_READ_ONLY_COLLECTOR_V1",
        "gate": "GO",
        "gcloud_entry_count": 8,
        "ok": True,
        "scope": {
            "end_utc": "2026-07-14T21:30:00Z",
            "excluded_manual_run_count": 1,
            "limit": 100,
            "project": "analyse-hippique",
            "region": "europe-west1",
            "required_cycles": 7,
            "revision": "gpi-lite-watchlist-00359-boq",
            "service": "gpi-lite-watchlist",
            "start_utc": "2026-07-14T21:00:00Z",
        },
    }


def invoke(
    namespace: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    raw_input: str,
    arguments: list[str] | None = None,
) -> tuple[int, dict[str, Any], str]:
    monkeypatch.setattr(namespace["sys"], "stdin", io.StringIO(raw_input))
    exit_code = namespace["main"](arguments or [])
    captured = capsys.readouterr()

    lines = [
        line.strip()
        for line in captured.out.splitlines()
        if line.strip()
    ]

    assert len(lines) == 1
    payload = json.loads(lines[0])
    return exit_code, payload, captured.err


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_valid_go_attestation(
    valid_payload: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    namespace = load_tool()
    raw = json.dumps(valid_payload)

    exit_code, output, stderr = invoke(
        namespace,
        monkeypatch,
        capsys,
        raw,
    )

    assert exit_code == 0
    assert stderr == ""
    assert output["ok"] is True
    assert output["gate"] == "GO"
    assert output["attestation_version"] == (
        "B2_14_CLOUD_AUDIT_ATTESTATION_V1"
    )
    assert output["counts"]["valid_cycle_count"] == 8
    assert output["counts"]["retained_cycle_count"] == 7
    assert len(output["retained_run_ids"]) == 7
    assert output["strict_contract"]["ticket_strict"] == "NO_BET"


def test_attestation_hashes_are_exact(
    valid_payload: dict[str, Any],
) -> None:
    namespace = load_tool()
    output = namespace["build_attestation"](valid_payload)

    assert output["source_canonical_sha256"] == canonical_hash(valid_payload)

    attestation_without_hash = copy.deepcopy(output)
    actual_hash = attestation_without_hash.pop("attestation_sha256")

    assert actual_hash == canonical_hash(attestation_without_hash)


def test_semantically_equivalent_input_is_deterministic(
    valid_payload: dict[str, Any],
) -> None:
    namespace = load_tool()

    first = namespace["build_attestation"](valid_payload)
    reordered = json.loads(
        json.dumps(valid_payload, sort_keys=True, indent=4)
    )
    second = namespace["build_attestation"](reordered)

    assert first == second
    assert first["source_canonical_sha256"] == (
        second["source_canonical_sha256"]
    )


def test_stdin_without_argument(
    valid_payload: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    namespace = load_tool()
    exit_code, output, _ = invoke(
        namespace,
        monkeypatch,
        capsys,
        json.dumps(valid_payload),
    )

    assert exit_code == 0
    assert output["gate"] == "GO"


def test_file_input_with_utf8_bom(
    valid_payload: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    namespace = load_tool()
    input_path = tmp_path / "collector.json"
    input_path.write_bytes(
        b"\xef\xbb\xbf"
        + json.dumps(valid_payload).encode("utf-8")
    )

    exit_code, output, _ = invoke(
        namespace,
        monkeypatch,
        capsys,
        "",
        ["--input", str(input_path)],
    )

    assert exit_code == 0
    assert output["gate"] == "GO"


@pytest.mark.parametrize(
    ("field", "value", "expected_code"),
    [
        ("ok", False, "COLLECTOR_NO_GO"),
        ("gate", "NO_GO", "COLLECTOR_NO_GO"),
        (
            "collector_version",
            "WRONG_VERSION",
            "COLLECTOR_VERSION_MISMATCH",
        ),
    ],
)
def test_invalid_collector_contract(
    valid_payload: dict[str, Any],
    field: str,
    value: Any,
    expected_code: str,
) -> None:
    namespace = load_tool()
    payload = copy.deepcopy(valid_payload)
    payload[field] = value

    with pytest.raises(namespace["AttestationError"]) as captured:
        namespace["build_attestation"](payload)

    assert captured.value.code == expected_code
    assert captured.value.exit_code == 1


@pytest.mark.parametrize(
    ("field", "value", "expected_code"),
    [
        ("ok", False, "AUDIT_NO_GO"),
        ("gate", "NO_GO", "AUDIT_NO_GO"),
        (
            "audit_version",
            "WRONG_VERSION",
            "AUDIT_VERSION_MISMATCH",
        ),
    ],
)
def test_invalid_audit_contract(
    valid_payload: dict[str, Any],
    field: str,
    value: Any,
    expected_code: str,
) -> None:
    namespace = load_tool()
    payload = copy.deepcopy(valid_payload)
    payload["audit"][field] = value

    with pytest.raises(namespace["AttestationError"]) as captured:
        namespace["build_attestation"](payload)

    assert captured.value.code == expected_code


def test_required_cycle_scope_mismatch(
    valid_payload: dict[str, Any],
) -> None:
    namespace = load_tool()
    payload = copy.deepcopy(valid_payload)
    payload["scope"]["required_cycles"] = 6

    with pytest.raises(namespace["AttestationError"]) as captured:
        namespace["build_attestation"](payload)

    assert captured.value.code == "REQUIRED_CYCLES_MISMATCH"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("eligible_cycle_count", 9),
        ("valid_cycle_count", 7),
        ("invalid_cycle_count", 1),
        ("retained_cycle_count", 6),
        ("anomaly_count", 1),
        ("duplicate_run_id_count", 1),
    ],
)
def test_invalid_audit_counts(
    valid_payload: dict[str, Any],
    field: str,
    value: int,
) -> None:
    namespace = load_tool()
    payload = copy.deepcopy(valid_payload)
    payload["audit"][field] = value

    with pytest.raises(namespace["AttestationError"]):
        namespace["build_attestation"](payload)


def test_duplicate_retained_run_ids(
    valid_payload: dict[str, Any],
) -> None:
    namespace = load_tool()
    payload = copy.deepcopy(valid_payload)
    payload["audit"]["retained_cycles"][1]["scheduler_run_id"] = (
        payload["audit"]["retained_cycles"][0]["scheduler_run_id"]
    )

    with pytest.raises(namespace["AttestationError"]) as captured:
        namespace["build_attestation"](payload)

    assert captured.value.code == "DUPLICATE_RETAINED_RUN_ID"


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-07-14T21:10:00",
        "not-a-timestamp",
        "2026-07-14T23:10:00+02:00",
    ],
)
def test_invalid_retained_timestamp(
    valid_payload: dict[str, Any],
    timestamp: str,
) -> None:
    namespace = load_tool()
    payload = copy.deepcopy(valid_payload)
    payload["audit"]["retained_cycles"][0]["timestamp"] = timestamp

    with pytest.raises(namespace["AttestationError"]) as captured:
        namespace["build_attestation"](payload)

    assert captured.value.code == "INVALID_UTC_TIMESTAMP"


def test_non_chronological_retained_cycles(
    valid_payload: dict[str, Any],
) -> None:
    namespace = load_tool()
    payload = copy.deepcopy(valid_payload)
    payload["audit"]["retained_cycles"][1]["timestamp"] = (
        "2026-07-14T21:09:00Z"
    )

    with pytest.raises(namespace["AttestationError"]) as captured:
        namespace["build_attestation"](payload)

    assert captured.value.code == "INVALID_RETAINED_ORDER"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("strict_allowed_oui_non", "oui"),
        ("strict_usable_oui_non", "oui"),
        ("ticket_strict", "BET"),
    ],
)
def test_invalid_strict_contract(
    valid_payload: dict[str, Any],
    field: str,
    value: str,
) -> None:
    namespace = load_tool()
    payload = copy.deepcopy(valid_payload)
    payload["audit"][field] = value

    with pytest.raises(namespace["AttestationError"]) as captured:
        namespace["build_attestation"](payload)

    assert captured.value.code == "INVALID_STRICT_CONTRACT"


@pytest.mark.parametrize(
    "forbidden_key",
    [
        "api_key",
        "authorization",
        "password",
        "raw_filter",
        "entries",
    ],
)
def test_forbidden_raw_or_secret_fields(
    valid_payload: dict[str, Any],
    forbidden_key: str,
) -> None:
    namespace = load_tool()
    payload = copy.deepcopy(valid_payload)
    payload[forbidden_key] = "must-not-appear"

    with pytest.raises(namespace["AttestationError"]) as captured:
        namespace["build_attestation"](payload)

    assert captured.value.code == "FORBIDDEN_INPUT_FIELD"
    assert "must-not-appear" not in captured.value.message


def test_malformed_json_has_exit_two_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    namespace = load_tool()
    exit_code, output, stderr = invoke(
        namespace,
        monkeypatch,
        capsys,
        "{invalid",
    )

    assert exit_code == 2
    assert output["error_code"] == "INVALID_JSON"
    assert output["gate"] == "INPUT_ERROR"
    assert "Traceback" not in stderr
    assert "Traceback" not in json.dumps(output)


def test_json_array_root_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    namespace = load_tool()
    exit_code, output, _ = invoke(
        namespace,
        monkeypatch,
        capsys,
        "[]",
    )

    assert exit_code == 2
    assert output["error_code"] == "INVALID_JSON_ROOT"


def test_missing_input_file_has_exit_two(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    namespace = load_tool()
    missing = tmp_path / "missing.json"

    exit_code, output, _ = invoke(
        namespace,
        monkeypatch,
        capsys,
        "",
        ["--input", str(missing)],
    )

    assert exit_code == 2
    assert output["error_code"] == "INPUT_FILE_NOT_FOUND"


def test_help_is_one_json_line(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    namespace = load_tool()
    exit_code, output, stderr = invoke(
        namespace,
        monkeypatch,
        capsys,
        "",
        ["--help"],
    )

    assert exit_code == 0
    assert output["ok"] is True
    assert "--input FILE|-" in output["usage"]
    assert stderr == ""


def test_unknown_argument_has_exit_two(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    namespace = load_tool()
    exit_code, output, stderr = invoke(
        namespace,
        monkeypatch,
        capsys,
        "",
        ["--unknown"],
    )

    assert exit_code == 2
    assert output["error_code"] == "ARGUMENT_ERROR"
    assert "Traceback" not in stderr


def test_output_excludes_raw_payload_and_secrets(
    valid_payload: dict[str, Any],
) -> None:
    namespace = load_tool()
    output = namespace["build_attestation"](valid_payload)
    serialized = json.dumps(output, sort_keys=True).lower()

    assert "audit" not in output
    assert "retained_cycles" not in serialized
    assert "jsonpayload" not in serialized
    assert "textpayload" not in serialized
    assert "authorization" not in serialized
    assert "api_key" not in serialized


def test_static_contract_has_no_network_subprocess_or_api_reference() -> None:
    source = TOOL_PATH.read_text(encoding="utf-8-sig")
    tree = ast.parse(source)

    imported_roots = set()

    for node in tree.body:
        if isinstance(node, ast.Import):
            imported_roots.update(
                alias.name.split(".")[0] for alias in node.names
            )

        if isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])

    assert "subprocess" not in imported_roots
    assert "requests" not in imported_roots
    assert "httpx" not in imported_roots
    assert "urllib" not in imported_roots
    assert "socket" not in imported_roots
    string_literals = {
        node.value.lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "gcloud" not in string_literals
    assert not any(
        value.startswith("gcloud ")
        or "gcloud logging read" in value
        or "gcloud logging write" in value
        for value in string_literals
    )
    assert "/scheduler/run-due-h5" not in source
    assert "api_main.py" not in source
    assert "audit_scheduler_b212_cycles.py" not in source
    assert "check_scheduler_b213_cloud_cycles.py" not in source
