"""Tests for the B2.13 read-only Cloud Logging collector."""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import pathlib
import subprocess
import sys
from typing import Any

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / "tools" / "check_scheduler_b213_cloud_cycles.py"
API_PATH = ROOT / "api_main.py"

SPEC = importlib.util.spec_from_file_location(
    "check_scheduler_b213_cloud_cycles",
    TOOL_PATH,
)
assert SPEC is not None
assert SPEC.loader is not None

MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

FIXED_NOW = dt.datetime(2026, 7, 15, 0, 0, tzinfo=dt.timezone.utc)

BASE_ARGS = [
    "--project",
    "analyse-hippique",
    "--region",
    "europe-west1",
    "--service",
    "gpi-lite-watchlist",
    "--revision",
    "gpi-lite-watchlist-00359-boq",
    "--start-utc",
    "2026-07-14T20:00:00Z",
    "--end-utc",
    "2026-07-14T23:00:00Z",
]


def audit_summary(gate: str = "GO") -> dict[str, Any]:
    ok = gate == "GO"

    return {
        "audit_version": "B2_12_MULTI_CYCLE_LOCAL_AUDIT_V1",
        "gate": gate,
        "ok": ok,
        "required_cycles": 7,
        "valid_cycle_count": 7 if ok else 6,
    }


