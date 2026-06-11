
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

RULE_PATH = OUTPUT_DIR / "latest_cp_sec_strict_rule.json"
ADMISSIBLE_PATHS = [
    OUTPUT_DIR / "cp_sec_strict_candidates_v0_1.csv",
]
PROXY_ERROR_PATH = OUTPUT_DIR / "proxy_error_by_segment_cp_candidates.csv"
SPLIT_PATH = OUTPUT_DIR / "train_test_split.csv"
DEO_ALIAS_PATH = OUTPUT_DIR / "latest_decision_engine_outputs.csv"

PATCH_RUNTIME_VERSION = "GPI_CP_SEC_STRICT_RULE_RUNTIME_V0_4"

BRIEF_TARGET_PATHS = {
    "/gpi/chatgpt-brief",
    "/gpi/latest-brief-from-cloud",
    "/gpi/latest-brief",
}
H5_TARGET_TOKENS = ("h5", "h-5", "hminus", "h_minus")
STRICT_STATUS_ENDPOINT = "/gpi/cp-sec-strict-rule"


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


def _first_existing_rows(paths: List[Path]) -> Tuple[Path, List[Dict[str, str]]]:
    for path in paths:
        rows = _read_csv(path)
        if rows:
            return path, rows
    return paths[0], []


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def _oui(value: Any) -> bool:
    return _norm(value) in {"oui", "yes", "true", "1", "y"}


def _non_empty(value: Any) -> bool:
    return str(value or "").strip() not in {"", "ND", "nd", "None", "none", "null"}


def _as_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        txt = str(value).strip().replace(",", ".")
        if txt in {"", "ND", "None", "null"}:
            return default
        return float(txt)
    except Exception:
        return default


def _walk_values(payload: Any) -> Iterable[Tuple[str, Any]]:
    if isinstance(payload, dict):
        for k, v in payload.items():
            yield str(k), v
            yield from _walk_values(v)
    elif isinstance(payload, list):
        for item in payload:
            yield from _walk_values(item)


def _flatten_tokens(payload: Dict[str, Any]) -> Dict[str, str]:
    tokens: Dict[str, str] = {}
    for key, value in _walk_values(payload):
        lk = str(key).strip().lower()
        if lk and value is not None and str(value).strip() != "":
            # premier trouvÃ© conservÃ© pour Ã©viter d'Ã©craser par bloc nested plus faible
            tokens.setdefault(lk, str(value).strip())
    return tokens


def _extract_selection(tokens: Dict[str, str]) -> str:
    for key in (
        "selection_sorted",
        "selection_cp_sec",
        "selection_cp_sec",
        "cp_sec_selection",
        "selection_cp_sec",
        "selection_cp_sec",
        "selection_cp_sec",
        "cp_sec_candidat",
        "selection_cp_sec",
        "selection_cp_sec",
        "selection_cp_sec",
        "ticket_exact",
    ):
        value = tokens.get(key)
        if value:
            nums = re.findall(r"\d+", value)
            if len(nums) >= 2:
                try:
                    return "-".join(str(x) for x in sorted([int(nums[0]), int(nums[1])]))
                except Exception:
                    return f"{nums[0]}-{nums[1]}"
    return ""


def _candidate_keys(row: Dict[str, str]) -> List[str]:
    keys: List[str] = []
    decision_id = row.get("decision_id", "")
    input_hash = row.get("input_hash", "")
    course_uid = row.get("course_uid", "") or row.get("source_course_uid", "")
    source_file = row.get("source_file", "")
    selection = row.get("selection_sorted", "") or row.get("selection", "")

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


