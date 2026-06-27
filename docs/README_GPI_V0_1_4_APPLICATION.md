# Passage GPI Lite Watchlist V0_1_4
## Doctrine
GPI Lite Watchlist ne sert plus seulement a dire NO_BET.
Il doit maintenant produire systematiquement :
- top3_etude
- outsider_etude
- CP_SEC_PAPER si seuil >= 75
- ticket reel = NO_BET tant que validation stricte absente
## Pourquoi
Le cout Cloud + ChatGPT rend inutile un systeme qui attend trop longtemps sans produire de signal exploitable.
La nouvelle doctrine transforme GPI Lite Watchlist en moteur d'apprentissage systematique.
## Donnees deja validees
Backtest Cloud purifie V0_1_3 :
- 205 decisions rejouees
- 188 NO_BET
- 17 CP_SEC_SIMULATION_LOW
- 0 CP_SEC_PAPER
Audit SIMULATION_LOW_RETRO :
- 17 cas
- 11 resultats disponibles
- 0 hit top3
- 6 ND
- seuil 75 conserve
## Regle maintenue
Aucun ticket reel sans validation stricte.
## Segments ROI
- STUDY_TOP3
- STUDY_OUTSIDER
- CP_SEC_PAPER
- SP_PAPER
- CP3_AUDIT
- SIMULATION_LOW_RETRO
- NO_BET
Aucun melange entre les segments.
