# V0_9_31B2_4_2B_HTML_GCS_FIELDS
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
import csv
import hashlib
import json
import re
from typing import Iterable, Any


PARSER_VERSION = "odds_html_parser_v0_2_np_status_guard"


@dataclass(frozen=True)
class OddsRunnerRow:
    course_uid: str
    snapshot_label: str
    numero: int
    cheval: str
    cote_sg: float | None
    cote_sp_min: float | None
    cote_sp_max: float | None
    statut: str
    is_np: str
    statut_source: str
    np_statut_confidence: str
    source_html_path: str
    parser_version: str
    confidence: str
    raw_fragment_hash: str
    extracted_at_utc: str
    html_gcs_path: str = "ND"
    html_gcs_uri: str = "ND"
    html_gcs_upload_ok: str = "non"
    html_gcs_upload_error: str = "ND"


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None

    cleaned = value.strip().replace(",", ".")
    cleaned = re.sub(r"[^0-9.]", "", cleaned)

    if not cleaned:
        return None

    try:
        return float(cleaned)
    except ValueError:
        return None


def _strip_html(html: str) -> str:
    html = re.sub(r"(?is)<script.*?</script>", " ", html)
    html = re.sub(r"(?is)<style.*?</style>", " ", html)
    html = re.sub(r"(?is)<br\s*/?>", "\n", html)
    html = re.sub(r"(?is)</(td|span|button)>", " ", html)
    html = re.sub(r"(?is)</(div|tr|li|p)>", "\n", html)
    html = re.sub(r"(?is)<[^>]+>", " ", html)
    html = unescape(html)
    html = html.replace("\u00a0", " ")
    html = re.sub(r"[ \t]+", " ", html)
    html = re.sub(r"\n+", "\n", html)
    return html.strip()


def _extract_balanced_object_after_marker(html: str, marker: str) -> str | None:
    idx = html.find(marker)
    if idx < 0:
        return None

    start = html.find("{", idx)
    if start < 0:
        return None

    depth = 0
    in_string = False
    escape = False

    for pos in range(start, len(html)):
        char = html[pos]

        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            continue

        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return html[start:pos + 1]

    return None


def _extract_runner_names_from_html(html: str) -> dict[int, str]:
    names: dict[int, str] = {}

    pattern_popup = re.compile(
        r'href="[^"]*/(?P<num>\d{1,2})/infos"(?P<body>.*?)data-popup-name="Fiche Partant\s*:\s*(?P<horse>[^"]+)"',
        flags=re.I | re.S,
    )

    for match in pattern_popup.finditer(html):
        try:
            numero = int(match.group("num"))
        except ValueError:
            continue

        horse = unescape(match.group("horse"))
        horse = re.sub(r"\s+", " ", horse).strip()

        if horse:
            names.setdefault(numero, horse)

    pattern_reverse = re.compile(
        r'data-popup-name="Fiche Partant\s*:\s*(?P<horse>[^"]+)"(?P<body>.*?)href="[^"]*/(?P<num>\d{1,2})/infos"',
        flags=re.I | re.S,
    )

    for match in pattern_reverse.finditer(html):
        try:
            numero = int(match.group("num"))
        except ValueError:
            continue

        horse = unescape(match.group("horse"))
        horse = re.sub(r"\s+", " ", horse).strip()

        if horse:
            names.setdefault(numero, horse)

    return names


def _parse_cotes_infos_rows(
    html: str,
    course_uid: str,
    snapshot_label: str,
    source_html_path: Path,
    extracted_at_utc: str,
) -> list[OddsRunnerRow]:
    raw_object = _extract_balanced_object_after_marker(html, "cotesInfos:")
    if not raw_object:
        return []

    try:
        payload: dict[str, Any] = json.loads(raw_object)
    except json.JSONDecodeError:
        return []

    names = _extract_runner_names_from_html(html)
    rows: list[OddsRunnerRow] = []

    for key, value in payload.items():
        if not str(key).isdigit():
            continue

        if not isinstance(value, dict):
            continue

        odds = value.get("odds")
        if not isinstance(odds, dict):
            continue

        numero = int(key)
        sg = odds.get("SG")
        sp_min = odds.get("SPMin")
        sp_max = odds.get("SPMax")

        cote_sg = _to_float(str(sg)) if sg not in (None, False) else None
        cote_sp_min = _to_float(str(sp_min)) if sp_min not in (None, False) else None
        cote_sp_max = _to_float(str(sp_max)) if sp_max not in (None, False) else None

        if cote_sg is None:
            continue

        cheval = names.get(numero, "ND")

        rows.append(
            OddsRunnerRow(
                course_uid=course_uid,
                snapshot_label=snapshot_label,
                numero=numero,
                cheval=cheval,
                cote_sg=cote_sg,
                cote_sp_min=cote_sp_min,
                cote_sp_max=cote_sp_max,
                statut="ND",
                is_np="ND",
                statut_source="ZETURF_HTML_STATUS_NOT_FOUND",
                np_statut_confidence="ND",
                source_html_path=str(source_html_path),
                parser_version=PARSER_VERSION,
                confidence="COTESINFOS_JSON" if cheval != "ND" else "COTESINFOS_JSON_NAME_ND",
                raw_fragment_hash=_hash_text(json.dumps(value, ensure_ascii=False, sort_keys=True)),
                extracted_at_utc=extracted_at_utc,
            )
        )

    return sorted(rows, key=lambda item: item.numero)


