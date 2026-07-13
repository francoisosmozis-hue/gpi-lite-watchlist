from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


# V0_9_31B2_4_2B_HTML_GCS_PERSISTENCE
def _p342b_bucket_name() -> str:
    import os

    return (
        os.getenv("GPI_BUCKET_NAME")
        or os.getenv("BUCKET_NAME")
        or os.getenv("GPI_BUCKET")
        or "gpi-lite-watchlist-data"
    )


def _p342b_safe_gcs_object_from_html(html_path: str) -> str:
    """
    Objet GCS stable pour les HTML ZEturf bruts.
    On utilise uniquement le nom du fichier pour ?viter de d?pendre d'un chemin Windows.
    """
    p = Path(str(html_path))
    return f"gpi-lite/raw/zeturf_snapshots/html/{p.name}"


def _p342b_upload_html_source_to_gcs(html_path: str) -> dict:
    """
    Upload best-effort.
    ?chec local = non bloquant pour conserver la compatibilit? du pipeline.
    """
    p = Path(str(html_path))

    if not p.exists():
        return {
            "html_gcs_path": "ND",
            "html_gcs_uri": "ND",
            "html_gcs_upload_ok": "non",
            "html_gcs_upload_error": "LOCAL_HTML_NOT_FOUND",
        }

    try:
        from google.cloud import storage

        bucket_name = _p342b_bucket_name()
        object_name = _p342b_safe_gcs_object_from_html(str(p))

        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(object_name)
        blob.upload_from_filename(str(p), content_type="text/html")

        return {
            "html_gcs_path": object_name,
            "html_gcs_uri": f"gs://{bucket_name}/{object_name}",
            "html_gcs_upload_ok": "oui",
            "html_gcs_upload_error": "ND",
        }
    except Exception as exc:
        return {
            "html_gcs_path": "ND",
            "html_gcs_uri": "ND",
            "html_gcs_upload_ok": "non",
            "html_gcs_upload_error": f"{type(exc).__name__}:{exc}",
        }


def _p342b_apply_html_gcs_info(rows, html_info: dict):
    """
    OddsRow est frozen=True.
    On utilise dataclasses.replace pour enrichir les lignes sans casser l'immuabilit?.
    """
    from dataclasses import replace

    out = []
    for row in rows:
        try:
            out.append(
                replace(
                    row,
                    html_gcs_path=html_info.get("html_gcs_path", "ND"),
                    html_gcs_uri=html_info.get("html_gcs_uri", "ND"),
                    html_gcs_upload_ok=html_info.get("html_gcs_upload_ok", "non"),
                    html_gcs_upload_error=html_info.get("html_gcs_upload_error", "ND"),
                )
            )
        except Exception:
            # Fallback compatible si row n'est pas une dataclass.
            try:
                setattr(row, "html_gcs_path", html_info.get("html_gcs_path", "ND"))
                setattr(row, "html_gcs_uri", html_info.get("html_gcs_uri", "ND"))
                setattr(row, "html_gcs_upload_ok", html_info.get("html_gcs_upload_ok", "non"))
                setattr(row, "html_gcs_upload_error", html_info.get("html_gcs_upload_error", "ND"))
            except Exception:
                pass
            out.append(row)

    return out


sys.path.insert(0, str(PROJECT_ROOT))

from engine.odds.zeturf_html_odds_parser import (
    extract_odds_from_html,
    write_odds_csv,
    write_odds_json,
)
from engine.odds.odds_movement_builder import (
    build_odds_movements,
    write_movements_csv,
    write_movements_json,
)
from engine.odds.odds_volatility_gate import (
    build_volatility_gate,
    write_gate_csv,
    write_gate_json,
)
from engine.odds.latest_odds_context import (
    build_latest_odds_context,
    write_latest_odds_context,
)


OUTPUT_DIR = PROJECT_ROOT / "data" / "output" / "odds"


def safe_slug(value: str) -> str:
    cleaned = "".join(
        c.lower() if c.isalnum() else "_"
        for c in value.strip()
    )

    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")

    return cleaned.strip("_") or "nd"


def build_output_prefix(course_uid: str, snapshot_label: str) -> Path:
    return OUTPUT_DIR / f"{safe_slug(course_uid)}_{safe_slug(snapshot_label)}"


def parse_snapshot(
    html_path: str,
    course_uid: str,
    snapshot_label: str,
) -> tuple[Path, Path, int]:
    prefix = build_output_prefix(course_uid, snapshot_label)

    rows = extract_odds_from_html(
        html_path=html_path,
        course_uid=course_uid,
        snapshot_label=snapshot_label,
    )

    html_gcs_info = _p342b_upload_html_source_to_gcs(html_path)
    rows = _p342b_apply_html_gcs_info(rows, html_gcs_info)

    csv_path = write_odds_csv(rows, f"{prefix}_odds.csv")
    json_path = write_odds_json(rows, f"{prefix}_odds.json")

    return csv_path, json_path, len(rows)


