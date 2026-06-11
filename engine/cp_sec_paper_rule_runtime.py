
from __future__ import annotations

import csv
import functools
import inspect
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from fastapi.responses import JSONResponse
except Exception:  # pragma: no cover
    JSONResponse = None


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "data" / "output"

FROZEN_RULE_PATH = OUTPUT_DIR / "latest_frozen_cp_sec_paper_rule.json"
ADMISSIBLE_PATH = OUTPUT_DIR / "cp_sec_admissible_paper_candidates.csv"

TARGET_PATHS = {
    "/gpi/chatgpt-brief",
    "/gpi/latest-brief-from-cloud",
    "/gpi/latest-brief",
}

PATCH_RUNTIME_VERSION = "GPI_CP_SEC_PAPER_RULE_RUNTIME_V0_1"


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []

    for enc in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            with path.open("r", encoding=enc, newline="") as f:
                return list(csv.DictReader(f))
        except Exception:
            continue

    return []


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


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


def _walk_values(payload: Any) -> Iterable[Tuple[str, Any]]:
    if isinstance(payload, dict):
        for k, v in payload.items():
            yield str(k), v
            yield from _walk_values(v)
    elif isinstance(payload, list):
        for item in payload:
            yield from _walk_values(item)


def _collect_payload_tokens(payload: Dict[str, Any]) -> Dict[str, str]:
    tokens: Dict[str, str] = {}

    wanted = {
        "decision_id",
        "input_hash",
        "course_uid",
        "source_file",
        "file_name",
        "latest_file",
        "selection_sorted",
        "selection_CP_SEC",
        "selection_cp_sec",
        "CP_SEC_selection",
        "cp_sec_selection",
        "ticket_exact",
        "structure_max",
        "segment_id",
    }
    wanted_lower = {w.lower() for w in wanted}

    for key, value in _walk_values(payload):
        if key in wanted or key.lower() in wanted_lower:
            if value is not None and str(value).strip() != "":
                tokens[key.lower()] = str(value).strip()

    return tokens


def _candidate_keys(row: Dict[str, str]) -> List[str]:
    keys = []

    decision_id = row.get("decision_id", "")
    input_hash = row.get("input_hash", "")
    course_uid = row.get("course_uid", "") or row.get("source_course_uid", "")
    selection = row.get("selection_sorted", "")
    source_file = row.get("source_file", "")

    if decision_id and input_hash:
        keys.append("decision_input|" + _norm(decision_id) + "|" + _norm(input_hash))

    if course_uid and selection:
        keys.append("course_selection|" + _norm(course_uid) + "|" + _norm(selection))

    if source_file and selection:
        keys.append("source_selection|" + _norm(source_file) + "|" + _norm(selection))

    if source_file:
        keys.append("source_file|" + _norm(source_file))

    return keys


