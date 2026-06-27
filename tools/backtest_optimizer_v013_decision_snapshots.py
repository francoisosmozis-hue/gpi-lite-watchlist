import csv
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.bet_selection_optimizer import optimize_bet_selection

CUTOFF_INT = 20260627

OUT_DIR = ROOT / "data" / "output" / "backtests"
OUT_DIR.mkdir(parents=True, exist_ok=True)

OUT_CSV = OUT_DIR / "backtest_optimizer_v013_decision_snapshots_pre_20260627.csv"
OUT_JSON = OUT_DIR / "backtest_optimizer_v013_decision_snapshots_pre_20260627_summary.json"


def s(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def f(v: Any) -> Optional[float]:
    try:
        txt = s(v).replace(",", ".")
        if not txt or txt.upper() in {"ND", "NA", "N/A", "NONE", "NULL"}:
            return None
        return float(txt)
    except Exception:
        return None


def date_int_from_uid(uid: str) -> Optional[int]:
    txt = s(uid)

    m = re.search(r"(\d{2})[_/-](\d{2})[_/-](\d{4})", txt)
    if m:
        dd, mm, yyyy = m.groups()
        return int(f"{yyyy}{mm}{dd}")

    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", txt)
    if m:
        yyyy, mm, dd = m.groups()
        return int(f"{yyyy}{mm}{dd}")

    return None


def read_csv(path: Path) -> List[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))
    except Exception:
        return []


def normalize_course_uid(uid: str) -> str:
    txt = s(uid)
    txt = txt.replace("Ã¨", "è").replace("Ã©", "é").replace("Ã´", "ô").replace("Ã®", "î")
    txt = re.sub(r"\s+", " ", txt)
    return txt.strip().upper()


def parse_pair(ticket: str) -> List[str]:
    nums = re.findall(r"\b\d{1,2}\b", s(ticket))
    return nums[:2]


def parse_arrival(row: Dict[str, Any]) -> List[str]:
    raw = (
        row.get("arrivee")
        or row.get("arrival")
        or row.get("resultat")
        or row.get("result")
        or row.get("classement")
        or ""
    )
    return re.findall(r"\b\d{1,2}\b", s(raw))


def cp_hit_top3(ticket: str, arrival: List[str]) -> str:
    pair = parse_pair(ticket)
    if len(pair) != 2:
        return "ND"
    if len(arrival) < 3:
        return "ND"

    top3 = set(arrival[:3])
    return "OUI" if pair[0] in top3 and pair[1] in top3 else "NON"


def get_rapport_cp(row: Dict[str, Any]) -> Optional[float]:
    keys = [
        "rapport_cp_sec",
        "rapport_cp",
        "rapport_couple_place",
        "rapport_jumele_place",
        "rapport_officiel",
        "retour_paper",
    ]

    for k in keys:
        val = f(row.get(k))
        if val is not None:
            return val

    for k, v in row.items():
        kl = str(k).lower()
        if ("rapport" in kl or "retour" in kl) and ("cp" in kl or "couple" in kl or "jumele" in kl):
            val = f(v)
            if val is not None:
                return val

    return None


def decision_files() -> List[Path]:
    d = ROOT / "data" / "output"

    files = []

    patterns = [
        "*_decision_engine_outputs.csv",
        "*_decision_lite.csv",
        "decision_engine_outputs.csv",
        "decision_lite.csv",
    ]

    for pat in patterns:
        files.extend(d.glob(pat))

    # On exclut les latest pour éviter les doublons avec fichiers nommés.
    files = [
        p for p in files
        if not p.name.startswith("latest_")
        and "backtest" not in p.name.lower()
    ]

    return sorted(set(files))


def load_results_by_course() -> Dict[str, Dict[str, Any]]:
    results: Dict[str, Dict[str, Any]] = {}

    paths = [
        ROOT / "data" / "output" / "paper_trading_results.csv",
        ROOT / "data" / "output" / "latest_paper_result_update.csv",
    ]

    for path in paths:
        if not path.exists():
            continue

        for row in read_csv(path):
            uid = row.get("course_uid")
            if not uid:
                continue
            results[normalize_course_uid(uid)] = row

    return results


def normalize_decision_row(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)

    # Harmonisation des champs historiques possibles.
    if not out.get("cp_sec_candidate"):
        out["cp_sec_candidate"] = (
            out.get("selection_CP_SEC")
            or out.get("CP_SEC_selection")
            or out.get("paper_selection")
            or ""
        )

    if not out.get("cp_sg_pair_sum"):
        out["cp_sg_pair_sum"] = (
            out.get("CP_SEC_sg_sum")
            or out.get("cp_sg_sum")
            or out.get("latest_cp_sg_pair_sum")
            or ""
        )

    if not out.get("cp_sg_filter_status"):
        out["cp_sg_filter_status"] = (
            out.get("CP_SEC_filter_status")
            or out.get("cp_filter_status")
            or "OK"
        )

    if not out.get("paper_candidate_reason"):
        if out.get("cp_sec_candidate"):
            out["paper_candidate_reason"] = "BACKTEST_DECISION_SNAPSHOT"
        else:
            out["paper_candidate_reason"] = "ND"

    return out


