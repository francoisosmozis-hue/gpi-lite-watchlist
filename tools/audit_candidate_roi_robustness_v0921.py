import csv
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

INPUT_CSV = Path("data/output/paper_roi_by_candidate_segment.csv")

OUT_CSV = Path("data/output/candidate_roi_robustness.csv")
OUT_JSON = Path("data/output/latest_candidate_roi_robustness.json")

VERSION = "GPI_LITE_V0_9_21D_CANDIDATE_ROI_ROBUSTNESS_A"


def read_csv(path: Path):
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def to_float(value, default=0.0):
    try:
        if value in ("", None):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def pct(value):
    if value is None:
        return None
    return round(value, 6)


def max_drawdown(gains):
    equity = 0.0
    peak = 0.0
    max_dd = 0.0

    for gain in gains:
        equity += gain
        if equity > peak:
            peak = equity
        dd = equity - peak
        if dd < max_dd:
            max_dd = dd

    return round(max_dd, 2)


def longest_losing_streak(rows):
    current = 0
    longest = 0

    for r in rows:
        status = r.get("candidate_result_status", "")
        if status == "LOSS":
            current += 1
            longest = max(longest, current)
        elif status == "WIN":
            current = 0

    return longest


def roi_without_top_returns(stake_values, return_values, remove_n):
    paired = list(zip(stake_values, return_values))

    if not paired:
        return None

    paired_sorted = sorted(paired, key=lambda x: x[1], reverse=True)
    kept = paired_sorted[remove_n:]

    if not kept:
        return None

    stake = sum(x[0] for x in kept)
    ret = sum(x[1] for x in kept)

    if stake <= 0:
        return None

    return (ret - stake) / stake


def share_top_returns(return_values, n):
    total = sum(return_values)

    if total <= 0:
        return None

    top_sum = sum(sorted(return_values, reverse=True)[:n])
    return top_sum / total


def robustness_status(summary):
    roi = summary.get("roi")
    roi_wo_top1 = summary.get("roi_without_top1")
    roi_wo_top3 = summary.get("roi_without_top3")
    core = summary.get("core", 0)
    wins = summary.get("wins", 0)
    losses = summary.get("losses", 0)
    top1_share = summary.get("top1_return_share")
    max_dd = summary.get("max_drawdown")

    if core < 50:
        return "FRAGILE_SAMPLE_LT_50"

    if wins == 0 or losses == 0:
        return "FRAGILE_DISTRIBUTION"

    if roi is None or roi <= 0:
        return "NEGATIVE_OR_FLAT"

    if roi_wo_top1 is not None and roi_wo_top1 <= 0:
        return "FRAGILE_TOP1_DEPENDENT"

    if roi_wo_top3 is not None and roi_wo_top3 <= 0:
        return "FRAGILE_TOP3_DEPENDENT"

    if top1_share is not None and top1_share > 0.25:
        return "FRAGILE_TOP1_SHARE_GT_25"

    if max_dd is not None and max_dd < -15:
        return "FRAGILE_DRAWDOWN"

    return "CANDIDATE_ROBUST_POSITIVE"


def summarize_segment(segment, rows):
    staked_rows = [
        r for r in rows
        if r.get("candidate_result_status") in ("WIN", "LOSS")
        and to_float(r.get("candidate_stake")) > 0
    ]

    stake_values = [to_float(r.get("candidate_stake")) for r in staked_rows]
    return_values = [to_float(r.get("candidate_return")) for r in staked_rows]
    gain_values = [to_float(r.get("candidate_gain_net")) for r in staked_rows]

    count = len(rows)
    core = len(staked_rows)
    wins = sum(1 for r in rows if r.get("candidate_result_status") == "WIN")
    losses = sum(1 for r in rows if r.get("candidate_result_status") == "LOSS")
    nd = count - core

    stake = sum(stake_values)
    ret = sum(return_values)
    gain = ret - stake

    roi = (gain / stake) if stake else None
    ratio = (ret / stake) if stake else None
    win_rate = (wins / core) if core else None

    positive_returns = [x for x in return_values if x > 0]

    summary = {
        "candidate_segment": segment,
        "count": count,
        "core": core,
        "wins": wins,
        "losses": losses,
        "nd": nd,
        "stake": round(stake, 2),
        "return": round(ret, 2),
        "gain_net": round(gain, 2),
        "roi": pct(roi),
        "ratio_retour": pct(ratio),
        "win_rate": pct(win_rate),
        "median_return": round(statistics.median(return_values), 2) if return_values else None,
        "median_gain": round(statistics.median(gain_values), 2) if gain_values else None,
        "mean_gain": round(statistics.mean(gain_values), 2) if gain_values else None,
        "max_return": round(max(return_values), 2) if return_values else None,
        "top1_return_share": pct(share_top_returns(return_values, 1)),
        "top3_return_share": pct(share_top_returns(return_values, 3)),
        "top5_return_share": pct(share_top_returns(return_values, 5)),
        "roi_without_top1": pct(roi_without_top_returns(stake_values, return_values, 1)),
        "roi_without_top3": pct(roi_without_top_returns(stake_values, return_values, 3)),
        "roi_without_top5": pct(roi_without_top_returns(stake_values, return_values, 5)),
        "max_drawdown": max_drawdown(gain_values),
        "longest_losing_streak": longest_losing_streak(rows),
        "positive_return_count": len(positive_returns),
        "strict_real_ticket_allowed": False,
    }

    summary["robustness_status"] = robustness_status(summary)

    return summary


def main():
    rows = read_csv(INPUT_CSV)

    rows_by_segment = defaultdict(list)

    for r in rows:
        segment = r.get("candidate_segment", "").strip() or "ND"
        rows_by_segment[segment].append(r)

    summaries = [
        summarize_segment(segment, segment_rows)
        for segment, segment_rows in sorted(rows_by_segment.items())
    ]

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "candidate_segment",
        "count",
        "core",
        "wins",
        "losses",
        "nd",
        "stake",
        "return",
        "gain_net",
        "roi",
        "ratio_retour",
        "win_rate",
        "median_return",
        "median_gain",
        "mean_gain",
        "max_return",
        "top1_return_share",
        "top3_return_share",
        "top5_return_share",
        "roi_without_top1",
        "roi_without_top3",
        "roi_without_top5",
        "max_drawdown",
        "longest_losing_streak",
        "positive_return_count",
        "robustness_status",
        "strict_real_ticket_allowed",
    ]

    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(summaries)

    status_counts = Counter(s["robustness_status"] for s in summaries)

    audit = {
        "ok": True,
        "version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input_csv": str(INPUT_CSV),
        "output_csv": str(OUT_CSV),
        "rows_input": len(rows),
        "segments": len(summaries),
        "status_counts": dict(status_counts),
        "segment_summary": summaries,
        "strict_real_ticket_allowed": False,
        "policy": "Audit robustesse paper candidat uniquement. Ne valide aucun ticket réel.",
    }

    OUT_JSON.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")

    print("CANDIDATE_ROI_ROBUSTNESS_CREATED")
    print(str(OUT_CSV))
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
