# Audit ROI Study V0_1_4 - Corrige apres parsing rapports
## Périmètre
- Watchlist étude : 143 courses
- Résultats matchés : 78
- H5 : 44 résultats matchés sur 45
- 9H : 34 résultats matchés sur 98
- Ticket réel : NO_BET
## Correction appliquée
Le premier audit ROI surestimait les segments Simple Placé.
Cause :
- le parseur lisait certaines lignes ZE 2 sur 4 comme des rapports Simple Placé ;
- les rapports simples placés étaient parfois écrasés par des valeurs postérieures au TRIO.
Correction :
- arrêt du parsing Simple Placé après passage aux lignes couplés ;
- arrêt du parsing CP après ligne TRIO ;
- lignes ZE 2 sur 4 ignorées pour SP et CP.
## Résultats corrigés H5
### CP3_TOP3_ETUDE_H5
- Décisions : 44
- Résolues : 40
- Hits : 25
- Mise totale : 120 €
- Retour total : 159 €
- Gain net : +39 €
- ROI : +32,50 %
### SP_TOP1_ETUDE_H5
- Décisions : 44
- Résolues : 41
- Hits : 19
- Mise totale : 41 €
- Retour total : 34,10 €
- Gain net : -6,90 €
- ROI : -16,83 %
### SP_OUTSIDER_ETUDE_H5
- Décisions : 42
- Résolues : 40
- Hits : 11
- Mise totale : 40 €
- Retour total : 28 €
- Gain net : -12 €
- ROI : -30,00 %
### CP_TOP2_ETUDE_H5
- Décisions : 44
- Résolues : 43
- Hits : 9
- Mise totale : 43 €
- Retour total : 38,20 €
- Gain net : -4,80 €
- ROI : -11,16 %
## Décision corrigée
- CP3_TOP3_ETUDE_H5 devient le seul segment PAPER candidat.
- SP_TOP1_ETUDE_H5 est rejeté après correction du parsing.
- SP_OUTSIDER_ETUDE_H5 est rejeté.
- CP_TOP2_ETUDE_H5 est rejeté.
- 9H reste étude uniquement.
- Ticket réel reste NO_BET.
## Conditions avant extension paper
Avant d'ouvrir une règle paper CP3_TOP3_ETUDE_H5 :
1. Contrôler manuellement les 15 plus gros retours CP3.
2. Vérifier que les rapports CP proviennent bien des lignes Jumelé/Couplé Placé.
3. Confirmer que les lignes ZE 2 sur 4 ne sont jamais utilisées.
4. Confirmer absence de fuite résultat dans les sélections H5.
5. Suivre le segment en live paper séparé.
6. Ne jamais mélanger avec CP_SEC, SP, 9H ou réel.
## Conclusion
La V0_1_4 reste utile comme moteur d'étude, mais le premier signal SP était un faux positif de parsing.
Le seul signal positif corrigé est CP3_TOP3_ETUDE_H5.
Statut : PAPER_CANDIDATE uniquement.
Réel : NO_BET.
