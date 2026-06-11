

def _gpi_parse_float(value):
    try:
        if value is None:
            return None
        text = str(value).strip().replace(",", ".")
        if not text or text.upper() in {"ND", "NAN", "NONE"}:
            return None
        return float(text)
    except Exception:
        return None


def _gpi_parse_selection_numbers(value):
    if value is None:
        return []
    text = str(value).strip()
    if not text or text.upper() in {"ND", "NO_BET"}:
        return []
    for sep in ["-", "/", ",", ";", "|"]:
        text = text.replace(sep, " ")
    nums = []
    for part in text.split():
        try:
            nums.append(int(part))
        except Exception:
            pass
    return nums


def _gpi_sg_map_from_h5_update_csv(course_uid):
    import csv
    from pathlib import Path

    sg_map = {}

    candidates = [
        Path("data/output") / f"{course_uid}_h5_update.csv",
        Path("data/output") / "latest_h5_update.csv",
    ]

    candidates.extend(
        sorted(
            Path("data/output").glob("*_h5_update.csv"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:3]
    )

    num_keys = [
        "numero", "num", "n", "N", "cheval_num", "No", "N°", "numero_cheval"
    ]

    sg_keys = [
        "sg", "SG", "cote_sg", "COTE_SG", "simple_gagnant",
        "Simple gagnant", "simple_gagnant_h5", "sg_h5", "SG_H5",
        "cote_h5", "Cotes", "cotes"
    ]

    for csv_path in candidates:
        if not csv_path.exists():
            continue

        try:
            with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    num = None
                    sg = None

                    for key in num_keys:
                        if key in row and str(row.get(key, "")).strip():
                            num = row.get(key)
                            break

                    for key in sg_keys:
                        if key in row and str(row.get(key, "")).strip():
                            sg = row.get(key)
                            break

                    try:
                        num_int = int(str(num).strip())
                    except Exception:
                        continue

                    sg_val = _gpi_parse_float(sg)
                    if sg_val is not None:
                        sg_map[num_int] = sg_val

        except Exception:
            continue

        if sg_map:
            break

    return sg_map


def _gpi_get_sg_map_from_watch(watch, course_uid=None):
    sg_map = {}

    runners = watch.get("runners") or []
    if isinstance(runners, list):
        for row in runners:
            if not isinstance(row, dict):
                continue

            num = (
                row.get("numero")
                or row.get("num")
                or row.get("N")
                or row.get("cheval_num")
            )

            sg = (
                row.get("SG")
                or row.get("sg")
                or row.get("cote_sg")
                or row.get("simple_gagnant")
            )

            try:
                num = int(str(num).strip())
            except Exception:
                continue

            sg_val = _gpi_parse_float(sg)
            if sg_val is not None:
                sg_map[num] = sg_val

    for key in ["sg_values", "SG_VALUES", "top6_sg", "shortlist_top6", "market_sg"]:
        raw = watch.get(key)
        if not raw:
            continue

        text = str(raw)
        normalized = (
            text.replace(":", " ")
            .replace("=", " ")
            .replace(";", " ")
            .replace("|", " ")
            .replace("/", " ")
        )

        parts = normalized.split()
        for i in range(len(parts) - 1):
            try:
                num = int(parts[i])
                sg = _gpi_parse_float(parts[i + 1])
                if sg is not None:
                    sg_map[num] = sg
            except Exception:
                continue

    if not sg_map and course_uid:
        sg_map = _gpi_sg_map_from_h5_update_csv(course_uid)

    return sg_map


def _gpi_cp_sg_filter_fields(watch, course_uid=None):
    sg_map = _gpi_get_sg_map_from_watch(watch, course_uid)

    cp_sec_candidate = (
        watch.get("cp_sec_candidat")
        or watch.get("CP_SEC_CANDIDAT")
        or watch.get("selection_cp_sec")
        or watch.get("selection_CP_SEC")
        or ""
    )

    cp3_candidate = (
        watch.get("cp3_candidat")
        or watch.get("CP3_CANDIDAT")
        or watch.get("selection_cp3")
        or watch.get("selection_CP3")
        or ""
    )

    cp_sec_nums = _gpi_parse_selection_numbers(cp_sec_candidate)
    cp3_nums = _gpi_parse_selection_numbers(cp3_candidate)

    cp_sg_pair_sum = "ND"
    cp_sg_filter_status = "ND"

    if len(cp_sec_nums) >= 2:
        a, b = cp_sec_nums[:2]
        if a in sg_map and b in sg_map:
            pair_sum = sg_map[a] + sg_map[b]
            cp_sg_pair_sum = round(pair_sum, 2)
            cp_sg_filter_status = "OK" if pair_sum > 6 else "KO"
        else:
            cp_sg_filter_status = "ND_SG_ABSENTE"

    cp3_sg_min_pair_sum = "ND"
    cp3_sg_filter_status = "ND"

    if len(cp3_nums) >= 3:
        sg_values = [sg_map.get(n) for n in cp3_nums if sg_map.get(n) is not None]
        if len(sg_values) >= 3:
            two_lowest = sorted(sg_values)[:2]
            min_pair_sum = two_lowest[0] + two_lowest[1]
            cp3_sg_min_pair_sum = round(min_pair_sum, 2)
            cp3_sg_filter_status = "OK" if min_pair_sum > 6 else "KO"
        else:
            cp3_sg_filter_status = "ND_SG_ABSENTE"

    return {
        "cp_sec_candidate": str(cp_sec_candidate or ""),
        "cp_sg_pair_sum": cp_sg_pair_sum,
        "cp_sg_filter_status": cp_sg_filter_status,
        "cp3_candidate": str(cp3_candidate or ""),
        "cp3_sg_min_pair_sum": cp3_sg_min_pair_sum,
        "cp3_sg_filter_status": cp3_sg_filter_status,
    }


from pathlib import Path
import re
import unicodedata
import uuid
from difflib import SequenceMatcher

import pandas as pd

from engine.pdf_extract import extract_text_from_pdf, parse_zeturf_pronostics_text
from engine.detector import detect_pdf_type
from engine.exports import export_csv
from engine.market import compare_9h_h5
from engine.gpi_lite import build_watchlist_row
from engine.prompt_gpt_builder import rewrite_prompt_gpt_from_decision
from engine.decision_engine_outputs import write_decision_engine_outputs
from engine.decision_engine_replay import replay_decision_engine_outputs
from engine.prompt_integrity import validate_prompt_file
from engine.paper_trading_registry import append_paper_trading_decision
from engine.paper_audit import write_paper_audit
from engine.volatilite_audit import write_volatilite_audit


DATA_DIR = Path("data")
H5_DIR = DATA_DIR / "h5"
OUTPUT_DIR = DATA_DIR / "output"


def normalize_name(value: str) -> str:
    value = str(value).lower()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))

    replacements = [
        ".pdf",
        ".csv",
        "_course_9h_clean",
        "_course_h5_clean",
        "pronostics & cotes",
        "pronostics cotes",
        "cotes",
        "h-5",
        "h5",
        "9h",
        "zeturf",
    ]

    for item in replacements:
        value = value.replace(item, " ")

    value = re.sub(r"[^a-z0-9]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()

    return value


def find_matching_9h_csv(h5_pdf: Path):
    h5_key = normalize_name(h5_pdf.stem)

    candidates = sorted(OUTPUT_DIR.glob("*_course_9h_clean.csv"))

    if not candidates:
        return None, "", "aucun fichier course_9h_clean.csv disponible"

    scored = []

    for csv_path in candidates:
        course_uid = csv_path.name.replace("_course_9h_clean.csv", "")
        csv_key = normalize_name(course_uid)

        ratio = SequenceMatcher(None, h5_key, csv_key).ratio()

        if h5_key in csv_key or csv_key in h5_key:
            ratio = max(ratio, 0.95)

        scored.append((ratio, csv_path, course_uid))

    scored.sort(reverse=True, key=lambda x: x[0])

    best_ratio, best_path, best_course_uid = scored[0]

    if best_ratio < 0.55:
        return None, "", f"aucune correspondance 9h fiable pour {h5_pdf.name}. Score={best_ratio:.2f}"

    return best_path, best_course_uid, ""


def process_h5_pdf(h5_pdf: Path):
    print("")
    print(f"Fichier H-5 : {h5_pdf.name}")

    file_9h, course_uid, error = find_matching_9h_csv(h5_pdf)

    if file_9h is None:
        print(error)
        return

    print(f"Correspondance 9h : {file_9h.name}")
    print(f"COURSE_UID : {course_uid}")

    df_9h = pd.read_csv(file_9h)

    pages = extract_text_from_pdf(h5_pdf)
    full_text = "\n".join(p["text"] for p in pages)

    pdf_type = detect_pdf_type(full_text)
    print(f"Type détecté : {pdf_type}")

    if pdf_type != "ZETURF_PRONOS_COTES":
        print("Action : ce fichier ne peut pas servir de H-5 pré-course")
        return

    df_h5 = parse_zeturf_pronostics_text(
        full_text,
        course_uid=course_uid,
        snapshot_time="H5"
    )

    required_h5_cols = ["numero", "SG", "SP_min", "SP_max", "statut", "is_np"]
    missing_h5_cols = [col for col in required_h5_cols if col not in df_h5.columns]

    if missing_h5_cols:
        raise ValueError(
            "H5_SCHEMA_INVALID | "
            f"course_uid={course_uid} | "
            f"missing_columns={missing_h5_cols} | "
            f"available_columns={list(df_h5.columns)}"
        )

    export_csv(df_h5, f"{course_uid}_course_h5_clean.csv")

    h5_update = compare_9h_h5(df_9h, df_h5)
    export_csv(h5_update, f"{course_uid}_h5_update.csv")

    watch = build_watchlist_row(df_h5, course_uid)

    variation_rouge_top6 = "non"
    top6_nums = [
        int(x) for x in str(watch["top6_sg_9h"]).split(",")
        if str(x).strip().isdigit()
    ]

    if top6_nums and "numero" in h5_update.columns and "statut_marche" in h5_update.columns:
        top6_update = h5_update[h5_update["numero"].isin(top6_nums)]
        if (top6_update["statut_marche"] == "KILL_SWITCH").any():
            variation_rouge_top6 = "oui"

    kill_switchs = []

    if variation_rouge_top6 == "oui":
        kill_switchs.append("VOLATILITE_KILL_SWITCH_TOP6")

    if watch["favori_fragile"] == "oui":
        kill_switchs.append("FAVORI_FRAGILE_SOUS_PAYE")

    if str(watch["cp3_statut"]).startswith("REFUS"):
        kill_switchs.append("CP3_REFUS")

    if kill_switchs:
        verdict = "NO_BET"
        structure_max = "CP_SEC_SIMULATION"
        edge_status = "EDGE_ABSENT"
        segment_roi = "NO_BET"
    else:
        verdict = "SIMULATION_OU_ETUDE"
        structure_max = "CP_SEC_SIMULATION"
        edge_status = "EDGE_FRAGILE"
        segment_roi = "SIMULATION"

    resume = f"""COURSE : {course_uid}
QUALITE : {watch['qualite_matin']}
PARTANTS_REELS : {watch['partants_reels']}
TOP6_SG : {watch['top6_sg_9h']}
ANOMALIES_7_12 : {watch['anomalies_7_12']}
FAVORI : {watch['favori']}
FAVORI_FRAGILE : {watch['favori_fragile']}
VARIATION_ROUGE_TOP6 : {variation_rouge_top6}
SP_VALUES : {watch['sp_values']}
CP_SEC_CANDIDAT : {watch['cp_sec_candidat']}
CP_SEC_STATUT : simulation CP. Filtre SG contrôlé. Ticket réel interdit si kill-switch ou calibration/proxy strict absent
CP3_STATUT : {watch['cp3_statut']}
KILL_SWITCHS : {', '.join(kill_switchs) if kill_switchs else 'aucun'}
VERDICT_PYTHON : {verdict}
ACTION_GPT : contrôler uniquement, ne pas transformer une simulation en ticket strict
"""

    out_resume = OUTPUT_DIR / f"{course_uid}_analyse_course_resume.txt"
    out_resume.write_text(resume, encoding="utf-8")

    prompt_gpt = f"""Applique GPI V19.1.4.

Ne change pas le verdict Python.
Ne transforme pas une simulation en ticket strict.
Si un kill-switch est actif, confirme NO BET.
Si kill-switch actif, calibration absente ou proxy CP strict absent, CP réel interdit.
Si SP calibration absente, SP standard interdit.
Contrôle seulement la cohérence.

Résumé Python :

{resume}

Réponds uniquement avec :
VERDICT
Qualité
Structure max
Ticket exact ou NO BET
Raison principale
Segment ROI
Edge
"""

    out_prompt = OUTPUT_DIR / f"{course_uid}_prompt_gpt.txt"
    out_prompt.write_text(prompt_gpt, encoding="utf-8")

    cp_sg_fields = _gpi_cp_sg_filter_fields(watch, course_uid)

    cp_sec_sg_sum = cp_sg_fields.get("cp_sg_pair_sum", "ND")
    cp_sec_sg_status = cp_sg_fields.get("cp_sg_filter_status", "ND")

    volatilite_fields = _gpi_volatilite_top6_fields(course_uid)

    volatilite_scope = str(volatilite_fields.get("volatilite_scope", "TOP6_UNION"))
    volatilite_top6_max_pct = str(volatilite_fields.get("volatilite_top6_max_pct", "ND"))
    volatilite_top6_max_numero = str(volatilite_fields.get("volatilite_top6_max_numero", "ND"))
    volatilite_top6_max_cheval = str(volatilite_fields.get("volatilite_top6_max_cheval", "ND"))
    volatilite_top6_sg_9h = str(volatilite_fields.get("volatilite_top6_sg_9h", "ND"))
    volatilite_top6_sg_h5 = str(volatilite_fields.get("volatilite_top6_sg_h5", "ND"))
    volatilite_top6_direction = str(volatilite_fields.get("volatilite_top6_direction", "ND"))
    volatilite_status = str(volatilite_fields.get("volatilite_status", "ND"))

    if any("VOLATILITE_KILL_SWITCH" in str(k).upper() for k in kill_switchs):
        volatilite_status = "KILL"

    strict_blockers = []

    if kill_switchs:
        strict_blockers.append("kill_switch:" + ",".join(str(k) for k in kill_switchs))

    if "SIMULATION" in str(structure_max).upper():
        strict_blockers.append("structure_simulation")

    if str(edge_status).upper() in {"EDGE_ABSENT", "EDGE_FRAGILE", "ND"}:
        strict_blockers.append("edge_non_net")

    if str(cp_sec_sg_status).upper() != "OK":
        strict_blockers.append("cp_sec_sg_filter_" + str(cp_sec_sg_status))

    if str(verdict).upper() == "NO_BET":
        strict_blockers.append("verdict_no_bet")

    decision_strict_allowed = "non" if strict_blockers else "oui"
    decision_block_reason = " | ".join(strict_blockers) if strict_blockers else "aucun blocage decision-level"
    strict_gate_summary = (
        "CLOSED: " + decision_block_reason
        if decision_strict_allowed == "non"
        else "OPEN: tous les verrous decision-level sont ouverts"
    )

    paper_fields = _gpi_paper_candidate_fields(
        watch=watch,
        kill_switchs=kill_switchs,
        cp_sec_sg_sum=cp_sec_sg_sum,
        cp_sec_sg_status=cp_sec_sg_status,
        volatilite_status=volatilite_status,
    )

    decision = pd.DataFrame([{
        "decision_id": str(uuid.uuid4()),
        "course_uid": course_uid,
        "snapshot_time": "H5",
        "qualite": watch["qualite_matin"],
        "score_jouabilite": "",
        "verdict": verdict,
        "structure_max": structure_max,
        "selection_cp_sec": watch["cp_sec_candidat"],
        "selection_cp3": "",
        "selection_sp": watch["sp_values"],
        "reason_codes": watch["cp3_statut"],
        "kill_switch_codes": ",".join(kill_switchs),
        "segment_roi": segment_roi,
        "edge_status": edge_status,
        "cp_sec_candidate": cp_sg_fields.get("cp_sec_candidate", ""),
        "cp_sg_pair_sum": cp_sg_fields.get("cp_sg_pair_sum", "ND"),
        "cp_sg_filter_status": cp_sg_fields.get("cp_sg_filter_status", "ND"),
        "cp3_candidate": cp_sg_fields.get("cp3_candidate", ""),
        "cp3_sg_min_pair_sum": cp_sg_fields.get("cp3_sg_min_pair_sum", "ND"),
        "cp3_sg_filter_status": cp_sg_fields.get("cp3_sg_filter_status", "ND"),
        "decision_strict_allowed": decision_strict_allowed,
        "decision_block_reason": decision_block_reason,
        "strict_gate_summary": strict_gate_summary,
        "volatilite_scope": volatilite_scope,
        "volatilite_top6_max_pct": volatilite_top6_max_pct,
        "volatilite_top6_max_numero": volatilite_top6_max_numero,
        "volatilite_top6_max_cheval": volatilite_top6_max_cheval,
        "volatilite_top6_sg_9h": volatilite_top6_sg_9h,
        "volatilite_top6_sg_h5": volatilite_top6_sg_h5,
        "volatilite_top6_direction": volatilite_top6_direction,
        "volatilite_status": volatilite_status,
        "cp_sec_sg_sum": cp_sec_sg_sum,
        "cp_sec_sg_status": cp_sec_sg_status,
        "paper_candidate_allowed": paper_fields.get("paper_candidate_allowed", "non"),
        "paper_candidate_type": paper_fields.get("paper_candidate_type", "NO_BET"),
        "paper_candidate_ticket": paper_fields.get("paper_candidate_ticket", "NO_BET"),
        "paper_candidate_segment": paper_fields.get("paper_candidate_segment", "PAPER_TRADING"),
        "paper_candidate_mise": paper_fields.get("paper_candidate_mise", "0"),
        "paper_candidate_reason": paper_fields.get("paper_candidate_reason", "ND"),

    }])

    export_csv(decision, f"{course_uid}_decision_lite.csv")

    print("")
    print("H5 update créé")
    print(f"data/output/{course_uid}_h5_update.csv")

    print("")
    print("Decision Lite créée")
    print(f"data/output/{course_uid}_decision_lite.csv")

    print("")
    try:
        rewrite_prompt_gpt_from_decision(course_uid)
    except Exception as exc:
        print(f"prompt_gpt rewrite impossible : {exc}")
    print("Prompt GPT créé")
    try:
        prompt_path = rewrite_prompt_gpt_from_decision(course_uid)
        prompt_ok, prompt_errors = validate_prompt_file(prompt_path)
        if not prompt_ok:
            raise RuntimeError('prompt_gpt invalide : ' + ' | '.join(prompt_errors))
    except Exception as exc:
        print(f"prompt_gpt integrity impossible : {exc}")
        raise

    try:
        deo_path, deo_latest_path, deo_canonical_path = write_decision_engine_outputs(course_uid)
        print("")
        print("Decision Engine Outputs créé")
        print(deo_path)
    
        try:
            replay_path, replay_latest_path, replay_canonical_path, replay_match = replay_decision_engine_outputs(course_uid)
            print("")
            print("Decision Engine Replay créé")
            print(replay_path)
            print(f"Replay match : {replay_match}")
            try:
                paper_path, latest_paper_path, paper_count = append_paper_trading_decision(course_uid)
                print("")
                print("Paper Trading mis à jour")
                print(paper_path)
                print(f"Paper decisions count : {paper_count}")
                try:
                    paper_audit_csv, paper_audit_json, paper_audit = write_paper_audit()
                    print("")
                    print("Paper Trading Audit créé")
                    print(paper_audit_csv)
                    print(paper_audit_json)
                    print(f"Paper unique count : {paper_audit.get('paper_unique_count')}")
                    print(f"Paper recommendation : {paper_audit.get('recommendation')}")

                    try:
                        vol_csv, vol_json, latest_vol_csv, latest_vol_json, vol_audit = write_volatilite_audit()
                        print("")
                        print("Volatilite Audit créé")
                        print(vol_csv)
                        print(vol_json)
                        print(latest_vol_csv)
                        print(latest_vol_json)
                        print(f"Volatilite OK : {vol_audit.get('volatilite_status_OK_count')}")
                        print(f"Volatilite DEGRADE : {vol_audit.get('volatilite_status_DEGRADE_count')}")
                        print(f"Volatilite KILL : {vol_audit.get('volatilite_status_KILL_count')}")
                        print(f"Paper candidates oui : {vol_audit.get('paper_candidate_allowed_oui_count')}")
                    except Exception as exc:
                        print(f"volatilite_audit impossible : {exc}")
                        raise

                except Exception as exc:
                    print(f"paper_audit impossible : {exc}")
                    raise

            except Exception as exc:
                print(f"paper_trading impossible : {exc}")
                raise

        except Exception as exc:
            print(f"decision_engine_replay impossible : {exc}")
            raise
    
    except Exception as exc:
        print(f"decision_engine_outputs impossible : {exc}")
        raise
    

    print(out_prompt)



def _gpi_float(value):
    try:
        text = str(value).strip().replace(",", ".")
        if not text or text.upper() in {"ND", "NAN", "NONE", "NULL"}:
            return None
        return float(text)
    except Exception:
        return None


def _gpi_volatilite_top6_fields(course_uid):
    out = {
        "volatilite_scope": "TOP6_UNION",
        "volatilite_top6_max_pct": "ND",
        "volatilite_top6_max_numero": "ND",
        "volatilite_top6_max_cheval": "ND",
        "volatilite_top6_sg_9h": "ND",
        "volatilite_top6_sg_h5": "ND",
        "volatilite_top6_direction": "ND",
        "volatilite_status": "ND",
        "volatilite_top6_source_ref": "9H",
        "volatilite_top6_source_h5": "H5",
    }

    path_9h = OUTPUT_DIR / f"{course_uid}_course_9h_clean.csv"
    path_h5 = OUTPUT_DIR / f"{course_uid}_course_h5_clean.csv"

    if not path_9h.exists() or not path_h5.exists():
        return out

    try:
        df_9h = pd.read_csv(path_9h, dtype=str, keep_default_na=False)
        df_h5 = pd.read_csv(path_h5, dtype=str, keep_default_na=False)
    except Exception:
        return out

    if df_9h.empty or df_h5.empty:
        return out

    for df in [df_9h, df_h5]:
        if "numero" not in df.columns or "SG" not in df.columns:
            return out

    def _rows_map(df):
        rows = []
        sg_map = {}
        cheval_map = {}

        for _, row in df.iterrows():
            statut = str(row.get("statut", "")).upper()
            is_np = str(row.get("is_np", "")).strip()

            if statut == "NP" or is_np == "1":
                continue

            try:
                num = int(str(row.get("numero", "")).strip())
            except Exception:
                continue

            sg = _gpi_float(row.get("SG", ""))
            if sg is None or sg <= 0:
                continue

            cheval = str(row.get("cheval", "")).strip()

            rows.append({
                "numero": num,
                "cheval": cheval,
                "SG": sg,
            })

            sg_map[num] = sg
            cheval_map[num] = cheval

        rows = sorted(rows, key=lambda r: r["SG"])

        return rows, sg_map, cheval_map

    rows_9h, sg_9h, cheval_9h = _rows_map(df_9h)
    rows_h5, sg_h5, cheval_h5 = _rows_map(df_h5)

    if not rows_9h or not rows_h5:
        return out

    top6_9h = {r["numero"] for r in rows_9h[:6]}
    top6_h5 = {r["numero"] for r in rows_h5[:6]}

    union_nums = sorted(top6_9h.union(top6_h5))

    max_item = None

    for num in union_nums:
        sg_ref = sg_9h.get(num)
        sg_now = sg_h5.get(num)

        if sg_ref is None or sg_now is None or sg_ref <= 0 or sg_now <= 0:
            continue

        pct = abs(sg_now - sg_ref) / sg_ref * 100

        if sg_now < sg_ref:
            direction = "STEAM"
        elif sg_now > sg_ref:
            direction = "DRIFT"
        else:
            direction = "STABLE"

        cheval = cheval_h5.get(num) or cheval_9h.get(num) or ""

        item = {
            "pct": round(pct, 2),
            "numero": num,
            "cheval": cheval,
            "sg_9h": sg_ref,
            "sg_h5": sg_now,
            "direction": direction,
        }

        if max_item is None or item["pct"] > max_item["pct"]:
            max_item = item

    if max_item is None:
        return out

    out["volatilite_scope"] = "TOP6_UNION"
    out["volatilite_top6_max_pct"] = str(max_item["pct"])
    out["volatilite_top6_max_numero"] = str(max_item["numero"])
    out["volatilite_top6_max_cheval"] = str(max_item["cheval"])
    out["volatilite_top6_sg_9h"] = str(max_item["sg_9h"])
    out["volatilite_top6_sg_h5"] = str(max_item["sg_h5"])
    out["volatilite_top6_direction"] = str(max_item["direction"])

    if max_item["pct"] > 35:
        out["volatilite_status"] = "KILL"
    elif max_item["pct"] > 20:
        out["volatilite_status"] = "DEGRADE"
    else:
        out["volatilite_status"] = "OK"

    return out


def _gpi_paper_candidate_fields(watch, kill_switchs, cp_sec_sg_sum, cp_sec_sg_status, volatilite_status):
    out = {
        "paper_candidate_allowed": "non",
        "paper_candidate_type": "NO_BET",
        "paper_candidate_ticket": "NO_BET",
        "paper_candidate_segment": "PAPER_TRADING",
        "paper_candidate_mise": "0",
        "paper_candidate_reason": "paper gate closed",
    }

    qualite = str(watch.get("qualite_matin", watch.get("qualite", "ND"))).upper().strip()
    cp_pair = str(watch.get("cp_sec_candidat", watch.get("selection_cp_sec", ""))).strip()
    cp_status = str(cp_sec_sg_status).upper().strip()
    vol_status = str(volatilite_status).upper().strip()

    try:
        cp_sum = float(str(cp_sec_sg_sum).replace(",", "."))
    except Exception:
        cp_sum = None

    blockers = []

    if qualite not in {"A", "B", "B+"}:
        blockers.append("qualite_non_A_B")

    if not cp_pair:
        blockers.append("cp_sec_absent")

    if cp_status != "OK":
        blockers.append("cp_sec_sg_status_" + str(cp_status or "ND"))

    if cp_sum is None:
        blockers.append("cp_sec_sg_sum_ND")
    elif cp_sum < 7.5:
        blockers.append("cp_sec_sg_sum_lt_7_5")

    if vol_status == "KILL":
        blockers.append("volatilite_KILL")

    if kill_switchs:
        blockers.append("kill_switch:" + ",".join(str(k) for k in kill_switchs))

    favori_fragile = str(watch.get("favori_fragile", "")).lower().strip()
    if favori_fragile in {"oui", "true", "1"}:
        blockers.append("favori_fragile")

    if blockers:
        out["paper_candidate_reason"] = " | ".join(blockers)
        return out

    out["paper_candidate_allowed"] = "oui"
    out["paper_candidate_type"] = "CP_SEC_PAPER"
    out["paper_candidate_ticket"] = f"CP_SEC {cp_pair} 1 EUR PAPER_ONLY"
    out["paper_candidate_segment"] = "CP_SEC_PAPER"
    out["paper_candidate_mise"] = "1"
    out["paper_candidate_reason"] = (
        f"qualite {qualite}, CP_SEC {cp_pair}, SG sum {cp_sum}, "
        f"volatilite {vol_status}, aucun kill-switch"
    )

    return out


def main():
    H5_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    h5_pdfs = sorted(H5_DIR.glob("*.pdf"))

    if not h5_pdfs:
        print("Aucun PDF H-5 trouvé dans data/h5")
        return

    print(f"{len(h5_pdfs)} PDF H-5 détecté(s) dans data/h5")

    for h5_pdf in h5_pdfs:
        process_h5_pdf(h5_pdf)


if __name__ == "__main__":
    main()

    # GPI_PATCH_H5_STRICT_CANDIDATES_WRITER_V0_1_START
    # Rebuild strict CP_SEC candidate universe after every H-5 decision_engine_outputs creation.
    # This does not open strict betting by itself.
    # A row is written in cp_sec_strict_candidates_v0_1.csv only if the DEO row passes:
    # decision_strict_allowed=oui, non-simulation structure, no kill-switch,
    # out-of-sample split/replay/calibration OK, proxy VERT, fallback present, R_prudent >= 3.00.
    try:
        from build_cp_sec_strict_candidates_v0_1 import main as _gpi_build_cp_sec_strict_candidates_v0_1
        _gpi_build_cp_sec_strict_candidates_v0_1()
        print()
        print("CP_SEC Strict Candidates V0_1 mis a jour")
        print(r"data\output\cp_sec_strict_candidates_v0_1.csv")
    except Exception as exc:
        # Non-bloquant: H-5 peut rester exploitable, mais aucun strict ne doit etre ouvert.
        print()
        print(f"CP_SEC Strict Candidates V0_1 erreur non bloquante : {exc}")
    # GPI_PATCH_H5_STRICT_CANDIDATES_WRITER_V0_1_END

