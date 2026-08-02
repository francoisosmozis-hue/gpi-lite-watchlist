"""GPI V20 P1.5, preuve prospective TEST_LIVE et règlement post-course.

Ce module n'a aucun pouvoir d'ouverture du strict. Il sépare physiquement la
décision pré-course du résultat ajouté après course.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import os
import re
from typing import Any, Iterable
from zoneinfo import ZoneInfo


VERSION = "GPI_V20_P1_5_PROSPECTIVE_TRACKER_V1"
MODE = "TEST_LIVE_OBSERVATION_ONLY"
NO_VALUE = {"", "ND", "NO_BET", "NON", "NONE", "NULL", "UNASSIGNED"}
CAPTURE_PREFIX = "gpi-lite/v20/p1-5/captures/"
SETTLEMENT_PREFIX = "gpi-lite/v20/p1-5/settlements/"
LEDGER_BLOB = "gpi-lite/v20/p1-5/tracker/test_live_ledger.csv"
STATUS_BLOB = "gpi-lite/v20/p1-5/tracker/latest_status.json"
DECISIONS_BLOBS = (
    "gpi-lite/outputs/decision_engine_outputs.csv",
    "gpi-lite/outputs/latest_decision_engine_outputs.csv",
)
SCHEDULE_BLOBS = (
    "gpi-lite/outputs/h5_auto_plan.csv",
    "gpi-lite/outputs/latest_h5_auto_plan.csv",
)
RESULT_BLOBS = (
    "gpi-lite/outputs/result_input.csv",
    "gpi-lite/sources/results/result_input.csv",
)

LEDGER_FIELDS = [
    "decision_id", "course_uid", "course_label", "decision_at_utc",
    "start_at_utc", "captured_at_utc", "decision_before_race_oui_non",
    "capture_valid_oui_non", "capture_errors", "input_hash",
    "engine_version", "ruleset_version", "split", "calibration_version",
    "leakage_check_ok", "split_manifest_id", "leakage_audit_id",
    "replay_run_id", "proof_bundle_hash", "replay_match_oui_non",
    "qualite", "score_jouabilite", "verdict", "paper_structure",
    "paper_ticket", "horses", "stake_eur", "strict_ticket",
    "strict_allowed_oui_non", "strict_block_reasons", "segment_roi",
    "edge_status", "reason_codes", "kill_switch_codes", "proxy_status",
    "segment_id", "sample_n", "confidence", "fallback_level", "C_prudent",
    "R_prudent", "erreur_proxy", "segment_degrade_oui_non",
    "CP_SEC_selection", "selection_CP3", "CP_SEC_domine_oui_non",
    "frozen_odds", "p_model", "ev", "proof_complete_oui_non",
    "statistical_inputs_complete_oui_non", "settlement_contract",
    "settlement_status", "result_status", "official_reports_parse_status",
    "return_eur", "net_eur", "roi", "settled_at_utc", "capture_hash",
]


def _text(row: dict[str, Any], *keys: str, default: str = "ND") -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return default


def _number(value: Any) -> float | None:
    try:
        return float(str(value).strip().replace("€", "").replace(",", "."))
    except (TypeError, ValueError):
        return None


def _bool_yes(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "oui", "yes", "ok"}


def _rows(text: str) -> list[dict[str, str]]:
    return [dict(row) for row in csv.DictReader(io.StringIO(text.lstrip("\ufeff")))]


def _course_date(course_uid: str) -> str | None:
    match = re.match(r"(\d{2})_(\d{2})_(\d{4})", course_uid)
    return f"{match.group(3)}-{match.group(2)}-{match.group(1)}" if match else None


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)
    except ValueError:
        return None


def _start_at(course_uid: str, schedule: dict[str, Any]) -> datetime | None:
    parsed = _parse_iso(_text(schedule, "start_at_utc", "depart_at", default=""))
    if parsed:
        return parsed
    date = _course_date(course_uid)
    hour = _text(schedule, "heure_depart", "heure", "horaire", default="")
    if not date or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", hour):
        return None
    local = datetime.fromisoformat(f"{date}T{hour}:00").replace(
        tzinfo=ZoneInfo("Europe/Paris")
    )
    return local.astimezone(timezone.utc)


def _decision_at(decision: dict[str, Any]) -> datetime | None:
    return _parse_iso(
        _text(
            decision,
            "created_at",
            "decision_at_utc",
            "generated_at_utc",
            default="",
        )
    )


def _candidate(decision: dict[str, Any]) -> tuple[str, str]:
    ticket = _text(
        decision,
        "paper_candidate_ticket",
        "paper_selection",
        "ticket_exact",
        default="NO_BET",
    )
    structure = _text(
        decision,
        "paper_candidate_type",
        "paper_structure",
        "structure_retenue",
        default="NO_BET",
    ).upper()
    if ticket.upper() in NO_VALUE:
        return "NO_BET", "NO_BET"
    return ticket, structure


def _ticket_numbers(ticket: str) -> list[str]:
    return re.findall(r"\d+", ticket)


def _canonical_pair(first: str, second: str) -> str:
    return "-".join(sorted((str(first), str(second)), key=int))


def _proof_complete(decision: dict[str, Any]) -> bool:
    required = (
        _text(decision, "input_hash"),
        _text(decision, "engine_version"),
        _text(decision, "ruleset_version"),
        _text(decision, "split"),
        _text(decision, "calibration_version"),
        _text(decision, "split_manifest_id"),
        _text(decision, "leakage_audit_id"),
        _text(decision, "replay_run_id"),
        _text(decision, "proof_bundle_hash"),
    )
    return (
        all(value.upper() not in NO_VALUE for value in required)
        and _bool_yes(decision.get("leakage_check_ok"))
        and _text(decision, "split").upper() == "TEST_LIVE"
    )


def build_capture(
    decision: dict[str, Any],
    schedule: dict[str, Any],
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    uid = _text(decision, "course_uid")
    decision_id = _text(decision, "decision_id")
    start = _start_at(uid, schedule)
    decided = _decision_at(decision)
    ticket, structure = _candidate(decision)
    horses = _ticket_numbers(ticket)
    cp3 = structure == "CP3" or ("CP3" in structure and len(horses) == 3)
    stake = 0.0 if ticket == "NO_BET" else (3.0 if cp3 else 1.0)
    frozen_odds = _number(
        decision.get("frozen_odds")
        or decision.get("odds_at_decision")
        or decision.get("sp_min")
    )
    probability = _number(
        decision.get("p_model")
        or decision.get("probability")
        or decision.get("proba_basse")
    )
    ev = _number(decision.get("ev") or decision.get("expected_value"))
    if ev is None and frozen_odds is not None and probability is not None:
        ev = frozen_odds * probability - 1.0

    errors: list[str] = []
    if uid.upper() in NO_VALUE:
        errors.append("COURSE_UID_MISSING")
    if decision_id.upper() in NO_VALUE:
        errors.append("DECISION_ID_MISSING")
    if start is None:
        errors.append("COURSE_TIME_MISSING")
    if decided is None:
        errors.append("DECISION_TIMESTAMP_MISSING")
    elif start is not None and decided >= start:
        errors.append("DECISION_NOT_PRE_RACE")
    if start is not None and now >= start:
        errors.append("CAPTURE_NOT_PRE_RACE")
    if ticket != "NO_BET" and not horses:
        errors.append("HORSE_SELECTION_INVALID")
    if structure == "CP_SEC" and len(horses) != 2:
        errors.append("CP_SEC_SELECTION_INVALID")
    if cp3 and len(horses) != 3:
        errors.append("CP3_SELECTION_INVALID")

    proof_complete = _proof_complete(decision)
    statistical_complete = bool(
        proof_complete
        and (
            ticket == "NO_BET"
            or (probability is not None and ev is not None)
        )
    )
    if ticket == "NO_BET":
        settlement_contract = "NO_BET"
    elif structure == "CP_SEC" and len(horses) == 2:
        settlement_contract = "CP_SEC_JUMELE_PLACE"
    elif cp3 and len(horses) == 3:
        settlement_contract = "CP3_JUMELE_PLACE_3_PAIRS"
    else:
        settlement_contract = "UNSUPPORTED"

    payload = {
        "tracker_version": VERSION,
        "mode": MODE,
        "decision_id": decision_id,
        "course_uid": uid,
        "course_label": _text(schedule, "course_label", default=uid),
        "decision_at_utc": None if decided is None else decided.isoformat(),
        "start_at_utc": None if start is None else start.isoformat(),
        "captured_at_utc": now.isoformat(),
        "decision_before_race_oui_non": (
            "oui" if decided is not None and start is not None and decided < start else "non"
        ),
        "capture_valid_oui_non": "non" if errors else "oui",
        "capture_errors": errors,
        "input_hash": _text(decision, "input_hash"),
        "engine_version": _text(decision, "engine_version"),
        "ruleset_version": _text(decision, "ruleset_version"),
        "split": _text(decision, "split"),
        "calibration_version": _text(decision, "calibration_version"),
        "leakage_check_ok": _text(decision, "leakage_check_ok"),
        "split_manifest_id": _text(decision, "split_manifest_id"),
        "leakage_audit_id": _text(decision, "leakage_audit_id"),
        "replay_run_id": _text(decision, "replay_run_id"),
        "proof_bundle_hash": _text(decision, "proof_bundle_hash"),
        "replay_match_oui_non": _text(decision, "replay_match_oui_non"),
        "qualite": _text(decision, "qualite"),
        "score_jouabilite": _text(decision, "score_jouabilite"),
        "verdict": _text(decision, "verdict", default="NO_BET"),
        "paper_structure": structure,
        "paper_ticket": ticket,
        "horses": horses,
        "stake_eur": stake,
        "strict_ticket": "NO_BET",
        "strict_allowed_oui_non": "non",
        "strict_block_reasons": _text(
            decision,
            "strict_block_reasons",
            "decision_block_reason",
            default="P1_5_OBSERVATION_ONLY",
        ),
        "segment_roi": _text(decision, "segment_ROI", "segment_roi"),
        "edge_status": _text(decision, "edge_status"),
        "reason_codes": _text(decision, "reason_codes"),
        "kill_switch_codes": _text(decision, "kill_switch_codes"),
        "proxy_status": _text(decision, "proxy_status"),
        "segment_id": _text(decision, "segment_id"),
        "sample_n": _text(decision, "sample_n"),
        "confidence": _text(decision, "confidence"),
        "fallback_level": _text(decision, "fallback_level"),
        "C_prudent": _text(decision, "C_prudent"),
        "R_prudent": _text(decision, "R_prudent"),
        "erreur_proxy": _text(decision, "erreur_proxy"),
        "segment_degrade_oui_non": _text(decision, "segment_degrade_oui_non"),
        "CP_SEC_selection": _text(
            decision, "CP_SEC_selection", "selection_CP_SEC", default=""
        ),
        "selection_CP3": _text(decision, "selection_CP3", default=""),
        "CP_SEC_domine_oui_non": _text(decision, "CP_SEC_domine_oui_non"),
        "frozen_odds": frozen_odds,
        "p_model": probability,
        "ev": ev,
        "proof_complete_oui_non": "oui" if proof_complete else "non",
        "statistical_inputs_complete_oui_non": (
            "oui" if statistical_complete else "non"
        ),
        "settlement_contract": settlement_contract,
        "can_open_strict": False,
        "ticket_strict": "NO_BET",
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    payload["capture_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload


def _reports(result: dict[str, Any], field: str) -> dict[str, float]:
    raw = result.get(field) or "{}"
    try:
        source = json.loads(raw) if isinstance(raw, str) else dict(raw)
        return {str(key): float(value) for key, value in source.items()}
    except (TypeError, ValueError):
        return {}


def settle_capture(
    capture: dict[str, Any],
    result: dict[str, Any],
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    contract = str(capture.get("settlement_contract", "UNSUPPORTED"))
    horses = [str(value) for value in capture.get("horses", [])]
    stake = float(capture.get("stake_eur", 0) or 0)
    result_status = _text(result, "result_status")
    parse_status = _text(result, "official_reports_parse_status")
    official_ok = result_status.upper() == "OK"
    returned: float | None = None
    status = "PENDING_RESULT"

    if contract == "NO_BET" and official_ok:
        returned = 0.0
        status = "SETTLED_NO_BET"
    elif not official_ok:
        status = "PENDING_RESULT_NOT_OK"
    elif contract == "CP_SEC_JUMELE_PLACE" and len(horses) == 2:
        reports = {
            _canonical_pair(*str(key).split("-", 1)): value
            for key, value in _reports(result, "official_jumele_place_json").items()
            if "-" in str(key)
        }
        returned = reports.get(_canonical_pair(horses[0], horses[1]), 0.0)
        status = "SETTLED"
    elif contract == "CP3_JUMELE_PLACE_3_PAIRS" and len(horses) == 3:
        reports = {
            _canonical_pair(*str(key).split("-", 1)): value
            for key, value in _reports(result, "official_jumele_place_json").items()
            if "-" in str(key)
        }
        pairs = (
            (horses[0], horses[1]),
            (horses[0], horses[2]),
            (horses[1], horses[2]),
        )
        returned = sum(reports.get(_canonical_pair(*pair), 0.0) for pair in pairs)
        status = "SETTLED"
    elif contract == "UNSUPPORTED":
        status = "PENDING_UNSUPPORTED_CONTRACT"

    net = None if returned is None else round(returned - stake, 2)
    roi = None if net is None or stake <= 0 else net / stake
    settled_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {
        "tracker_version": VERSION,
        "mode": MODE,
        "decision_id": capture.get("decision_id", "ND"),
        "course_uid": capture.get("course_uid", "ND"),
        "capture_hash": capture.get("capture_hash", "ND"),
        "settlement_status": status,
        "settlement_contract": contract,
        "result_status": result_status,
        "official_reports_parse_status": parse_status,
        "stake_eur": stake,
        "return_eur": returned,
        "net_eur": net,
        "roi": roi,
        "settled_at_utc": settled_at.isoformat(),
        "can_open_strict": False,
        "ticket_strict": "NO_BET",
    }


def _bucket():
    from google.cloud import storage

    name = (
        os.getenv("GPI_BUCKET_NAME")
        or os.getenv("BUCKET_NAME")
        or "gpi-lite-watchlist-data"
    )
    return storage.Client().bucket(name)


def _csv_blob(bucket: Any, names: Iterable[str]) -> list[dict[str, str]]:
    for name in names:
        blob = bucket.blob(name)
        if blob.exists():
            return _rows(blob.download_as_text(encoding="utf-8"))
    return []


def _upload_json_once(bucket: Any, name: str, payload: dict[str, Any]) -> bool:
    try:
        bucket.blob(name).upload_from_string(
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            content_type="application/json",
            if_generation_match=0,
        )
        return True
    except Exception as exc:
        if type(exc).__name__ == "PreconditionFailed":
            return False
        raise


def _load_json_blobs(bucket: Any, prefix: str) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for blob in bucket.list_blobs(prefix=prefix):
        payload = json.loads(blob.download_as_text(encoding="utf-8"))
        decision_id = str(payload.get("decision_id", "ND"))
        records[decision_id] = payload
    return records


def refresh_ledger(bucket: Any | None = None) -> dict[str, Any]:
    bucket = bucket or _bucket()
    captures = _load_json_blobs(bucket, CAPTURE_PREFIX)
    settlements = _load_json_blobs(bucket, SETTLEMENT_PREFIX)
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=LEDGER_FIELDS, extrasaction="ignore")
    writer.writeheader()
    ticket_rows = settled_ticket_rows = no_bet_rows = 0
    total_stake = total_return = 0.0

    for decision_id in sorted(
        captures, key=lambda key: str(captures[key].get("captured_at_utc", ""))
    ):
        capture = dict(captures[decision_id])
        settlement = settlements.get(decision_id, {})
        row = {**capture, **settlement}
        row["capture_errors"] = "|".join(capture.get("capture_errors", []))
        row["horses"] = "-".join(str(value) for value in capture.get("horses", []))
        writer.writerow({field: row.get(field, "") for field in LEDGER_FIELDS})
        if capture.get("paper_ticket") == "NO_BET":
            no_bet_rows += 1
        else:
            ticket_rows += 1
            total_stake += float(capture.get("stake_eur", 0) or 0)
            if str(settlement.get("settlement_status", "")).startswith("SETTLED"):
                settled_ticket_rows += 1
                total_return += float(settlement.get("return_eur", 0) or 0)

    bucket.blob(LEDGER_BLOB).upload_from_string(
        output.getvalue(), content_type="text/csv; charset=utf-8"
    )
    net = total_return - total_stake
    status = {
        "ok": True,
        "tracker_version": VERSION,
        "mode": MODE,
        "captures": len(captures),
        "settlements": len(settlements),
        "ticket_rows": ticket_rows,
        "settled_ticket_rows": settled_ticket_rows,
        "no_bet_rows": no_bet_rows,
        "total_stake_eur": round(total_stake, 2),
        "total_return_eur": round(total_return, 2),
        "net_eur": round(net, 2),
        "roi": None if total_stake <= 0 else net / total_stake,
        "can_open_strict": False,
        "ticket_strict": "NO_BET",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    bucket.blob(STATUS_BLOB).upload_from_string(
        json.dumps(status, ensure_ascii=False, sort_keys=True),
        content_type="application/json",
    )
    return status


def run_capture_latest() -> dict[str, Any]:
    bucket = _bucket()
    decisions = _csv_blob(bucket, DECISIONS_BLOBS)
    schedules = _csv_blob(bucket, SCHEDULE_BLOBS)
    schedule_by_uid = {_text(row, "course_uid"): row for row in schedules}
    written = existing = rejected = 0
    details = []

    for decision in decisions:
        uid = _text(decision, "course_uid")
        capture = build_capture(decision, schedule_by_uid.get(uid, {}))
        decision_id = str(capture["decision_id"])
        if capture["capture_valid_oui_non"] != "oui":
            rejected += 1
            details.append(
                {
                    "decision_id": decision_id,
                    "written": False,
                    "errors": capture["capture_errors"],
                }
            )
            continue
        date = _course_date(uid) or "date-nd"
        name = f"{CAPTURE_PREFIX}{date}/{decision_id}.json"
        created = _upload_json_once(bucket, name, capture)
        written += int(created)
        existing += int(not created)
        details.append(
            {"decision_id": decision_id, "written": created, "errors": []}
        )

    ledger = refresh_ledger(bucket)
    return {
        "ok": True,
        "tracker_version": VERSION,
        "mode": MODE,
        "decisions_seen": len(decisions),
        "written": written,
        "already_captured": existing,
        "rejected": rejected,
        "captures": details,
        "ledger": ledger,
        "can_open_strict": False,
        "ticket_strict": "NO_BET",
    }


def run_settle_due(max_captures: int = 1000) -> dict[str, Any]:
    bucket = _bucket()
    results = _csv_blob(bucket, RESULT_BLOBS)
    result_by_uid = {_text(row, "course_uid"): row for row in results}
    settled = existing = pending = 0

    for index, blob in enumerate(bucket.list_blobs(prefix=CAPTURE_PREFIX)):
        if index >= max_captures:
            break
        capture = json.loads(blob.download_as_text(encoding="utf-8"))
        uid = str(capture.get("course_uid", "ND"))
        result = result_by_uid.get(uid)
        if result is None:
            pending += 1
            continue
        settlement = settle_capture(capture, result)
        if not str(settlement["settlement_status"]).startswith("SETTLED"):
            pending += 1
            continue
        name = f"{SETTLEMENT_PREFIX}{capture.get('decision_id', 'ND')}.json"
        created = _upload_json_once(bucket, name, settlement)
        settled += int(created)
        existing += int(not created)

    ledger = refresh_ledger(bucket)
    return {
        "ok": True,
        "tracker_version": VERSION,
        "mode": MODE,
        "settled": settled,
        "already_settled": existing,
        "pending": pending,
        "ledger": ledger,
        "can_open_strict": False,
        "ticket_strict": "NO_BET",
    }


def run_post_h5_capture(
    *, h5_run_ok: bool, capture_count: int, briefs_created: int
) -> dict[str, Any]:
    productive = int(capture_count or 0) > 0 or int(briefs_created or 0) > 0
    base = {"can_open_strict": False, "ticket_strict": "NO_BET"}
    if not h5_run_ok:
        return {**base, "ok": True, "attempted": False, "reason": "H5_RUN_NOT_OK"}
    if not productive:
        return {
            **base,
            "ok": True,
            "attempted": False,
            "reason": "H5_NO_NEW_ARTIFACT",
        }
    try:
        result = run_capture_latest()
        return {
            **base,
            "ok": bool(result.get("ok")),
            "attempted": True,
            "reason": "P1_5_CAPTURE_CALLED_AFTER_H5",
            "result": result,
        }
    except Exception as exc:
        return {
            **base,
            "ok": False,
            "attempted": True,
            "reason": "P1_5_CAPTURE_ERROR_NON_BLOCKING",
            "error": f"{type(exc).__name__}:{exc}",
        }


def run_post_results_settlement(*, results_pipeline_ok: bool) -> dict[str, Any]:
    base = {"can_open_strict": False, "ticket_strict": "NO_BET"}
    if not results_pipeline_ok:
        return {
            **base,
            "ok": True,
            "attempted": False,
            "reason": "RESULTS_PIPELINE_NOT_OK",
        }
    try:
        result = run_settle_due(max_captures=5000)
        return {
            **base,
            "ok": bool(result.get("ok")),
            "attempted": True,
            "reason": "P1_5_SETTLEMENT_CALLED_AFTER_RESULTS",
            "result": result,
        }
    except Exception as exc:
        return {
            **base,
            "ok": False,
            "attempted": True,
            "reason": "P1_5_SETTLEMENT_ERROR_NON_BLOCKING",
            "error": f"{type(exc).__name__}:{exc}",
        }


def get_status() -> dict[str, Any]:
    bucket = _bucket()
    blob = bucket.blob(STATUS_BLOB)
    if blob.exists():
        status = json.loads(blob.download_as_text(encoding="utf-8"))
        status["can_open_strict"] = False
        status["ticket_strict"] = "NO_BET"
        return status
    return refresh_ledger(bucket)
