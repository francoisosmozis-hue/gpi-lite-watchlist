# CP3 TOP3 H5 PAPER LIVE V0_1_4
## Statut
Segment : CP3_TOP3_ETUDE_H5_PAPER  
Mode : PAPER LIVE uniquement  
Ticket réel : NO_BET  
Strict allowed : NON  
## Origine du segment
Le segment CP3_TOP3_ETUDE_H5 a été identifié par rétro-audit V0_1_4.
Résultat conservateur H5 :
- Décisions : 44
- Résolues : 38
- ND : 6
- Hits : 23
- Mise totale : 114 €
- Retour total : 148 €
- Gain net : +34 €
- ROI rétro : +29,82 %
Ce résultat ne suffit pas pour ouvrir le jeu réel.
## Règle mère
Rétro-audit != paper live != réel.
Les anciennes courses historiques ne doivent jamais être injectées dans le journal live.
Le journal live ne reçoit que des décisions prises avant course.
## Fichiers
### Builder paper rétro
data/output/cp3_top3_h5_paper_v014.csv
Usage :
- construit les candidats historiques H5 ;
- sert au contrôle du modèle ;
- ne compte pas dans le ROI live.
### Journal live
data/output/paper_cp3_top3_h5_live_log.csv
Usage :
- reçoit uniquement les décisions paper prises avant course ;
- chaque ligne doit avoir decision_before_race = OUI ;
- ticket_real doit rester NO_BET.
### Synthèse live
data/output/paper_cp3_top3_h5_live_summary.csv
Usage :
- calcule le ROI live ;
- ne compte que les lignes RESOLVED ;
- conserve ticket_real = NO_BET.
## Scripts
### Initialiser le journal live
python .\tools\init_paper_cp3_top3_h5_live_log_v014.py
### Ajouter une décision live avant course
python .\tools\append_cp3_top3_h5_live_decision_v014.py `
  --course-uid "DATE - HIPPODROME - COURSE" `
  --top3 "10-4-13" `
  --source-type "H5" `
  --confirm-before-race OUI `
  --notes "Decision H5 avant course"
Conditions :
- source_type = H5
- top3 valide
- décision prise avant course
- résultat non connu
- ticket réel = NO_BET
### Valider le journal live
python .\tools\validate_paper_cp3_top3_h5_live_log_v014.py
### Résoudre une décision après course
python .\tools\resolve_cp3_top3_h5_live_result_v014.py `
  --course-uid "DATE - HIPPODROME - COURSE" `
  --arrival-top3 "11-7-3" `
  --cp-place-reports "3-11:4.8;3-7:3.5;7-11:4.3" `
  --notes "Resultat officiel ZEturf renseigne apres course"
Conditions :
- la décision doit déjà exister dans le journal live ;
- result_status doit être PENDING ;
- decision_before_race doit être OUI ;
- result_known_at_decision doit être NON ;
- maximum 3 rapports CP fournis ;
- aucune ligne ZE 2 sur 4 ne doit être utilisée.
### Résumer le ROI live
python .\tools\summarize_paper_cp3_top3_h5_live_roi_v014.py
### Smoke test global
python .\tools\smoke_test_cp3_top3_h5_live_v014.py
## Gouvernance
Le segment CP3_TOP3_ETUDE_H5_PAPER reste séparé de :
- CP_SEC
- CP3 rétro
- SP
- 9H
- réel
Aucune décision paper ne devient un ticket réel automatiquement.
## Statut actuel
ROI rétro : +29,82 %  
ROI live : 0 décision  
Ticket réel : NO_BET  
## Condition minimale avant discussion réelle
Avant toute discussion de passage réel :
- minimum 30 à 50 décisions live paper ;
- decision_before_race = OUI sur chaque décision ;
- result_known_at_decision = NON ;
- aucun replay KO ;
- aucun mélange de segment ;
- drawdown acceptable ;
- ROI médian non négatif ;
- contrôle manuel des rapports CP ;
- validation stricte séparée.
## Conclusion
CP3_TOP3_ETUDE_H5_PAPER est un segment paper live candidat.
Il n’autorise aucun ticket réel.
Un pari GPI doit être fiable, rejouable, hors échantillon, calibré, couvert et segmenté. Sinon, NO BET.