def main():
    results_by_course = load_results_by_course()

    rows_out = []
    seen = set()

    for path in decision_files():
        for raw in read_csv(path):
            row = normalize_decision_row(raw)

            course_uid = s(row.get("course_uid"))
            if not course_uid:
                continue

            date_int = date_int_from_uid(course_uid)
            if date_int is None or date_int >= CUTOFF_INT:
                continue

            key = normalize_course_uid(course_uid)
            if key in seen:
                continue
            seen.add(key)

            out = optimize_bet_selection(row)

            result_row = results_by_course.get(key, {})
            arrival = parse_arrival(result_row)
            ticket_paper = s(out.get("ticket_paper"))

            hit = "ND"
            rapport = None
            gain = "ND"
            roi = "ND"

            if ticket_paper != "NO_BET":
                hit = cp_hit_top3(ticket_paper, arrival)
                rapport = get_rapport_cp(result_row)

                stake = f(out.get("stake_paper")) or 0.0

                if rapport is not None and stake > 0:
                    gain_val = rapport * stake
                    gain = round(gain_val, 4)
                    roi = round((gain_val - stake) / stake, 6)

            rows_out.append({
                "course_uid": course_uid,
                "date_int": date_int,
                "source_file": str(path.relative_to(ROOT)),
                "optimizer_version": s(out.get("optimizer_version")),
                "qualite": s(row.get("qualite") or row.get("quality")),
                "best_structure": s(out.get("best_structure")),
                "ticket_paper": ticket_paper,
                "ticket_real": s(out.get("ticket_real")),
                "stake_paper": s(out.get("stake_paper")),
                "segment_roi": s(out.get("segment_roi")),
                "cp_sec_priority_score": s(out.get("cp_sec_priority_score")),
                "cp_sec_priority_level": s(out.get("cp_sec_priority_level")),
                "cp_sec_candidate": s(row.get("cp_sec_candidate")),
                "cp_sg_pair_sum": s(row.get("cp_sg_pair_sum")),
                "cp_sg_filter_status": s(row.get("cp_sg_filter_status")),
                "paper_candidate_reason": s(row.get("paper_candidate_reason")),
                "result_joined": "OUI" if result_row else "NON",
                "arrivee": s(result_row.get("arrivee")),
                "hit_top3_sportif": hit,
                "rapport_zeturf_cp": rapport if rapport is not None else "ND",
                "gain_paper": gain,
                "roi_paper": roi,
                "reason_codes": "|".join(out.get("reason_codes", [])),
                "rejected_structures": "|".join(out.get("rejected_structures", [])),
                "hard_blocks": "|".join(out.get("hard_blocks", [])),
            })

    rows_out.sort(key=lambda r: (r["date_int"], r["course_uid"]))

    fieldnames = [
        "course_uid",
        "date_int",
        "source_file",
        "optimizer_version",
        "qualite",
        "best_structure",
        "ticket_paper",
        "ticket_real",
        "stake_paper",
        "segment_roi",
        "cp_sec_priority_score",
        "cp_sec_priority_level",
        "cp_sec_candidate",
        "cp_sg_pair_sum",
        "cp_sg_filter_status",
        "paper_candidate_reason",
        "result_joined",
        "arrivee",
        "hit_top3_sportif",
        "rapport_zeturf_cp",
        "gain_paper",
        "roi_paper",
        "reason_codes",
        "rejected_structures",
        "hard_blocks",
    ]

    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows_out)

    summary = {
        "cutoff_exclusive": "2026-06-27",
        "decision_files_used": len(decision_files()),
        "decision_rows_replayed": len(rows_out),
        "results_joined": sum(1 for r in rows_out if r["result_joined"] == "OUI"),
        "by_best_structure": {},
        "by_segment_roi": {},
        "paper_tickets_count": sum(1 for r in rows_out if r["ticket_paper"] != "NO_BET"),
        "paper_tickets_hit_top3_oui": sum(1 for r in rows_out if r["hit_top3_sportif"] == "OUI"),
        "paper_tickets_hit_top3_non": sum(1 for r in rows_out if r["hit_top3_sportif"] == "NON"),
        "paper_tickets_hit_top3_nd": sum(1 for r in rows_out if r["ticket_paper"] != "NO_BET" and r["hit_top3_sportif"] == "ND"),
        "paper_tickets_with_rapport_cp": sum(1 for r in rows_out if r["rapport_zeturf_cp"] != "ND"),
        "roi_status": "ND si aucun rapport CP officiel ZEturf",
        "output_csv": str(OUT_CSV.relative_to(ROOT)),
    }

    for r in rows_out:
        summary["by_best_structure"][r["best_structure"]] = summary["by_best_structure"].get(r["best_structure"], 0) + 1
        summary["by_segment_roi"][r["segment_roi"]] = summary["by_segment_roi"].get(r["segment_roi"], 0) + 1

    OUT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("")
    print("BACKTEST STRICT DECISION SNAPSHOTS V0_1_3 AVANT 2026-06-27")
    print("==========================================================")
    print(f"Fichiers décisionnels utilisés : {summary['decision_files_used']}")
    print(f"Lignes décisionnelles rejouées : {summary['decision_rows_replayed']}")
    print(f"Résultats joints : {summary['results_joined']}")
    print(f"Tickets papier : {summary['paper_tickets_count']}")
    print(f"Hits sportifs top3 OUI : {summary['paper_tickets_hit_top3_oui']}")
    print(f"Hits sportifs top3 NON : {summary['paper_tickets_hit_top3_non']}")
    print(f"Hits sportifs top3 ND : {summary['paper_tickets_hit_top3_nd']}")
    print(f"Tickets avec rapport CP : {summary['paper_tickets_with_rapport_cp']}")
    print("")
    print("Par structure :")
    for k, v in sorted(summary["by_best_structure"].items()):
        print(f"- {k}: {v}")
    print("")
    print("Fichiers générés :")
    print(f"- {OUT_CSV}")
    print(f"- {OUT_JSON}")


if __name__ == "__main__":
    main()
