import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

PAPER_FILE = Path("data/output/paper_trading_50_decisions.csv")
RESULT_INPUT = Path("data/results/result_input.csv")

OUT_CSV = Path("data/output/paper_roi_by_candidate_segment.csv")
OUT_JSON = Path("data/output/latest_candidate_roi_audit.json")


def norm_pair(pair: str) -> str:
    nums = [x.strip() for x in str(pair or "").split("-") if x.strip().isdigit()]
    if len(nums) != 2:
        return ""
    return "-".join(sorted(nums, key=lambda x: int(x)))


def load_json_dict(value: str) -> dict:
    try:
        obj = json.loads(value or "{}")
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def read_csv(path: Path):
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def main() -> None:
    paper_rows = read_csv(PAPER_FILE)
    result_rows = read_csv(RESULT_INPUT)

    results_by_uid = {r.get("course_uid", ""): r for r in result_rows}

    out_rows = []

    segment_stats = defaultdict(lambda: {
        "count": 0,
        "matched": 0,
        "core": 0,
        "wins": 0,
        "losses": 0,
        "nd": 0,
        "stake": 0.0,
        "return": 0.0,
    })

    for p in paper_rows:
        cp_sec = p.get("CP_SEC_selection", "").strip()
        if not cp_sec:
            continue

        course_uid = p.get("course_uid", "")
        result = results_by_uid.get(course_uid)

        candidate_segment = "CP_SEC_CANDIDATE_SIMULATION"
        stake = 1.0
        return_amount = 0.0
        result_status = "RESULT_MATCH_ND"
        official_pair = norm_pair(cp_sec)
        official_report = ""

        stats = segment_stats[candidate_segment]
        stats["count"] += 1

        if not result:
            stats["nd"] += 1
            candidate_stake = 0
            gain_net = ""
            roi = ""
        else:
            stats["matched"] += 1
            report_status = result.get("official_reports_parse_status", "ND")

            if report_status != "OK_CORE_REPORTS":
                result_status = report_status or "REPORT_STATUS_ND"
                stats["nd"] += 1
                candidate_stake = 0
                gain_net = ""
                roi = ""
            else:
                stats["core"] += 1

                jp = load_json_dict(result.get("official_jumele_place_json", ""))
                jp_norm = {norm_pair(k): v for k, v in jp.items()}

                raw_return = jp_norm.get(official_pair, 0) or 0

                try:
                    return_amount = float(raw_return)
                except Exception:
                    return_amount = 0.0

                candidate_stake = stake
                gain_net = return_amount - stake
                roi = gain_net / stake if stake else ""

                if return_amount > 0:
                    result_status = "WIN"
                    stats["wins"] += 1
                else:
                    result_status = "LOSS"
                    stats["losses"] += 1

                official_report = json.dumps(jp, ensure_ascii=False, sort_keys=True)

                stats["stake"] += stake
                stats["return"] += return_amount

        out_rows.append({
            "course_uid": course_uid,
            "date": p.get("date", ""),
            "hippodrome": p.get("hippodrome", ""),
            "discipline": p.get("discipline", ""),
            "split": p.get("split", ""),
            "engine_version": p.get("engine_version", ""),
            "ruleset_version": p.get("ruleset_version", ""),
            "input_hash": p.get("input_hash", ""),
            "decision_before_race_oui_non": p.get("decision_before_race_oui_non", ""),
            "replay_match_oui_non": p.get("replay_match_oui_non", ""),
            "strict_allowed_oui_non": p.get("strict_allowed_oui_non", ""),
            "structure_retenue": p.get("structure_retenue", ""),
            "structure_max": p.get("structure_max", ""),
            "candidate_segment": candidate_segment,
            "candidate_selection": cp_sec,
            "candidate_selection_norm": official_pair,
            "candidate_stake": candidate_stake,
            "official_jumele_place_json": official_report,
            "candidate_return": return_amount if result_status in ("WIN", "LOSS") else "",
            "candidate_gain_net": gain_net,
            "candidate_roi": roi,
            "candidate_result_status": result_status,
            "arrivee": result.get("arrivee", "") if result else "",
        })

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "course_uid",
        "date",
        "hippodrome",
        "discipline",
        "split",
        "engine_version",
        "ruleset_version",
        "input_hash",
        "decision_before_race_oui_non",
        "replay_match_oui_non",
        "strict_allowed_oui_non",
        "structure_retenue",
        "structure_max",
        "candidate_segment",
        "candidate_selection",
        "candidate_selection_norm",
        "candidate_stake",
        "official_jumele_place_json",
        "candidate_return",
        "candidate_gain_net",
        "candidate_roi",
        "candidate_result_status",
        "arrivee",
    ]

    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(out_rows)

    segment_summary = {}

    for segment, s in segment_stats.items():
        gain = s["return"] - s["stake"]
        roi = gain / s["stake"] if s["stake"] else None
        ratio = s["return"] / s["stake"] if s["stake"] else None

        segment_summary[segment] = {
            "count": s["count"],
            "matched": s["matched"],
            "core": s["core"],
            "wins": s["wins"],
            "losses": s["losses"],
            "nd": s["nd"],
            "stake": round(s["stake"], 2),
            "return": round(s["return"], 2),
            "gain_net": round(gain, 2),
            "roi": round(roi, 6) if roi is not None else None,
            "ratio_retour": round(ratio, 6) if ratio is not None else None,
        }

    audit = {
        "ok": True,
        "version": "GPI_LITE_V0_9_21_A4_CANDIDATE_ROI_CP_SEC",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "paper_file": str(PAPER_FILE),
        "result_input_file": str(RESULT_INPUT),
        "output_csv": str(OUT_CSV),
        "rows_input": len(paper_rows),
        "result_input_rows": len(result_rows),
        "rows_output": len(out_rows),
        "status_counts": dict(Counter(r["candidate_result_status"] for r in out_rows)),
        "segment_summary": segment_summary,
        "strict_real_ticket_allowed": False,
    }

    OUT_JSON.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")

    print("CANDIDATE_ROI_CREATED")
    print(str(OUT_CSV))
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