def _match_payload(payload: Dict[str, Any], rows: List[Dict[str, str]]) -> Tuple[bool, Optional[Dict[str, str]], str]:
    if not rows:
        return False, None, "STRICT_CANDIDATE_FILE_MISSING_OR_EMPTY"

    tokens = _flatten_tokens(payload)
    index = _build_admissible_index(rows)

    decision_id = tokens.get("decision_id", "")
    input_hash = tokens.get("input_hash", "")
    course_uid = tokens.get("course_uid", "")
    source_file = tokens.get("source_file", "") or tokens.get("file_name", "") or tokens.get("latest_file", "")
    selection = _extract_selection(tokens)

    possible: List[str] = []
    if decision_id and input_hash:
        possible.append("decision_input|" + _norm(decision_id) + "|" + _norm(input_hash))
    if course_uid and selection:
        possible.append("course_selection|" + _norm(course_uid) + "|" + _norm(selection))
    if source_file and selection:
        possible.append("source_selection|" + _norm(source_file) + "|" + _norm(selection))
    if source_file:
        possible.append("source_file|" + _norm(source_file))

    for key in possible:
        if key in index:
            return True, index[key], "MATCH_" + key.split("|", 1)[0].upper()

    return False, None, "NO_MATCH_TO_CP_SEC_STRICT_ADMISSIBLE"


def _kill_switch_tokens(tokens: Dict[str, str]) -> str:
    for key in ("kill_switch_codes", "kill_switchs", "killswitchs", "kill_switch", "kill_switch_codes"):
        if tokens.get(key):
            return tokens[key]
    return ""


def _has_kill_switch(tokens: Dict[str, str]) -> bool:
    ks = _norm(_kill_switch_tokens(tokens))
    if ks in {"", "nd", "none", "aucun", "no", "non", "[]", "null"}:
        return False
    return True


def _get_first(tokens: Dict[str, str], *names: str) -> str:
    for n in names:
        v = tokens.get(n.lower())
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    return ""


