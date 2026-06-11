from pathlib import Path
import sys


def write_text(path: Path, suffix: str, text: str):
    out = path.with_name(path.stem + suffix)
    out.write_text(text or "", encoding="utf-8", errors="ignore")
    print(f"{out} | chars={len(text or '')}")


def extract_with_pymupdf(path: Path) -> str:
    try:
        import fitz
    except Exception as exc:
        return f"[IMPORT_ERROR pymupdf/fitz] {exc}"

    parts = []
    try:
        doc = fitz.open(str(path))
        for i, page in enumerate(doc, start=1):
            parts.append(f"\n===== PAGE {i} =====\n")
            parts.append(page.get_text("text") or "")
    except Exception as exc:
        return f"[EXTRACT_ERROR pymupdf] {exc}"

    return "\n".join(parts)


def extract_with_pypdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except Exception as exc:
        return f"[IMPORT_ERROR pypdf] {exc}"

    parts = []
    try:
        reader = PdfReader(str(path))
        for i, page in enumerate(reader.pages, start=1):
            parts.append(f"\n===== PAGE {i} =====\n")
            parts.append(page.extract_text() or "")
    except Exception as exc:
        return f"[EXTRACT_ERROR pypdf] {exc}"

    return "\n".join(parts)


def extract_with_pdfplumber(path: Path) -> str:
    try:
        import pdfplumber
    except Exception as exc:
        return f"[IMPORT_ERROR pdfplumber] {exc}"

    parts = []
    try:
        with pdfplumber.open(str(path)) as pdf:
            for i, page in enumerate(pdf.pages, start=1):
                parts.append(f"\n===== PAGE {i} TEXT =====\n")
                parts.append(page.extract_text() or "")

                parts.append(f"\n===== PAGE {i} TABLES =====\n")
                tables = page.extract_tables() or []
                for t_idx, table in enumerate(tables, start=1):
                    parts.append(f"\n--- TABLE {t_idx} ---\n")
                    for row in table:
                        parts.append(" | ".join("" if c is None else str(c) for c in row))
    except Exception as exc:
        return f"[EXTRACT_ERROR pdfplumber] {exc}"

    return "\n".join(parts)


def main():
    if len(sys.argv) < 2:
        print("Usage: python tools/inspect_pdf_text.py fichier1.pdf fichier2.pdf")
        raise SystemExit(1)

    for arg in sys.argv[1:]:
        path = Path(arg)

        if not path.exists():
            print(f"ABSENT: {path}")
            continue

        print(f"\nPDF: {path}")

        write_text(path, "__pymupdf.txt", extract_with_pymupdf(path))
        write_text(path, "__pypdf.txt", extract_with_pypdf(path))
        write_text(path, "__pdfplumber.txt", extract_with_pdfplumber(path))


if __name__ == "__main__":
    main()
