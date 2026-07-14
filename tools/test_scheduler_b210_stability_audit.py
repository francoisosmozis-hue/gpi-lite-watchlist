import ast
from pathlib import Path


SOURCE_PATH = Path(__file__).resolve().parents[1] / "api_main.py"


def _load_tree():
    source = SOURCE_PATH.read_text(encoding="utf-8")
    return source, ast.parse(source)


def _find_function(tree, function_name):
    for node in tree.body:
        if isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef),
        ) and node.name == function_name:
            return node

    raise AssertionError(
        f"Fonction introuvable : {function_name}"
    )


def _called_function_names(function_node):
    names = []

    for node in ast.walk(function_node):
        if not isinstance(node, ast.Call):
            continue

        if isinstance(node.func, ast.Name):
            names.append(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.append(node.func.attr)

    return names


def test_scheduler_b210_audit_contract_present():
    source, tree = _load_tree()
    scheduler = _find_function(
        tree,
        "scheduler_run_due_h5",
    )

    scheduler_source = ast.get_source_segment(
        source,
        scheduler,
    )

    assert scheduler_source is not None
    assert (
        "GPI_H5_SCHEDULER_STABILITY_AUDIT_PATCH_"
        "V0_9_31B2_10"
    ) in scheduler_source
    assert "b210_stability_audit" in scheduler_source
    assert "scheduler_run_id" in scheduler_source
    assert "latest_course_uid_consistent_oui_non" in scheduler_source
    assert '"strict_allowed_oui_non": "non"' in scheduler_source
    assert '"ticket_strict": "NO_BET"' in scheduler_source


def test_scheduler_b210_version_present():
    source, _ = _load_tree()

    assert (
        "SCHEDULER_RUN_DUE_H5_V0_9_31B2_10"
    ) in source
    assert (
        '"stability_audit": b210_stability_audit'
    ) in source


def test_p31_does_not_call_p372():
    _, tree = _load_tree()
    p31_function = _find_function(
        tree,
        "_p31_find_official_np_statut_rows_for_course",
    )

    called_names = _called_function_names(p31_function)

    assert (
        "_p372_force_enriched_course_context"
        not in called_names
    )
