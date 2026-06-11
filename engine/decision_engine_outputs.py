from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import pandas as pd

from engine.split_registry import get_split_info
from engine.proxy_registry import get_proxy_info
from engine.backtest_cp_registry import get_cp_sec_vs_cp3_info


OUTPUT_DIR = Path("data/output")
ENGINE_VERSION = "GPI_LITE_V0_9_5"
RULESET_VERSION = "GPI_ZEturf_V19_1_4"


def _as_text(value, default="ND"):
    if value is None:
        return default

    try:
        if pd.isna(value):
            return default
    except Exception:
        pass

    text = str(value).strip()

    if text == "" or text.lower() in ["nan", "none"]:
        return default

    return text


def _first(row, names, default="ND"):
    if row is None:
        return default

    for name in names:
        if name in row:
            value = _as_text(row.get(name), default="")
            if value != "":
                return value

    return default


def _read_first_row(path):
    path = Path(path)

    if not path.exists():
        return None

    try:
        df = pd.read_csv(path)
    except Exception:
        return None

    if df.empty:
        return None

    return df.iloc[0]


def _read_df(path):
    path = Path(path)

    if not path.exists():
        return None

    try:
        df = pd.read_csv(path)
    except Exception:
        return None

    if df.empty:
        return None

    return df


def _sha256_file(path):
    path = Path(path)

    if not path.exists():
        return "ABSENT"

    h = hashlib.sha256()

    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)

    return h.hexdigest()


def _short_hash(text, size=16):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:size]


def _course_paths(course_uid):
    return {
        "decision_lite": OUTPUT_DIR / f"{course_uid}_decision_lite.csv",
        "h5_update": OUTPUT_DIR / f"{course_uid}_h5_update.csv",
        "course_h5_clean": OUTPUT_DIR / f"{course_uid}_course_h5_clean.csv",
        "course_9h_clean": OUTPUT_DIR / f"{course_uid}_course_9h_clean.csv",
        "prompt_gpt": OUTPUT_DIR / f"{course_uid}_prompt_gpt.txt",
        "analyse_resume": OUTPUT_DIR / f"{course_uid}_analyse_course_resume.txt",
        "train_test_split": OUTPUT_DIR / "train_test_split.csv",
        "proxy_error_by_segment": Path("data/config/proxy_error_by_segment.csv"),
        "backtest_cp_sec_vs_cp3": Path("data/config/backtest_cp_sec_vs_cp3.csv"),
    }


def _input_payload(course_uid, paths):
    payload = {
        "course_uid": course_uid,
        "engine_version": ENGINE_VERSION,
        "ruleset_version": RULESET_VERSION,
        "sources": {},
    }

    for key, path in paths.items():
        payload["sources"][key] = {
            "path": str(path).replace("\\", "/"),
            "sha256": _sha256_file(path),
        }

    return payload


def _build_input_hash(course_uid, paths):
    payload = _input_payload(course_uid, paths)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _input_source_ids(paths):
    parts = []

    for key, path in paths.items():
        parts.append(f"{key}:{str(path).replace(chr(92), '/')}:{_sha256_file(path)[:16]}")

    return " | ".join(parts)


def _infer_snapshot_time(decision_row, h5_update_row, h5_df):
    value = _first(decision_row, ["snapshot_time", "SNAPSHOT_TIME"], "")

    if value != "":
        return value

    value = _first(h5_update_row, ["snapshot_time", "SNAPSHOT_TIME"], "")

    if value != "":
        return value

    if h5_df is not None and "snapshot_time" in h5_df.columns:
        values = h5_df["snapshot_time"].dropna().astype(str).str.strip()
        if len(values) > 0:
            return values.iloc[0]

    return "H5"


def _count_partants_h5(h5_df):
    if h5_df is None or h5_df.empty:
        return "ND"

    if "statut" not in h5_df.columns and "is_np" not in h5_df.columns:
        return str(len(h5_df))

    n = 0

    for _, row in h5_df.iterrows():
        statut = _as_text(row.get("statut", ""), default="").upper()
        is_np = _as_text(row.get("is_np", ""), default="").lower()

        if statut in ["NP", "NON_PARTANT", "NON-PARTANT"]:
            continue

        if is_np in ["1", "true", "oui", "yes"]:
            continue

        n += 1

    return str(n)


