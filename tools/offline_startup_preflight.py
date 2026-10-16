"""Read-only FastAPI source preflight: never imports application or contacts services.

This scanner is deliberately not a runtime smoke test. It verifies Python syntax,
first-party import locations and static FastAPI route declarations without executing
any application code, making it safe to run before an isolated startup test.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
from importlib import metadata
from pathlib import Path


ROOTS_WITH_LOCAL_IMPORTS = {"engine", "tools"}
REQUIRED_FILES = ("api_main.py", "requirements.txt")


def _local_path(root: Path, module: str) -> Path | None:
    """Resolve a first-party Python module strictly inside the checkout."""
    parts = module.split(".")
    if not parts or any(not p.isidentifier() for p in parts):
        return None
    base = root.joinpath(*parts)
    for path in (base.with_suffix(".py"), base / "__init__.py"):
        if path.is_file():
            return path
    return None


def _relative_name(path: Path, root: Path, module: str, level: int) -> str:
    if not level:
        return module
    rel = path.relative_to(root).with_suffix("")
    package = list(rel.parts[:-1])
    if path.name == "__init__.py":
        package = list(rel.parts[:-1])
    trim = level - 1
    if trim > len(package):
        return ""
    return ".".join(package[:len(package) - trim] + ([module] if module else []))


def _imports(tree: ast.Module, path: Path, root: Path):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno
        elif isinstance(node, ast.ImportFrom):
            name = _relative_name(path, root, node.module or "", node.level)
            if name:
                yield name, node.lineno


def _routes(tree: ast.Module):
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not decorator.args:
                continue
            f = decorator.func
            if not (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "app"):
                continue
            if f.attr.lower() not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                continue
            if isinstance(decorator.args[0], ast.Constant) and isinstance(decorator.args[0].value, str):
                yield (f.attr.upper(), decorator.args[0].value, node.lineno)


def _has_fastapi_app(tree: ast.Module) -> bool:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(x, ast.Name) and x.id == "app" for x in node.targets):
            val = node.value
            if isinstance(val, ast.Call) and isinstance(val.func, ast.Name) and val.func.id == "FastAPI":
                return True
    return False


def scan(root: Path, *, check_environment: bool = True) -> dict:
    root = root.resolve(strict=True)
    errors: list[dict] = []
    warnings: list[dict] = []
    visited: set[Path] = set()
    unresolved: set[tuple[str, int, str]] = set()
    pending = [root / "api_main.py"]
    route_occurrences: dict[tuple[str, str], list[int]] = {}
    app_declared = False
    runtime_imports: set[str] = set()
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        display = path.relative_to(root).as_posix()
        if not path.is_file():
            errors.append({"code": "SOURCE_MISSING", "path": display})
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=display)
        except (SyntaxError, UnicodeError) as exc:
            errors.append({"code": "SOURCE_SYNTAX_ERROR", "path": display,
                           "line": getattr(exc, "lineno", None), "detail": str(exc)})
            continue
        if display == "api_main.py":
            app_declared = _has_fastapi_app(tree)
            for method, route, line in _routes(tree):
                route_occurrences.setdefault((method, route), []).append(line)
        for name, line in _imports(tree, path, root):
            local_root = name.split(".")[0]
            if local_root == "requests":
                runtime_imports.add("requests")
            is_local = local_root in ROOTS_WITH_LOCAL_IMPORTS or _local_path(root, local_root) is not None
            if not is_local:
                continue
            target = _local_path(root, name)
            if target is None:
                unresolved.add((name, line, display))
            elif target not in visited:
                pending.append(target)
    if not app_declared:
        errors.append({"code": "FASTAPI_APP_CONSTRUCTOR_NOT_FOUND", "path": "api_main.py"})
    for name, line, source in sorted(unresolved):
        errors.append({"code": "LOCAL_IMPORT_MISSING", "module": name, "path": source, "line": line})
    for (method, route), lines in sorted(route_occurrences.items()):
        if len(lines) > 1:
            errors.append({"code": "DUPLICATE_STATIC_ROUTE", "method": method, "route": route, "lines": lines})

    req_path = root / "requirements.txt"
    dependencies = []
    if not req_path.is_file():
        errors.append({"code": "REQUIREMENTS_MISSING", "path": "requirements.txt"})
    else:
        for lineno, line in enumerate(req_path.read_text(encoding="utf-8-sig").splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            match = re.match(r"^([a-zA-Z][a-zA-Z0-9._-]*)(?:\[[^\]]*\])?", line)
            if not match:
                warnings.append({"code": "REQUIREMENT_UNPARSED", "line": lineno})
                continue
            name = match.group(1)
            pinned = "==" in line or "@" in line
            item = {"package": name, "exact_pin": pinned}
            if not pinned:
                warnings.append({"code": "DEPENDENCY_NOT_EXACTLY_PINNED", "package": name})
            if check_environment:
                try:
                    item["local_version"] = metadata.version(name)
                except metadata.PackageNotFoundError:
                    item["local_version"] = None
                    warnings.append({"code": "DISTRIBUTION_NOT_INSTALLED", "package": name})
            dependencies.append(item)
    declared = {entry["package"].lower().replace("_", "-") for entry in dependencies}
    for pkg in sorted(runtime_imports):
        if pkg not in declared:
            errors.append({"code": "RUNTIME_DEPENDENCY_UNDECLARED", "package": pkg})
    return {
        "mode": "STRICT_OFFLINE_STATIC_ONLY",
        "root": str(root),
        "status": "PASS_STATIC" if not errors else "BLOCKED_STATIC",
        "source_modules_scanned": len(visited),
        "static_api_routes": len(route_occurrences),
        "fastapi_app_constructor_found": app_declared,
        "dependencies": dependencies,
        "errors": errors,
        "warnings": warnings,
        "runtime_import": "NOT_PERFORMED",
        "http_smoke": "NOT_PERFORMED",
        "network_calls": 0,
        "gcp_calls": 0,
        "governance_transition": "NONE",
        "strict_allowed": False,
        "real_bet_allowed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--no-environment", action="store_true", help="Skip local installed-package metadata reads")
    args = parser.parse_args()
    try:
        report = scan(args.root, check_environment=not args.no_environment)
    except (OSError, ValueError) as exc:
        report = {"status": "BLOCKED_STATIC", "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "PASS_STATIC" else 2


if __name__ == "__main__":
    raise SystemExit(main())
