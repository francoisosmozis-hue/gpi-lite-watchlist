from __future__ import annotations

import csv
import json
import re
import unicodedata
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo


ARCHIVE_VERSION = "ARCHIVE_H5_HISTORY_V0_1"
PARIS_TZ = ZoneInfo("Europe/Paris")


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def now_paris_iso() -> str:
    return datetime.now(PARIS_TZ).replace(microsecond=0).isoformat()


def slugify(value: str, max_len: int = 140) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = value.encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    return (value.upper()[:max_len] or "COURSE_ND")


def extract_date_course(course_uid: str, fallback: str = "ND") -> str:
    match = re.search(r"(\d{2})_(\d{2})_(\d{4})", course_uid or "")
    if not match:
        return fallback

    day, month, year = match.groups()
    return f"{year}-{month}-{day}"


def parse_iso_datetime(value: str) -> Optional[datetime]:
    if not value or value == "ND":
        return None

    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def build_history_dir(
    *,
    base_dir: str,
    date_course: str,
    course_uid: str,
) -> Path:
    if not date_course or date_course == "ND":
        date_course = datetime.now(PARIS_TZ).strftime("%Y-%m-%d")

    year, month, day = date_course.split("-")

    return (
        Path(base_dir)
        / "history"
        / "h5"
        / year
        / month
        / day
        / slugify(course_uid)
    )


def build_artifact_path(
    *,
    base_dir: str,
    date_course: str,
    course_uid: str,
    artifact_type: str,
    capture_label: str = "ND",
    extension: str = "json",
) -> Path:
    history_dir = build_history_dir(
        base_dir=base_dir,
        date_course=date_course,
        course_uid=course_uid,
    )

    filename = f"{slugify(artifact_type, 80)}_{slugify(capture_label, 30)}.{extension}"
    return history_dir / filename


def build_index_path(
    *,
    base_dir: str,
    date_course: str,
) -> Path:
    if not date_course or date_course == "ND":
        date_course = datetime.now(PARIS_TZ).strftime("%Y-%m-%d")

    year, month, day = date_course.split("-")

    return (
        Path(base_dir)
        / "history"
        / "h5"
        / year
        / month
        / day
        / f"index_{date_course}.csv"
    )


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp_path.replace(path)


def append_csv_row(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    file_exists = path.exists()
    fieldnames = list(row.keys())

    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)

        if not file_exists:
            writer.writeheader()

        writer.writerow(row)


def live_guard_status(
    *,
    now_paris: str,
    depart_dt: str,
    decision_before_race_oui_non: str,
    result_known_at_decision: str,
    input_hash: str = "ND",
    engine_version: str = "ND",
    ruleset_version: str = "ND",
) -> Tuple[bool, str]:
    if decision_before_race_oui_non != "oui":
        return False, "REFUSED_DECISION_NOT_BEFORE_RACE"

    if result_known_at_decision != "non":
        return False, "REFUSED_RESULT_RISK"

    if not input_hash or input_hash == "ND":
        return False, "REFUSED_INPUT_HASH_MISSING"

    if not engine_version or engine_version == "ND":
        return False, "REFUSED_ENGINE_VERSION_MISSING"

    if not ruleset_version or ruleset_version == "ND":
        return False, "REFUSED_RULESET_VERSION_MISSING"

    now_dt = parse_iso_datetime(now_paris)
    depart = parse_iso_datetime(depart_dt)

    if now_dt is None:
        return False, "REFUSED_NOW_TIMESTAMP_INVALID"

    if depart is None:
        return False, "REFUSED_DEPART_TIMESTAMP_INVALID"

    if now_dt >= depart:
        return False, "REFUSED_AFTER_RACE"

    return True, "LIVE_VALID_PRE_RACE"


