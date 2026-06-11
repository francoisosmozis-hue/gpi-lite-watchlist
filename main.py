from pathlib import Path

import pandas as pd

from engine.pdf_extract import extract_text_from_pdf, parse_zeturf_pronostics_text
from engine.exports import export_csv
from engine.detector import detect_pdf_type
from engine.gpi_lite import build_watchlist_row
from engine.zeturf_pymupdf_fallback import parse_zeturf_morning_pymupdf


def normalize_zetturf_odds_columns(df):
    if df is None:
        return pd.DataFrame()

    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    aliases = {
        "SG": "SG",
        "sg": "SG",
        "Cote": "SG",
        "Cotes": "SG",
        "Côte": "SG",
        "Côtes": "SG",
        "Cote SG": "SG",
        "Côte SG": "SG",
        "Cote gagnant": "SG",
        "Côte gagnant": "SG",
        "Simple Gagnant": "SG",
        "Rapport gagnant": "SG",
        "odds_win": "SG",
        "cote_win": "SG"
    }

    for col in list(df.columns):
        clean = str(col).strip()
        if clean in aliases and aliases[clean] not in df.columns:
            df = df.rename(columns={col: aliases[clean]})

    if "SG" in df.columns:
        df["SG"] = (
            df["SG"]
            .astype(str)
            .str.replace(",", ".", regex=False)
            .str.extract(r"([0-9]+(?:\.[0-9]+)?)")[0]
        )
        df["SG"] = pd.to_numeric(df["SG"], errors="coerce")

    return df


def validate_sg_for_morning(df, source_name="morning"):
    if df is None or df.empty:
        return {
            "ok": False,
            "error": "morning non exploitable : extraction vide",
            "source": source_name,
            "columns": []
        }

    if "SG" not in df.columns:
        return {
            "ok": False,
            "error": "morning non exploitable : colonne SG/Cotes absente",
            "source": source_name,
            "columns": list(df.columns)
        }

    if df["SG"].dropna().empty:
        return {
            "ok": False,
            "error": "morning non exploitable : colonne SG/Cotes vide ou illisible",
            "source": source_name,
            "columns": list(df.columns)
        }

    return {"ok": True}


def main():
    pdfs = list(Path("data/morning").glob("*.pdf"))

    if not pdfs:
        print("Aucun PDF trouvé dans data/morning")
        return

    watchlist_rows = []

    for pdf in pdfs:
        print("")
        print(f"Fichier : {pdf.name}")

        try:
            pages = extract_text_from_pdf(pdf)
            full_text = "\n".join(p["text"] for p in pages)
        except Exception as exc:
            print(f"morning non exploitable : extraction PDF impossible : {exc}")
            continue

        pdf_type = detect_pdf_type(full_text)
        print(f"Type détecté : {pdf_type}")

        if pdf_type != "ZETURF_PRONOS_COTES":
            print("Action : pas d'extraction de cotes sur ce fichier")
            continue

        course_uid = pdf.stem.replace("_zeturf_9h", "")

        try:
            df = parse_zeturf_pronostics_text(
                full_text,
                course_uid=course_uid,
                snapshot_time="09:00"
            )
        except Exception as exc:
            print(f"morning non exploitable : parsing impossible : {exc}")
            continue

        df = normalize_zetturf_odds_columns(df)

        if df is None or df.empty:
            try:
                df = parse_zeturf_morning_pymupdf(
                    pdf,
                    course_uid=course_uid,
                    snapshot_time="09:00"
                )
                df = normalize_zetturf_odds_columns(df)

                if df is not None and not df.empty:
                    print("Fallback PyMuPDF actif : extraction morning récupérée")
            except Exception as exc:
                print(f"Fallback PyMuPDF impossible : {exc}")

        sg_check = validate_sg_for_morning(df, source_name=str(pdf))

        if not sg_check["ok"]:
            print(sg_check["error"])
            print("Colonnes detectees :", sg_check["columns"])
            continue

        try:
            out = export_csv(df, f"{course_uid}_course_9h_clean.csv")
        except Exception as exc:
            print(f"morning non exploitable : export course impossible : {exc}")
            continue

        print(f"Export créé : {out}")
        print("")
        print(df.to_string(index=False))

        try:
            watchlist_row = build_watchlist_row(df, course_uid)
            watchlist_rows.append(watchlist_row)
        except KeyError as exc:
            print(f"morning non exploitable : colonne obligatoire absente dans build_watchlist_row : {exc}")
            print("Colonnes detectees :", list(df.columns))
            continue
        except Exception as exc:
            print(f"morning non exploitable : build_watchlist_row impossible : {exc}")
            print("Colonnes detectees :", list(df.columns))
            continue

    if watchlist_rows:
        watchlist = pd.DataFrame(watchlist_rows)
        out_watchlist = export_csv(watchlist, "daily_watchlist.csv")

        print("")
        print(f"Watchlist créée : {out_watchlist}")
        print("")
        print(watchlist.to_string(index=False))
    else:
        print("")
        print("Aucune course exploitable pour la watchlist")


if __name__ == "__main__":
    main()