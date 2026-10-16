"""Fail-closed ruleset document reconciliation, without importing api_main.

Only these two functions are compiled from the actual repository source.
No FastAPI app, MCP lifespan or cloud client is imported.
"""
import ast
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "api_main.py"
PACK_NAMES = [
    "Document_0_Workflow_Operationnel_H5_GPI_ZEturf_V19_1_4_SOURCE.txt",
    "Document_1_Prompt_Maitre_GPI_ZEturf_V19_1_4_ACTIF_MAJ_2026-05-23.txt",
    "manifest_GPI_V19_1_4.json",
]
BLOCKING = [
    "sp_calibration_segments.csv",
    "train_test_split.csv",
    "decision_engine_outputs.csv",
]


def load_functions(tmp_path, pack_status):
    source_tree = ast.parse(SOURCE.read_text(encoding="utf-8-sig"))
    names = {"list_existing_files", "build_ruleset_status"}
    functions = [node for node in source_tree.body
                 if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in functions} == names
    code = compile(ast.Module(body=functions, type_ignores=[]), str(SOURCE), "exec")
    docs = tmp_path / "project_sources" / "GPI_ZEturf_V19_1_4"
    docs.mkdir(parents=True, exist_ok=True)
    cal = tmp_path / "sources" / "calibration"
    cal.mkdir(parents=True, exist_ok=True)
    for name in PACK_NAMES:
        (docs / name).write_text("synthetic fixture, not a source pack")
    ns = {
        "Path": Path,
        "DOCS_DIR": docs,
        "CALIBRATION_DIR": cal,
        "EXPECTED_DOCS": PACK_NAMES,
        "MANIFEST_NAME": "manifest_GPI_V19_1_4.json",
        "EXPECTED_CALIBRATION_AVAILABLE": [
            "calibration_segments.csv", "proxy_error_by_segment.csv",
            "backtest_cp_sec_vs_cp3.csv", "run_metrics.json"
        ],
        "BLOCKING_CALIBRATION_FILES": BLOCKING,
        "RULESET_VERSION": "GPI_ZEturf_V19_1_4",
        "ENGINE_VERSION": "GPI_LITE_V0_9_1",
        "safe_posix_path": lambda p: p.as_posix(),
        "build_source_pack_status": lambda: pack_status,
    }
    exec(code, ns)
    return ns["build_ruleset_status"], docs, cal


def qualified_pack(tmp_path):
    docs = tmp_path / "project_sources" / "GPI_ZEturf_V19_1_4"
    return {
        "ok": True,
        "manifest_file": "manifest_GPI_V19_1_4.json",
        "source_pack_dir": str(docs),
        "manifest_sha256": "ab" * 32,
        "source_pack_version": "GPI_ZEturf_V19_1_4",
        "error": "",
        "source_documents": [
            {"file": name, "hash_ok": True, "bytes_ok": True}
            for name in PACK_NAMES if name != "manifest_GPI_V19_1_4.json"
        ],
    }


def test_tracked_pack_is_located_by_api_source():
    src = SOURCE.read_text(encoding="utf-8-sig")
    assert "DOCS_DIR = SOURCE_PACK_DIR" in src
    assert "SOURCE_PACK_DIR, MANIFEST_NAME, build_source_pack_status" in src


def test_integrity_pass_never_opens_strict_without_calibrations(tmp_path):
    function, _, _ = load_functions(tmp_path, qualified_pack(tmp_path))
    result = function()
    assert result["documents"]["integrity_ok"] is True
    assert result["documents"]["missing_count"] == 0
    assert result["strict_ready"] is False
    assert set(result["missing_blocking_files"]) == set(BLOCKING)


def test_bad_manifest_status_refuses(tmp_path):
    status = qualified_pack(tmp_path)
    status["ok"] = False
    function, _, _ = load_functions(tmp_path, status)
    result = function()
    assert result["documents"]["integrity_ok"] is False
    assert result["strict_ready"] is False


def test_manifest_names_mismatch_refuses(tmp_path):
    status = qualified_pack(tmp_path)
    status["source_documents"][0]["file"] = "UNEXPECTED.txt"
    function, _, _ = load_functions(tmp_path, status)
    result = function()
    assert result["documents"]["pack_manifest_names_match"] is False
    assert result["strict_ready"] is False


def test_file_hash_failure_refuses(tmp_path):
    status = qualified_pack(tmp_path)
    status["source_documents"][0]["hash_ok"] = False
    function, _, _ = load_functions(tmp_path, status)
    assert function()["strict_ready"] is False


def test_file_byte_mismatch_refuses(tmp_path):
    status = qualified_pack(tmp_path)
    status["source_documents"][0]["bytes_ok"] = False
    function, _, _ = load_functions(tmp_path, status)
    assert function()["strict_ready"] is False


def test_document_missing_refuses(tmp_path):
    function, docs, _ = load_functions(tmp_path, qualified_pack(tmp_path))
    (docs / PACK_NAMES[0]).unlink()
    result = function()
    assert result["strict_ready"] is False
    assert PACK_NAMES[0] in result["documents"]["missing"]


def test_wrong_pack_path_refuses(tmp_path):
    status = qualified_pack(tmp_path)
    status["source_pack_dir"] = str(tmp_path / "other")
    function, _, _ = load_functions(tmp_path, status)
    assert function()["strict_ready"] is False
