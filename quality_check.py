from pathlib import Path
import json
import glob
import pandas as pd


OUTPUT_DIR = Path("data/output")


def count_files(pattern):
    return len(glob.glob(pattern))


def add_row(rows, bloc, statut, detail):
    rows.append({
        "bloc": bloc,
        "statut": statut,
        "detail": detail,
    })


def latest_h5_context_ok():
    path = OUTPUT_DIR / "latest_h5_context.json"

    if not path.exists():
        return False, "latest_h5_context.json absent"

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"latest_h5_context.json illisible : {exc}"

    if not data.get("ok"):
        return False, "latest_h5_context ok=false"

    context_9h = data.get("context_9h", {})

    if not context_9h.get("ok"):
        return False, "context_9h ok=false"

    if context_9h.get("missing", []):
        return False, "context_9h missing non vide"

    checked = context_9h.get("checked", [])

    found_count = sum(1 for item in checked if item.get("found") is True)

    if found_count <= 0:
        return False, "aucune correspondance 9h trouvée"

    return True, "contexte 9h cloud OK"


def read_csv_len(path):
    try:
        df = pd.read_csv(path)
        return len(df), None
    except Exception as exc:
        return 0, str(exc)


def main():
    rows = []

    pdf_9h_count = count_files("data/morning/*.pdf")
    pdf_h5_count = count_files("data/h5/*.pdf")
    pdf_results_count = count_files("data/results/*.pdf")

    daily_watchlist_path = OUTPUT_DIR / "daily_watchlist.csv"

    course_9h_files = count_files("data/output/*_course_9h_clean.csv")
    course_h5_files = count_files("data/output/*_course_h5_clean.csv")
    h5_update_files = count_files("data/output/*_h5_update.csv")
    decision_files = count_files("data/output/*decision_lite.csv")
    prompt_files = count_files("data/output/*_prompt_gpt.txt")

    h5_context_is_ok, h5_context_detail = latest_h5_context_ok()

    h5_mode = (
        pdf_h5_count > 0
        or course_h5_files > 0
        or h5_update_files > 0
        or decision_files > 0
        or prompt_files > 0
        or h5_context_is_ok
    )

    h5_outputs_complete = (
        course_h5_files > 0
        and h5_update_files > 0
        and decision_files > 0
        and prompt_files > 0
    )

    add_row(rows, "PDF 9h source", "INFO", f"{pdf_9h_count} fichier(s)")
    add_row(rows, "PDF H5 source", "INFO", f"{pdf_h5_count} fichier(s)")
    add_row(rows, "PDF resultats source", "INFO", f"{pdf_results_count} fichier(s)")

    if daily_watchlist_path.exists():
        n, error = read_csv_len(daily_watchlist_path)
        if error:
            add_row(rows, "daily_watchlist.csv", "MANQUANT", f"illisible : {error}")
        else:
            add_row(rows, "daily_watchlist.csv", "OK", f"{n} ligne(s)")
    elif h5_context_is_ok:
        add_row(rows, "daily_watchlist.csv", "INFO", "watchlist absente localement mais contexte 9h cloud OK")
    elif h5_mode:
        add_row(rows, "daily_watchlist.csv", "INFO", "watchlist absente localement en mode H-5")
    else:
        add_row(rows, "daily_watchlist.csv", "MANQUANT", "fichier absent")

    if course_9h_files > 0:
        add_row(rows, "course_9h_clean.csv", "OK", f"{course_9h_files} fichier(s)")
    elif h5_context_is_ok:
        add_row(rows, "course_9h_clean.csv", "INFO", "absent localement mais correspondance 9h cloud OK")
    elif h5_mode and h5_outputs_complete:
        add_row(rows, "course_9h_clean.csv", "INFO", "absent localement mais sorties H-5 complètes")
    else:
        add_row(rows, "course_9h_clean.csv", "MANQUANT", "0 fichier(s)")

    if h5_mode:
        add_row(
            rows,
            "course_h5_clean.csv",
            "OK" if course_h5_files > 0 else "MANQUANT",
            f"{course_h5_files} fichier(s)"
        )

        add_row(
            rows,
            "h5_update.csv",
            "OK" if h5_update_files > 0 else "MANQUANT",
            f"{h5_update_files} fichier(s)"
        )

        add_row(
            rows,
            "decision_lite.csv",
            "OK" if decision_files > 0 else "MANQUANT",
            f"{decision_files} fichier(s)"
        )

        add_row(
            rows,
            "prompt_gpt.txt",
            "OK" if prompt_files > 0 else "MANQUANT",
            f"{prompt_files} fichier(s)"
        )
    else:
        add_row(rows, "course_h5_clean.csv", "INFO", f"{course_h5_files} fichier(s)")
        add_row(rows, "h5_update.csv", "INFO", f"{h5_update_files} fichier(s)")
        add_row(rows, "decision_lite.csv", "INFO", f"{decision_files} fichier(s)")
        add_row(rows, "prompt_gpt.txt", "INFO", f"{prompt_files} fichier(s)")


    paper_path = OUTPUT_DIR / "paper_trading_50_decisions.csv"

    if paper_path.exists():
        n, error = read_csv_len(paper_path)

        if error:
            add_row(rows, "paper_trading_50_decisions.csv", "INFO", f"illisible : {error}")
            add_row(rows, "paper_trading_ready_50", "INFO", "non, fichier illisible")
        else:
            add_row(rows, "paper_trading_50_decisions.csv", "OK", f"{n} ligne(s)")

            if n >= 50:
                add_row(rows, "paper_trading_ready_50", "OK", f"{n}/50 decisions")
            else:
                add_row(rows, "paper_trading_ready_50", "INFO", f"{n}/50 decisions")
    else:
        add_row(rows, "paper_trading_50_decisions.csv", "INFO", "fichier absent")
        add_row(rows, "paper_trading_ready_50", "INFO", "0/50 decisions")

    result_input = OUTPUT_DIR / "result_input.csv"

    if result_input.exists():
        n, error = read_csv_len(result_input)
        if error:
            add_row(rows, "result_input.csv", "INFO", f"illisible : {error}")
        else:
            add_row(rows, "result_input.csv", "OK", f"{n} ligne(s)")
    else:
        add_row(rows, "result_input.csv", "INFO", "fichier absent")

    audit_files = count_files("data/output/*audit*.csv") + count_files("data/output/journal_audit.csv")

    if audit_files > 0:
        add_row(rows, "audit", "OK", f"{audit_files} fichier(s)")
    else:
        add_row(rows, "audit", "INFO", "pas encore d'audit final")

    df = pd.DataFrame(rows)

    output_path = OUTPUT_DIR / "quality_check.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False, encoding="utf-8")

    print("")
    print("GPI-Lite Quality Check")
    print("Mode : controle des sorties, PDF sources ignores")
    print("")
    print(df.to_string(index=False))
    print("")
    print("Rapport qualite cree")
    print(output_path)

    blocking = df[df["statut"] == "MANQUANT"]

    if blocking.empty:
        print("")
        print("STATUT GLOBAL : OK POUR CONTINUER")
    else:
        print("")
        print("STATUT GLOBAL : INCOMPLET")
        print("")
        print("Elements bloquants :")
        print(blocking.to_string(index=False))


if __name__ == "__main__":
    main()