def _strict_gate(payload: Dict[str, Any], matched: bool, row: Optional[Dict[str, str]], rule: Dict[str, Any]) -> Tuple[bool, List[str], Dict[str, Any]]:
    tokens = _flatten_tokens(payload)

    reasons: List[str] = []
    facts: Dict[str, Any] = {}

    verdict = _get_first(tokens, "verdict_python", "verdict")
    structure = _get_first(tokens, "structure_max")
    decision_strict_allowed = _get_first(tokens, "decision_strict_allowed", "decision_strict_allowed_oui_non")
    strict_allowed_top = _get_first(tokens, "strict_allowed_oui_non")
    replay_match = _get_first(tokens, "replay_match", "replay_match_oui_non", "decision_engine_replay_match")
    leakage_ok = _get_first(tokens, "leakage_check_ok")
    decision_before = _get_first(tokens, "decision_before_race_oui_non")
    split = _get_first(tokens, "split", "split_name", "split_type")
    calibration = _get_first(tokens, "calibration_version")
    input_hash = _get_first(tokens, "input_hash")
    engine_version = _get_first(tokens, "engine_version")
    ruleset_version = _get_first(tokens, "ruleset_version")
    segment_id = _get_first(tokens, "segment_id", "proxy_segment_id")
    statut_proxy = _get_first(tokens, "statut_proxy", "proxy_status")
    fallback = _get_first(tokens, "fallback", "proxy_fallback")
    confidence = _get_first(tokens, "confidence", "proxy_confidence")
    r_prudent = _as_float(_get_first(tokens, "r_prudent", "R_prudent"), None)
    c_prudent = _as_float(_get_first(tokens, "c_prudent", "C_prudent"), None)
    erreur_proxy = _get_first(tokens, "erreur_proxy", "proxy_error")
    sample_n = _as_float(_get_first(tokens, "sample_n", "proxy_sample_n"), None)
    cp_sec_domine = _get_first(tokens, "cp_sec_domine_oui_non")
    vol_status = _get_first(tokens, "volatilite_status")
    selection = _extract_selection(tokens)

    facts.update({
        "verdict": verdict or "ND",
        "structure_max": structure or "ND",
        "decision_strict_allowed": decision_strict_allowed or "ND",
        "strict_allowed_top": strict_allowed_top or "ND",
        "selection": selection or "ND",
        "matched_candidate": matched,
        "match_row_selection": (row or {}).get("selection_sorted", ""),
        "input_hash": input_hash or "ND",
        "engine_version": engine_version or "ND",
        "ruleset_version": ruleset_version or "ND",
        "split": split or "ND",
        "calibration_version": calibration or "ND",
        "leakage_check_ok": leakage_ok or "ND",
        "decision_before_race_oui_non": decision_before or "ND",
        "replay_match": replay_match or "ND",
        "segment_id": segment_id or "ND",
        "sample_n": sample_n if sample_n is not None else "ND",
        "confidence": confidence or "ND",
        "fallback": fallback or "ND",
        "C_prudent": c_prudent if c_prudent is not None else "ND",
        "R_prudent": r_prudent if r_prudent is not None else "ND",
        "erreur_proxy": erreur_proxy or "ND",
        "statut_proxy": statut_proxy or "ND",
        "volatilite_status": vol_status or "ND",
    })

    if not matched:
        reasons.append("NO_MATCH_TO_CP_SEC_STRICT_ADMISSIBLE")

    if _norm(verdict) in {"no_bet", "nobet", "no bet"}:
        reasons.append("VERDICT_NO_BET")

    if "simulation" in _norm(structure):
        reasons.append("STRUCTURE_SIMULATION")

    if not selection:
        reasons.append("CP_SEC_SELECTION_MISSING")

    if _has_kill_switch(tokens):
        reasons.append("KILL_SWITCH_ACTIVE")

    if _norm(vol_status) in {"kill", "rouge", "red"}:
        reasons.append("VOLATILITE_KILL")

    # Le strict ne doit jamais Ãªtre ouvert par dÃ©faut : il faut un signal explicite du moteur.
    if not (_oui(decision_strict_allowed) or _oui(strict_allowed_top)):
        reasons.append("STRICT_NOT_OPENED_BY_ENGINE")

    if not _non_empty(input_hash):
        reasons.append("INPUT_HASH_MISSING")
    if not _non_empty(engine_version):
        reasons.append("ENGINE_VERSION_MISSING")
    if not _non_empty(ruleset_version):
        reasons.append("RULESET_VERSION_MISSING")
    if not _non_empty(calibration):
        reasons.append("CALIBRATION_VERSION_MISSING")
    if not _oui(leakage_ok):
        reasons.append("LEAKAGE_CHECK_NOT_OK")
    if _norm(decision_before) not in {"oui", "yes", "true", "1"}:
        reasons.append("DECISION_BEFORE_RACE_NOT_CONFIRMED")
    if _norm(split) not in {"test_live", "test", "validation"}:
        reasons.append("SPLIT_NOT_OUT_OF_SAMPLE")
    if _norm(replay_match) not in {"oui", "yes", "true", "1"}:
        reasons.append("REPLAY_MATCH_NOT_CONFIRMED")

    # Proxy strict obligatoire pour CP_SEC rÃ©el.
    if not _non_empty(segment_id):
        reasons.append("PROXY_SEGMENT_ID_MISSING")
    if sample_n is None or sample_n < float(rule.get("min_sample_n", 50)):
        reasons.append("PROXY_SAMPLE_N_TOO_LOW_OR_MISSING")
    if _norm(statut_proxy) not in {"vert", "green", "utilisable"}:
        reasons.append("PROXY_STATUS_NOT_VERT")
    if _norm(confidence) not in {"robuste", "high", "forte", "usable", "utilisable"}:
        reasons.append("PROXY_CONFIDENCE_NOT_ROBUST")
    if not _non_empty(fallback):
        reasons.append("PROXY_FALLBACK_MISSING")
    if r_prudent is None:
        reasons.append("R_PRUDENT_MISSING")
    elif r_prudent < float(rule.get("min_R_prudent", 3.0)):
        reasons.append("R_PRUDENT_TOO_LOW")
    if c_prudent is None:
        reasons.append("C_PRUDENT_MISSING")
    if not _non_empty(erreur_proxy):
        reasons.append("ERREUR_PROXY_MISSING")
    if _norm(cp_sec_domine) == "oui":
        # Pour CP_SEC strict, ce champ n'est pas bloquant si la structure est CP_SEC. On le conserve en fait.
        pass

    allowed = len(reasons) == 0
    return allowed, reasons, facts


def _ticket_from_row_or_payload(row: Optional[Dict[str, str]], payload: Dict[str, Any]) -> str:
    tokens = _flatten_tokens(payload)
    selection = (row or {}).get("selection_sorted", "") or _extract_selection(tokens)
    if not selection:
        return "NO_BET"
    mise = (row or {}).get("mise", "") or "1"
    return f"CP_SEC {selection} {mise} EUR"


