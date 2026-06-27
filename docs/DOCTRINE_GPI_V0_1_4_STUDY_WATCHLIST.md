# Doctrine GPI Lite Watchlist V0_1_4 - Study Watchlist Systematique
## Changement principal
GPI Lite Watchlist ne sert plus seulement a dire NO_BET.
Il doit produire systematiquement une watchlist etude pour chaque course analysee.
## Sorties obligatoires
Pour chaque course :
1. PRONOSTIC_ETUDE
   - top3_etude
   - outsider_etude
   - commentaire_etude
   - segment_roi = STUDY_TOP3 / STUDY_OUTSIDER
2. TICKET_PAPIER
   - CP_SEC_PAPER si seuil >= 75
   - SP_PAPER seulement si calibration SP utilisable
   - CP3_PAPER seulement audit, jamais par defaut
   - sinon NO_BET
3. TICKET_REEL
   - toujours NO_BET tant que validation stricte absente
## Regle fondamentale
NO_BET reel ne signifie plus absence de pronostic.
NO_BET reel signifie :
- pas de ticket argent reel
- mais production d'une watchlist etude obligatoire
## ROI
Les ROI doivent etre segmentes :
- ROI_SP
- ROI_CP_SEC
- ROI_CP3
- ROI_STUDY_TOP3
- ROI_STUDY_OUTSIDER
- ROI_SIMULATION_LOW_RETRO
Aucun melange entre les segments.
## Donnees requises pour ROI
Un ROI est calculable uniquement si :
- selection pre-course disponible
- rapport ZEturf disponible
- arrivee disponible
- course_uid fiable
- mise theorique connue
Sinon :
- rapport = ND
- ROI = ND
## Ticket reel
Aucun ticket reel sans validation stricte :
- moteur rejouable
- split valide
- calibration OK
- leakage_check_ok
- decision_before_race = oui
- rapports/proxy audites
- volatilite acceptable
- ROI segment positif sur echantillon suffisant
