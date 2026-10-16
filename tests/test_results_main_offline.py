"""Offline regression tests for results_main.py (never fetches race data)."""

import ast
import importlib.util
import sys
import types
from pathlib import Path

import pandas as pd
import pytest


ENTRYPOINT = Path(__file__).resolve().parents[1] / "results_main.py"


def load_entrypoint(monkeypatch, extractor):
    """Use a PDF extractor stub: no real PDFs, network calls, or cloud writes."""
    engine = types.ModuleType("engine")
    engine.__path__ = []
    results_extract = types.ModuleType("engine.results_extract")
    results_extract.build_result_input_from_pdf = extractor
    monkeypatch.setitem(sys.modules, "engine", engine)
    monkeypatch.setitem(sys.modules, "engine.results_extract", results_extract)
    spec = importlib.util.spec_from_file_location("_gpi_results_main_offline", ENTRYPOINT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_results_entrypoint_compiles():
    source = ENTRYPOINT.read_text(encoding="utf-8-sig")
    ast.parse(source, filename=str(ENTRYPOINT))


def test_no_pdf_yields_no_output(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    module = load_entrypoint(monkeypatch, lambda _: pytest.fail("Extractor must not run"))
    module.main()
    assert "Aucun PDF resultats trouve" in capsys.readouterr().out
    assert not (tmp_path / "data/results/result_input.csv").exists()


@pytest.mark.parametrize("extractor_result", [None, pd.DataFrame()])
def test_empty_or_missing_pdf_extraction_fails_closed(monkeypatch, tmp_path, extractor_result):
    monkeypatch.chdir(tmp_path)
    pdf = tmp_path / "data/results/case.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.touch()
    module = load_entrypoint(monkeypatch, lambda _: extractor_result)
    module.main()
    assert not (pdf.parent / "result_input.csv").exists()


def test_successful_result_records_provenance(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    pdf_dir = tmp_path / "data/results"
    pdf_dir.mkdir(parents=True)
    for filename in ("R1C1.pdf", "R1C2.pdf"):
        (pdf_dir / filename).touch()

    def fake_extract(pdf):
        return pd.DataFrame([{"course_uid": pdf.stem, "ticket_joue_oui_non": "non", "type_pari": "NO_BET"}])

    module = load_entrypoint(monkeypatch, fake_extract)
    module.main()
    result = pd.read_csv(pdf_dir / "result_input.csv")
    assert result["course_uid"].tolist() == ["R1C1", "R1C2"]
    assert result["source_pdf"].tolist() == ["R1C1.pdf", "R1C2.pdf"]
    assert result["type_pari"].tolist() == ["NO_BET", "NO_BET"]


def test_one_pdf_failure_does_not_poison_valid_output(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    pdf_dir = tmp_path / "data/results"
    pdf_dir.mkdir(parents=True)
    for filename in ("bad.pdf", "good.pdf"):
        (pdf_dir / filename).touch()

    def fake_extract(pdf):
        if pdf.name == "bad.pdf":
            raise ValueError("Malformed offline fixture")
        return pd.DataFrame([{"course_uid": "R2C3", "type_pari": "NO_BET"}])

    module = load_entrypoint(monkeypatch, fake_extract)
    module.main()
    rows = pd.read_csv(pdf_dir / "result_input.csv")
    assert len(rows) == 1
    assert rows.loc[0, "source_pdf"] == "good.pdf"