def build_cp_sec_strict_block(payload: Dict[str, Any]) -> Dict[str, Any]:
    rule = _read_json(RULE_PATH)
    if not rule:
        rule = {
            "rule_id": "CP_SEC_STRICT_RULE_V0_4",
            "min_sample_n": 50,
            "min_R_prudent": 3.0,
            "strict_mode": "LOCKED_UNTIL_ALL_GATES_OK",
        }

    candidate_path, rows = _first_existing_rows(ADMISSIBLE_PATHS)
    matched, row, match_method = _match_payload(payload, rows)
    allowed, reasons, facts = _strict_gate(payload, matched, row, rule)

    ticket = _ticket_from_row_or_payload(row, payload) if allowed else "NO_BET"

    return {
        "runtime_version": PATCH_RUNTIME_VERSION,
        "rule_id": rule.get("rule_id", "CP_SEC_STRICT_RULE_V0_4"),
        "strict_allowed_oui_non": "oui" if allowed else "non",
        "strict_usable_oui_non": "oui" if allowed else "non",
        "ticket_strict": ticket,
        "structure_max_strict": "CP_SEC_STRICT" if allowed else "NO_STRICT_BET",
        "segment_roi_strict": "CP_SEC" if allowed else "NO_BET",
        "edge_status_strict": "EDGE_NET" if allowed else "EDGE_ABSENT",
        "strict_rule_match_method": match_method,
        "strict_candidate_file": str(candidate_path),
        "strict_candidate_rows": len(rows),
        "strict_candidate_selection": (row or {}).get("selection_sorted", ""),
        "facts": facts,
        "reason_codes": ["CP_SEC_STRICT_ALLOWED"] if allowed else reasons,
        "strict_policy": rule.get("strict_mode", "LOCKED_UNTIL_ALL_GATES_OK"),
    }


def decorate_payload_with_cp_sec_strict_rule(payload: Dict[str, Any]) -> Dict[str, Any]:
    block = build_cp_sec_strict_block(payload)

    payload["cp_sec_strict_rule"] = block

    # Ne pas Ã©craser un NO_BET par une simulation.
    payload["strict_rule_id"] = block["rule_id"]
    payload["strict_rule_match_method"] = block["strict_rule_match_method"]
    payload["strict_allowed_oui_non"] = block["strict_allowed_oui_non"]
    payload["strict_usable_oui_non"] = block["strict_usable_oui_non"]
    payload["ticket_strict"] = block["ticket_strict"]

    if block["strict_allowed_oui_non"] == "oui":
        payload["structure_max"] = "CP_SEC_STRICT"
        payload["segment_roi"] = "CP_SEC"
        payload["edge_status"] = "EDGE_NET"
        payload["ticket_exact"] = block["ticket_strict"]
    else:
        # SÃ©curitÃ© : jamais de ticket strict si une porte manque.
        if payload.get("ticket_exact") not in (None, "", "NO_BET"):
            payload["ticket_exact_original_before_strict_gate"] = payload.get("ticket_exact")
        payload["ticket_exact"] = "NO_BET"

    # Enrichir le texte du brief si prÃ©sent.
    brief_txt = payload.get("brief")
    if isinstance(brief_txt, str):
        block_txt = (
            "\n\nCP_SEC_STRICT_RULE :\n"
            f"STRICT_ALLOWED : {block['strict_allowed_oui_non']}\n"
            f"STRICT_USABLE : {block['strict_usable_oui_non']}\n"
            f"STRICT_RULE_ID : {block['rule_id']}\n"
            f"STRICT_MATCH : {block['strict_rule_match_method']}\n"
            f"STRICT_TICKET : {block['ticket_strict']}\n"
            f"STRICT_STRUCTURE : {block['structure_max_strict']}\n"
            f"STRICT_SEGMENT_ROI : {block['segment_roi_strict']}\n"
            f"STRICT_EDGE : {block['edge_status_strict']}\n"
            f"STRICT_REASONS : {','.join(block['reason_codes'])}\n"
            "REGLE_CP_SEC_STRICT : ticket rÃ©el uniquement si tous les verrous moteur, split, proxy, volatilitÃ© et replay sont validÃ©s.\n"
        )
        if "CP_SEC_STRICT_RULE :" not in brief_txt:
            payload["brief"] = brief_txt.rstrip() + block_txt

    return payload


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
    if "course_uid" in keys and ("structure_max" in keys or "ticket_exact" in keys or "segment_roi" in keys):
        return True
    return False


