# Audit ROI Study V0_1_4 - Parsing conservateur
## Périmètre
- Watchlist étude : 143 courses
- Résultats matchés : 78
- H5 : 44 résultats matchés sur 45
- 9H : 34 résultats matchés sur 98
- Ticket réel : NO_BET
## Corrections appliquées
### Simple Placé
Le premier audit surestimait les segments Simple Placé.
Cause :
- des lignes ZE 2 sur 4 étaient lues comme des rapports Simple Placé ;
- les rapports simples placés pouvaient être écrasés après le TRIO.
Correction :
- arrêt du parsing Simple Placé après passage aux lignes couplés ;
- lignes ZE 2 sur 4 ignorées pour SP.
### CP / CP3
Le contrôle CP3 a détecté des blocs suspects à 6 paires, typiques du ZE 2 sur 4.
Correction :
- si plus de 3 paires CP sont détectées dans le bloc exploité, le bloc est considéré ambigu ;
- CP / CP3 passent en ND ;
- aucun rapport ZE 2 sur 4 ne doit alimenter CP_TOP2 ou CP3_TOP3.
## Résultats corrigés H5
### CP3_TOP3_ETUDE_H5
- Décisions : 44
- Résolues : 38
- ND : 6
- Hits : 23
- Mise totale : 114 €
- Retour total : 148 €
- Gain net : +34 €
- ROI : +29,82 %
### SP_TOP1_ETUDE_H5
- Décisions : 44
- Résolues : 41
- ND : 3
- Hits : 19
- Mise totale : 41 €
- Retour total : 34,10 €
- Gain net : -6,90 €
- ROI : -16,83 %
### SP_OUTSIDER_ETUDE_H5
- Décisions : 42
- Résolues : 40
- ND : 2
- Hits : 11
- Mise totale : 40 €
- Retour total : 28 €
- Gain net : -12 €
- ROI : -30,00 %
### CP_TOP2_ETUDE_H5
- Décisions : 44
- Résolues : 42
- ND : 2
- Hits : 8
- Mise totale : 42 €
- Retour total : 35,50 €
- Gain net : -6,50 €
- ROI : -15,48 %
## Décision
- CP3_TOP3_ETUDE_H5 devient le seul segment PAPER_CANDIDATE.
- SP_TOP1_ETUDE_H5 est rejeté.
- SP_OUTSIDER_ETUDE_H5 est rejeté.
- CP_TOP2_ETUDE_H5 est rejeté.
- 9H reste étude uniquement.
- Ticket réel reste NO_BET.
## Conditions avant paper live
- source_type = H5 uniquement
- top3_etude disponible
- décision avant course
- NP/statut propres
- volatilité non rouge
- mise paper = 3 €
- segment_roi = CP3_TOP3_ETUDE_H5_PAPER
- aucun mélange avec CP_SEC, SP, 9H ou réel
- ticket réel = NO_BET
## Conclusion
Le seul signal positif conservateur est CP3_TOP3_ETUDE_H5.
Statut : PAPER_CANDIDATE.
Réel : NO_BET.