def classify_archive_status(
    *,
    now_paris: str,
    depart_dt: str,
    decision_before_race_oui_non: str,
    result_known_at_decision: str,
    input_hash: str = "ND",
    engine_version: str = "ND",
    ruleset_version: str = "ND",
) -> Dict[str, str]:
    allowed, reason = live_guard_status(
        now_paris=now_paris,
        depart_dt=depart_dt,
        decision_before_race_oui_non=decision_before_race_oui_non,
        result_known_at_decision=result_known_at_decision,
        input_hash=input_hash,
        engine_version=engine_version,
        ruleset_version=ruleset_version,
    )

    if allowed:
        return {
            "status": "LIVE_VALID_PRE_RACE",
            "reason": reason,
            "live_eligible_oui_non": "oui",
            "retro_audit_only_oui_non": "non",
        }

    if reason == "REFUSED_AFTER_RACE":
        return {
            "status": "RETRO_AUDIT_ONLY",
            "reason": reason,
            "live_eligible_oui_non": "non",
            "retro_audit_only_oui_non": "oui",
        }

    return {
        "status": reason,
        "reason": reason,
        "live_eligible_oui_non": "non",
        "retro_audit_only_oui_non": "non",
    }


@dataclass
class H5HistoryIndexRow:
    archive_version: str
    created_at_utc: str
    created_at_paris: str
    course_uid: str
    course_uid_slug: str
    date_course: str
    capture_label: str
    source_route: str
    artifact_type: str
    local_path: str
    status: str
    reason: str
    live_eligible_oui_non: str
    retro_audit_only_oui_non: str
    decision_before_race_oui_non: str
    result_known_at_decision: str


def archive_h5_artifact(
    *,
    base_dir: str,
    payload: Dict[str, Any],
    course_uid: str,
    date_course: Optional[str],
    capture_label: str,
    source_route: str,
    artifact_type: str,
    status: str,
    reason: str,
    decision_before_race_oui_non: str = "ND",
    result_known_at_decision: str = "ND",
    live_eligible_oui_non: str = "non",
    retro_audit_only_oui_non: str = "non",
    extension: str = "json",
) -> Dict[str, Any]:
    resolved_date = date_course or extract_date_course(course_uid)

    artifact_path = build_artifact_path(
        base_dir=base_dir,
        date_course=resolved_date,
        course_uid=course_uid,
        artifact_type=artifact_type,
        capture_label=capture_label,
        extension=extension,
    )

    archive_payload = {
        "archive_version": ARCHIVE_VERSION,
        "created_at_utc": now_utc_iso(),
        "created_at_paris": now_paris_iso(),
        "course_uid": course_uid,
        "course_uid_slug": slugify(course_uid),
        "date_course": resolved_date,
        "capture_label": capture_label,
        "source_route": source_route,
        "artifact_type": artifact_type,
        "status": status,
        "reason": reason,
        "live_eligible_oui_non": live_eligible_oui_non,
        "retro_audit_only_oui_non": retro_audit_only_oui_non,
        "decision_before_race_oui_non": decision_before_race_oui_non,
        "result_known_at_decision": result_known_at_decision,
        "payload": payload,
    }

    write_json(artifact_path, archive_payload)

    index_path = build_index_path(
        base_dir=base_dir,
        date_course=resolved_date,
    )

    index_row = asdict(
        H5HistoryIndexRow(
            archive_version=ARCHIVE_VERSION,
            created_at_utc=now_utc_iso(),
            created_at_paris=now_paris_iso(),
            course_uid=course_uid,
            course_uid_slug=slugify(course_uid),
            date_course=resolved_date,
            capture_label=capture_label,
            source_route=source_route,
            artifact_type=artifact_type,
            local_path=str(artifact_path),
            status=status,
            reason=reason,
            live_eligible_oui_non=live_eligible_oui_non,
            retro_audit_only_oui_non=retro_audit_only_oui_non,
            decision_before_race_oui_non=decision_before_race_oui_non,
            result_known_at_decision=result_known_at_decision,
        )
    )

    append_csv_row(index_path, index_row)

    return {
        "ok": True,
        "archive_version": ARCHIVE_VERSION,
        "artifact_path": str(artifact_path),
        "index_path": str(index_path),
        "status": status,
        "reason": reason,
    }