def _decorate_nested(obj: Any, stats: Dict[str, Any], path: str = "$") -> Any:
    if isinstance(obj, dict):
        if _looks_like_gpi_brief(obj):
            try:
                decorate_payload_with_cp_sec_strict_rule(obj)
                stats["decorated_count"] += 1
                stats["decorated_paths"].append(path)
            except Exception as exc:
                stats["errors"].append({"path": path, "error": str(exc)})
        for key, value in list(obj.items()):
            if key in {"cp_sec_strict_rule"}:
                continue
            obj[key] = _decorate_nested(value, stats, path=f"{path}.{key}")
    elif isinstance(obj, list):
        for i, item in enumerate(list(obj)):
            obj[i] = _decorate_nested(item, stats, path=f"{path}[{i}]")
    return obj


def decorate_response_payload(payload: Dict[str, Any], route_path: str = "ND") -> Dict[str, Any]:
    stats: Dict[str, Any] = {
        "runtime_version": PATCH_RUNTIME_VERSION,
        "route_path": route_path,
        "decorated_count": 0,
        "decorated_paths": [],
        "errors": [],
    }

    _decorate_nested(payload, stats)

    if stats["decorated_count"] == 0:
        block = build_cp_sec_strict_block(payload)
        payload["cp_sec_strict_rule"] = block
        payload["strict_rule_id"] = block["rule_id"]
        payload["strict_rule_match_method"] = block["strict_rule_match_method"]
        payload["strict_allowed_oui_non"] = block["strict_allowed_oui_non"]
        payload["strict_usable_oui_non"] = block["strict_usable_oui_non"]
        payload["ticket_strict"] = block["ticket_strict"]

    payload["cp_sec_strict_rule_patch"] = stats
    return payload


def _wrap_endpoint(endpoint, route_path: str):
    if inspect.iscoroutinefunction(endpoint):
        @functools.wraps(endpoint)
        async def async_wrapper(*args, **kwargs):
            result = await endpoint(*args, **kwargs)
            original, payload, mode = _as_dict_response(result)
            if payload is None:
                return result
            payload = decorate_response_payload(payload, route_path=route_path)
            return _rebuild_response(original, payload, mode)
        return async_wrapper

    @functools.wraps(endpoint)
    def sync_wrapper(*args, **kwargs):
        result = endpoint(*args, **kwargs)
        original, payload, mode = _as_dict_response(result)
        if payload is None:
            return result
        payload = decorate_response_payload(payload, route_path=route_path)
        return _rebuild_response(original, payload, mode)
    return sync_wrapper


def _is_target_path(path: str) -> bool:
    p = str(path or "").lower()
    if p in BRIEF_TARGET_PATHS:
        return True
    if p.startswith("/drive/") and any(tok in p for tok in H5_TARGET_TOKENS):
        return True
    return False


def apply_cp_sec_strict_rule_to_app(app) -> Dict[str, Any]:
    patched: List[str] = []
    skipped: List[str] = []

    for route in getattr(app, "routes", []):
        path = getattr(route, "path", None)
        if not path or path == STRICT_STATUS_ENDPOINT:
            continue
        if not _is_target_path(path):
            continue

        endpoint = getattr(route, "endpoint", None)
        if endpoint is None:
            skipped.append(str(path))
            continue
        if getattr(endpoint, "_gpi_cp_sec_strict_rule_wrapped", False):
            continue

        wrapped = _wrap_endpoint(endpoint, route_path=str(path))
        setattr(wrapped, "_gpi_cp_sec_strict_rule_wrapped", True)

        route.endpoint = wrapped
        dependant = getattr(route, "dependant", None)
        if dependant is not None and hasattr(dependant, "call"):
            dependant.call = wrapped

        patched.append(str(path))

    return {
        "ok": True,
        "runtime_version": PATCH_RUNTIME_VERSION,
        "patched_paths": patched,
        "skipped_paths": skipped,
        "rule_path": str(RULE_PATH),
        "status_endpoint": STRICT_STATUS_ENDPOINT,
    }


