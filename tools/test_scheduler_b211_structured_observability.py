import ast
from pathlib import Path


SOURCE_PATH = Path(__file__).resolve().parents[1] / "api_main.py"

EXPECTED_EVENT_KEYS = {
    "severity",
    "message",
    "event",
    "observability_version",
    "scheduler_run_id",
    "scheduler_version",
    "run_status",
    "h5_run_ok",
    "stability_ok",
    "stage_ok",
    "stage_error_count",
    "latest_course_uid_consistent_oui_non",
    "p31_non_recursive_guard_preserved_oui_non",
    "p31_reader_version_expected",
    "strict_allowed_oui_non",
    "strict_usable_oui_non",
    "ticket_strict",
    "duration_ms",
    "new_artifact_oui_non",
    "capture_count",
    "briefs_created",
    "movement_rows_total",
}

EXPECTED_RESPONSE_KEYS = {
    "ok",
    "h5_run_ok",
    "run_status",
    "route",
    "scheduler_version",
    "scheduler_run_id",
    "stability_audit",
    "history_archive_ok",
    "history_archive_count",
    "history_archive_paths",
    "history_archive_items",
    "now_paris",
    "runs_h5",
    "writes_gcs",
    "brief_skipped_oui_non",
    "brief_skip_reason",
    "plan",
    "url_resolver",
    "strict_allowed_oui_non",
    "strict_usable_oui_non",
    "ticket_strict",
    "strict_policy",
    "new_artifact_oui_non",
    "latest_brief_course_uid",
    "latest_strict_kpi_exclusion_reason",
    "latest_decision_before_race_oui_non",
    "capture",
    "brief",
    "status_after",
    "next_read_routes",
    "morning_self_heal",
    "prospective_capture_after_h5",
    "prospective_tracker_version",
}


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
        f"Function not found: {function_name}"
    )


def _named_assignments(function_node, name):
    assignments = []

    for node in ast.walk(function_node):
        if not isinstance(node, ast.Assign):
            continue

        if any(
            isinstance(target, ast.Name)
            and target.id == name
            for target in node.targets
        ):
            assignments.append(node)

    return assignments


def _dict_items(dict_node):
    assert isinstance(dict_node, ast.Dict)

    items = {}

    for key_node, value_node in zip(
        dict_node.keys,
        dict_node.values,
    ):
        assert isinstance(key_node, ast.Constant)
        assert isinstance(key_node.value, str)
        items[key_node.value] = value_node

    return items


def _is_print_call(node):
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "print"
    )


def test_scheduler_b211_structured_event_contract():
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
        "GPI_H5_SCHEDULER_STRUCTURED_OBSERVABILITY_PATCH_"
        "V0_9_31B2_11"
    ) in scheduler_source
    assert (
        "GPI_H5_SCHEDULER_STRUCTURED_LOG_V0_9_31B2_11"
    ) in scheduler_source

    assignments = _named_assignments(
        scheduler,
        "b211_scheduler_event",
    )

    assert len(assignments) == 1

    event_items = _dict_items(
        assignments[0].value
    )

    assert set(event_items) == EXPECTED_EVENT_KEYS
    assert ast.literal_eval(event_items["severity"]) == "INFO"
    assert (
        ast.literal_eval(event_items["message"])
        == "GPI_H5_SCHEDULER_AUDIT"
    )
    assert (
        ast.literal_eval(event_items["event"])
        == "gpi_h5_scheduler_cycle"
    )
    assert (
        ast.literal_eval(
            event_items["observability_version"]
        )
        == "GPI_H5_SCHEDULER_STRUCTURED_LOG_V0_9_31B2_11"
    )


def test_scheduler_b211_event_is_bounded():
    _, tree = _load_tree()
    scheduler = _find_function(
        tree,
        "scheduler_run_due_h5",
    )

    assignment = _named_assignments(
        scheduler,
        "b211_scheduler_event",
    )[0]

    event_keys = set(
        _dict_items(assignment.value)
    )

    forbidden_keys = {
        "api_key",
        "gpi_api_key",
        "url",
        "latest_brief_course_uid",
        "status_latest_course_uid",
        "history_archive_paths",
        "history_archive_items",
        "stage_errors",
        "odds",
        "horses",
    }

    assert event_keys.isdisjoint(forbidden_keys)


def test_scheduler_b211_emits_one_json_line():
    _, tree = _load_tree()
    scheduler = _find_function(
        tree,
        "scheduler_run_due_h5",
    )

    print_calls = [
        node
        for node in ast.walk(scheduler)
        if _is_print_call(node)
    ]

    assert len(print_calls) == 1

    print_call = print_calls[0]

    assert len(print_call.args) == 1

    dumps_call = print_call.args[0]

    assert isinstance(dumps_call, ast.Call)
    assert isinstance(dumps_call.func, ast.Attribute)
    assert dumps_call.func.attr == "dumps"
    assert isinstance(dumps_call.func.value, ast.Name)
    assert dumps_call.func.value.id == "json"

    dumps_keywords = {
        keyword.arg: keyword.value
        for keyword in dumps_call.keywords
    }

    print_keywords = {
        keyword.arg: keyword.value
        for keyword in print_call.keywords
    }

    assert (
        ast.literal_eval(dumps_keywords["ensure_ascii"])
        is False
    )
    assert (
        ast.literal_eval(dumps_keywords["separators"])
        == (",", ":")
    )
    assert (
        ast.literal_eval(dumps_keywords["sort_keys"])
        is True
    )
    assert (
        ast.literal_eval(print_keywords["flush"])
        is True
    )


def test_scheduler_b211_logging_is_fail_open():
    _, tree = _load_tree()
    scheduler = _find_function(
        tree,
        "scheduler_run_due_h5",
    )

    event_tries = [
        node
        for node in scheduler.body
        if isinstance(node, ast.Try)
        and any(
            _is_print_call(child)
            for child in ast.walk(node)
        )
    ]

    assert len(event_tries) == 1

    event_try = event_tries[0]

    assert len(event_try.handlers) == 1
    assert event_try.orelse == []
    assert event_try.finalbody == []

    handler = event_try.handlers[0]

    assert isinstance(handler.type, ast.Name)
    assert handler.type.id == "Exception"
    assert len(handler.body) == 1
    assert isinstance(handler.body[0], ast.Pass)

    direct_returns = [
        node
        for node in scheduler.body
        if isinstance(node, ast.Return)
    ]

    assert len(direct_returns) == 1

    final_return = direct_returns[0]

    assert isinstance(final_return.value, ast.Name)
    assert final_return.value.id == "b211_response_payload"
    assert (
        scheduler.body.index(event_try)
        < scheduler.body.index(final_return)
    )


def test_scheduler_b211_preserves_b210_response_keys():
    _, tree = _load_tree()
    scheduler = _find_function(
        tree,
        "scheduler_run_due_h5",
    )

    assignments = [
        node
        for node in scheduler.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name)
            and target.id == "b211_response_payload"
            for target in node.targets
        )
    ]

    assert len(assignments) == 1

    response_items = _dict_items(
        assignments[0].value
    )

    assert set(response_items) == EXPECTED_RESPONSE_KEYS
    assert "stability_audit" in response_items
    assert "scheduler_run_id" in response_items
    assert "scheduler_version" in response_items
