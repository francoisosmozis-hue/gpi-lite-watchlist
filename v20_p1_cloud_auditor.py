"""GPI V20 P1 - auditeur cloud, sans pouvoir de décision.

Ce module ne modifie jamais un ticket et ne peut jamais ouvrir le strict.
Il transforme des artefacts JSON et CSV hétérogènes en observations auditables.
"""

from __future__ import annotations

from collections import defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from typing import Any, Iterable


VERSION = "GPI_V20_P1_2_CLOUD_CSV_AUDITOR_V1"
NO_TICKET = {"", "ND", "NO_BET", "NON", "NONE", "NULL"}
PROOF_FIELDS = (
    "split_manifest_id",
    "leakage_audit_id",
    "replay_run_id",
    "proof_bundle_hash",
)


def _first(doc: dict[str, Any], *paths: str, default: Any = None) -> Any:
    for path in paths:
        value: Any = doc
        for part in path.split("."):
            if not isinstance(value, dict) or part not in value:
                value = None
                break
            value = value[part]
        if value not in (None, ""):
            return value
    return default


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        text = str(value).strip().replace("€", "").replace(",", ".")
        return float(text)
    except (TypeError, ValueError):
        return default


def _time(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _ticket_type(doc: dict[str, Any], ticket: str) -> str:
    raw = str(_first(doc, "ticket_type", "type_ticket", "structure", default="")).upper()
    if "CP3" in raw or len([p for p in ticket.replace("-", " ").split() if p.isdigit()]) == 3:
        return "CP3"
    if "MICRO" in raw:
        return "MICRO_SP"
    if raw:
        return raw
    return "STANDARD"


def normalize_document(doc: dict[str, Any], source_uri: str = "ND") -> dict[str, Any]:
    payload = doc.get("payload") if isinstance(doc.get("payload"), dict) else {}
    report = payload.get("report") if isinstance(payload.get("report"), dict) else {}
    merged = {**doc, **payload, **report}

    ticket = str(_first(
        merged,
        "ticket_strict", "ticket_reel_strict.ticket", "ticket.ticket",
        "ticket_reel", "ticket_exact", "paper_candidate_ticket",
        "selection", "selection_CP3", "selection_CP_SEC", default="NO_BET",
    )).strip()
    played = ticket.upper() not in NO_TICKET
    ticket_type = _ticket_type(merged, ticket)
    expected_stake = 3.0 if ticket_type == "CP3" else 1.0
    stake = _number(_first(
        merged, "stake_eur", "mise", "mise_totale", "mise_paper",
        "paper_stake", "mise_theorique",
    ), expected_stake if played else 0.0) or 0.0
    returned = _number(_first(
        merged, "return_eur", "retour", "retour_paper", "official_return",
        "rapport_total",
    ))
    explicit_net = _number(_first(merged, "net_eur", "gain_net_paper"))
    net = explicit_net if explicit_net is not None else (None if returned is None else returned - stake)

    evidence = str(_first(merged, "evidence_level", default="ND")).upper()
    proof_values = {name: str(_first(merged, name, default="ND")) for name in PROOF_FIELDS}
    proof_complete = evidence == "P1_STATISTICAL_EDGE" and all(
        value.upper() not in NO_TICKET | {"UNASSIGNED"} for value in proof_values.values()
    )

    decision_at = _time(_first(
        merged, "decision_at_utc", "decision_created_at", "generated_at_utc",
        "created_at_utc", "created_at",
    ))
    start_at = _time(_first(merged, "start_at_utc", "depart_at", "course.depart_at"))
    pre_race = bool(decision_at and start_at and decision_at < start_at)
    odds_frozen = _number(_first(merged, "frozen_odds", "odds_at_decision", "cote_figee"))
    probability = _number(_first(merged, "p_model", "probability", "proba_basse"))
    ev = _number(_first(merged, "ev", "expected_value", "edge"))
    if ev is None and odds_frozen is not None and probability is not None:
        ev = odds_frozen * probability - 1.0

    decision_pre_course = str(_first(merged, "decision_pre_course", "decision_mode", default="ND")).upper()
    structural_ex_post = (
        "STRUCTURAL_EX_POST" in decision_pre_course
        or str(_first(merged, "mode_backtest", default="")).upper() == "STRUCTURAL_PAYOFF_AUDIT"
    )
    statistical_edge = bool(proof_complete and pre_race and odds_frozen is not None and ev is not None and ev > 0)

    return {
        "course_uid": str(_first(merged, "course_uid", default="ND")),
        "decision_id": str(_first(merged, "decision_id", default="ND")),
        "source_uri": source_uri,
        "ticket": ticket,
        "ticket_type": ticket_type,
        "played": played,
        "stake_eur": round(stake, 2),
        "expected_stake_eur": expected_stake if played else 0.0,
        "return_eur": returned,
        "net_eur": net,
        "evidence_level": evidence,
        "proof_complete": proof_complete,
        "pre_race_timestamp_valid": pre_race,
        "odds_frozen": odds_frozen,
        "ev": ev,
        "statistical_edge_demonstrated": statistical_edge,
        "structural_ex_post_only": structural_ex_post,
        "stake_policy_ok": (not played) or math.isclose(stake, expected_stake, abs_tol=0.001),
        "false_no_bet_evaluable": (not played) and statistical_edge and returned is not None,
        "false_no_bet": (not played) and statistical_edge and returned is not None and returned > expected_stake,
        "ticket_without_edge": played and not statistical_edge,
        "avoidable_loss": played and net is not None and net < 0 and not statistical_edge,
        "proof_refs": proof_values,
    }


def _roi_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    settled = [r for r in rows if r["played"] and r["net_eur"] is not None and r["stake_eur"] > 0]
    stake = sum(r["stake_eur"] for r in settled)
    net = sum(float(r["net_eur"]) for r in settled)
    roi = net / stake if stake else None
    unit_returns = [float(r["net_eur"]) / r["stake_eur"] for r in settled]
    ci_low = ci_high = None
    if len(unit_returns) >= 2:
        mean = sum(unit_returns) / len(unit_returns)
        variance = sum((x - mean) ** 2 for x in unit_returns) / (len(unit_returns) - 1)
        margin = 1.96 * math.sqrt(variance / len(unit_returns))
        ci_low, ci_high = mean - margin, mean + margin
    n = len(settled)
    status = "INSUFFICIENT_N_LT_30" if n < 30 else "EXPLORATORY_N_LT_100" if n < 100 else "USABLE"
    return {
        "settled_tickets": n,
        "stake_eur": round(stake, 2),
        "net_eur": round(net, 2),
        "roi": None if roi is None else round(roi, 6),
        "roi_ci95_low": None if ci_low is None else round(ci_low, 6),
        "roi_ci95_high": None if ci_high is None else round(ci_high, 6),
        "sample_status": status,
        "positive_edge_statistically_demonstrated": bool(n >= 100 and ci_low is not None and ci_low > 0),
    }


def _deep_merge(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        elif value not in (None, "", "ND"):
            merged[key] = value
    return merged


def build_audit(documents: Iterable[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    # Les décisions et résultats sont souvent archivés dans des objets distincts.
    # Le rapprochement est exclusivement fait par course_uid stable, jamais par nom
    # de cheval ou proximité temporelle.
    grouped: dict[str, tuple[list[str], dict[str, Any]]] = {}
    course_results: dict[str, dict[str, Any]] = {}
    ungrouped: list[tuple[str, dict[str, Any]]] = []
    raw_document_count = 0
    for uri, doc in documents:
        if not isinstance(doc, dict):
            continue
        raw_document_count += 1
        uid = str(_first(doc, "course_uid", "payload.course_uid", default="ND"))
        if uid.upper() in NO_TICKET:
            ungrouped.append((uri, doc))
            continue
        decision_id = str(_first(doc, "decision_id", "payload.decision_id", default="ND"))
        is_result_only = decision_id.upper() in NO_TICKET and any(
            key in doc for key in (
                "arrivee", "official_simple_gagnant_json", "result_status",
                "return_eur", "retour_paper", "gain_net_paper",
            )
        )
        if is_result_only:
            course_results[uid] = _deep_merge(course_results.get(uid, {}), doc)
            continue
        key = f"decision:{decision_id}" if decision_id.upper() not in NO_TICKET else f"course:{uid}"
        uris, current = grouped.get(key, ([], {}))
        grouped[key] = (uris + [uri], _deep_merge(current, doc))

    consolidated = []
    matched_result_courses: set[str] = set()
    for uris, doc in grouped.values():
        uid = str(_first(doc, "course_uid", default="ND"))
        if uid in course_results:
            doc = _deep_merge(doc, course_results[uid])
            matched_result_courses.add(uid)
        consolidated.append((" | ".join(uris), doc))
    for uid, result_doc in course_results.items():
        if uid not in matched_result_courses:
            consolidated.append(("course-result", result_doc))
    consolidated += ungrouped
    rows = [normalize_document(doc, uri) for uri, doc in consolidated]
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_type[row["ticket_type"]].append(row)

    false_evaluable = sum(r["false_no_bet_evaluable"] for r in rows)
    report = {
        "ok": True,
        "version": VERSION,
        "mode": "AUDIT_ONLY",
        "can_open_strict": False,
        "ticket_strict": "NO_BET",
        "bankroll_eur": 1000.0,
        "standard_stake_eur": 1.0,
        "cp3_stake_eur": 3.0,
        "documents_scanned": raw_document_count,
        "courses_consolidated": len(rows),
        "overall": _roi_summary(rows),
        "by_ticket_type": {key: _roi_summary(value) for key, value in sorted(by_type.items())},
        "avoidable_losses": sum(r["avoidable_loss"] for r in rows),
        "tickets_without_demonstrated_edge": sum(r["ticket_without_edge"] for r in rows),
        "stake_policy_violations": sum(not r["stake_policy_ok"] for r in rows),
        "false_no_bet_evaluable": false_evaluable,
        "false_no_bet": sum(r["false_no_bet"] for r in rows),
        "no_bet_not_evaluable_without_p1": sum((not r["played"]) and not r["false_no_bet_evaluable"] for r in rows),
        "structural_ex_post_documents": sum(r["structural_ex_post_only"] for r in rows),
        "data_quality": {
            "settled_played": sum(r["played"] and r["net_eur"] is not None for r in rows),
            "pre_race_timestamp_valid": sum(r["pre_race_timestamp_valid"] for r in rows),
            "independent_p1_proof_complete": sum(r["proof_complete"] for r in rows),
            "frozen_odds_available": sum(r["odds_frozen"] is not None for r in rows),
        },
        "rows": rows,
    }
    canonical = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    report["audit_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    report["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
    return report


def _csv_rows(text: str) -> list[dict[str, Any]]:
    return [dict(row) for row in csv.DictReader(io.StringIO(text.lstrip("\ufeff")))]


def run_cloud_audit(max_documents: int = 5000) -> dict[str, Any]:
    from google.cloud import storage

    bucket_name = (
        os.getenv("GPI_BUCKET_NAME") or os.getenv("BUCKET_NAME")
        or os.getenv("GPI_BUCKET") or "gpi-lite-watchlist-data"
    )
    prefixes = [p.strip() for p in os.getenv(
        "GPI_V20_P1_HISTORY_PREFIXES",
        "gpi-lite/outputs/,gpi-lite/sources/results/,gpi-lite/history/h5/,gpi-lite/history/results/,gpi-lite/audits/paper/",
    ).split(",") if p.strip()]
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    documents: list[tuple[str, dict[str, Any]]] = []
    read_errors: list[str] = []
    transient_not_found: list[str] = []
    seen: set[str] = set()
    source_counts: dict[str, int] = defaultdict(int)
    csv_suffixes = (
        "decision_engine_outputs.csv",
        "paper_trading_results.csv",
        "result_input.csv",
    )

    def load_blob(blob: Any) -> None:
        name = str(blob.name)
        is_json = name.endswith(".json")
        uri = f"gs://{bucket_name}/{name}"
        text = blob.download_as_text(encoding="utf-8")
        loaded = [json.loads(text)] if is_json else _csv_rows(text)
        for row in loaded:
            if len(documents) >= max_documents:
                break
            documents.append((uri, row))
            source_counts["json" if is_json else next(
                suffix for suffix in csv_suffixes if name.endswith(suffix)
            )] += 1

    canonical_csv_objects = (
        "gpi-lite/outputs/paper_trading_results.csv",
        "gpi-lite/outputs/result_input.csv",
        "gpi-lite/sources/results/result_input.csv",
        "gpi-lite/outputs/decision_engine_outputs.csv",
    )
    for name in canonical_csv_objects:
        if len(documents) >= max_documents:
            break
        blob = bucket.blob(name)
        try:
            if not blob.exists():
                continue
            seen.add(name)
            load_blob(blob)
        except Exception as exc:
            read_errors.append(f"{name}:{type(exc).__name__}")

    for prefix in prefixes:
        for blob in bucket.list_blobs(prefix=prefix):
            if len(documents) >= max_documents:
                break
            name = str(blob.name)
            # outputs/ contient de nombreux JSON d'exécution volatils sans
            # contrat historique. P1.2 n'y lit que les CSV auditables.
            is_json = name.endswith(".json") and not prefix.startswith("gpi-lite/outputs/")
            is_audit_csv = name.endswith(csv_suffixes)
            if (not is_json and not is_audit_csv) or name in seen:
                continue
            seen.add(blob.name)
            try:
                load_blob(blob)
            except Exception as exc:
                error = f"{blob.name}:{type(exc).__name__}"
                if type(exc).__name__ == "NotFound":
                    transient_not_found.append(error)
                else:
                    read_errors.append(error)
        if len(documents) >= max_documents:
            break
    report = build_audit(documents)
    report["cloud"] = {
        "bucket": bucket_name,
        "prefixes": prefixes,
        "max_documents": max_documents,
        "read_error_count": len(read_errors),
        "read_errors_sample": read_errors[:20],
        "transient_not_found_count": len(transient_not_found),
        "transient_not_found_sample": transient_not_found[:20],
        "source_row_counts": dict(source_counts),
    }
    return report