def get_cp_sec_strict_rule_status() -> Dict[str, Any]:
    rule = _read_json(RULE_PATH)
    candidate_path, rows = _first_existing_rows(ADMISSIBLE_PATHS)
    proxy_rows = _read_csv(PROXY_ERROR_PATH)
    split_rows = _read_csv(SPLIT_PATH)
    return {
        "ok": True,
        "runtime_version": PATCH_RUNTIME_VERSION,
        "rule_id": rule.get("rule_id", "CP_SEC_STRICT_RULE_V0_4") if rule else "CP_SEC_STRICT_RULE_V0_4",
        "strict_allowed_oui_non": "non",
        "strict_default": "NO_BET_UNTIL_ALL_GATES_OK",
        "candidate_file": str(candidate_path),
        "candidate_rows": len(rows),
        "proxy_error_file": str(PROXY_ERROR_PATH),
        "proxy_error_rows": len(proxy_rows),
        "split_file": str(SPLIT_PATH),
        "split_rows": len(split_rows),
        "required_fields": [
            "decision_id",
            "input_hash",
            "engine_version",
            "ruleset_version",
            "calibration_version",
            "leakage_check_ok",
            "decision_before_race_oui_non",
            "split",
            "replay_match",
            "segment_id",
            "sample_n",
            "confidence",
            "fallback",
            "C_prudent",
            "R_prudent",
            "erreur_proxy",
            "statut_proxy",
            "kill_switch_codes empty",
        ],
    }

# >>> CP_SEC_STRICT_RULE_V0_4_APPEND >>>
# V0_2 : lit le Decision Engine Outputs de la course H-5 depuis data/output,
# puis merge ses champs techniques dans cp_sec_strict_rule.facts avant gate strict.
PATCH_RUNTIME_VERSION = "GPI_CP_SEC_STRICT_RULE_RUNTIME_V0_4"


def _v02_norm_key(value):
    k = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    k = re.sub(r"[^a-z0-9_]+", "_", k)
    return re.sub(r"_+", "_", k).strip("_")