def build_movement(
    previous_csv: Path,
    current_csv: Path,
    course_uid: str,
    previous_label: str,
    current_label: str,
) -> tuple[Path, Path, int, int]:
    prefix = OUTPUT_DIR / (
        f"{safe_slug(course_uid)}_"
        f"{safe_slug(previous_label)}_to_{safe_slug(current_label)}"
    )

    rows = build_odds_movements(
        previous_csv=previous_csv,
        current_csv=current_csv,
        previous_snapshot_label=previous_label,
        current_snapshot_label=current_label,
    )

    csv_path = write_movements_csv(rows, f"{prefix}_movements.csv")
    json_path = write_movements_json(rows, f"{prefix}_movements.json")

    kill_switch_count = sum(
        1 for row in rows
        if row.statut_marche == "KILL_SWITCH"
    )

    return csv_path, json_path, len(rows), kill_switch_count


def build_gate(
    current_csv: Path,
    movements_csv: Path,
    course_uid: str,
    previous_label: str,
    current_label: str,
    top_n: int,
) -> tuple[Path, Path, str, str, str]:
    prefix = OUTPUT_DIR / (
        f"{safe_slug(course_uid)}_"
        f"{safe_slug(previous_label)}_to_{safe_slug(current_label)}"
    )

    result = build_volatility_gate(
        current_odds_csv=current_csv,
        movements_csv=movements_csv,
        top_n=top_n,
    )

    csv_path = write_gate_csv(result, f"{prefix}_volatility_gate.csv")
    json_path = write_gate_json(result, f"{prefix}_volatility_gate.json")

    return (
        csv_path,
        json_path,
        result.course_statut_marche,
        result.kill_switch_topn_oui_non,
        result.reason_codes,
    )


def build_latest_context(
    current_csv: Path,
    movements_csv: Path,
    gate_json: Path,
    course_uid: str,
    previous_label: str,
    current_label: str,
) -> tuple[Path, Path, str, str, str]:
    prefix = OUTPUT_DIR / (
        f"{safe_slug(course_uid)}_"
        f"{safe_slug(previous_label)}_to_{safe_slug(current_label)}"
    )

    context = build_latest_odds_context(
        current_odds_csv=current_csv,
        movements_csv=movements_csv,
        gate_json=gate_json,
    )

    course_context_path = write_latest_odds_context(
        context,
        f"{prefix}_latest_odds_context.json",
    )

    latest_context_path = write_latest_odds_context(
        context,
        OUTPUT_DIR / "latest_odds_context.json",
    )

    return (
        course_context_path,
        latest_context_path,
        context.strict_allowed_by_odds_context,
        context.edge_status_by_odds_context,
        context.reason_codes,
    )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Pipeline local snapshots ZEturf : HTML vers cotes, volatilité SG, gate GPI."
    )
    parser.add_argument("--course-uid", required=True)
    parser.add_argument("--current-html", required=True)
    parser.add_argument("--current-label", required=True)
    parser.add_argument("--previous-html", required=False)
    parser.add_argument("--previous-label", required=False)
    parser.add_argument("--top-n", type=int, default=6)

    args = parser.parse_args()

    current_csv, current_json, current_count = parse_snapshot(
        html_path=args.current_html,
        course_uid=args.course_uid,
        snapshot_label=args.current_label,
    )

    print(f"Snapshot courant : {args.current_label}")
    print(f"Lignes extraites : {current_count}")
    print(f"CSV courant : {current_csv}")
    print(f"JSON courant : {current_json}")

    if args.previous_html and args.previous_label:
        previous_csv, previous_json, previous_count = parse_snapshot(
            html_path=args.previous_html,
            course_uid=args.course_uid,
            snapshot_label=args.previous_label,
        )

        print(f"Snapshot précédent : {args.previous_label}")
        print(f"Lignes extraites : {previous_count}")
        print(f"CSV précédent : {previous_csv}")
        print(f"JSON précédent : {previous_json}")

        movements_csv, movements_json, movement_count, kill_switch_count = build_movement(
            previous_csv=previous_csv,
            current_csv=current_csv,
            course_uid=args.course_uid,
            previous_label=args.previous_label,
            current_label=args.current_label,
        )

        print(f"Lignes mouvement : {movement_count}")
        print(f"Kill-switch volatilité : {kill_switch_count}")
        print(f"CSV mouvements : {movements_csv}")
        print(f"JSON mouvements : {movements_json}")

        gate_csv, gate_json, course_statut, kill_switch_topn, reason_codes = build_gate(
            current_csv=current_csv,
            movements_csv=movements_csv,
            course_uid=args.course_uid,
            previous_label=args.previous_label,
            current_label=args.current_label,
            top_n=args.top_n,
        )

        print(f"Course statut marché : {course_statut}")
        print(f"Kill-switch top{args.top_n} : {kill_switch_topn}")
        print(f"Reason codes : {reason_codes}")
        print(f"CSV gate volatilité : {gate_csv}")
        print(f"JSON gate volatilité : {gate_json}")

        (
            course_context_path,
            latest_context_path,
            strict_allowed_odds,
            edge_status_odds,
            context_reason_codes,
        ) = build_latest_context(
            current_csv=current_csv,
            movements_csv=movements_csv,
            gate_json=gate_json,
            course_uid=args.course_uid,
            previous_label=args.previous_label,
            current_label=args.current_label,
        )

        print(f"Contexte odds course : {course_context_path}")
        print(f"Contexte odds latest : {latest_context_path}")
        print(f"Strict allowed odds : {strict_allowed_odds}")
        print(f"Edge odds : {edge_status_odds}")
        print(f"Reason codes contexte : {context_reason_codes}")


if __name__ == "__main__":
    main()
