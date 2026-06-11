
from __future__ import annotations

import functools
import inspect
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from fastapi.responses import JSONResponse
except Exception:  # pragma: no cover
    JSONResponse = None

from engine.cp_sec_paper_rule_runtime import (
    decorate_payload_with_cp_sec_paper_rule,
    get_cp_sec_paper_rule_status,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "data" / "output"

PATCH_RUNTIME_VERSION = "GPI_H5_DRIVE_CP_SEC_PAPER_RULE_RUNTIME_V0_1"

EXPLICIT_TARGET_PATHS = {
    "/drive/run/h5",
    "/drive/run/h-5",
    "/drive/run/hminus",
    "/drive/run/h_minus",
    "/drive/h5",
    "/drive/h-5",
    "/drive/hminus",
}

STATUS_ENDPOINT = "/gpi/h5-drive-cp-sec-paper-rule-status"


def _is_h5_drive_path(path: str) -> bool:
    p = str(path or "").lower()

    if p in EXPLICIT_TARGET_PATHS:
        return True

    if not p.startswith("/drive/"):
        return False

    return any(token in p for token in ("h5", "h-5", "hminus", "h_minus"))


def _as_dict_response(result: Any) -> Tuple[Any, Optional[Dict[str, Any]], str]:
    if isinstance(result, dict):
        return result, result, "dict"

    if JSONResponse is not None and isinstance(result, JSONResponse):
        try:
            body = result.body
            if isinstance(body, bytes):
                payload = json.loads(body.decode("utf-8"))
            else:
                payload = json.loads(str(body))
            if isinstance(payload, dict):
                return result, payload, "jsonresponse"
        except Exception:
            return result, None, "unsupported"

    return result, None, "unsupported"


def _rebuild_response(original: Any, payload: Dict[str, Any], mode: str) -> Any:
    if mode == "dict":
        return payload

    if mode == "jsonresponse" and JSONResponse is not None:
        status_code = getattr(original, "status_code", 200)
        headers = dict(getattr(original, "headers", {}) or {})
        media_type = getattr(original, "media_type", None)
        return JSONResponse(content=payload, status_code=status_code, headers=headers, media_type=media_type)

    return original


def _looks_like_gpi_brief(payload: Dict[str, Any]) -> bool:
    keys = set(payload.keys())

    if "brief" in keys:
        return True

    if {"verdict_python", "ticket_exact"} & keys:
        return True

    if {"source_decision", "prompt_gpt", "kill_switch_codes"} & keys:
        return True

    if "course_uid" in keys and ("structure_max" in keys or "ticket_exact" in keys or "segment_roi" in keys):
        return True

    return False


def _decorate_nested(obj: Any, stats: Dict[str, Any], path: str = "$") -> Any:
    if isinstance(obj, dict):
        if _looks_like_gpi_brief(obj):
            try:
                decorate_payload_with_cp_sec_paper_rule(obj)
                stats["decorated_count"] += 1
                stats["decorated_paths"].append(path)
            except Exception as exc:
                stats["errors"].append({"path": path, "error": str(exc)})

        for key, value in list(obj.items()):
            # Eviter de redécorer le bloc de preuve lui-même.
            if key in {"cp_sec_paper_rule"}:
                continue
            obj[key] = _decorate_nested(value, stats, path=f"{path}.{key}")

    elif isinstance(obj, list):
        for i, item in enumerate(list(obj)):
            obj[i] = _decorate_nested(item, stats, path=f"{path}[{i}]")

    return obj


def decorate_h5_drive_payload(payload: Dict[str, Any], route_path: str = "ND") -> Dict[str, Any]:
    stats: Dict[str, Any] = {
        "runtime_version": PATCH_RUNTIME_VERSION,
        "route_path": route_path,
        "decorated_count": 0,
        "decorated_paths": [],
        "errors": [],
        "strict_policy": {
            "strict_allowed_oui_non": "non",
            "strict_usable_oui_non": "non",
            "ticket_strict": "NO_BET",
        },
    }

    _decorate_nested(payload, stats)

    # Si aucun sous-bloc n'a été reconnu mais le payload est un résultat direct H5,
    # ajouter au moins le statut runtime, sans inventer de match.
    if stats["decorated_count"] == 0:
        payload.setdefault("paper_allowed_oui_non", "non")
        payload.setdefault("paper_rule_match_method", "NO_GPI_BRIEF_PAYLOAD_FOUND_IN_H5_RESPONSE")
        payload.setdefault("strict_allowed_oui_non", "non")
        payload.setdefault("strict_usable_oui_non", "non")
        payload.setdefault("ticket_strict", "NO_BET")

    payload["h5_drive_cp_sec_paper_rule_patch"] = stats

    return payload


def _wrap_endpoint(endpoint, route_path: str):
    if inspect.iscoroutinefunction(endpoint):
        @functools.wraps(endpoint)
        async def async_wrapper(*args, **kwargs):
            result = await endpoint(*args, **kwargs)
            original, payload, mode = _as_dict_response(result)
            if payload is None:
                return result
            payload = decorate_h5_drive_payload(payload, route_path=route_path)
            return _rebuild_response(original, payload, mode)

        return async_wrapper

    @functools.wraps(endpoint)
    def sync_wrapper(*args, **kwargs):
        result = endpoint(*args, **kwargs)
        original, payload, mode = _as_dict_response(result)
        if payload is None:
            return result
        payload = decorate_h5_drive_payload(payload, route_path=route_path)
        return _rebuild_response(original, payload, mode)

    return sync_wrapper


def apply_h5_drive_cp_sec_paper_rule_to_app(app) -> Dict[str, Any]:
    patched: List[str] = []
    skipped: List[str] = []

    for route in getattr(app, "routes", []):
        path = getattr(route, "path", None)
        if not path:
            continue

        if path == STATUS_ENDPOINT:
            continue

        if not _is_h5_drive_path(path):
            continue

        endpoint = getattr(route, "endpoint", None)
        if endpoint is None:
            skipped.append(str(path))
            continue

        if getattr(endpoint, "_gpi_h5_drive_cp_sec_paper_rule_wrapped", False):
            continue

        wrapped = _wrap_endpoint(endpoint, route_path=str(path))
        setattr(wrapped, "_gpi_h5_drive_cp_sec_paper_rule_wrapped", True)

        route.endpoint = wrapped

        dependant = getattr(route, "dependant", None)
        if dependant is not None and hasattr(dependant, "call"):
            dependant.call = wrapped

        patched.append(str(path))

    status = {
        "ok": True,
        "runtime_version": PATCH_RUNTIME_VERSION,
        "patched_paths": patched,
        "skipped_paths": skipped,
        "target_rule_status": get_cp_sec_paper_rule_status(),
        "strict_policy": {
            "strict_allowed_oui_non": "non",
            "strict_usable_oui_non": "non",
            "ticket_strict": "NO_BET",
        },
    }

    return status


def get_h5_drive_cp_sec_paper_rule_patch_status(app=None) -> Dict[str, Any]:
    routes = []
    if app is not None:
        for route in getattr(app, "routes", []):
            path = getattr(route, "path", "")
            if _is_h5_drive_path(path):
                endpoint = getattr(route, "endpoint", None)
                routes.append(
                    {
                        "path": path,
                        "wrapped": bool(getattr(endpoint, "_gpi_h5_drive_cp_sec_paper_rule_wrapped", False)),
                    }
                )

    return {
        "ok": True,
        "runtime_version": PATCH_RUNTIME_VERSION,
        "routes": routes,
        "target_rule_status": get_cp_sec_paper_rule_status(),
        "strict_policy": {
            "strict_allowed_oui_non": "non",
            "strict_usable_oui_non": "non",
            "ticket_strict": "NO_BET",
        },
        "note": "Patch runtime H5 Drive. Décore uniquement les payloads de brief reconnus; ne crée aucun ticket strict.",
    }
