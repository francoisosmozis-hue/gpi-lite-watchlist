from __future__ import annotations

import ast
import copy
import importlib
import importlib.util
import io
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


TOOLS_DIR = Path(__file__).resolve().parent
TOOL_PATH = TOOLS_DIR / "verify_scheduler_b215_cloud_audit_attestation.py"
B214_PATH = TOOLS_DIR / "attest_scheduler_b214_cloud_audit.py"

if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))


@pytest.fixture(scope="module")
def verifier_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "b215_verifier_under_test",
        TOOL_PATH,
    )
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def b214_module() -> ModuleType:
    return importlib.import_module(
        "attest_scheduler_b214_cloud_audit"
    )


@pytest.fixture
def valid_source() -> dict[str, Any]:
    timestamps = [
        "2026-07-14T21:10:55.515838Z",
        "2026-07-14T21:13:32.738797Z",
        "2026-07-14T21:16:42.512031Z",
        "2026-07-14T21:19:37.356307Z",
        "2026-07-14T21:22:47.073326Z",
        "2026-07-14T21:25:34.800313Z",
        "2026-07-14T21:28:34.871224Z",
    ]

    retained_cycles = [
        {
            "briefs_created": 15,
            "capture_count": 0,
            "duration_ms": 90000 + index,
            "movement_rows_total": 193,
            "new_artifact_oui_non": "non",
            "run_status": (
                "H5_RUN_BRIEF_REPLAYED_POST_START_EXCLUDED"
            ),
            "scheduler_run_id": f"B215-LOCAL-{index:04d}",
            "timestamp": timestamp,
        }
        for index, timestamp in enumerate(timestamps, start=1)
    ]

    return {
        "audit": {
            "anomalies": [],
            "anomalies_truncated_count": 0,
            "anomaly_count": 0,
            "audit_version": (
                "B2_12_MULTI_CYCLE_LOCAL_AUDIT_V1"
            ),
            "candidate_event_count": 8,
            "duplicate_run_id_count": 0,
            "duplicate_run_ids": [],
            "eligible_cycle_count": 8,
            "excluded_manual_count": 0,
            "gate": "GO",
            "input_format": "json_array",
            "input_item_count": 8,
            "invalid_cycle_count": 0,
            "ok": True,
            "required_cycles": 7,
            "retained_cycle_count": 7,
            "retained_cycles": retained_cycles,
            "strict_allowed_oui_non": "non",
            "strict_usable_oui_non": "non",
            "ticket_strict": "NO_BET",
            "unrelated_entries_ignored": 0,
            "valid_cycle_count": 8,
        },
        "collector_version": (
            "B2_13_CLOUD_LOGGING_READ_ONLY_COLLECTOR_V1"
        ),
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


@pytest.fixture
def valid_attestation(
    valid_source: dict[str, Any],
    b214_module: ModuleType,
) -> dict[str, Any]:
    return b214_module.build_attestation(
        copy.deepcopy(valid_source)
    )


def write_json(
    path: Path,
    payload: Any,
    *,
    bom: bool = False,
    indent: int | None = None,
) -> None:
    text = json.dumps(
        payload,
        ensure_ascii=False,
        indent=indent,
        sort_keys=True,
    )

    raw = text.encode("utf-8")

    if bom:
        raw = b"\xef\xbb\xbf" + raw

    path.write_bytes(raw)


def invoke(
    verifier_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
    *,
    stdin_text: str = "",
) -> tuple[int, dict[str, Any], str]:
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(stdin_text),
    )

    exit_code = verifier_module.main(arguments)
    captured = capsys.readouterr()

    assert captured.err == ""

    lines = [
        line
        for line in captured.out.splitlines()
        if line.strip()
    ]

    assert len(lines) == 1

    payload = json.loads(lines[0])
    assert isinstance(payload, dict)

    return exit_code, payload, lines[0]