def make_runner(
    *,
    entries: list[Any] | None = None,
    audit_gate: str = "GO",
    gcloud_returncode: int = 0,
    gcloud_stdout: str | None = None,
    gcloud_stderr: str = "",
    auditor_stdout: str | None = None,
    auditor_returncode: int | None = None,
    timeout_on_gcloud: bool = False,
) -> tuple[Any, list[tuple[list[str], dict[str, Any]]]]:
    calls: list[tuple[list[str], dict[str, Any]]] = []
    cloud_entries = entries if entries is not None else [{"jsonPayload": {}}]

    def runner(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((list(command), dict(kwargs)))

        if command[:3] == ["gcloud", "logging", "read"]:
            if timeout_on_gcloud:
                raise subprocess.TimeoutExpired(command, kwargs.get("timeout", 0))

            stdout = (
                gcloud_stdout
                if gcloud_stdout is not None
                else json.dumps(cloud_entries)
            )

            return subprocess.CompletedProcess(
                command,
                gcloud_returncode,
                stdout=stdout,
                stderr=gcloud_stderr,
            )

        exit_code = (
            auditor_returncode
            if auditor_returncode is not None
            else {"GO": 0, "NO_GO": 1, "INPUT_ERROR": 2}[audit_gate]
        )
        stdout = (
            auditor_stdout
            if auditor_stdout is not None
            else json.dumps(audit_summary(audit_gate))
        )

        return subprocess.CompletedProcess(
            command,
            exit_code,
            stdout=stdout,
            stderr="",
        )

    return runner, calls


def invoke(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    args: list[str],
    runner: Any,
) -> tuple[int, dict[str, Any], list[str]]:
    monkeypatch.setattr(MODULE.subprocess, "run", runner)

    exit_code = MODULE.run(args, now_utc=FIXED_NOW)
    captured = capsys.readouterr()

    assert captured.err == ""

    lines = [
        line.strip()
        for line in captured.out.splitlines()
        if line.strip()
    ]

    assert len(lines) == 1

    return exit_code, json.loads(lines[0]), lines


def test_help_is_one_json_line_without_cloud_call(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def forbidden_runner(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("subprocess must not run for --help")

    monkeypatch.setattr(MODULE.subprocess, "run", forbidden_runner)

    exit_code = MODULE.run(["--help"], now_utc=FIXED_NOW)
    captured = capsys.readouterr()
    lines = [line for line in captured.out.splitlines() if line.strip()]

    assert exit_code == 0
    assert captured.err == ""
    assert len(lines) == 1

    payload = json.loads(lines[0])
    assert payload["ok"] is True
    assert payload["collector_version"] == MODULE.COLLECTOR_VERSION


def test_exact_gcloud_read_command(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner, calls = make_runner(entries=[])
    exit_code, payload, _ = invoke(
        monkeypatch,
        capsys,
        BASE_ARGS,
        runner,
    )

    assert exit_code == 0
    assert payload["gate"] == "GO"
    assert len(calls) == 2

    command, kwargs = calls[0]

    assert command[0:3] == ["gcloud", "logging", "read"]
    assert "--project" in command
    assert "analyse-hippique" in command
    assert "--format=json" in command
    assert "--order=asc" in command
    assert "--limit" in command

    logging_filter = command[3]
    assert 'resource.type="cloud_run_revision"' in logging_filter
    assert 'resource.labels.location="europe-west1"' in logging_filter
    assert 'resource.labels.service_name="gpi-lite-watchlist"' in logging_filter
    assert 'resource.labels.revision_name="gpi-lite-watchlist-00359-boq"' in logging_filter
    assert 'jsonPayload.event="gpi_h5_scheduler_cycle"' in logging_filter

    assert kwargs["shell"] is False
    assert kwargs["check"] is False
    assert kwargs["capture_output"] is True


def test_delegation_uses_b212_stdin_and_forwards_options(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    entries = [{"jsonPayload": {"event": "gpi_h5_scheduler_cycle"}}]
    runner, calls = make_runner(entries=entries)

    args = BASE_ARGS + [
        "--required-cycles",
        "9",
        "--exclude-run-id",
        "MANUAL-002",
        "--exclude-run-id",
        "MANUAL-001",
    ]

    exit_code, payload, _ = invoke(
        monkeypatch,
        capsys,
        args,
        runner,
    )

    assert exit_code == 0
    assert payload["scope"]["required_cycles"] == 9
    assert payload["scope"]["excluded_manual_run_count"] == 2

    command, kwargs = calls[1]

    assert command[0] == sys.executable
    assert command[1].endswith("audit_scheduler_b212_cycles.py")
    assert command[2:4] == ["--input", "-"]
    assert "--required-cycles" in command
    assert command.count("--exclude-run-id") == 2
    assert json.loads(kwargs["input"]) == entries
    assert kwargs["shell"] is False


@pytest.mark.parametrize(
    "gate,expected_exit",
    [
        ("GO", 0),
        ("NO_GO", 1),
        ("INPUT_ERROR", 2),
    ],
)
def test_auditor_exit_code_and_gate_are_forwarded(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    gate: str,
    expected_exit: int,
) -> None:
    runner, _ = make_runner(audit_gate=gate)

    exit_code, payload, _ = invoke(
        monkeypatch,
        capsys,
        BASE_ARGS,
        runner,
    )

    assert exit_code == expected_exit
    assert payload["gate"] == gate
    assert payload["audit"]["gate"] == gate
    assert payload["ok"] is (gate == "GO")


@pytest.mark.parametrize(
    "argument,value",
    [
        ("--project", "INVALID PROJECT"),
        ("--region", "EUROPE_WEST1"),
        ("--service", "Invalid_Service"),
        ("--revision", "invalid/revision"),
    ],
)
def test_invalid_identifiers_fail_before_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argument: str,
    value: str,
) -> None:
    args = list(BASE_ARGS)
    args[args.index(argument) + 1] = value

    def forbidden_runner(*unused_args: Any, **unused_kwargs: Any) -> None:
        raise AssertionError("subprocess must not run")

    exit_code, payload, _ = invoke(
        monkeypatch,
        capsys,
        args,
        forbidden_runner,
    )

    assert exit_code == 2
    assert payload["gate"] == "INPUT_ERROR"
    assert payload["error_code"] == "ARGUMENT_ERROR"


@pytest.mark.parametrize(
    "start_utc,end_utc",
    [
        ("2026-07-14T23:00:00Z", "2026-07-14T20:00:00Z"),
        ("2026-07-14T20:00:00", "2026-07-14T23:00:00Z"),
        ("2026-07-14T20:00:00+01:00", "2026-07-14T23:00:00Z"),
        ("2026-07-14T20:00:00Z", "2026-07-15T01:00:00Z"),
    ],
)
def test_invalid_time_ranges_fail_before_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    start_utc: str,
    end_utc: str,
) -> None:
    args = list(BASE_ARGS)
    args[args.index("--start-utc") + 1] = start_utc
    args[args.index("--end-utc") + 1] = end_utc

    def forbidden_runner(*unused_args: Any, **unused_kwargs: Any) -> None:
        raise AssertionError("subprocess must not run")

    exit_code, payload, _ = invoke(
        monkeypatch,
        capsys,
        args,
        forbidden_runner,
    )

    assert exit_code == 2
    assert payload["gate"] == "INPUT_ERROR"


def test_gcloud_failure_is_bounded_and_does_not_expose_secret(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "SECRET_TOKEN_MUST_NOT_APPEAR"
    runner, _ = make_runner(
        gcloud_returncode=1,
        gcloud_stderr=f"authorization failed {secret}",
    )

    exit_code, payload, lines = invoke(
        monkeypatch,
        capsys,
        BASE_ARGS,
        runner,
    )

    assert exit_code == 3
    assert payload["gate"] == "COLLECTION_ERROR"
    assert payload["error_code"] == "GCLOUD_READ_FAILED"
    assert payload["gcloud_exit_code"] == 1
    assert secret not in lines[0]


def test_gcloud_timeout_returns_collection_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner, _ = make_runner(timeout_on_gcloud=True)

    exit_code, payload, _ = invoke(
        monkeypatch,
        capsys,
        BASE_ARGS,
        runner,
    )

    assert exit_code == 3
    assert payload["error_code"] == "GCLOUD_TIMEOUT"
    assert payload["gate"] == "COLLECTION_ERROR"


@pytest.mark.parametrize(
    "stdout",
    [
        "not-json",
        '{"not":"an-array"}',
    ],
)
def test_invalid_gcloud_payload_returns_collection_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    stdout: str,
) -> None:
    runner, _ = make_runner(gcloud_stdout=stdout)

    exit_code, payload, _ = invoke(
        monkeypatch,
        capsys,
        BASE_ARGS,
        runner,
    )

    assert exit_code == 3
    assert payload["gate"] == "COLLECTION_ERROR"


def test_invalid_auditor_json_returns_collection_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner, _ = make_runner(auditor_stdout="not-json")

    exit_code, payload, _ = invoke(
        monkeypatch,
        capsys,
        BASE_ARGS,
        runner,
    )

    assert exit_code == 3
    assert payload["error_code"] == "B212_AUDITOR_INVALID_JSON"


def test_output_is_deterministic_for_same_inputs(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner1, _ = make_runner(entries=[{"insertId": "one"}])
    exit1, payload1, lines1 = invoke(
        monkeypatch,
        capsys,
        BASE_ARGS,
        runner1,
    )

    runner2, _ = make_runner(entries=[{"insertId": "one"}])
    exit2, payload2, lines2 = invoke(
        monkeypatch,
        capsys,
        BASE_ARGS,
        runner2,
    )

    assert exit1 == exit2 == 0
    assert payload1 == payload2
    assert lines1 == lines2


def test_raw_cloud_entries_and_secrets_are_not_emitted(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "B213_SECRET_SENTINEL"
    entries = [
        {
            "insertId": "raw-entry",
            "jsonPayload": {
                "event": "gpi_h5_scheduler_cycle",
                "api_key": secret,
            },
        }
    ]
    runner, _ = make_runner(entries=entries)

    exit_code, payload, lines = invoke(
        monkeypatch,
        capsys,
        BASE_ARGS,
        runner,
    )

    assert exit_code == 0
    assert payload["gcloud_entry_count"] == 1
    assert "raw-entry" not in lines[0]
    assert secret not in lines[0]


def test_runtime_does_not_modify_api_main(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    before = hashlib.sha256(API_PATH.read_bytes()).hexdigest()
    runner, _ = make_runner()

    exit_code, _, _ = invoke(
        monkeypatch,
        capsys,
        BASE_ARGS,
        runner,
    )

    after = hashlib.sha256(API_PATH.read_bytes()).hexdigest()

    assert exit_code == 0
    assert before == after


def test_source_has_no_http_client_or_cloud_write_command() -> None:
    source = TOOL_PATH.read_text(encoding="utf-8-sig")

    assert "requests" not in source
    assert "httpx" not in source
    assert "google.cloud" not in source
    assert "gcloud run" not in source
    assert "gcloud storage" not in source
    assert "scheduler jobs run" not in source
    assert "shell=True" not in source
# B2_13_WINDOWS_GCLOUD_RESOLUTION_TESTS_V1
def test_scheduler_b213_windows_gcloud_resolution_uses_cmd(monkeypatch):
    import os
    import runpy
    from pathlib import Path

    namespace = runpy.run_path(
        str(Path(__file__).with_name("check_scheduler_b213_cloud_cycles.py"))
    )

    calls = []

    def fake_which(name):
        calls.append(name)
        if name == "gcloud.cmd":
            return r"C:\CloudSDK\bin\gcloud.cmd"
        return None

    monkeypatch.setattr(namespace["os"], "name", "nt")
    monkeypatch.setattr(namespace["shutil"], "which", fake_which)

    resolved = namespace["_resolve_gcloud_executable"]()

    assert resolved == r"C:\CloudSDK\bin\gcloud.cmd"
    assert calls == ["gcloud.cmd"]


def test_scheduler_b213_gcloud_subprocess_uses_resolved_executable():
    import ast
    from pathlib import Path

    source = Path(__file__).with_name(
        "check_scheduler_b213_cloud_cycles.py"
    ).read_text(encoding="utf-8")

    tree = ast.parse(source)

    subprocess_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
    ]

    resolved_calls = []

    for call in subprocess_calls:
        for keyword in call.keywords:
            if (
                keyword.arg == "executable"
                and isinstance(keyword.value, ast.Call)
                and isinstance(keyword.value.func, ast.Name)
                and keyword.value.func.id == "_resolve_gcloud_executable"
            ):
                resolved_calls.append(call)

    assert len(subprocess_calls) == 2
    assert len(resolved_calls) == 1

    for call in subprocess_calls:
        shell_keywords = [
            keyword for keyword in call.keywords if keyword.arg == "shell"
        ]

        assert len(shell_keywords) == 1
        assert isinstance(shell_keywords[0].value, ast.Constant)
        assert shell_keywords[0].value.value is False
# B2_13_PROMOTED_SEVERITY_NORMALIZATION_TESTS_V1
def _load_b213_severity_normalization_namespace():
    import runpy
    from pathlib import Path

    return runpy.run_path(
        str(Path(__file__).with_name("check_scheduler_b213_cloud_cycles.py"))
    )


def test_scheduler_b213_restores_promoted_severity_for_json_payload():
    namespace = _load_b213_severity_normalization_namespace()
    normalize = namespace["_restore_promoted_severity"]

    entries = [
        {
            "severity": "INFO",
            "jsonPayload": {
                "event": "gpi_h5_scheduler_cycle",
                "scheduler_run_id": "B213-JSON-SEVERITY",
            },
        }
    ]

    normalized = normalize(entries)

    assert normalized[0]["jsonPayload"]["severity"] == "INFO"
    assert "severity" not in entries[0]["jsonPayload"]
    assert normalized is not entries
    assert normalized[0] is not entries[0]
    assert normalized[0]["jsonPayload"] is not entries[0]["jsonPayload"]


def test_scheduler_b213_preserves_existing_payload_severity():
    namespace = _load_b213_severity_normalization_namespace()
    normalize = namespace["_restore_promoted_severity"]

    entries = [
        {
            "severity": "ERROR",
            "jsonPayload": {
                "event": "gpi_h5_scheduler_cycle",
                "severity": "INFO",
            },
        }
    ]

    normalized = normalize(entries)

    assert normalized[0]["jsonPayload"]["severity"] == "INFO"


def test_scheduler_b213_restores_promoted_severity_for_text_payload():
    import json

    namespace = _load_b213_severity_normalization_namespace()
    normalize = namespace["_restore_promoted_severity"]

    entries = [
        {
            "severity": "INFO",
            "textPayload": json.dumps(
                {
                    "event": "gpi_h5_scheduler_cycle",
                    "scheduler_run_id": "B213-TEXT-SEVERITY",
                }
            ),
        }
    ]

    normalized = normalize(entries)
    normalized_payload = json.loads(normalized[0]["textPayload"])

    assert normalized_payload["severity"] == "INFO"
    assert "severity" not in json.loads(entries[0]["textPayload"])


def test_scheduler_b213_delegates_normalized_cloud_entries():
    import ast
    from pathlib import Path

    source = Path(__file__).with_name(
        "check_scheduler_b213_cloud_cycles.py"
    ).read_text(encoding="utf-8")

    tree = ast.parse(source)

    normalized_delegate_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "delegate_to_b212"
        and node.args
        and isinstance(node.args[0], ast.Call)
        and isinstance(node.args[0].func, ast.Name)
        and node.args[0].func.id == "_restore_promoted_severity"
        and len(node.args[0].args) == 1
        and isinstance(node.args[0].args[0], ast.Name)
        and node.args[0].args[0].id == "entries"
    ]

    assert len(normalized_delegate_calls) == 1