def _v02_walk(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k), v
            yield from _v02_walk(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _v02_walk(item)


def _v02_flatten(payload):
    out = {}
    for k, v in _v02_walk(payload):
        nk = _v02_norm_key(k)
        if nk and v is not None and str(v).strip() != "":
            out.setdefault(nk, str(v).strip())
    return out


def _v02_all_text(payload):
    return "\n".join(str(v) for _k, v in _v02_walk(payload) if isinstance(v, str) and v.strip())


def _v02_parse_label(text, labels):
    for label in labels:
        pattern = r"(?im)^\s*" + re.escape(label).replace(r"\ ", r"[\s_]+") + r"\s*:\s*(.*?)\s*$"
        m = re.search(pattern, text or "")
        if m:
            return m.group(1).strip()
    return ""


_V02_LABELS = {
    "verdict_python": ["VERDICT_PYTHON", "VERDICT"],
    "structure_max": ["STRUCTURE_MAX", "Structure max"],
    "course_uid": ["COURSE_UID", "COURSE"],
    "selection_cp_sec": ["SELECTION_CP_SEC", "CP_SEC_CANDIDAT"],
    "cp_sec_candidat": ["CP_SEC_CANDIDAT", "SELECTION_CP_SEC"],
    "kill_switch_codes": ["KILL_SWITCHS", "KILL_SWITCH_CODES"],
    "volatilite_status": ["VOLATILITE_STATUS"],
    "decision_strict_allowed": ["DECISION_STRICT_ALLOWED"],
    "replay_match": ["Replay match", "REPLAY_MATCH"],
    "input_hash": ["INPUT_HASH"],
    "engine_version": ["ENGINE_VERSION"],
    "ruleset_version": ["RULESET_VERSION"],
    "calibration_version": ["CALIBRATION_VERSION"],
    "leakage_check_ok": ["LEAKAGE_CHECK_OK"],
    "decision_before_race_oui_non": ["DECISION_BEFORE_RACE_OUI_NON"],
    "split": ["SPLIT", "SPLIT_NAME", "SPLIT_TYPE"],
    "segment_id": ["SEGMENT_ID", "PROXY_SEGMENT_ID"],
    "sample_n": ["SAMPLE_N", "PROXY_SAMPLE_N"],
    "confidence": ["CONFIDENCE", "PROXY_CONFIDENCE"],
    "fallback": ["FALLBACK", "PROXY_FALLBACK"],
    "c_prudent": ["C_PRUDENT"],
    "r_prudent": ["R_PRUDENT"],
    "erreur_proxy": ["ERREUR_PROXY", "PROXY_ERROR"],
    "statut_proxy": ["STATUT_PROXY", "PROXY_STATUS"],
}


def _v02_normalize_row(row):
    return {_v02_norm_key(k): ("" if v is None else str(v).strip()) for k, v in (row or {}).items() if _v02_norm_key(k)}


def _v02_first_csv(path):
    rows = _read_csv(path)
    return _v02_normalize_row(rows[0]) if rows else {}


def _v02_candidate_deo_paths(payload, tokens):
    text = _v02_all_text(payload)
    paths = []

    source_decision = tokens.get("source_decision", "")
    if source_decision:
        p = Path(source_decision)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        if p.name.endswith("_decision_lite.csv"):
            paths.append(p.with_name(p.name.replace("_decision_lite.csv", "_decision_engine_outputs.csv")))

    course_uid = tokens.get("course_uid") or _v02_parse_label(text, ["COURSE_UID", "COURSE"])
    if course_uid:
        paths.append(OUTPUT_DIR / f"{course_uid.strip()}_decision_engine_outputs.csv")

    m = re.search(r"data[\\/]+output[\\/]+(.+?_decision_engine_outputs\.csv)", text)
    if m:
        paths.append(OUTPUT_DIR / m.group(1))

    paths += [OUTPUT_DIR / "latest_decision_engine_outputs.csv", OUTPUT_DIR / "decision_engine_outputs.csv"]

    out, seen = [], set()
    for p in paths:
        key = str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _v02_load_deo(payload, tokens):
    for p in _v02_candidate_deo_paths(payload, tokens):
        row = _v02_first_csv(p)
        if row:
            return row, str(p), True
    return {}, "ND", False


def _v02_payload_with_deo(payload):
    tokens = _v02_flatten(payload)
    text = _v02_all_text(payload)

    parsed = {}
    for key, labels in _V02_LABELS.items():
        value = _v02_parse_label(text, labels)
        if value:
            parsed[key] = value
            tokens.setdefault(key, value)

    deo, deo_source, deo_found = _v02_load_deo(payload, tokens)
    technical = {
        "decision_id", "input_hash", "engine_version", "ruleset_version",
        "calibration_version", "leakage_check_ok", "decision_before_race_oui_non",
        "split", "split_name", "split_type", "replay_match", "replay_match_oui_non",
        "decision_engine_replay_match", "segment_id", "sample_n", "confidence", "fallback",
        "c_prudent", "r_prudent", "erreur_proxy", "statut_proxy",
        "proxy_segment_id", "proxy_sample_n", "proxy_confidence", "proxy_fallback",
        "proxy_status", "proxy_error", "cp_sec_domine_oui_non",
    }
    for k, v in deo.items():
        if v and k in technical:
            tokens[k] = v
        elif v:
            tokens.setdefault(k, v)

    aliases = {
        "split_name": "split",
        "split_type": "split",
        "replay_match_oui_non": "replay_match",
        "decision_engine_replay_match": "replay_match",
        "proxy_segment_id": "segment_id",
        "proxy_sample_n": "sample_n",
        "proxy_confidence": "confidence",
        "proxy_fallback": "fallback",
        "fallback_level": "fallback",
        "proxy_status": "statut_proxy",
        "proxy_error": "erreur_proxy",
    }
    for src, dst in aliases.items():
        if src in tokens and dst not in tokens:
            tokens[dst] = tokens[src]

    meta = {
        "deo_found": deo_found,
        "deo_source": deo_source,
        "deo_fields_count": len(deo),
        "parsed_from_text_keys": sorted(parsed.keys()),
    }
    tokens["__v02_deo_source"] = deo_source
    tokens["__v02_deo_found"] = "oui" if deo_found else "non"
    return tokens, meta


def build_cp_sec_strict_block(payload):
    rule = _read_json(RULE_PATH) or {
        "rule_id": "CP_SEC_STRICT_RULE_V0_4",
        "min_sample_n": 50,
        "min_R_prudent": 3.0,
        "strict_mode": "LOCKED_UNTIL_ALL_GATES_OK",
    }

    merged_payload, meta = _v02_payload_with_deo(payload)
    candidate_path, rows = _first_existing_rows(ADMISSIBLE_PATHS)
    matched, row, match_method = _match_payload(merged_payload, rows)
    allowed, reasons, facts = _strict_gate(merged_payload, matched, row, rule)

    facts["deo_found"] = meta["deo_found"]
    facts["deo_source"] = meta["deo_source"]
    facts["deo_fields_count"] = meta["deo_fields_count"]
    facts["parsed_from_text_keys"] = meta["parsed_from_text_keys"]

    ticket = _ticket_from_row_or_payload(row, merged_payload) if allowed else "NO_BET"
    return {
        "runtime_version": PATCH_RUNTIME_VERSION,
        "rule_id": rule.get("rule_id", "CP_SEC_STRICT_RULE_V0_4"),
        "strict_allowed_oui_non": "oui" if allowed else "non",
        "strict_usable_oui_non": "oui" if allowed else "non",
        "ticket_strict": ticket,
        "structure_max_strict": "CP_SEC_STRICT" if allowed else "NO_STRICT_BET",
        "segment_roi_strict": "CP_SEC" if allowed else "NO_BET",
        "edge_status_strict": "EDGE_NET" if allowed else "EDGE_ABSENT",
        "strict_rule_match_method": match_method,
        "strict_candidate_file": str(candidate_path),
        "strict_candidate_rows": len(rows),
        "strict_candidate_selection": (row or {}).get("selection_sorted", ""),
        "facts": facts,
        "reason_codes": ["CP_SEC_STRICT_ALLOWED"] if allowed else reasons,
        "strict_policy": rule.get("strict_mode", "LOCKED_UNTIL_ALL_GATES_OK"),
    }


def get_cp_sec_strict_rule_status():
    rule = _read_json(RULE_PATH) or {}
    candidate_path, rows = _first_existing_rows(ADMISSIBLE_PATHS)

    proxy_path = next((p for p in [OUTPUT_DIR / "proxy_error_by_segment_cp_candidates.csv", OUTPUT_DIR / "proxy_error_by_segment.csv"] if p.exists()), OUTPUT_DIR / "proxy_error_by_segment_cp_candidates.csv")
    split_path = next((p for p in [OUTPUT_DIR / "train_test_split.csv", OUTPUT_DIR / "event_split_map.csv"] if p.exists()), OUTPUT_DIR / "train_test_split.csv")
    latest_deo = next((p for p in [OUTPUT_DIR / "latest_decision_engine_outputs.csv", OUTPUT_DIR / "decision_engine_outputs.csv"] if p.exists()), None)
    latest_deo_row = _v02_first_csv(latest_deo) if latest_deo else {}

    return {
        "ok": True,
        "runtime_version": PATCH_RUNTIME_VERSION,
        "rule_id": rule.get("rule_id", "CP_SEC_STRICT_RULE_V0_4"),
        "strict_allowed_oui_non": "non",
        "strict_default": "NO_BET_UNTIL_ALL_GATES_OK",
        "h5_source_policy": "H5_PDFS_ALWAYS_FROM_GOOGLE_DRIVE_THEN_LOCAL_DATA_OUTPUT",
        "candidate_file": str(candidate_path),
        "candidate_rows": len(rows),
        "proxy_error_file": str(proxy_path),
        "proxy_error_rows": len(_read_csv(proxy_path)),
        "split_file": str(split_path),
        "split_rows": len(_read_csv(split_path)),
        "latest_deo_file": str(latest_deo) if latest_deo else "ND",
        "latest_deo_found": bool(latest_deo),
        "latest_deo_fields": sorted(latest_deo_row.keys())[:80],
        "required_fields": [
            "decision_id", "input_hash", "engine_version", "ruleset_version",
            "calibration_version", "leakage_check_ok", "decision_before_race_oui_non",
            "split", "replay_match", "segment_id", "sample_n", "confidence", "fallback",
            "C_prudent", "R_prudent", "erreur_proxy", "statut_proxy", "kill_switch_codes empty",
        ],
    }
# <<< CP_SEC_STRICT_RULE_V0_4_APPEND <<<