def _reason_codes(decision_row):
    reason = _first(decision_row, ["reason_codes", "REASON_CODES"], "")

    if reason == "":
        reason = "NO_BET_DEFAULT"

    return reason


def _kill_switch_codes(decision_row):
    kill = _first(decision_row, ["kill_switch_codes", "kill_switchs", "KILL_SWITCHS"], "")

    if kill == "":
        return "aucun"

    return kill


def _strict_allowed(row):
    proxy_status = _as_text(row.get("proxy_status", "ND")).upper()

    proxy_ok = proxy_status not in [
        "",
        "ND",
        "PROXY_ND",
        "ROUGE",
        "PROXY_ROUGE",
        "FRAGILE",
    ]

    kill_switch_codes = _as_text(row.get("kill_switch_codes", "")).lower()
    kill_switch_ok = kill_switch_codes in ["", "aucun"]

    required_ok = [
        _as_text(row.get("input_hash")) not in ["", "ND"],
        _as_text(row.get("split")) not in ["", "ND"],
        _as_text(row.get("calibration_version")) not in ["", "ND"],
        _as_text(row.get("leakage_check_ok")).lower() == "true",
        _as_text(row.get("decision_before_race_oui_non")).lower() == "oui",
        proxy_ok,
        _as_text(row.get("cp_sec_vs_cp3_controlled_oui_non")).lower() == "oui",
        _as_text(row.get("backtest_pre_course_validated_oui_non")).lower() == "oui",
        _as_text(row.get("verdict")) != "NO_BET",
        _as_text(row.get("ticket_exact")) != "NO_BET",
        kill_switch_ok,
        _as_text(row.get("replay_match_oui_non")).lower() == "oui",
    ]

    return "oui" if all(required_ok) else "non"



