import pandas as pd


def _to_float(value):
    try:
        if value is None:
            return None
        if pd.isna(value):
            return None
        text = str(value).strip().replace(",", ".")
        if text in ["", "None", "nan", "NaN", "ND"]:
            return None
        return float(text)
    except Exception:
        return None


def _is_true(value):
    return str(value).strip().lower() in ["1", "true", "oui", "yes", "np", "non-partant"]


def _is_foreign_course(course_uid):
    text = str(course_uid).upper()

    foreign_markers = [
        "ABY",
        "BATH",
        "CURRAGH",
        "MONS",
        "HOPPEGARTEN",
        "CONCEPCION",
        "CHILE",
        "SUÈDE",
        "SUEDE",
        "BELGIQUE",
        "ALLEMAGNE",
        "ANGLETERRE",
        "IRLANDE",
    ]

    return any(marker in text for marker in foreign_markers)


def build_watchlist_row(df, course_uid):
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    required = ["numero", "cheval", "SG"]
    for col in required:
        if col not in df.columns:
            raise KeyError(col)

    if "statut" not in df.columns:
        df["statut"] = "PARTANT"

    if "is_np" not in df.columns:
        df["is_np"] = 0

    df["SG_num"] = df["SG"].apply(_to_float)
    df["numero_str"] = df["numero"].astype(str).str.strip()
    df["cheval_str"] = df["cheval"].astype(str).str.strip()
    df["is_np_bool"] = df["is_np"].apply(_is_true)
    df["statut_str"] = df["statut"].astype(str).str.upper().str.strip()

    partants_declares = len(df)

    partants_df = df[
        (df["is_np_bool"] == False)
        & (~df["statut_str"].isin(["NP", "NON_PARTANT", "NON-PARTANT"]))
    ].copy()

    partants_reels = len(partants_df)

    sg_valides = partants_df["SG_num"].notna().sum()
    sg_ratio = sg_valides / partants_reels if partants_reels else 0

    top_sg = partants_df.dropna(subset=["SG_num"]).sort_values("SG_num", ascending=True)
    top6 = top_sg.head(6)
    top6_sg_9h = ",".join(top6["numero_str"].tolist())

    anomalies = []
    if len(top_sg) >= 12:
        anomalies_df = top_sg.iloc[6:12]
        anomalies = [
            f"{row.numero_str} {row.cheval_str} SG {row.SG_num:g}"
            for row in anomalies_df.itertuples()
            if row.SG_num <= 16
        ]

    anomalies_7_12 = " | ".join(anomalies)

    favori = ""
    favori_fragile = "ND"

    if not top_sg.empty:
        fav = top_sg.iloc[0]
        favori = f"{fav['numero_str']} {fav['cheval_str']} SG {fav['SG_num']:g}"
        fav_sg = fav["SG_num"]

        if fav_sg is None:
            favori_fragile = "ND"
        elif fav_sg < 1.8:
            favori_fragile = "oui"
        elif fav_sg > 6:
            favori_fragile = "oui"
        else:
            favori_fragile = "non"

    sp_values = ""
    sp_count = 0
    sp_ratio = 0
    sp_status_morning = "NON_ATTENDU"

    if "SP_min" in df.columns and "SP_max" in df.columns:
        sp_rows = []

        for row in partants_df.itertuples():
            sp_min = _to_float(getattr(row, "SP_min", None))
            sp_max = _to_float(getattr(row, "SP_max", None))

            if sp_min is not None or sp_max is not None:
                sp_count += 1
                sp_rows.append(
                    f"{row.numero_str}:{sp_min if sp_min is not None else 'ND'}-{sp_max if sp_max is not None else 'ND'}"
                )

        sp_values = " | ".join(sp_rows)

    sp_ratio = sp_count / partants_reels if partants_reels else 0

    if sp_count == 0:
        sp_status_morning = "NON_ATTENDU"
    elif sp_ratio < 0.70:
        sp_status_morning = "PRESENT_PARTIEL"
    else:
        sp_status_morning = "PRESENT_EXPLOITABLE"

    cp_sec_candidat = ""
    if len(top_sg) >= 2:
        cp_sec_candidat = f"{top_sg.iloc[0]['numero_str']}-{top_sg.iloc[1]['numero_str']}"

    kill_switchs = []
    motifs = []

    if partants_reels < 8:
        kill_switchs.append("MOINS_DE_8_PARTANTS")
        motifs.append("moins de 8 partants réels")

    if partants_reels >= 16:
        motifs.append("champ large, variance élevée")

    if sg_ratio < 0.70:
        kill_switchs.append("SG_INCOMPLETES")
        motifs.append("cotes SG incomplètes")

    if not top6_sg_9h:
        kill_switchs.append("TOP6_SG_ABSENT")
        motifs.append("top 6 SG absent")

    if _is_foreign_course(course_uid):
        motifs.append("course étrangère, hors périmètre strict France")

    if favori_fragile == "oui":
        motifs.append("favori fragile ou sous-payé")

    sp_note_morning = (
        "SP non attendu en morning"
        if sp_status_morning == "NON_ATTENDU"
        else f"SP morning {sp_status_morning.lower()} ({sp_count}/{partants_reels})"
    )

    qualite_matin = "B"

    if kill_switchs:
        qualite_matin = "D"
    elif partants_reels < 10 or partants_reels >= 16:
        qualite_matin = "C"
    elif sg_ratio >= 0.90 and 8 <= partants_reels <= 15:
        qualite_matin = "B"

    score_watchlist = 0

    if partants_reels >= 8:
        score_watchlist += 1

    if 10 <= partants_reels <= 14:
        score_watchlist += 1

    if sg_ratio >= 0.90:
        score_watchlist += 1

    if favori_fragile == "non":
        score_watchlist += 1

    cp3_statut = "SIMULATION CP. Rapport CP pré-course indisponible. Étude basée sur SG ZEturf. Somme SG paire > 6 requise. CP_SEC prioritaire avant CP3"
    structure_max = "CP_SEC_SIMULATION"
    large_field = partants_reels >= 15

    if kill_switchs:
        decision_matin = "NO_BET_DIRECT"
        statut_matin = "NO_BET_DIRECT"
        priorite_h5 = "P9"
        h5_obligatoire = "non"
        niveau_etude = "refus"
        no_bet_reason = ", ".join(kill_switchs)
        structure_max = "NO_BET"

    elif _is_foreign_course(course_uid):
        decision_matin = "SIMULATION"
        statut_matin = "SIMULATION"
        priorite_h5 = "P7"
        h5_obligatoire = "non"
        niveau_etude = "hors périmètre strict"
        no_bet_reason = "course étrangère, aucun ticket strict"
        structure_max = "SIMULATION"

    elif score_watchlist >= 4 and qualite_matin == "B" and not large_field:
        decision_matin = "A_ETUDIER"
        statut_matin = "A_ETUDIER"
        priorite_h5 = "P1"
        h5_obligatoire = "oui"
        niveau_etude = "étude prioritaire"
        no_bet_reason = ""
        structure_max = "CP_SEC_ETUDE"

    elif score_watchlist >= 3:
        decision_matin = "SURVEILLER_H5"
        statut_matin = "A_REVOIR_H5"
        priorite_h5 = "P2"
        h5_obligatoire = "oui"
        niveau_etude = "surveillance H-5"
        no_bet_reason = ""
        structure_max = "CP_SEC_SIMULATION"

    elif score_watchlist == 2:
        decision_matin = "SIMULATION"
        statut_matin = "SIMULATION"
        priorite_h5 = "P5"
        h5_obligatoire = "optionnel"
        niveau_etude = "simulation uniquement"
        no_bet_reason = "score watchlist insuffisant"
        structure_max = "SIMULATION"

    else:
        decision_matin = "NO_BET_DIRECT"
        statut_matin = "NO_BET_DIRECT"
        priorite_h5 = "P9"
        h5_obligatoire = "non"
        niveau_etude = "refus"
        no_bet_reason = "score watchlist trop faible"
        structure_max = "NO_BET"

    if decision_matin == "A_ETUDIER":
        tri_watchlist = 1
    elif decision_matin == "SURVEILLER_H5":
        tri_watchlist = 2
    elif decision_matin == "SIMULATION":
        tri_watchlist = 5
    else:
        tri_watchlist = 9

    kill_switchs_matin = ",".join(kill_switchs) if kill_switchs else "aucun"
    motifs_filtrage = " | ".join(motifs) if motifs else "aucun signal bloquant"

    return {
        "course_uid": course_uid,
        "partants_declares": partants_declares,
        "partants_reels": partants_reels,
        "qualite_matin": qualite_matin,
        "statut_matin": statut_matin,
        "decision_matin": decision_matin,
        "tri_watchlist": tri_watchlist,
        "score_watchlist": score_watchlist,
        "sp_count": sp_count,
        "sp_ratio": round(sp_ratio, 2),
        "sp_status_morning": sp_status_morning,
        "sp_note_morning": sp_note_morning,
        "niveau_etude": niveau_etude,
        "top6_sg_9h": top6_sg_9h,
        "anomalies_7_12": anomalies_7_12,
        "favori": favori,
        "favori_fragile": favori_fragile,
        "sp_values": sp_values,
        "cp_sec_candidat": cp_sec_candidat,
        "cp3_statut": cp3_statut,
        "kill_switchs_matin": kill_switchs_matin,
        "motifs_filtrage": motifs_filtrage,
        "a_revoir_h5": "oui" if h5_obligatoire == "oui" else "non",
        "h5_obligatoire": h5_obligatoire,
        "structure_max": structure_max,
        "priorite_h5": priorite_h5,
        "priorite_h5_reason": motifs_filtrage,
        "no_bet_reason": no_bet_reason,
    }
