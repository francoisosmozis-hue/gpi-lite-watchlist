from pathlib import Path


def export_csv(df, output_name):
    output_dir = Path("data/output")
    output_dir.mkdir(parents=True, exist_ok=True)

    out = output_dir / output_name
    df.to_csv(out, index=False, encoding="utf-8-sig")

    return out