def write_valid_pair(
    tmp_path: Path,
    source: dict[str, Any],
    attestation: dict[str, Any],
) -> tuple[Path, Path]:
    source_path = tmp_path / "source.json"
    attestation_path = tmp_path / "attestation.json"

    write_json(source_path, source)
    write_json(attestation_path, attestation)

    return source_path, attestation_path


def test_valid_pair_file_inputs(
    verifier_module: ModuleType,
    valid_source: dict[str, Any],
    valid_attestation: dict[str, Any],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path, attestation_path = write_valid_pair(
        tmp_path,
        valid_source,
        valid_attestation,
    )

    exit_code, payload, _ = invoke(
        verifier_module,
        capsys,
        monkeypatch,
        [
            "--source",
            str(source_path),
            "--attestation",
            str(attestation_path),
        ],
    )

    assert exit_code == 0
    assert payload["gate"] == "GO"
    assert payload["ok"] is True
    assert payload["verified"] is True
    assert payload["attestation_version"] == (
        "B2_14_CLOUD_AUDIT_ATTESTATION_V1"
    )
    assert payload["source_canonical_sha256"] == (
        valid_attestation["source_canonical_sha256"]
    )
    assert payload["attestation_sha256"] == (
        valid_attestation["attestation_sha256"]
    )


def test_valid_pair_source_stdin(
    verifier_module: ModuleType,
    valid_source: dict[str, Any],
    valid_attestation: dict[str, Any],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attestation_path = tmp_path / "attestation.json"
    write_json(attestation_path, valid_attestation)

    exit_code, payload, _ = invoke(
        verifier_module,
        capsys,
        monkeypatch,
        [
            "--source",
            "-",
            "--attestation",
            str(attestation_path),
        ],
        stdin_text=json.dumps(valid_source),
    )

    assert exit_code == 0
    assert payload["verified"] is True


def test_valid_pair_attestation_stdin(
    verifier_module: ModuleType,
    valid_source: dict[str, Any],
    valid_attestation: dict[str, Any],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "source.json"
    write_json(source_path, valid_source)

    exit_code, payload, _ = invoke(
        verifier_module,
        capsys,
        monkeypatch,
        [
            "--source",
            str(source_path),
            "--attestation",
            "-",
        ],
        stdin_text=json.dumps(valid_attestation),
    )

    assert exit_code == 0
    assert payload["verified"] is True


def test_both_stdin_inputs_are_rejected(
    verifier_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exit_code, payload, _ = invoke(
        verifier_module,
        capsys,
        monkeypatch,
        [
            "--source",
            "-",
            "--attestation",
            "-",
        ],
    )

    assert exit_code == 2
    assert payload["error_code"] == "MULTIPLE_STDIN_INPUTS"
    assert payload["gate"] == "INPUT_ERROR"


def test_utf8_bom_files_are_supported(
    verifier_module: ModuleType,
    valid_source: dict[str, Any],
    valid_attestation: dict[str, Any],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "source-bom.json"
    attestation_path = tmp_path / "attestation-bom.json"

    write_json(source_path, valid_source, bom=True, indent=2)
    write_json(
        attestation_path,
        valid_attestation,
        bom=True,
        indent=2,
    )

    exit_code, payload, _ = invoke(
        verifier_module,
        capsys,
        monkeypatch,
        [
            "--source",
            str(source_path),
            "--attestation",
            str(attestation_path),
        ],
    )

    assert exit_code == 0
    assert payload["verified"] is True


def test_semantically_equivalent_json_is_verified(
    verifier_module: ModuleType,
    valid_source: dict[str, Any],
    valid_attestation: dict[str, Any],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "source-pretty.json"
    attestation_path = tmp_path / "attestation-compact.json"

    write_json(source_path, valid_source, indent=4)
    attestation_path.write_text(
        json.dumps(
            valid_attestation,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )

    exit_code, payload, _ = invoke(
        verifier_module,
        capsys,
        monkeypatch,
        [
            "--source",
            str(source_path),
            "--attestation",
            str(attestation_path),
        ],
    )

    assert exit_code == 0
    assert payload["verified"] is True


def test_source_only_tampering_is_detected(
    verifier_module: ModuleType,
    valid_source: dict[str, Any],
    valid_attestation: dict[str, Any],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tampered_source = copy.deepcopy(valid_source)
    tampered_source["scope"]["limit"] = 101

    source_path, attestation_path = write_valid_pair(
        tmp_path,
        tampered_source,
        valid_attestation,
    )

    exit_code, payload, _ = invoke(
        verifier_module,
        capsys,
        monkeypatch,
        [
            "--source",
            str(source_path),
            "--attestation",
            str(attestation_path),
        ],
    )

    assert exit_code == 1
    assert payload["error_code"] == "ATTESTATION_MISMATCH"
    assert payload["verified"] is False


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("source_canonical_sha256",), "0" * 64),
        (("attestation_sha256",), "1" * 64),
        (("scope", "project"), "other-project"),
        (("counts", "valid_cycle_count"), 7),
        (
            ("retained_run_ids",),
            ["B215-TAMPERED-0001"],
        ),
        (
            ("retained_time_bounds", "first_utc"),
            "2026-07-14T21:00:00Z",
        ),
        (
            ("strict_contract", "ticket_strict"),
            "TAMPERED",
        ),
        (
            ("source_versions", "collector"),
            "TAMPERED",
        ),
        (("attestation_version",), "TAMPERED"),
        (("gate",), "NO_GO"),
    ],
)
def test_attestation_field_tampering_is_detected(
    path: tuple[str, ...],
    replacement: Any,
    verifier_module: ModuleType,
    valid_source: dict[str, Any],
    valid_attestation: dict[str, Any],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tampered = copy.deepcopy(valid_attestation)
    target: Any = tampered

    for key in path[:-1]:
        target = target[key]

    target[path[-1]] = replacement

    source_path, attestation_path = write_valid_pair(
        tmp_path,
        valid_source,
        tampered,
    )

    exit_code, payload, _ = invoke(
        verifier_module,
        capsys,
        monkeypatch,
        [
            "--source",
            str(source_path),
            "--attestation",
            str(attestation_path),
        ],
    )

    assert exit_code == 1
    assert payload["error_code"] == "ATTESTATION_MISMATCH"


def test_extra_attestation_field_is_rejected(
    verifier_module: ModuleType,
    valid_source: dict[str, Any],
    valid_attestation: dict[str, Any],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tampered = copy.deepcopy(valid_attestation)
    tampered["extra"] = "forbidden"

    source_path, attestation_path = write_valid_pair(
        tmp_path,
        valid_source,
        tampered,
    )

    exit_code, payload, _ = invoke(
        verifier_module,
        capsys,
        monkeypatch,
        [
            "--source",
            str(source_path),
            "--attestation",
            str(attestation_path),
        ],
    )

    assert exit_code == 1
    assert payload["error_code"] == "ATTESTATION_MISMATCH"


def test_missing_attestation_field_is_rejected(
    verifier_module: ModuleType,
    valid_source: dict[str, Any],
    valid_attestation: dict[str, Any],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tampered = copy.deepcopy(valid_attestation)
    del tampered["attestation_sha256"]

    source_path, attestation_path = write_valid_pair(
        tmp_path,
        valid_source,
        tampered,
    )

    exit_code, payload, _ = invoke(
        verifier_module,
        capsys,
        monkeypatch,
        [
            "--source",
            str(source_path),
            "--attestation",
            str(attestation_path),
        ],
    )

    assert exit_code == 1
    assert payload["error_code"] == "ATTESTATION_MISMATCH"


def test_coordinated_replacement_is_pair_consistent(
    verifier_module: ModuleType,
    b214_module: ModuleType,
    valid_source: dict[str, Any],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replaced_source = copy.deepcopy(valid_source)
    replaced_source["scope"]["limit"] = 101

    replaced_attestation = b214_module.build_attestation(
        copy.deepcopy(replaced_source)
    )

    source_path, attestation_path = write_valid_pair(
        tmp_path,
        replaced_source,
        replaced_attestation,
    )

    exit_code, payload, _ = invoke(
        verifier_module,
        capsys,
        monkeypatch,
        [
            "--source",
            str(source_path),
            "--attestation",
            str(attestation_path),
        ],
    )

    assert exit_code == 0
    assert payload["verified"] is True


def test_invalid_source_contract_returns_no_go(
    verifier_module: ModuleType,
    valid_source: dict[str, Any],
    valid_attestation: dict[str, Any],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid_source = copy.deepcopy(valid_source)
    invalid_source["audit"]["gate"] = "NO_GO"

    source_path, attestation_path = write_valid_pair(
        tmp_path,
        invalid_source,
        valid_attestation,
    )

    exit_code, payload, _ = invoke(
        verifier_module,
        capsys,
        monkeypatch,
        [
            "--source",
            str(source_path),
            "--attestation",
            str(attestation_path),
        ],
    )

    assert exit_code == 1
    assert payload["error_code"] == "SOURCE_CONTRACT_INVALID"


@pytest.mark.parametrize("invalid_target", ["source", "attestation"])
def test_malformed_json_returns_input_error(
    invalid_target: str,
    verifier_module: ModuleType,
    valid_source: dict[str, Any],
    valid_attestation: dict[str, Any],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path, attestation_path = write_valid_pair(
        tmp_path,
        valid_source,
        valid_attestation,
    )

    target_path = (
        source_path
        if invalid_target == "source"
        else attestation_path
    )
    target_path.write_text(
        '{"invalid":',
        encoding="utf-8",
    )

    exit_code, payload, line = invoke(
        verifier_module,
        capsys,
        monkeypatch,
        [
            "--source",
            str(source_path),
            "--attestation",
            str(attestation_path),
        ],
    )

    assert exit_code == 2
    assert payload["error_code"] == "INVALID_JSON"
    assert "traceback" not in line.lower()


@pytest.mark.parametrize("invalid_target", ["source", "attestation"])
def test_json_array_returns_input_error(
    invalid_target: str,
    verifier_module: ModuleType,
    valid_source: dict[str, Any],
    valid_attestation: dict[str, Any],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path, attestation_path = write_valid_pair(
        tmp_path,
        valid_source,
        valid_attestation,
    )

    target_path = (
        source_path
        if invalid_target == "source"
        else attestation_path
    )
    write_json(target_path, [])

    exit_code, payload, _ = invoke(
        verifier_module,
        capsys,
        monkeypatch,
        [
            "--source",
            str(source_path),
            "--attestation",
            str(attestation_path),
        ],
    )

    assert exit_code == 2
    assert payload["error_code"] == "JSON_OBJECT_REQUIRED"


@pytest.mark.parametrize("invalid_target", ["source", "attestation"])
def test_oversized_input_returns_input_error(
    invalid_target: str,
    verifier_module: ModuleType,
    valid_source: dict[str, Any],
    valid_attestation: dict[str, Any],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path, attestation_path = write_valid_pair(
        tmp_path,
        valid_source,
        valid_attestation,
    )

    target_path = (
        source_path
        if invalid_target == "source"
        else attestation_path
    )
    target_path.write_bytes(
        b"x" * (1_048_576 + 1)
    )

    exit_code, payload, _ = invoke(
        verifier_module,
        capsys,
        monkeypatch,
        [
            "--source",
            str(source_path),
            "--attestation",
            str(attestation_path),
        ],
    )

    assert exit_code == 2
    assert payload["error_code"] == "INPUT_TOO_LARGE"


@pytest.mark.parametrize("missing_target", ["source", "attestation"])
def test_missing_file_returns_input_error(
    missing_target: str,
    verifier_module: ModuleType,
    valid_source: dict[str, Any],
    valid_attestation: dict[str, Any],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path, attestation_path = write_valid_pair(
        tmp_path,
        valid_source,
        valid_attestation,
    )

    if missing_target == "source":
        source_path.unlink()
    else:
        attestation_path.unlink()

    exit_code, payload, _ = invoke(
        verifier_module,
        capsys,
        monkeypatch,
        [
            "--source",
            str(source_path),
            "--attestation",
            str(attestation_path),
        ],
    )

    assert exit_code == 2
    assert payload["error_code"] == "FILE_NOT_FOUND"


def test_output_is_deterministic(
    verifier_module: ModuleType,
    valid_source: dict[str, Any],
    valid_attestation: dict[str, Any],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path, attestation_path = write_valid_pair(
        tmp_path,
        valid_source,
        valid_attestation,
    )

    arguments = [
        "--source",
        str(source_path),
        "--attestation",
        str(attestation_path),
    ]

    first_exit, _, first_line = invoke(
        verifier_module,
        capsys,
        monkeypatch,
        arguments,
    )
    second_exit, _, second_line = invoke(
        verifier_module,
        capsys,
        monkeypatch,
        arguments,
    )

    assert first_exit == 0
    assert second_exit == 0
    assert first_line == second_line


def test_output_excludes_raw_data_and_secrets(
    verifier_module: ModuleType,
    valid_source: dict[str, Any],
    valid_attestation: dict[str, Any],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_with_secret = copy.deepcopy(valid_source)
    source_with_secret["api_key"] = "forbidden-secret"

    source_path, attestation_path = write_valid_pair(
        tmp_path,
        source_with_secret,
        valid_attestation,
    )

    _, _, line = invoke(
        verifier_module,
        capsys,
        monkeypatch,
        [
            "--source",
            str(source_path),
            "--attestation",
            str(attestation_path),
        ],
    )

    lowered = line.lower()

    assert "forbidden-secret" not in lowered
    assert "jsonpayload" not in lowered
    assert "textpayload" not in lowered
    assert "retained_cycles" not in lowered
    assert "authorization" not in lowered


def test_help_is_one_json_line(
    verifier_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exit_code, payload, _ = invoke(
        verifier_module,
        capsys,
        monkeypatch,
        ["--help"],
    )

    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["verifier_version"] == (
        "B2_15_CLOUD_AUDIT_ATTESTATION_VERIFIER_V1"
    )


def test_unknown_argument_has_no_traceback(
    verifier_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exit_code, payload, line = invoke(
        verifier_module,
        capsys,
        monkeypatch,
        ["--unknown"],
    )

    assert exit_code == 2
    assert payload["error_code"] == "ARGUMENT_ERROR"
    assert "traceback" not in line.lower()


def test_static_contract_delegates_only_to_b214_builder() -> None:
    source = TOOL_PATH.read_text(encoding="utf-8-sig")
    tree = ast.parse(source)

    import_matches = [
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "attest_scheduler_b214_cloud_audit"
    ]

    assert len(import_matches) == 1
    assert {
        alias.name
        for alias in import_matches[0].names
    } == {"build_attestation"}

    build_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "build_attestation"
    ]

    assert len(build_calls) == 1
    assert "canonical_hash" not in source
    assert "hashlib" not in source


def test_static_contract_has_no_network_or_subprocess() -> None:
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
    assert "api_main.py" not in source
    assert "/scheduler/run-due-h5" not in source
    assert "gcloud logging" not in source.lower()


def test_existing_components_are_not_referenced_for_writes() -> None:
    source = TOOL_PATH.read_text(encoding="utf-8-sig")

    assert "open(" not in source
    assert "write_text" not in source
    assert "write_bytes" not in source
    assert "unlink" not in source
    assert "rename" not in source
    assert "replace(" not in source
    assert B214_PATH.exists()
