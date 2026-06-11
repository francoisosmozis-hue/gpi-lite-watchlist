from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import hashlib
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]

RAW_HTML_DIR = PROJECT_ROOT / "data" / "raw" / "zeturf_snapshots" / "html"
RAW_SCREENSHOT_DIR = PROJECT_ROOT / "data" / "raw" / "zeturf_snapshots" / "screenshots"
RAW_NETWORK_DIR = PROJECT_ROOT / "data" / "raw" / "zeturf_snapshots" / "network"
OUTPUT_ODDS_DIR = PROJECT_ROOT / "data" / "output" / "odds"


@dataclass(frozen=True)
class OddsSnapshotPlan:
    course_uid: str
    reunion_course: str
    hippodrome: str
    snapshot_label: str
    source_url: str
    capture_mode: str
    html_path: str
    screenshot_path: str
    network_path: str
    output_json_path: str
    created_at_utc: str
    plan_hash: str


def _safe_slug(value: str) -> str:
    cleaned = "".join(
        c.lower() if c.isalnum() else "_"
        for c in value.strip()
    )
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_") or "nd"


def _hash_payload(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def ensure_odds_dirs() -> None:
    for path in (RAW_HTML_DIR, RAW_SCREENSHOT_DIR, RAW_NETWORK_DIR, OUTPUT_ODDS_DIR):
        path.mkdir(parents=True, exist_ok=True)


def build_snapshot_plan(
    course_uid: str,
    reunion_course: str,
    hippodrome: str,
    snapshot_label: str,
    source_url: str,
    capture_mode: str = "manual_or_browser_export",
) -> OddsSnapshotPlan:
    ensure_odds_dirs()

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    slug = "_".join(
        [
            _safe_slug(course_uid),
            _safe_slug(reunion_course),
            _safe_slug(snapshot_label),
        ]
    )

    html_path = RAW_HTML_DIR / f"{slug}.html"
    screenshot_path = RAW_SCREENSHOT_DIR / f"{slug}.png"
    network_path = RAW_NETWORK_DIR / f"{slug}.har"
    output_json_path = OUTPUT_ODDS_DIR / f"{slug}_odds_snapshot_plan.json"

    base_payload = {
        "course_uid": course_uid,
        "reunion_course": reunion_course,
        "hippodrome": hippodrome,
        "snapshot_label": snapshot_label,
        "source_url": source_url,
        "capture_mode": capture_mode,
        "html_path": str(html_path),
        "screenshot_path": str(screenshot_path),
        "network_path": str(network_path),
        "output_json_path": str(output_json_path),
        "created_at_utc": now,
    }

    plan_hash = _hash_payload(base_payload)

    return OddsSnapshotPlan(
        **base_payload,
        plan_hash=plan_hash,
    )


def save_snapshot_plan(plan: OddsSnapshotPlan) -> Path:
    output_path = Path(plan.output_json_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    output_path.write_text(
        json.dumps(asdict(plan), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return output_path


def main() -> None:
    plan = build_snapshot_plan(
        course_uid="demo_course_uid",
        reunion_course="R1C1",
        hippodrome="ND",
        snapshot_label="H5",
        source_url="ND",
    )

    output_path = save_snapshot_plan(plan)
    print(f"Snapshot plan créé : {output_path}")


if __name__ == "__main__":
    main()
