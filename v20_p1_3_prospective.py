"""GPI V20 P1.3 - registre prospectif Cloud, sans pouvoir de jeu."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import os
import re
from typing import Any
from zoneinfo import ZoneInfo


VERSION = "GPI_V20_P1_3_PROSPECTIVE_OBSERVER_V1"
NO_TICKET = {"", "ND", "NO_BET", "NON", "NONE", "NULL"}
CAPTURE_PREFIX = "gpi-lite/v20/p1-3/prospective/"
SETTLEMENT_PREFIX = "gpi-lite/v20/p1-3/settlements/"


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


def _rows(text: str) -> list[dict[str, str]]:
    return [dict(row) for row in csv.DictReader(io.StringIO(text.lstrip("\ufeff")))]


def _course_date(course_uid: str) -> str | None:
    match = re.match(r"(\d{2})_(\d{2})_(\d{4})", course_uid)
    return f"{match.group(3)}-{match.group(2)}-{match.group(1)}" if match else None


def _start_at(course_uid: str, schedule: dict[str, Any]) -> datetime | None:
    raw_iso = _text(schedule, "start_at_utc", "depart_at", default="")
    if raw_iso:
        try:
            parsed = datetime.fromisoformat(raw_iso.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)
        except ValueError:
            return None
    date = _course_date(course_uid)
    hour = _text(schedule, "heure_depart", "heure", "horaire", default="")
    if not date or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", hour):
        return None
    local = datetime.fromisoformat(f"{date}T{hour}:00").replace(tzinfo=ZoneInfo("Europe/Paris"))
    return local.astimezone(timezone.utc)


def _decision_at(decision: dict[str, Any]) -> datetime | None:
    raw = _text(decision, "created_at", "decision_at_utc", "generated_at_utc", default="")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)
    except ValueError:
        return None


def _candidate(decision: dict[str, Any]) -> tuple[str, str]:
    ticket = _text(
        decision, "paper_candidate_ticket", "paper_selection",
        "selection_CP3", "selection_CP_SEC", default="NO_BET",
    )
    structure = _text(
        decision, "paper_candidate_type", "paper_structure", "structure_retenue",
        default="NO_BET",
    ).upper()
    return ticket, structure


def _ticket_numbers(ticket: str) -> list[str]:
    return re.findall(r"\d+", ticket)


def build_capture(
    decision: dict[str, Any], schedule: dict[str, Any], now_utc: datetime | None = None,
) -> dict[str, Any]:
    now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    uid = _text(decision, "course_uid")
    decision_id = _text(decision, "decision_id")
    start = _start_at(uid, schedule)
    decided = _decision_at(decision)
    ticket, structure = _candidate(decision)
    numbers = _ticket_numbers(ticket)
    cp3 = "CP3" in structure or len(numbers) == 3
    stake = 0.0 if ticket.upper() in NO_TICKET else (3.0 if cp3 else 1.0)
    frozen_odds = _number(decision.get("frozen_odds") or decision.get("odds_at_decision"))
    probability = _number(decision.get("p_model") or decision.get("probability"))
    ev = _number(decision.get("ev") or decision.get("expected_value"))
    if ev is None and frozen_odds is not None and probability is not None:
        ev = frozen_odds * probability - 1.0

    errors: list[str] = []
    if uid.upper() in NO_TICKET:
        errors.append("COURSE_UID_MISSING")
    if decision_id.upper() in NO_TICKET:
        errors.append("DECISION_ID_MISSING")
    if start is None:
        errors.append("COURSE_TIME_MISSING")
    if decided is None:
        errors.append("DECISION_TIMESTAMP_MISSING")
    elif start is not None and decided >= start:
        errors.append("DECISION_NOT_PRE_RACE")
    if start is not None and now >= start:
        errors.append("CAPTURE_NOT_PRE_RACE")
    if ticket.upper() not in NO_TICKET and not numbers:
        errors.append("HORSE_SELECTION_INVALID")

    proof_refs = {
        key: _text(decision, key)
        for key in ("split_manifest_id", "leakage_audit_id", "replay_run_id", "proof_bundle_hash")
    }
    independent_proof = all(value.upper() not in NO_TICKET | {"UNASSIGNED"} for value in proof_refs.values())
    statistical_complete = bool(independent_proof and frozen_odds is not None and probability is not None and ev is not None)
    settlement_contract = (
        "CP3_JUMELE_PLACE_3_PAIRS" if cp3 and len(numbers) == 3
        else "CP_SEC_JUMELE_PLACE" if len(numbers) == 2
        else "UNSUPPORTED"
    )
    payload = {
        "observer_version": VERSION,
        "mode": "PROSPECTIVE_OBSERVATION_ONLY",
        "can_open_strict": False,
        "ticket_strict": "NO_BET",
        "decision_id": decision_id,
        "course_uid": uid,
        "course_label": _text(schedule, "course_label", default=uid),
        "heure_depart": _text(schedule, "heure_depart", "heure", "horaire"),
        "start_at_utc": None if start is None else start.isoformat(),
        "decision_at_utc": None if decided is None else decided.isoformat(),
        "captured_at_utc": now.isoformat(),
        "paper_ticket": ticket,
        "paper_structure": structure,
        "horses": numbers,
        "stake_eur": stake,
        "bankroll_eur": 1000.0,
        "standard_stake_eur": 1.0,
        "cp3_stake_eur": 3.0,
        "frozen_odds": frozen_odds,
        "p_model": probability,
        "ev": ev,
        "proof_refs": proof_refs,
        "independent_proof_complete": independent_proof,
        "statistical_inputs_complete": statistical_complete,
        "settlement_contract": settlement_contract,
        "capture_valid": not errors,
        "capture_errors": errors,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["capture_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload


def settle_capture(capture: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    contract = str(capture.get("settlement_contract", "UNSUPPORTED"))
    horses = [str(x) for x in capture.get("horses", [])]
    reports_raw = result.get("official_jumele_place_json") or "{}"
    try:
        reports = json.loads(reports_raw) if isinstance(reports_raw, str) else dict(reports_raw)
    except (ValueError, TypeError):
        reports = {}
    normalized = {"-".join(sorted(str(k).split("-"), key=int)): float(v) for k, v in reports.items()}
    stake = float(capture.get("stake_eur", 0) or 0)
    returned: float | None = None
    if contract == "CP_SEC_JUMELE_PLACE" and len(horses) == 2:
        returned = normalized.get("-".join(sorted(horses, key=int)), 0.0)
    elif contract == "CP3_JUMELE_PLACE_3_PAIRS" and len(horses) == 3:
        pairs = [(horses[0], horses[1]), (horses[0], horses[2]), (horses[1], horses[2])]
        returned = sum(normalized.get("-".join(sorted(pair, key=int)), 0.0) for pair in pairs)
    status = "SETTLED" if returned is not None else "PENDING_UNSUPPORTED_CONTRACT"
    return {
        "observer_version": VERSION,
        "decision_id": capture.get("decision_id", "ND"),
        "course_uid": capture.get("course_uid", "ND"),
        "settlement_status": status,
        "settlement_contract": contract,
        "stake_eur": stake,
        "return_eur": returned,
        "net_eur": None if returned is None else round(returned - stake, 2),
        "result_status": result.get("result_status", "ND"),
        "settled_at_utc": datetime.now(timezone.utc).isoformat(),
        "can_open_strict": False,
        "ticket_strict": "NO_BET",
    }


def _bucket():
    from google.cloud import storage
    name = os.getenv("GPI_BUCKET_NAME") or os.getenv("BUCKET_NAME") or "gpi-lite-watchlist-data"
    return storage.Client().bucket(name)


def _csv_blob(bucket: Any, name: str) -> list[dict[str, str]]:
    blob = bucket.blob(name)
    return _rows(blob.download_as_text(encoding="utf-8")) if blob.exists() else []


def run_capture_latest() -> dict[str, Any]:
    bucket = _bucket()
    decisions = _csv_blob(bucket, "gpi-lite/outputs/decision_engine_outputs.csv")
    schedules = _csv_blob(bucket, "gpi-lite/outputs/h5_auto_plan.csv")
    schedule_by_uid = {_text(row, "course_uid"): row for row in schedules}
    captures = []
    for decision in decisions:
        uid = _text(decision, "course_uid")
        capture = build_capture(decision, schedule_by_uid.get(uid, {}))
        if not capture["capture_valid"]:
            captures.append({"decision_id": capture["decision_id"], "written": False, "errors": capture["capture_errors"]})
            continue
        date = _course_date(uid) or "date-nd"
        name = f"{CAPTURE_PREFIX}{date}/{capture['decision_id']}.json"
        try:
            bucket.blob(name).upload_from_string(
                json.dumps(capture, ensure_ascii=False, sort_keys=True),
                content_type="application/json", if_generation_match=0,
            )
            written = True
        except Exception as exc:
            if type(exc).__name__ == "PreconditionFailed":
                written = False
            else:
                raise
        captures.append({"decision_id": capture["decision_id"], "written": written, "errors": []})
    return {
        "ok": True, "version": VERSION, "mode": "PROSPECTIVE_OBSERVATION_ONLY",
        "can_open_strict": False, "ticket_strict": "NO_BET",
        "decisions_seen": len(decisions), "captures": captures,
    }


def run_settle_due(max_captures: int = 1000) -> dict[str, Any]:
    bucket = _bucket()
    results = _csv_blob(bucket, "gpi-lite/outputs/result_input.csv")
    result_by_uid = {_text(row, "course_uid"): row for row in results}
    settled = pending = existing = 0
    for blob in bucket.list_blobs(prefix=CAPTURE_PREFIX, max_results=max_captures):
        capture = json.loads(blob.download_as_text(encoding="utf-8"))
        uid = str(capture.get("course_uid", "ND"))
        if uid not in result_by_uid:
            pending += 1
            continue
        name = f"{SETTLEMENT_PREFIX}{capture.get('decision_id', 'ND')}.json"
        settlement = settle_capture(capture, result_by_uid[uid])
        try:
            bucket.blob(name).upload_from_string(
                json.dumps(settlement, ensure_ascii=False, sort_keys=True),
                content_type="application/json", if_generation_match=0,
            )
            settled += 1
        except Exception as exc:
            if type(exc).__name__ == "PreconditionFailed":
                existing += 1
            else:
                raise
    return {
        "ok": True, "version": VERSION, "mode": "PROSPECTIVE_OBSERVATION_ONLY",
        "settled": settled, "already_settled": existing, "pending": pending,
        "can_open_strict": False, "ticket_strict": "NO_BET",
    }


def run_post_h5_capture(
    *, h5_run_ok: bool, capture_count: int, briefs_created: int,
) -> dict[str, Any]:
    """Capture once after a productive H5 cycle, without affecting H5."""
    productive = int(capture_count or 0) > 0 or int(briefs_created or 0) > 0
    base = {"can_open_strict": False, "ticket_strict": "NO_BET"}
    if not h5_run_ok:
        return {**base, "ok": True, "attempted": False, "reason": "H5_RUN_NOT_OK"}
    if not productive:
        return {**base, "ok": True, "attempted": False, "reason": "H5_NO_NEW_ARTIFACT"}
    try:
        result = run_capture_latest()
        return {
            **base, "ok": bool(result.get("ok")), "attempted": True,
            "reason": "CAPTURE_CALLED_AFTER_H5", "result": result,
        }
    except Exception as exc:
        return {
            **base, "ok": False, "attempted": True,
            "reason": "CAPTURE_ERROR_NON_BLOCKING",
            "error": f"{type(exc).__name__}:{exc}",
        }


def get_status() -> dict[str, Any]:
    bucket = _bucket()
    captures = sum(1 for _ in bucket.list_blobs(prefix=CAPTURE_PREFIX))
    settlements = sum(1 for _ in bucket.list_blobs(prefix=SETTLEMENT_PREFIX))
    return {
        "ok": True, "version": VERSION, "mode": "PROSPECTIVE_OBSERVATION_ONLY",
        "captures": captures, "settlements": settlements,
        "can_open_strict": False, "ticket_strict": "NO_BET",
        "bankroll_eur": 1000.0, "standard_stake_eur": 1.0, "cp3_stake_eur": 3.0,
    }