def build_decision_engine_outputs(course_uid):
    paths = _course_paths(course_uid)

    decision_row = _read_first_row(paths["decision_lite"])
    h5_update_row = _read_first_row(paths["h5_update"])
    h5_df = _read_df(paths["course_h5_clean"])

    if decision_row is None:
        raise FileNotFoundError(f"decision_lite introuvable : {paths['decision_lite']}")

    split_info = get_split_info(course_uid)
    proxy_info = get_proxy_info(course_uid, decision_row, h5_df)
    backtest_info = get_cp_sec_vs_cp3_info(proxy_info)

    input_hash = _build_input_hash(course_uid, paths)

    verdict = _first(decision_row, ["verdict_python", "verdict", "VERDICT_PYTHON"], "NO_BET")
    structure_max = _first(decision_row, ["structure_max", "STRUCTURE_MAX"], "NO_BET")
    ticket_exact = _first(decision_row, ["ticket_exact", "TICKET_EXACT"], "NO_BET")
    segment_roi = _first(decision_row, ["segment_roi", "segment_ROI", "SEGMENT_ROI"], "NO_BET")
    edge_status = _first(decision_row, ["edge_status", "EDGE", "EDGE_STATUS"], "EDGE_ABSENT")

    kill_switch_codes = _kill_switch_codes(decision_row)
    reason_codes = _reason_codes(decision_row)

    split = split_info.get("split", "ND")
    calibration_version = split_info.get("calibration_version", "ND")
    leakage_check_ok = split_info.get("leakage_check_ok", "false").lower()

    if leakage_check_ok not in ["true", "false"]:
        leakage_check_ok = "false"

    row = {
        "decision_id": "",
        "course_uid": course_uid,
        "snapshot_time": _infer_snapshot_time(decision_row, h5_update_row, h5_df),
        "engine_version": ENGINE_VERSION,
        "ruleset_version": RULESET_VERSION,
        "input_hash": input_hash,
        "input_source_ids": _input_source_ids(paths),
        "qualite": _first(decision_row, ["qualite", "QUALITE"], "ND"),
        "score_jouabilite": _first(decision_row, ["score_jouabilite", "score_watchlist", "SCORE_JOUABILITE"], "ND"),
        "partants_reels": _first(decision_row, ["partants_reels", "PARTANTS_REELS"], _count_partants_h5(h5_df)),
        "verdict": verdict,
        "structure_max": structure_max,
        "structure_retenue": ticket_exact if ticket_exact != "NO_BET" else "NO_BET",
        "ticket_exact": ticket_exact,
        "selection_CP_SEC": _first(decision_row, ["selection_cp_sec", "cp_sec_candidat", "CP_SEC_CANDIDAT"], ""),
        "selection_CP3": _first(decision_row, ["selection_cp3", "CP3_SELECTION"], ""),
        "selection_SP": _first(decision_row, ["selection_sp", "SP_VALUES", "sp_values"], ""),
        "cp_sec_candidate": _first(decision_row, ["cp_sec_candidate", "selection_cp_sec", "selection_CP_SEC", "CP_SEC_CANDIDAT"], ""),
        "cp_sg_pair_sum": _first(decision_row, ["cp_sg_pair_sum", "CP_SG_PAIR_SUM"], "ND"),
        "cp_sg_filter_status": _first(decision_row, ["cp_sg_filter_status", "CP_SG_FILTER_STATUS"], "ND"),
        "cp3_candidate": _first(decision_row, ["cp3_candidate", "selection_cp3", "selection_CP3", "CP3_CANDIDATE"], ""),
        "cp3_sg_min_pair_sum": _first(decision_row, ["cp3_sg_min_pair_sum", "CP3_SG_MIN_PAIR_SUM"], "ND"),
        "cp3_sg_filter_status": _first(decision_row, ["cp3_sg_filter_status", "CP3_SG_FILTER_STATUS"], "ND"),
        "decision_strict_allowed": _first(decision_row, ["decision_strict_allowed", "DECISION_STRICT_ALLOWED"], "non"),
        "decision_block_reason": _first(decision_row, ["decision_block_reason", "DECISION_BLOCK_REASON"], "ND"),
        "strict_gate_summary": _first(decision_row, ["strict_gate_summary", "STRICT_GATE_SUMMARY"], "ND"),
        "volatilite_scope": _first(decision_row, ["volatilite_scope", "VOLATILITE_SCOPE"], "TOP6_UNION"),
        "volatilite_top6_max_pct": _first(decision_row, ["volatilite_top6_max_pct", "VOLATILITE_TOP6_MAX_PCT"], "ND"),
        "volatilite_top6_max_numero": _first(decision_row, ["volatilite_top6_max_numero", "VOLATILITE_TOP6_MAX_NUMERO"], "ND"),
        "volatilite_top6_max_cheval": _first(decision_row, ["volatilite_top6_max_cheval", "VOLATILITE_TOP6_MAX_CHEVAL"], "ND"),
        "volatilite_top6_sg_9h": _first(decision_row, ["volatilite_top6_sg_9h", "VOLATILITE_TOP6_SG_9H"], "ND"),
        "volatilite_top6_sg_h5": _first(decision_row, ["volatilite_top6_sg_h5", "VOLATILITE_TOP6_SG_H5"], "ND"),
        "volatilite_top6_direction": _first(decision_row, ["volatilite_top6_direction", "VOLATILITE_TOP6_DIRECTION"], "ND"),
        "volatilite_status": _first(decision_row, ["volatilite_status", "VOLATILITE_STATUS"], "ND"),
        "cp_sec_sg_sum": _first(decision_row, ["cp_sec_sg_sum", "CP_SEC_SG_SUM", "cp_sg_pair_sum", "CP_SG_PAIR_SUM"], "ND"),
        "cp_sec_sg_status": _first(decision_row, ["cp_sec_sg_status", "CP_SEC_SG_STATUS", "cp_sg_filter_status", "CP_SG_FILTER_STATUS"], "ND"),
        "paper_candidate_allowed": _first(decision_row, ["paper_candidate_allowed", "PAPER_CANDIDATE_ALLOWED"], "non"),
        "paper_candidate_type": _first(decision_row, ["paper_candidate_type", "PAPER_CANDIDATE_TYPE"], "NO_BET"),
        "paper_candidate_ticket": _first(decision_row, ["paper_candidate_ticket", "PAPER_CANDIDATE_TICKET"], "NO_BET"),
        "paper_candidate_segment": _first(decision_row, ["paper_candidate_segment", "PAPER_CANDIDATE_SEGMENT"], "PAPER_TRADING"),
        "paper_candidate_mise": _first(decision_row, ["paper_candidate_mise", "PAPER_CANDIDATE_MISE"], "0"),
        "paper_candidate_reason": _first(decision_row, ["paper_candidate_reason", "PAPER_CANDIDATE_REASON"], "ND"),
        "mise_theorique": "0" if verdict == "NO_BET" else "ND",
        "segment_ROI": segment_roi,
        "edge_status": edge_status,
        "reason_codes": reason_codes,
        "kill_switch_codes": kill_switch_codes,
        "proxy_status": proxy_info.get("proxy_status", "PROXY_ND"),
        "segment_id": proxy_info.get("segment_id", "ND"),
        "sample_n": proxy_info.get("sample_n", "ND"),
        "confidence": proxy_info.get("confidence", "ND"),
        "fallback_level": proxy_info.get("fallback_level", "ND"),
        "C_prudent": proxy_info.get("C_prudent", "ND"),
        "R_prudent": proxy_info.get("R_prudent", "ND"),
        "CP_SEC_domine_oui_non": backtest_info.get("CP_SEC_domine_oui_non", "ND"),
        "score_total": _first(decision_row, ["score_total", "SCORE_TOTAL"], "ND"),
        "statut_score": _first(decision_row, ["statut_score", "STATUT_SCORE"], "ND"),
        "statut_marche": "KILL_SWITCH" if "VOLATILITE_KILL_SWITCH" in kill_switch_codes else "ND",
        "erreur_proxy": proxy_info.get("erreur_proxy", "ND"),
        "erreur_relative_mediane": proxy_info.get("erreur_relative_mediane", "ND"),
        "taux_surestimation": proxy_info.get("taux_surestimation", "ND"),
        "segment_degrade_oui_non": proxy_info.get("segment_degrade_oui_non", "ND"),
        "motif_degradation": proxy_info.get("motif_degradation", "ND"),
        "proxy_file": proxy_info.get("proxy_file", "ND"),
        "bucket_N_detected": proxy_info.get("bucket_N_detected", "ND"),
        "bucket_marche_detected": proxy_info.get("bucket_marche_detected", "ND"),
        "bucket_selection_detected": proxy_info.get("bucket_selection_detected", "ND"),
        "bucket_rangs_detected": proxy_info.get("bucket_rangs_detected", "ND"),
        "discipline_bucket_detected": proxy_info.get("discipline_bucket_detected", "ND"),
        "selection_cp_sec_detected": proxy_info.get("selection_cp_sec_detected", "ND"),
        "rang_1_detected": proxy_info.get("rang_1_detected", "ND"),
        "rang_2_detected": proxy_info.get("rang_2_detected", "ND"),
        "cp_sec_vs_cp3_controlled_oui_non": backtest_info.get("cp_sec_vs_cp3_controlled_oui_non", "non"),
        "backtest_pre_course_validated_oui_non": backtest_info.get("backtest_pre_course_validated_oui_non", "non"),
        "backtest_cp_sample_n": backtest_info.get("backtest_cp_sample_n", "ND"),
        "ROI_CP_SEC": backtest_info.get("ROI_CP_SEC", "ND"),
        "ROI_CP3": backtest_info.get("ROI_CP3", "ND"),
        "ratio_median_CP_SEC": backtest_info.get("ratio_median_CP_SEC", "ND"),
        "ratio_median_CP3": backtest_info.get("ratio_median_CP3", "ND"),
        "drawdown_max_CP_SEC": backtest_info.get("drawdown_max_CP_SEC", "ND"),
        "drawdown_max_CP3": backtest_info.get("drawdown_max_CP3", "ND"),
        "cp3_remboursant_rate": backtest_info.get("cp3_remboursant_rate", "ND"),
        "dominance_score_backtest": backtest_info.get("dominance_score_backtest", "ND"),
        "structure_retenue_backtest": backtest_info.get("structure_retenue_backtest", "ND"),
        "cause_arbitrage": backtest_info.get("cause_arbitrage", "ND"),
        "backtest_mode": backtest_info.get("backtest_mode", "ND"),
        "backtest_file": backtest_info.get("backtest_file", "ND"),
        "split": split,
        "calibration_version": calibration_version,
        "leakage_check_ok": leakage_check_ok,
        "split_source": split_info.get("split_source", "ND"),
        "split_file": split_info.get("split_file", "ND"),
        "decision_before_race_oui_non": split_info.get("decision_before_race_oui_non", "non"),
        "output_replayed_oui_non": "non",
        "replay_match_oui_non": "ND",
        "strict_allowed_oui_non": "non",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    decision_material = {
        "course_uid": row["course_uid"],
        "engine_version": row["engine_version"],
        "ruleset_version": row["ruleset_version"],
        "input_hash": row["input_hash"],
        "verdict": row["verdict"],
        "structure_max": row["structure_max"],
        "ticket_exact": row["ticket_exact"],
        "cp_sec_candidate": row["cp_sec_candidate"],
        "cp_sg_pair_sum": row["cp_sg_pair_sum"],
        "cp_sg_filter_status": row["cp_sg_filter_status"],
        "cp3_candidate": row["cp3_candidate"],
        "cp3_sg_min_pair_sum": row["cp3_sg_min_pair_sum"],
        "cp3_sg_filter_status": row["cp3_sg_filter_status"],
        "decision_strict_allowed": row["decision_strict_allowed"],
        "decision_block_reason": row["decision_block_reason"],
        "strict_gate_summary": row["strict_gate_summary"],
        "volatilite_scope": row["volatilite_scope"],
        "volatilite_top6_max_pct": row["volatilite_top6_max_pct"],
        "volatilite_top6_max_numero": row["volatilite_top6_max_numero"],
        "volatilite_top6_max_cheval": row["volatilite_top6_max_cheval"],
        "volatilite_top6_sg_9h": row["volatilite_top6_sg_9h"],
        "volatilite_top6_sg_h5": row["volatilite_top6_sg_h5"],
        "volatilite_top6_direction": row["volatilite_top6_direction"],
        "volatilite_status": row["volatilite_status"],
        "cp_sec_sg_sum": row["cp_sec_sg_sum"],
        "cp_sec_sg_status": row["cp_sec_sg_status"],
        "paper_candidate_allowed": row["paper_candidate_allowed"],
        "paper_candidate_type": row["paper_candidate_type"],
        "paper_candidate_ticket": row["paper_candidate_ticket"],
        "paper_candidate_segment": row["paper_candidate_segment"],
        "paper_candidate_mise": row["paper_candidate_mise"],
        "paper_candidate_reason": row["paper_candidate_reason"],
        "reason_codes": row["reason_codes"],
        "kill_switch_codes": row["kill_switch_codes"],
    }

    row["decision_id"] = "DEO_" + _short_hash(json.dumps(decision_material, ensure_ascii=False, sort_keys=True), 20)

    raw_decision_strict_allowed = str(row.get("decision_strict_allowed", "non")).strip().lower()
    row["strict_allowed_oui_non"] = _strict_allowed(row)
    raw_engine_strict_allowed = str(row.get("strict_allowed_oui_non", "non")).strip().lower()

    if raw_decision_strict_allowed == "oui" and raw_engine_strict_allowed == "oui":
        row["decision_strict_allowed"] = "oui"
        if str(row.get("decision_block_reason", "ND")).strip().upper() in {"", "ND"}:
            row["decision_block_reason"] = "aucun blocage decision-level"
        row["strict_gate_summary"] = "OPEN: tous les verrous decision-level et moteur strict sont ouverts"
    else:
        row["decision_strict_allowed"] = "non"

        existing_reason = str(row.get("decision_block_reason", "ND")).strip()
        reasons = []

        if existing_reason and existing_reason.upper() not in {"ND", "AUCUN BLOCAGE DECISION-LEVEL"}:
            reasons.append(existing_reason)

        if raw_engine_strict_allowed != "oui":
            reasons.append("strict_allowed_oui_non=" + str(row.get("strict_allowed_oui_non", "non")))

        row["decision_block_reason"] = " | ".join(reasons) if reasons else "decision strict gate closed"
        row["strict_gate_summary"] = "CLOSED: " + str(row.get("decision_block_reason", "ND"))

    # Fail-safe audit paper.
    # Si le paper est refusé, la raison ne doit jamais rester ND.
    paper_allowed = str(row.get("paper_candidate_allowed", "non")).strip().lower()
    paper_reason = str(row.get("paper_candidate_reason", "ND")).strip()

    if paper_allowed != "oui" and paper_reason.upper() in {"", "ND", "NONE", "NULL"}:
        paper_blockers = []

        qualite_paper = str(row.get("qualite", "ND")).strip().upper()
        if qualite_paper not in {"A", "B", "B+"}:
            paper_blockers.append("qualite_non_A_B")

        cp_pair_paper = (
            str(row.get("cp_sec_candidate", "")).strip()
            or str(row.get("selection_CP_SEC", "")).strip()
            or str(row.get("selection_cp_sec", "")).strip()
        )
        if not cp_pair_paper:
            paper_blockers.append("cp_sec_absent")

        cp_status_paper = str(row.get("cp_sec_sg_status", row.get("cp_sg_filter_status", "ND"))).strip().upper()
        if cp_status_paper != "OK":
            paper_blockers.append("cp_sec_sg_status_" + cp_status_paper)

        try:
            cp_sum_paper = float(str(row.get("cp_sec_sg_sum", row.get("cp_sg_pair_sum", "ND"))).replace(",", "."))
        except Exception:
            cp_sum_paper = None

        if cp_sum_paper is None:
            paper_blockers.append("cp_sec_sg_sum_ND")
        elif cp_sum_paper < 7.5:
            paper_blockers.append("cp_sec_sg_sum_lt_7_5")

        vol_status_paper = str(row.get("volatilite_status", "ND")).strip().upper()
        if vol_status_paper == "KILL":
            paper_blockers.append("volatilite_KILL")

        kill_codes_paper = str(row.get("kill_switch_codes", "")).strip()
        if kill_codes_paper and kill_codes_paper.upper() not in {"AUCUN", "NONE", "ND"}:
            paper_blockers.append("kill_switch:" + kill_codes_paper)

        row["paper_candidate_reason"] = " | ".join(paper_blockers) if paper_blockers else "paper gate closed"

    if paper_allowed == "oui":
        if str(row.get("paper_candidate_reason", "ND")).strip().upper() in {"", "ND", "NONE", "NULL"}:
            row["paper_candidate_reason"] = "paper candidate accepted by CP_SEC paper gate"

    return row


def write_decision_engine_outputs(course_uid):
    row = build_decision_engine_outputs(course_uid)
    df = pd.DataFrame([row])

    specific_path = OUTPUT_DIR / f"{course_uid}_decision_engine_outputs.csv"
    latest_path = OUTPUT_DIR / "latest_decision_engine_outputs.csv"
    canonical_path = OUTPUT_DIR / "decision_engine_outputs.csv"

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    df.to_csv(specific_path, index=False, encoding="utf-8")
    df.to_csv(latest_path, index=False, encoding="utf-8")
    df.to_csv(canonical_path, index=False, encoding="utf-8")

    return specific_path, latest_path, canonical_path