def _iter_candidate_lines(text: str) -> Iterable[str]:
    for line in text.splitlines():
        cleaned = line.strip()
        if len(cleaned) < 6:
            continue

        if not re.search(r"\b\d{1,2}\b", cleaned):
            continue

        if not re.search(r"\d+[,.]\d+", cleaned):
            continue

        yield cleaned


def _parse_line_to_row(
    line: str,
    course_uid: str,
    snapshot_label: str,
    source_html_path: Path,
    extracted_at_utc: str,
) -> OddsRunnerRow | None:
    pattern = re.compile(
        r"^\s*(?P<num>\d{1,2})\s+"
        r"(?P<horse>[A-Za-zÀ-ÖØ-öø-ÿ' .-]{2,}?)\s+"
        r"(?P<sg>\d+[,.]\d+)"
        r"(?:\s+(?P<sp_min>\d+[,.]\d+)\s*[-–]\s*(?P<sp_max>\d+[,.]\d+))?"
    )

    match = pattern.search(line)
    if not match:
        return None

    numero = int(match.group("num"))
    cheval = re.sub(r"\s+", " ", match.group("horse")).strip()
    cote_sg = _to_float(match.group("sg"))
    cote_sp_min = _to_float(match.group("sp_min"))
    cote_sp_max = _to_float(match.group("sp_max"))

    if not cheval or cote_sg is None:
        return None

    return OddsRunnerRow(
        course_uid=course_uid,
        snapshot_label=snapshot_label,
        numero=numero,
        cheval=cheval,
        cote_sg=cote_sg,
        cote_sp_min=cote_sp_min,
        cote_sp_max=cote_sp_max,
        statut="ND",
        is_np="ND",
        statut_source="ZETURF_HTML_STATUS_NOT_FOUND",
        np_statut_confidence="ND",
        source_html_path=str(source_html_path),
        parser_version=PARSER_VERSION,
        confidence="TEXT_PATTERN",
        raw_fragment_hash=_hash_text(line),
        extracted_at_utc=extracted_at_utc,
    )


def extract_odds_from_html(
    html_path: str | Path,
    course_uid: str,
    snapshot_label: str,
) -> list[OddsRunnerRow]:
    source_html_path = Path(html_path)

    if not source_html_path.exists():
        raise FileNotFoundError(f"HTML introuvable : {source_html_path}")

    html = source_html_path.read_text(encoding="utf-8", errors="ignore")
    extracted_at_utc = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    cotes_infos_rows = _parse_cotes_infos_rows(
        html=html,
        course_uid=course_uid,
        snapshot_label=snapshot_label,
        source_html_path=source_html_path,
        extracted_at_utc=extracted_at_utc,
    )

    if cotes_infos_rows:
        return cotes_infos_rows

    text = _strip_html(html)

    rows: list[OddsRunnerRow] = []
    seen_numbers: set[int] = set()

    for line in _iter_candidate_lines(text):
        row = _parse_line_to_row(
            line=line,
            course_uid=course_uid,
            snapshot_label=snapshot_label,
            source_html_path=source_html_path,
            extracted_at_utc=extracted_at_utc,
        )

        if row is None:
            continue

        if row.numero in seen_numbers:
            continue

        seen_numbers.add(row.numero)
        rows.append(row)

    return sorted(rows, key=lambda item: item.numero)


def write_odds_csv(rows: list[OddsRunnerRow], output_path: str | Path) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = list(asdict(rows[0]).keys()) if rows else [
        "course_uid",
        "snapshot_label",
        "numero",
        "cheval",
        "cote_sg",
        "cote_sp_min",
        "cote_sp_max",
        "statut",
        "is_np",
        "statut_source",
        "np_statut_confidence",
        "source_html_path",
        "parser_version",
        "confidence",
        "raw_fragment_hash",
        "extracted_at_utc",
        "html_gcs_path",
        "html_gcs_uri",
        "html_gcs_upload_ok",
        "html_gcs_upload_error",
    ]

    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))

    return output


def write_odds_json(rows: list[OddsRunnerRow], output_path: str | Path) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "parser_version": PARSER_VERSION,
        "rows_count": len(rows),
        "rows": [asdict(row) for row in rows],
    }

    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return output


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Extrait les cotes SG/SP depuis un HTML ZEturf sauvegardé."
    )
    parser.add_argument("--html", required=True, help="Chemin du fichier HTML local")
    parser.add_argument("--course-uid", required=True)
    parser.add_argument("--snapshot-label", required=True)
    parser.add_argument("--csv-out", required=True)
    parser.add_argument("--json-out", required=True)

    args = parser.parse_args()

    rows = extract_odds_from_html(
        html_path=args.html,
        course_uid=args.course_uid,
        snapshot_label=args.snapshot_label,
    )

    csv_path = write_odds_csv(rows, args.csv_out)
    json_path = write_odds_json(rows, args.json_out)

    print(f"Lignes extraites : {len(rows)}")
    print(f"CSV : {csv_path}")
    print(f"JSON : {json_path}")


if __name__ == "__main__":
    main()
