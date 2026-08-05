import ast
from pathlib import Path


SOURCE_PATH = Path(__file__).with_name("api_main.py")
EXPECTED_VERSION = "0.7.0-mcp-cloud"


def load_tree():
    return ast.parse(
        SOURCE_PATH.read_text(encoding="utf-8"),
        filename=str(SOURCE_PATH),
    )


def test_app_version_is_unique_and_canonical():
    tree = load_tree()

    assignments = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name)
            and target.id == "APP_VERSION"
            for target in node.targets
        )
    ]

    assert len(assignments) == 1
    assert isinstance(assignments[0].value, ast.Constant)
    assert assignments[0].value.value == EXPECTED_VERSION


def test_fastapi_uses_app_version():
    tree = load_tree()

    fastapi_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "FastAPI"
    ]

    assert len(fastapi_calls) == 1

    version_keywords = [
        keyword
        for keyword in fastapi_calls[0].keywords
        if keyword.arg == "version"
    ]

    assert len(version_keywords) == 1
    assert isinstance(version_keywords[0].value, ast.Name)
    assert version_keywords[0].value.id == "APP_VERSION"


def return_dictionary(function_name):
    tree = load_tree()

    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == function_name
    ]

    assert len(functions) == 1

    dictionaries = [
        node.value
        for node in ast.walk(functions[0])
        if isinstance(node, ast.Return)
        and isinstance(node.value, ast.Dict)
    ]

    assert len(dictionaries) == 1
    return dictionaries[0]


def dictionary_value(dictionary, expected_key):
    for key, value in zip(dictionary.keys, dictionary.values):
        if (
            isinstance(key, ast.Constant)
            and key.value == expected_key
        ):
            return value

    raise AssertionError(f"Clé absente : {expected_key}")


def test_root_and_health_use_app_version():
    for function_name in ("root", "health"):
        value = dictionary_value(
            return_dictionary(function_name),
            "version",
        )

        assert isinstance(value, ast.Name)
        assert value.id == "APP_VERSION"


def test_backend_version_uses_app_version():
    tree = load_tree()

    backend_values = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue

        for key, value in zip(node.keys, node.values):
            if (
                isinstance(key, ast.Constant)
                and key.value == "backend_version"
            ):
                backend_values.append(value)

    assert len(backend_values) == 1
    assert isinstance(backend_values[0], ast.Name)
    assert backend_values[0].id == "APP_VERSION"


def test_obsolete_version_literals_are_removed():
    source = SOURCE_PATH.read_text(encoding="utf-8")

    assert "0.4.0-cloud-ready" not in source
    assert "0.7.0-mcp\"" not in source