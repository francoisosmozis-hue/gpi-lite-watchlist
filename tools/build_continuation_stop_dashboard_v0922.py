import csv
import json
from datetime import datetime, timezone
from pathlib import Path

VERSION = "GPI_LITE_V0_9_22_CONTINUATION_STOP_DASHBOARD_A"

ROI_AUDIT_JSON = Path("data/output/latest_candidate_roi_audit.json")
ROBUSTNESS_JSON = Path("data/output/latest_candidate_roi_robustness.json")

OUT_CSV = Path("data/output/continuation_stop_dashboard.csv")
OUT_JSON = Path("data/output/latest_continuation_stop_dashboard.json")


def load_json(path: Path):
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def to_float(value, default=None):
    try:
        if value in ("", None):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def to_int(value, default=0):
    try:
        if value in ("", None):
            return default
        return int(float(str(value).replace(",", ".")))
    except Exception:
        return default


def first_segment_summary(robustness):
    segments = robustness.get("segment_summary", [])
    if not isinstance(segments, list) or not segments:
        return {}
    return segments[0] if isinstance(segments[0], dict) else {}


def decide(seg):
    segment = seg.get("candidate_segment", "ND")
    core = to_int(seg.get("core"), 0)
    wins = to_int(seg.get("wins"), 0)
    losses = to_int(seg.get("losses"), 0)
    roi = to_float(seg.get("roi"))
    ratio = to_float(seg.get("ratio_retour"))
    median_gain = to_float(seg.get("median_gain"))
    max_drawdown = to_float(seg.get("max_drawdown"))
    roi_without_top1 = to_float(seg.get("roi_without_top1"))
    roi_without_top3 = to_float(seg.get("roi_without_top3"))
    roi_without_top5 = to_float(seg.get("roi_without_top5"))
    top1_share = to_float(seg.get("top1_return_share"))
    top3_share = to_float(seg.get("top3_return_share"))
    robustness_status = seg.get("robustness_status", "ND")

    if not segment or segment == "ND":
        return "STOP_CLOUD_NO_SEGMENT", "non", True, ["NO_CANDIDATE_SEGMENT"], "Arrêter : aucun segment candidat exploitable."

    if core < 50:
        return "WAIT_SAMPLE_LT_50", "oui_conditionnel", False, ["SAMPLE_LT_50"], "Continuer paper uniquement jusqu'à 50 décisions exploitables."

    if core < 100:
        return "CONTINUE_UNTIL_CORE_100", "oui_conditionnel", False, ["SAMPLE_LT_100"], "Continuer paper uniquement jusqu'à 100 décisions exploitables."

    if roi is None or ratio is None:
        return "STOP_CLOUD_ROI_ND", "non", True, ["ROI_ND"], "Arrêter ou corriger : ROI candidat non calculable."

    if roi <= 0 or ratio <= 1:
        return "STOP_CLOUD_NEGATIVE_OR_FLAT", "non", True, ["ROI_NEGATIVE_OR_FLAT"], "Arrêter : aucun edge paper exploitable."

    if wins <= 0 or losses <= 0:
        return "STOP_CLOUD_BAD_DISTRIBUTION", "non", True, ["BAD_WIN_LOSS_DISTRIBUTION"], "Arrêter ou auditer : distribution non représentative."

    if robustness_status == "CANDIDATE_ROBUST_POSITIVE":
        if median_gain is not None and median_gain >= 0 and max_drawdown is not None and max_drawdown >= -20:
            return "CONTINUE_PAPER_ROBUST", "oui", False, ["ROBUST_POSITIVE", "NO_REAL_TICKET_ALLOWED"], "Continuer paper. Aucun réel sans validation split/proxy/calibration."

    reasons = []

    if robustness_status.startswith("FRAGILE"):
        reasons.append(robustness_status)

    if roi_without_top1 is not None and roi_without_top1 <= 0:
        reasons.append("TOP1_DEPENDENT")
    if roi_without_top3 is not None and roi_without_top3 <= 0:
        reasons.append("TOP3_DEPENDENT")
    if roi_without_top5 is not None and roi_without_top5 <= 0:
        reasons.append("TOP5_DEPENDENT")
    if median_gain is not None and median_gain < 0:
        reasons.append("MEDIAN_GAIN_NEGATIVE")
    if max_drawdown is not None and max_drawdown < -20:
        reasons.append("DRAWDOWN_LT_MINUS_20")
    if top1_share is not None and top1_share > 0.25:
        reasons.append("TOP1_SHARE_GT_25")
    if top3_share is not None and top3_share > 0.30:
        reasons.append("TOP3_SHARE_GT_30")

    if reasons:
        return "CONTINUE_PAPER_ONLY_FRAGILE", "oui_conditionnel", False, reasons, "Continuer Cloud uniquement en paper candidat. Aucun réel. Aucun CP3/exotique."

    return "STOP_CLOUD_UNCLASSIFIED_RISK", "non", True, ["UNCLASSIFIED_RISK", robustness_status], "Arrêter ou auditer manuellement avant poursuite."


def main():
    roi_audit = load_json(ROI_AUDIT_JSON)
    robustness = load_json(ROBUSTNESS_JSON)
    seg = first_segment_summary(robustness)

    decision, continue_cloud, stop_cloud, reason_codes, next_action = decide(seg)

    row = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "version": VERSION,
        "candidate_segment": seg.get("candidate_segment", "ND"),
        "decision": decision,
        "continue_cloud_oui_non": continue_cloud,
        "stop_cloud_recommended": stop_cloud,
        "strict_real_ticket_allowed": False,
        "next_action": next_action,
        "reason_codes": "|".join(reason_codes),
        "rows_input_roi": roi_audit.get("rows_input", "ND"),
        "rows_output_roi": roi_audit.get("rows_output", "ND"),
        "rows_input_robustness": robustness.get("rows_input", "ND"),
        "core": seg.get("core", "ND"),
        "wins": seg.get("wins", "ND"),
        "losses": seg.get("losses", "ND"),
        "nd": seg.get("nd", "ND"),
        "stake": seg.get("stake", "ND"),
        "return": seg.get("return", "ND"),
        "gain_net": seg.get("gain_net", "ND"),
        "roi": seg.get("roi", "ND"),
        "ratio_retour": seg.get("ratio_retour", "ND"),
        "median_gain": seg.get("median_gain", "ND"),
        "max_drawdown": seg.get("max_drawdown", "ND"),
        "roi_without_top1": seg.get("roi_without_top1", "ND"),
        "roi_without_top3": seg.get("roi_without_top3", "ND"),
        "roi_without_top5": seg.get("roi_without_top5", "ND"),
        "robustness_status": seg.get("robustness_status", "ND"),
        "policy": "Dashboard continuation/stop. Ne valide aucun ticket réel.",
    }

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)

    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        writer.writeheader()
        writer.writerow(row)

    audit = {
        "ok": True,
        "version": VERSION,
        "created_at": row["created_at"],
        "decision": decision,
        "continue_cloud_oui_non": continue_cloud,
        "stop_cloud_recommended": stop_cloud,
        "strict_real_ticket_allowed": False,
        "reason_codes": reason_codes,
        "next_action": next_action,
        "dashboard": row,
        "policy": "Dashboard continuation/stop. Ne valide aucun ticket réel.",
    }

    OUT_JSON.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")

    print("CONTINUATION_STOP_DASHBOARD_CREATED")
    print(str(OUT_CSV))
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
