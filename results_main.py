from pathlib import Path

import pandas as pd

from engine.results_extract import build_result_input_from_pdf


def main():
    results_dir = Path("data/results")
    results_dir.mkdir(parents=True, exist_ok=True)

    pdfs = sorted([
        path for path in results_dir.glob("*.pdf")
        if path.is_file()
    ])

    if not pdfs:
        print("Aucun PDF resultats trouve dans data/results")
        print("Nom attendu : n'importe quel PDF Resultats & Rapports ZEturf")
        return

    print(f"{len(pdfs)} PDF resultats detecte(s) dans data/results")

    all_rows = []

    for pdf in pdfs:
        print("")
        print(f"Traitement resultats : {pdf.name}")

        try:
            df = build_result_input_from_pdf(pdf)
	    df["source_pdf"] = pdf.name
        except Exception as exc:
            print(f"Erreur extraction resultats : {pdf.name}")
            print(str(exc))
            continue

        if df is None or df.empty:
            print(f"Aucune ligne resultat extraite : {pdf.name}")
            continue

        all_rows.append(df)
        print(df.to_string(index=False))

    if not all_rows:
        print("Aucun result_input exploitable cree")
        return

    if len(all_rows) == 1:
        result_input = all_rows[0]
    else:
        result_input = pd.concat(all_rows, ignore_index=True)

    out = Path("data/results/result_input.csv")
    result_input.to_csv(out, index=False, encoding="utf-8-sig")

    print("")
    print("result_input.csv cree")
    print(out)


if __name__ == "__main__":
    main()