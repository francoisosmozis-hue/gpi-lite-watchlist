from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Tuple

from starlette.responses import Response

RUNTIME_VERSION = "GPI_2SUR4_EXP_RUNTIME_V0_5_TEXT_FIELDS"
RULE_ID = "2SUR4_EXP_PAPER_RULE_V0_1"
MIN_PARTANTS_2SUR4 = 10
MAX_STAKE_EUR = 1.0
STATUS_ENDPOINT = "/gpi/2sur4-exp-status"


TEXT_FIELDS = ("prompt_text", "brief", "gpt_brief", "message", "stdout")


def strip_accents(value: Any) -> str:
    s = str(value or "")
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def canonical_course_uid(value: Any) -> str:
    s = strip_accents(str(value or "")).lower()
    s = s.replace("’", "'").replace("–", "-").replace("—", "-").replace("_", " ")
    s = re.sub(r"^(\d{2})[\s_\-/]+(\d{2})[\s_\-/]+(\d{4})", r"\1-\2-\3", s)
    s = re.sub(r"\s*pronostics\s*&\s*cotes.*$", "", s, flags=re.I)
    s = re.sub(r"\s*resultats\s*&\s*rapports.*$", "", s, flags=re.I)
    s = s.replace("'", " ")
    s = re.sub(r"\s+-\s+", " - ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _as_float(value: Any) -> Optional[float]:
    try:
        txt = str(value or "").strip().replace(",", ".")
        txt = re.sub(r"[^0-9.\-]+", "", txt)
        if txt in {"", ".", "-", "-."}:
            return None
        return float(txt)
    except Exception:
        return None


def _as_int(value: Any) -> Optional[int]:
    f = _as_float(value)
    if f is None:
        return None
    return int(f)


def _extract_numbers(value: Any) -> List[int]:
    return [int(x) for x in re.findall(r"\d+", str(value or ""))]


def _text_values(payload: Dict[str, Any]) -> List[str]:
    out: List[str] = []

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            for k, v in x.items():
                if str(k).lower() in TEXT_FIELDS and isinstance(v, str) and v.strip():
                    out.append(v)
                elif isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(x, list):
            for it in x:
                walk(it)

    walk(payload)
    return out


def _lookup_text_label(payload: Dict[str, Any], labels: Iterable[str]) -> str:
    labs = [strip_accents(x).upper().replace(" ", r"\s*") for x in labels]
    for txt in _text_values(payload):
        plain = strip_accents(txt)
        for lab in labs:
            m = re.search(rf"(?m)^\s*{lab}\s*:\s*(.+?)\s*$", plain, flags=re.I)
            if m:
                return m.group(1).strip()
    return ""


def _sorted_pair_from(value: Any) -> str:
    """Extrait une paire cheval sûre. Bloque V0_1/RULE_ID => 0-1."""
    if value is None:
        return ""

    if isinstance(value, dict):
        for key in (
            "selection", "selection_cp_sec", "cp_sec_candidate", "cp_sec_candidat",
            "cp_sec_pair", "candidate_pair", "pair_cp_sec", "strict_candidate_selection",
        ):
            if key in value:
                pair = _sorted_pair_from(value.get(key))
                if pair:
                    return pair
        return ""

    if isinstance(value, (list, tuple)):
        nums: List[int] = []
        for item in value:
            if isinstance(item, int):
                nums.append(item)
            else:
                vals = _extract_numbers(item)
                if len(vals) == 1:
                    nums.append(vals[0])
        nums = [n for n in nums if 1 <= n <= 30]
    else:
        txt = str(value or "")

        if re.search(r"\bV\d+[_\-.]\d+", txt, flags=re.I) or "RULE" in txt.upper():
            explicit = re.findall(
                r"(?<![A-Z0-9])([1-9]|[12]\d|30)\s*[-/]\s*([1-9]|[12]\d|30)(?![A-Z0-9])",
                txt,
                flags=re.I,
            )
            if explicit:
                vals = sorted({int(explicit[0][0]), int(explicit[0][1])})
                return "-".join(str(v) for v in vals) if len(vals) == 2 else ""
            return ""

        explicit = re.findall(
            r"(?<![A-Z0-9])([1-9]|[12]\d|30)\s*[-/]\s*([1-9]|[12]\d|30)(?![A-Z0-9])",
            txt,
            flags=re.I,
        )
        if explicit:
            vals = sorted({int(explicit[0][0]), int(explicit[0][1])})
            return "-".join(str(v) for v in vals) if len(vals) == 2 else ""

        nums = _extract_numbers(txt)
        nums = [n for n in nums if 1 <= n <= 30]

    if len(nums) < 2:
        return ""

    vals = sorted({nums[0], nums[1]})
    if len(vals) != 2:
        return ""

    return "-".join(str(v) for v in vals)


def _collect_values(obj: Any, key_patterns: Iterable[str]) -> List[Any]:
    out: List[Any] = []
    pats = [p.lower() for p in key_patterns]

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            for k, v in x.items():
                lk = str(k).lower()
                if lk in {"cp_sec_paper_rule", "cp_sec_strict_rule", "two_sur_four_exp"}:
                    if isinstance(v, dict):
                        facts = v.get("facts")
                        if isinstance(facts, dict):
                            walk(facts)
                    continue
                if any(p in lk for p in pats):
                    out.append(v)
                walk(v)
        elif isinstance(x, list):
            for it in x:
                walk(it)

    walk(obj)
    return out


def _collect_exact_key_values(obj: Any, exact_keys: Iterable[str]) -> List[Any]:
    out: List[Any] = []
    keys = {str(k).lower() for k in exact_keys}

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            for k, v in x.items():
                lk = str(k).lower()
                if lk in {"cp_sec_paper_rule", "cp_sec_strict_rule", "two_sur_four_exp"}:
                    if isinstance(v, dict):
                        facts = v.get("facts")
                        if isinstance(facts, dict):
                            walk(facts)
                    continue
                if lk in keys:
                    out.append(v)
                walk(v)
        elif isinstance(x, list):
            for it in x:
                walk(it)

    walk(obj)
    return out


def _first_str(payload: Dict[str, Any], keys: Iterable[str]) -> str:
    keys_l = {k.lower() for k in keys}
    found = ""

    def walk(x: Any) -> None:
        nonlocal found
        if found:
            return
        if isinstance(x, dict):
            for k, v in x.items():
                if str(k).lower() in keys_l and str(v or "").strip():
                    found = str(v).strip()
                    return
                walk(v)
                if found:
                    return
        elif isinstance(x, list):
            for it in x:
                walk(it)
                if found:
                    return

    walk(payload)
    return found


def infer_nb_partants(payload: Dict[str, Any]) -> Tuple[Optional[int], str]:
    explicit_keys = [
        "nb_partants", "nombre_partants", "partants_count", "runners_count",
        "starters_count", "field_size", "nb_chevaux", "nombre_chevaux",
        "partants_reels",
    ]
    for key in explicit_keys:
        for value in _collect_values(payload, [key]):
            n = _as_int(value)
            if n is not None and 1 <= n <= 30:
                return n, key

    # Fallback texte : prompt_text / brief H-5 contient souvent PARTANTS_REELS : 11.
    txt_value = _lookup_text_label(payload, ["PARTANTS_REELS", "NB_PARTANTS", "NOMBRE_PARTANTS"])
    n = _as_int(txt_value)
    if n is not None and 1 <= n <= 30:
        return n, "text:PARTANTS_REELS"

    list_keys = {"partants", "runners", "starters", "chevaux", "horses"}
    stack: List[Any] = [payload]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            for k, v in x.items():
                lk = str(k).lower()
                if lk in list_keys and isinstance(v, list) and 1 <= len(v) <= 30:
                    return len(v), f"len({k})"
                if isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(x, list):
            stack.extend(x)

    return None, "ND"


def extract_cp_sec_pair(payload: Dict[str, Any]) -> Tuple[str, str]:
    exact_keys = [
        "selection_cp_sec", "cp_sec_candidate", "cp_sec_candidat", "cp_sec_pair",
        "candidate_pair", "pair_cp_sec",
    ]
    for key in exact_keys:
        for value in _collect_exact_key_values(payload, [key]):
            pair = _sorted_pair_from(value)
            if pair:
                return pair, key

    for value in _collect_exact_key_values(payload, ["selection", "candidate_selection", "strict_candidate_selection"]):
        pair = _sorted_pair_from(value)
        if pair:
            return pair, "selection"

    txt_selection = _lookup_text_label(payload, ["SELECTION_CP_SEC", "CP_SEC_CANDIDAT", "CP_SEC"])
    pair = _sorted_pair_from(txt_selection)
    if pair:
        return pair, "text:SELECTION_CP_SEC"

    for key in ["ticket_paper", "ticket_exact", "structure_paper"]:
        value = _first_str(payload, [key])
        if value and ("2SUR4" in value.upper() or "CP" in value.upper()):
            pair = _sorted_pair_from(value)
            if pair:
                return pair, key

    return "", "ND"


def extract_r_prudent(payload: Dict[str, Any]) -> Optional[float]:
    for key in ["r_prudent", "R_prudent", "rapport_prudent"]:
        for value in _collect_values(payload, [key]):
            f = _as_float(value)
            if f is not None:
                return f
    return None


def extract_c_prudent(payload: Dict[str, Any]) -> Optional[float]:
    for key in ["c_prudent", "C_prudent", "couverture_prudente"]:
        for value in _collect_values(payload, [key]):
            f = _as_float(value)
            if f is not None:
                return f
    return None


def extract_sample_n(payload: Dict[str, Any]) -> Optional[int]:
    for key in ["sample_n", "n_sample", "segment_sample_n"]:
        for value in _collect_values(payload, [key]):
            n = _as_int(value)
            if n is not None:
                return n
    return None


def extract_confidence(payload: Dict[str, Any]) -> str:
    return _first_str(payload, ["confidence", "proxy_confidence", "segment_confidence"]) or "ND"


def extract_proxy_status(payload: Dict[str, Any]) -> str:
    return _first_str(payload, ["statut_proxy", "proxy_status", "segment_status"]) or "ND"


def extract_kill_switch(payload: Dict[str, Any]) -> str:
    # Priorité aux champs explicites, puis au texte H-5.
    explicit = _first_str(payload, ["kill_switch_codes", "kill_switchs", "kill_switch"])
    if explicit:
        return explicit

    text_kill = _lookup_text_label(payload, ["KILL_SWITCHS", "KILL_SWITCH_CODES", "VOLATILITE_STATUS"])
    return text_kill or ""


def has_kill_switch(payload: Dict[str, Any]) -> bool:
    k = extract_kill_switch(payload).upper()
    if not k:
        return False
    return any(token in k for token in [
        "VOLATILITE_KILL", "KILL", "NO_BET_FORCE", "PROXY_ROUGE",
        "SEGMENT_REJETE", "LEAKAGE", "NP_INCERTAIN",
    ])


def evaluate_2sur4_exp(payload: Dict[str, Any]) -> Dict[str, Any]:
    course_uid = _first_str(payload, ["course_uid", "course_key", "source_course_uid"])
    pair, pair_source = extract_cp_sec_pair(payload)
    nb_partants, partants_source = infer_nb_partants(payload)
    r_prudent = extract_r_prudent(payload)
    c_prudent = extract_c_prudent(payload)
    sample_n = extract_sample_n(payload)
    confidence = extract_confidence(payload)
    proxy_status = extract_proxy_status(payload)
    kill = extract_kill_switch(payload)

    reasons: List[str] = []

    if not pair:
        reasons.append("PAIR_CP_SEC_ND")
    if nb_partants is None:
        reasons.append("NB_PARTANTS_ND")
    elif nb_partants < MIN_PARTANTS_2SUR4:
        reasons.append("NB_PARTANTS_LT_10")
    if has_kill_switch(payload):
        reasons.append("KILL_SWITCH_ACTIVE")
    if proxy_status.upper() in {"ROUGE", "REJETE"}:
        reasons.append("PROXY_STATUS_NOT_USABLE")
    if confidence.upper() in {"REJETE", "LOW", "FAIBLE"}:
        reasons.append("CONFIDENCE_NOT_USABLE")
    if sample_n is not None and sample_n < 50:
        reasons.append("SAMPLE_N_LT_50")
    if r_prudent is None:
        reasons.append("R_PRUDENT_ND")

    paper_allowed = (
        pair
        and nb_partants is not None
        and nb_partants >= MIN_PARTANTS_2SUR4
        and not has_kill_switch(payload)
        and proxy_status.upper() not in {"ROUGE", "REJETE"}
        and confidence.upper() not in {"REJETE", "LOW", "FAIBLE"}
    )

    if paper_allowed:
        action = f"Paper trading uniquement : 2sur4 {pair} à {MAX_STAKE_EUR:.0f} EUR max. Aucun ticket strict."
        structure = "2SUR4_EXP_PAPER"
        segment_roi = "2SUR4_EXP"
        edge_status = "EDGE_FRAGILE"
    else:
        action = "Pas de 2sur4 H-5 : " + ",".join(reasons)
        structure = "NO_2SUR4_EXP"
        segment_roi = "NO_BET"
        edge_status = "EDGE_ABSENT"

    return {
        "rule_id": RULE_ID,
        "runtime_version": RUNTIME_VERSION,
        "definition": "2 chevaux sélectionnés doivent finir dans les 4 premiers, ordre indifférent",
        "course_uid": course_uid,
        "course_uid_canonical": canonical_course_uid(course_uid),
        "paper_allowed_oui_non": "oui" if paper_allowed else "non",
        "strict_allowed_oui_non": "non",
        "ticket_strict": "NO_BET",
        "ticket_paper": f"2SUR4 {pair} 1 EUR max" if paper_allowed else "NO_BET",
        "structure_paper": structure,
        "segment_roi": segment_roi,
        "edge_status": edge_status,
        "pair": pair if pair else "ND",
        "pair_source": pair_source,
        "nb_partants": nb_partants if nb_partants is not None else "ND",
        "partants_source": partants_source,
        "min_partants": MIN_PARTANTS_2SUR4,
        "r_prudent": r_prudent if r_prudent is not None else "ND",
        "c_prudent": c_prudent if c_prudent is not None else "ND",
        "sample_n": sample_n if sample_n is not None else "ND",
        "confidence": confidence,
        "statut_proxy": proxy_status,
        "kill_switch_codes": kill,
        "reason_codes": reasons,
        "action_gpt": action,
        "status": "PAPER_ONLY" if paper_allowed else "BLOCKED",
        "doctrine": "Audit historique positif brut non suffisant pour strict; 2SUR4_EXP reste paper/expérimental jusqu'à validation outliers et split.",
    }


def decorate_response_payload(payload: Dict[str, Any], route_path: str = "ND") -> Dict[str, Any]:
    block = evaluate_2sur4_exp(payload)
    payload["two_sur_four_exp"] = block
    payload["two_sur_four_exp_structure_max"] = block["structure_paper"]
    payload["two_sur_four_exp_ticket_paper"] = block["ticket_paper"]
    payload["two_sur_four_exp_action_gpt"] = block["action_gpt"]
    payload["two_sur_four_exp_strict_allowed_oui_non"] = "non"
    payload["two_sur_four_exp_patch"] = {
        "runtime_version": RUNTIME_VERSION,
        "rule_id": RULE_ID,
        "route_path": route_path,
        "decorated_count": 1,
        "strict_allowed_oui_non": "non",
        "mode": "middleware",
    }
    return payload


def _target_path(path: str) -> bool:
    p = str(path or "").lower()
    if p == STATUS_ENDPOINT:
        return False
    if p.startswith("/drive/") and any(x in p for x in ("h5", "h-5", "hminus", "h_minus")):
        return True
    if p.startswith("/gpi/") and any(x in p for x in ("brief", "latest")):
        return True
    return False


async def _json_body_from_response(response) -> bytes:
    body = b""
    async for chunk in response.body_iterator:
        body += chunk
    return body


def get_2sur4_exp_status(app=None) -> Dict[str, Any]:
    patch_state = {}
    if app is not None:
        patch_state = getattr(getattr(app, "state", None), "gpi_2sur4_exp_patch", {}) or {}
    return {
        "ok": True,
        "runtime_version": RUNTIME_VERSION,
        "rule_id": RULE_ID,
        "strict_allowed_oui_non": "non",
        "paper_allowed_possible": "oui",
        "min_partants_2sur4": MIN_PARTANTS_2SUR4,
        "max_stake_eur": MAX_STAKE_EUR,
        "definition": "2 chevaux sélectionnés dans les 4 premiers, ordre indifférent",
        "required_h5_fields": [
            "course_uid",
            "nb_partants ou liste partants",
            "selection_cp_sec ou cp_sec_candidate",
            "kill_switch_codes",
            "proxy confidence/statut si disponible",
        ],
        "middleware_active": bool(patch_state.get("middleware_active")),
        "patched_routes_mode": "middleware",
        "cloud_policy": "runtime intégré API; ne dépend pas des scripts locaux d'audit historique",
        "strict_policy": "NO_BET strict; 2SUR4_EXP seulement PAPER tant que outliers/split/proxy ne sont pas validés",
        "patch_state": patch_state,
    }


def apply_2sur4_exp_to_app(app) -> Dict[str, Any]:
    if getattr(app.state, "gpi_2sur4_exp_middleware_installed", False):
        return getattr(app.state, "gpi_2sur4_exp_patch", {})

    @app.middleware("http")
    async def _gpi_2sur4_exp_middleware(request, call_next):
        response = await call_next(request)
        if not _target_path(request.url.path):
            return response

        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type.lower():
            return response

        try:
            body = await _json_body_from_response(response)
            payload = json.loads(body.decode("utf-8"))
            if isinstance(payload, dict):
                payload = decorate_response_payload(payload, route_path=request.url.path)
                new_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                headers = dict(response.headers)
                headers.pop("content-length", None)
                return Response(
                    content=new_body,
                    status_code=response.status_code,
                    headers=headers,
                    media_type="application/json",
                )
        except Exception:
            return response

        return response

    existing_paths = {getattr(r, "path", "") for r in getattr(app, "routes", [])}
    if STATUS_ENDPOINT not in existing_paths:
        @app.get(STATUS_ENDPOINT)
        def _gpi_2sur4_exp_status():
            return get_2sur4_exp_status(app)

    app.state.gpi_2sur4_exp_middleware_installed = True
    app.state.gpi_2sur4_exp_patch = {
        "runtime_version": RUNTIME_VERSION,
        "rule_id": RULE_ID,
        "middleware_active": True,
        "target_paths": ["/drive/*h5*", "/gpi/*brief*", "/gpi/*latest*"],
        "strict_allowed_oui_non": "non",
    }
    return app.state.gpi_2sur4_exp_patch
