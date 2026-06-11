import pandas as pd


def compare_9h_h5(df_9h, df_h5):
    a = df_9h[["numero", "cheval", "SG", "SP_min", "SP_max", "statut", "is_np"]].copy()
    b = df_h5[["numero", "SG", "SP_min", "SP_max", "statut", "is_np"]].copy()

    a = a.rename(columns={
        "SG": "SG_9h",
        "SP_min": "SP_min_9h",
        "SP_max": "SP_max_9h",
        "statut": "statut_9h",
        "is_np": "is_np_9h",
    })

    b = b.rename(columns={
        "SG": "SG_H5",
        "SP_min": "SP_min_H5",
        "SP_max": "SP_max_H5",
        "statut": "statut_H5",
        "is_np": "is_np_H5",
    })

    merged = a.merge(b, on="numero", how="outer")

    merged["SG_9h"] = pd.to_numeric(merged["SG_9h"], errors="coerce")
    merged["SG_H5"] = pd.to_numeric(merged["SG_H5"], errors="coerce")

    merged["variation_pct"] = (
        (merged["SG_H5"] - merged["SG_9h"]).abs() / merged["SG_9h"]
    )

    def classify_variation(value):
        if pd.isna(value):
            return "ND"
        if value > 0.35:
            return "KILL_SWITCH"
        if value > 0.20:
            return "INSTABLE"
        if value > 0.10:
            return "SURVEILLABLE"
        return "STABLE"

    merged["statut_marche"] = merged["variation_pct"].apply(classify_variation)

    return merged
