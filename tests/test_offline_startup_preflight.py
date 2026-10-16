"""Network-free tests of the API preflight scanner, using disposable source trees."""
import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "offline_startup_preflight.py"
SPEC = importlib.util.spec_from_file_location("_gpi_preflight", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def make_repo(tmp_path, api='from fastapi import FastAPI\napp = FastAPI()\n', req='fastapi==0.116.0\n'):
    (tmp_path / "api_main.py").write_text(api, encoding="utf-8")
    (tmp_path / "requirements.txt").write_text(req, encoding="utf-8")
    return tmp_path


def codes(report):
    return {x["code"] for x in report["errors"]}


def test_good_minimal_app_passes_without_import(tmp_path):
    root = make_repo(tmp_path, 'from fastapi import FastAPI\napp = FastAPI()\nraise RuntimeError("must never execute")\n')
    report = MODULE.scan(root, check_environment=False)
    assert report["status"] == "PASS_STATIC"
    assert report["runtime_import"] == "NOT_PERFORMED"
    assert report["network_calls"] == 0


def test_syntax_break_blocks(tmp_path):
    root = make_repo(tmp_path, 'from fastapi import FastAPI\napp = FastAPI()\ndef broken(:\n')
    assert "SOURCE_SYNTAX_ERROR" in codes(MODULE.scan(root, check_environment=False))


def test_local_module_missing_blocks(tmp_path):
    root = make_repo(tmp_path, 'from fastapi import FastAPI\nfrom engine.missing import x\napp = FastAPI()\n')
    assert "LOCAL_IMPORT_MISSING" in codes(MODULE.scan(root, check_environment=False))


def test_nested_import_is_recursively_checked(tmp_path):
    root = make_repo(tmp_path, 'from fastapi import FastAPI\nfrom engine.helper import x\napp = FastAPI()\n')
    (tmp_path / "engine").mkdir()
    (tmp_path / "engine" / "__init__.py").write_text('')
    (tmp_path / "engine" / "helper.py").write_text('def broken(:\n')
    report = MODULE.scan(root, check_environment=False)
    assert "SOURCE_SYNTAX_ERROR" in codes(report)
    assert report["source_modules_scanned"] >= 2


def test_duplicate_endpoint_blocks(tmp_path):
    root = make_repo(tmp_path, '''from fastapi import FastAPI
app = FastAPI()
@app.get("/health")
def one(): pass
@app.get("/health")
def two(): pass
''')
    assert "DUPLICATE_STATIC_ROUTE" in codes(MODULE.scan(root, check_environment=False))


def test_different_http_methods_are_not_duplicates(tmp_path):
    root = make_repo(tmp_path, '''from fastapi import FastAPI
app = FastAPI()
@app.get("/health")
def one(): pass
@app.post("/health")
def two(): pass
''')
    assert "DUPLICATE_STATIC_ROUTE" not in codes(MODULE.scan(root, check_environment=False))


def test_missing_api_blocks(tmp_path):
    (tmp_path / "requirements.txt").write_text('fastapi\n')
    assert "SOURCE_MISSING" in codes(MODULE.scan(tmp_path, check_environment=False))


def test_missing_constructor_blocks(tmp_path):
    root = make_repo(tmp_path, 'from fastapi import FastAPI\napi = FastAPI()\n')
    assert "FASTAPI_APP_CONSTRUCTOR_NOT_FOUND" in codes(MODULE.scan(root, check_environment=False))


def test_missing_requirements_blocks(tmp_path):
    (tmp_path / "api_main.py").write_text('from fastapi import FastAPI\napp=FastAPI()\n')
    assert "REQUIREMENTS_MISSING" in codes(MODULE.scan(tmp_path, check_environment=False))


def test_unpinned_requirement_is_warning_only(tmp_path):
    root = make_repo(tmp_path, req='fastapi\nfastmcp\n')
    report = MODULE.scan(root, check_environment=False)
    assert report["status"] == "PASS_STATIC"
    assert sum(x["code"] == "DEPENDENCY_NOT_EXACTLY_PINNED" for x in report["warnings"]) == 2
    assert report["strict_allowed"] is False
    assert report["real_bet_allowed"] is False