def _build_admissible_index(rows: List[Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    index: Dict[str, Dict[str, str]] = {}
    for row in rows:
        for key in _candidate_keys(row):
            index.setdefault(key, row)
    return index


def _selection_from_tokens(tokens: Dict[str, str]) -> str:
    for key in ("selection_sorted", "selection_cp_sec", "cp_sec_selection", "ticket_exact"):
        value = tokens.get(key)
        if value:
            nums = re.findall(r"\d+", value)
            if len(nums) >= 2:
                a, b = nums[0], nums[1]
                try:
                    return "-".join(str(x) for x in sorted([int(a), int(b)]))
                except Exception:
                    return f"{a}-{b}"
    return ""


def _match_payload_to_admissible(payload: Dict[str, Any], admissible_rows: List[Dict[str, str]]) -> Tuple[bool, Optional[Dict[str, str]], str]:
    if not admissible_rows:
        return False, None, "ADMISSIBLE_FILE_MISSING_OR_EMPTY"

    index = _build_admissible_index(admissible_rows)
    tokens = _collect_payload_tokens(payload)

    decision_id = tokens.get("decision_id", "")
    input_hash = tokens.get("input_hash", "")
    course_uid = tokens.get("course_uid", "")
    selection = _selection_from_tokens(tokens)

    source_file = tokens.get("source_file", "") or tokens.get("file_name", "") or tokens.get("latest_file", "")

    possible_keys = []

    if decision_id and input_hash:
        possible_keys.append("decision_input|" + _norm(decision_id) + "|" + _norm(input_hash))

    if course_uid and selection:
        possible_keys.append("course_selection|" + _norm(course_uid) + "|" + _norm(selection))

    if source_file and selection:
        possible_keys.append("source_selection|" + _norm(source_file) + "|" + _norm(selection))

    if source_file:
        possible_keys.append("source_file|" + _norm(source_file))

    for key in possible_keys:
        if key in index:
            return True, index[key], "MATCH_" + key.split("|", 1)[0].upper()

    return False, None, "NO_MATCH_TO_CP_SEC_PAPER_ADMISSIBLE"


def payload_safe_value(rule: Dict[str, Any], key: str, default: Any) -> Any:
    value = rule.get(key)
    if value is None or value == "":
        return default
    return value


def _paper_block(rule: Dict[str, Any], matched: bool, row: Optional[Dict[str, str]], match_method: str) -> Dict[str, Any]:
    evidence = rule.get("evidence", {}) or {}
    criteria = rule.get("criteria", {}) or {}

    if not rule:
        return {
            "runtime_version": PATCH_RUNTIME_VERSION,
            "paper_allowed_oui_non": "non",
            "paper_rule_id": "ND",
            "paper_rule_match_method": "FROZEN_RULE_MISSING",
            "structure_max": "NO_BET",
            "ticket_strict": "NO_BET",
            "strict_allowed_oui_non": "non",
            "strict_usable_oui_non": "non",
            "reason_codes": ["FROZEN_CP_SEC_PAPER_RULE_MISSING"],
        }

    if matched:
        return {
            "runtime_version": PATCH_RUNTIME_VERSION,
            "paper_allowed_oui_non": "oui",
            "paper_rule_id": rule.get("rule_id", "ND"),
            "paper_rule_match_method": match_method,
            "paper_candidate_selection": (row or {}).get("selection_sorted", ""),
            "paper_candidate_decision_id": (row or {}).get("decision_id", ""),
            "structure_max": "CP_SEC_PAPER_ONLY",
            "segment_roi": "PAPER_TRADING_CP_SEC",
            "edge_status": "EDGE_FRAGILE_PAPER_ONLY",
            "ticket_strict": "NO_BET",
            "strict_allowed_oui_non": "non",
            "strict_usable_oui_non": "non",
            "strict_usable_count": 0,
            "criteria": criteria,
            "evidence": evidence,
            "reason_codes": [
                "PAPER_RULE_VALIDATED_FOR_AUDIT",
                "STRICT_DISABLED_BY_DESIGN",
                "PROXY_ORANGE_EDGE_FRAGILE",
                "PROXY_SURESTIMATION_REMAINS",
            ],
        }

    return {
        "runtime_version": PATCH_RUNTIME_VERSION,
        "paper_allowed_oui_non": "non",
        "paper_rule_id": rule.get("rule_id", "ND"),
        "paper_rule_match_method": match_method,
        "structure_max": "NO_PAPER_MATCH",
        "segment_roi": "NO_BET",
        "edge_status": "EDGE_ABSENT",
        "ticket_strict": "NO_BET",
        "strict_allowed_oui_non": "non",
        "strict_usable_oui_non": "non",
        "strict_usable_count": 0,
        "reason_codes": [
            "NO_MATCH_TO_FROZEN_CP_SEC_PAPER_RULE",
            "STRICT_DISABLED_BY_DESIGN",
            "NO_PAPER_MATCH",
        ],
    }




def _append_cp_sec_paper_fields_to_brief_text(payload: Dict[str, Any], block: Dict[str, Any]) -> None:
    # Injecte les champs CP_SEC paper dans le champ texte brief, sans modifier le verdict Python.
    brief = payload.get("brief")

    if not isinstance(brief, str):
        return

    # Idempotence : ne pas dupliquer si le endpoint est appelé après reload ou si un autre patch a déjà injecté.
    if "CP_SEC_PAPER_RULE :" in brief or "PAPER_ALLOWED :" in brief:
        return

    paper_allowed = str(block.get("paper_allowed_oui_non", "non"))
    paper_rule_id = str(block.get("paper_rule_id", "ND"))
    paper_match = str(block.get("paper_rule_match_method", "ND"))
    paper_structure = str(block.get("structure_max", "ND"))
    paper_segment_roi = str(block.get("segment_roi", "ND"))
    paper_edge = str(block.get("edge_status", "ND"))
    paper_selection = str(block.get("paper_candidate_selection", "ND") or "ND")
    ticket_strict = "NO_BET"
    strict_allowed = "non"
    strict_usable = "non"

    paper_text = (
        "\n\n"
        "CP_SEC_PAPER_RULE :\n"
        f"PAPER_ALLOWED : {paper_allowed}\n"
        f"PAPER_RULE_ID : {paper_rule_id}\n"
        f"PAPER_RULE_MATCH : {paper_match}\n"
        f"PAPER_SELECTION : {paper_selection}\n"
        f"PAPER_STRUCTURE_MAX : {paper_structure}\n"
        f"PAPER_SEGMENT_ROI : {paper_segment_roi}\n"
        f"PAPER_EDGE : {paper_edge}\n"
        f"STRICT_ALLOWED : {strict_allowed}\n"
        f"STRICT_USABLE : {strict_usable}\n"
        f"TICKET_STRICT : {ticket_strict}\n"
        "REGLE_CP_SEC_PAPER : paper uniquement. Ne jamais transformer en ticket strict tant que strict_allowed_oui_non = non.\n"
    )

    payload["brief"] = brief.rstrip() + paper_text


def decorate_payload_with_cp_sec_paper_rule(payload: Dict[str, Any]) -> Dict[str, Any]:
    rule = _read_json(FROZEN_RULE_PATH)
    admissible_rows = _read_csv(ADMISSIBLE_PATH)

    matched, row, match_method = _match_payload_to_admissible(payload, admissible_rows)
    block = _paper_block(rule, matched, row, match_method)

    payload["cp_sec_paper_rule"] = block

    # Champs top-level pour le format brief obligatoire.
    payload["paper_allowed_oui_non"] = block["paper_allowed_oui_non"]
    payload["paper_rule_id"] = block["paper_rule_id"]
    payload["paper_rule_match_method"] = block["paper_rule_match_method"]
    payload["strict_allowed_oui_non"] = "non"
    payload["strict_usable_oui_non"] = "non"
    payload["strict_usable_count"] = 0
    payload["ticket_strict"] = "NO_BET"

    _append_cp_sec_paper_fields_to_brief_text(payload, block)

    if matched:
        payload["structure_max"] = "CP_SEC_PAPER_ONLY"
        payload["segment_roi"] = "PAPER_TRADING_CP_SEC"
        payload["edge_status"] = "EDGE_FRAGILE_PAPER_ONLY"

    return payload


def _wrap_endpoint(endpoint):
    if inspect.iscoroutinefunction(endpoint):
        @functools.wraps(endpoint)
        async def async_wrapper(*args, **kwargs):
            result = await endpoint(*args, **kwargs)
            original, payload, mode = _as_dict_response(result)
            if payload is None:
                return result
            payload = decorate_payload_with_cp_sec_paper_rule(payload)
            return _rebuild_response(original, payload, mode)

        return async_wrapper

    @functools.wraps(endpoint)
    def sync_wrapper(*args, **kwargs):
        result = endpoint(*args, **kwargs)
        original, payload, mode = _as_dict_response(result)
        if payload is None:
            return result
        payload = decorate_payload_with_cp_sec_paper_rule(payload)
        return _rebuild_response(original, payload, mode)

    return sync_wrapper


def apply_cp_sec_paper_rule_to_app(app) -> Dict[str, Any]:
    patched = []

    for route in getattr(app, "routes", []):
        path = getattr(route, "path", None)
        if path not in TARGET_PATHS:
            continue

        endpoint = getattr(route, "endpoint", None)
        if endpoint is None:
            continue

        if getattr(endpoint, "_gpi_cp_sec_paper_rule_wrapped", False):
            continue

        wrapped = _wrap_endpoint(endpoint)
        setattr(wrapped, "_gpi_cp_sec_paper_rule_wrapped", True)

        route.endpoint = wrapped

        dependant = getattr(route, "dependant", None)
        if dependant is not None and hasattr(dependant, "call"):
            dependant.call = wrapped

        patched.append(path)

    return {
        "ok": True,
        "runtime_version": PATCH_RUNTIME_VERSION,
        "patched_paths": patched,
        "frozen_rule_path": str(FROZEN_RULE_PATH),
        "admissible_path": str(ADMISSIBLE_PATH),
    }


def get_cp_sec_paper_rule_status() -> Dict[str, Any]:
    rule = _read_json(FROZEN_RULE_PATH)
    admissible_rows = _read_csv(ADMISSIBLE_PATH)

    return {
        "ok": bool(rule),
        "runtime_version": PATCH_RUNTIME_VERSION,
        "rule_id": rule.get("rule_id", "ND") if rule else "ND",
        "paper_allowed_oui_non": rule.get("paper_allowed_oui_non", "non") if rule else "non",
        "strict_allowed_oui_non": "non",
        "ticket_strict": "NO_BET",
        "structure_max": rule.get("structure_max", "NO_BET") if rule else "NO_BET",
        "segment_roi": rule.get("segment_roi", "ND") if rule else "ND",
        "edge_status": rule.get("edge_status", "ND") if rule else "ND",
        "admissible_rows": len(admissible_rows),
        "frozen_rule_path": str(FROZEN_RULE_PATH),
        "admissible_path": str(ADMISSIBLE_PATH),
    }
