from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from html import unescape
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
import csv
import json
import re
import unicodedata


RESOLVER_VERSION = "zeturf_url_resolver_v0_1"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PLAN_CSV = PROJECT_ROOT / "data" / "output" / "h5_auto_plan.csv"
DEFAULT_RESOLVED_CSV = PROJECT_ROOT / "data" / "output" / "h5_auto_plan_resolved.csv"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "data" / "output" / "h5_url_resolver_report.json"

ZETURF_BASE_URL = "https://www.zeturf.fr"


@dataclass(frozen=True)
class ResolvedUrlRow:
    course_uid: str
    priorite_h5: str
    url_zeturf: str
    url_status: str
    url_resolver_reason: str
    heure_depart: str
    capture_label: str
    capture_dt: str
    status: str
    source_reunion_url: str
    resolver_version: str
    resolved_at_utc: str


def strip_accents(value: str) -> str:
    value = unicodedata.normalize("NFD", value)
    return "".join(c for c in value if unicodedata.category(c) != "Mn")


def safe_slug(value: str) -> str:
    value = strip_accents(value.lower())
    value = value.replace("œ", "oe").replace("æ", "ae")
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value)
    return value.strip("-")


def parse_course_uid(course_uid: str) -> tuple[str, str, str]:
    """
    Attend :
    12_06_2026 - FEURS - Prix xxx_ Pronostics & Cotes
    Retourne :
    date_iso, hippodrome, nom_course
    """
    raw = course_uid.strip()

    m = re.match(r"(?P<d>\d{2})_(?P<m>\d{2})_(?P<y>\d{4})\s*-\s*(?P<hippo>[^-]+?)\s*-\s*(?P<name>.+)$", raw)
    if not m:
        return "ND", "ND", "ND"

    date_iso = f"{m.group('y')}-{m.group('m')}-{m.group('d')}"
    hippo = m.group("hippo").strip()
    name = m.group("name").strip()
    name = re.sub(r"_?\s*Pronostics\s*&\s*Cotes\s*$", "", name, flags=re.I).strip()
    return date_iso, hippo, name


def fetch_text(url: str, timeout: int = 20) -> str:
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 GPI-Lite Watchlist URL Resolver",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.5",
        },
    )
    with urlopen(req, timeout=timeout) as response:
        raw = response.read()
    return raw.decode("utf-8", errors="ignore")


def extract_course_links(html: str, date_iso: str) -> list[str]:
    html = unescape(html)
    pattern = re.compile(
        rf'(?P<href>/fr/course-du-jour/{re.escape(date_iso)}/R\d+C\d+[^"\'<\s]+)',
        flags=re.I,
    )
    links = []
    seen = set()
    for m in pattern.finditer(html):
        href = m.group("href")
        if href not in seen:
            seen.add(href)
            links.append(href)
    return links


def score_link(link: str, hippo_slug: str, course_slug: str) -> int:
    lower = link.lower()
    score = 0

    if f"-{hippo_slug}-" in lower or lower.endswith(f"-{hippo_slug}"):
        score += 20

    if course_slug and course_slug in lower:
        score += 100

    # Tolérance : compare par fragments du nom de course.
    fragments = [p for p in course_slug.split("-") if len(p) >= 3]
    matched = sum(1 for p in fragments if p in lower)
    score += matched * 5

    return score


def resolve_course_url(course_uid: str) -> tuple[str, str, str]:
    date_iso, hippo, course_name = parse_course_uid(course_uid)

    if date_iso == "ND" or hippo == "ND" or course_name == "ND":
        return "ND", "PARSE_COURSE_UID_FAILED", "ND"

    hippo_slug = safe_slug(hippo)
    course_slug = safe_slug(course_name)

    best_url = "ND"
    best_score = 0
    best_reunion_url = "ND"

    for reunion_num in range(1, 21):
        reunion_url = f"{ZETURF_BASE_URL}/fr/reunion-du-jour/{date_iso}/R{reunion_num}-{hippo_slug}"

        try:
            html = fetch_text(reunion_url)
        except (HTTPError, URLError, TimeoutError, Exception):
            continue

        links = extract_course_links(html, date_iso)

        for href in links:
            current_score = score_link(href, hippo_slug, course_slug)
            if current_score > best_score:
                best_score = current_score
                best_url = ZETURF_BASE_URL + href
                best_reunion_url = reunion_url

        if best_score >= 100:
            return best_url, "OK", best_reunion_url

    if best_url != "ND" and best_score >= 40:
        return best_url, "LOW_CONFIDENCE_MATCH", best_reunion_url

    return "ND", "URL_NOT_FOUND", best_reunion_url


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)

    if rows:
        fieldnames = list(rows[0].keys())
    else:
        fieldnames = [
            "course_uid",
            "priorite_h5",
            "url_zeturf",
            "url_status",
            "url_resolver_reason",
            "heure_depart",
            "capture_label",
            "capture_dt",
            "status",
            "source_reunion_url",
            "resolver_version",
            "resolved_at_utc",
        ]

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return path


def resolve_plan(
    plan_csv: str | Path = DEFAULT_PLAN_CSV,
    output_csv: str | Path = DEFAULT_RESOLVED_CSV,
    report_json: str | Path = DEFAULT_REPORT_JSON,
) -> dict[str, object]:
    plan_path = Path(plan_csv)
    output_path = Path(output_csv)
    report_path = Path(report_json)

    rows = read_csv(plan_path)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    cache: dict[str, tuple[str, str, str]] = {}
    resolved_rows: list[dict[str, str]] = []

    for row in rows:
        course_uid = (row.get("course_uid") or "").strip()

        if course_uid not in cache:
            cache[course_uid] = resolve_course_url(course_uid)

        url, reason, reunion_url = cache[course_uid]

        url_status = "OK" if reason == "OK" else reason
        status = row.get("status", "")

        if url_status == "OK" and status == "URL_REQUIRED":
            status = "PLANNED_URL_OK"

        out = dict(row)
        out["url_zeturf"] = url
        out["url_status"] = url_status
        out["url_resolver_reason"] = reason
        out["status"] = status
        out["source_reunion_url"] = reunion_url
        out["resolver_version"] = RESOLVER_VERSION
        out["resolved_at_utc"] = now
        resolved_rows.append(out)

    write_csv(output_path, resolved_rows)

    report = {
        "ok": True,
        "resolver_version": RESOLVER_VERSION,
        "input_csv": str(plan_path),
        "output_csv": str(output_path),
        "rows_input": len(rows),
        "rows_output": len(resolved_rows),
        "unique_courses": len(cache),
        "url_ok_count": sum(1 for row in resolved_rows if row.get("url_status") == "OK"),
        "url_not_found_count": sum(1 for row in resolved_rows if row.get("url_status") == "URL_NOT_FOUND"),
        "low_confidence_count": sum(1 for row in resolved_rows if row.get("url_status") == "LOW_CONFIDENCE_MATCH"),
        "created_at_utc": now,
        "strict_allowed_oui_non": "non",
        "ticket_strict": "NO_BET",
    }

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    return report


def main() -> None:
    report = resolve_plan()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
