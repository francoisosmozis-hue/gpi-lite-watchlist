PACK SOURCE PROJET ANALYSE HIPPIQUE GPI - V19.1.4 / 2026-05-23
A INTEGRER APRES SUPPRESSION DES ANCIENNES SOURCES

DECISION V19.1.4
Ce pack V19.1.4 remplace les sources actives V19.1.3.
Il ne complete pas V19.1.3. Il le corrige et le verrouille autour de trois ajouts bloquants :
1. moteur de decision reproductible ;
2. validation hors echantillon ;
3. calibration SP independante.

Objectif :
decision ROI auditee, pre-course, ZEturf France, centree sur CP_SEC, CP3 rare et SP calibre, avec NO BET comme decision normale.

CORRECTIONS PRIORITAIRES VS V19.1.3
1. decision_engine_outputs.csv devient obligatoire pour rendre les decisions rejouables.
2. input_hash + engine_version + ruleset_version doivent produire le meme verdict.
3. train_test_split.csv devient bloquant : TRAIN calibre, VALIDATION controle, TEST_LIVE audite.
4. leakage_check_ok = false exclut la ligne du strict.
5. proxy_error_by_segment.csv reste obligatoire avant utilisation intensive CP/2sur4.
6. CP_SEC vs CP3 reste obligatoire avant tout CP3 strict.
7. SP standard exige sp_calibration_segments.csv avec segment_SP UTILISABLE.
8. ROI strictement segmente. ROI global interdit comme preuve.
9. proba_basse doit etre calibree par score, segment et resultat reel.
10. historique utilisateur separe des donnees publiques de calibration.
11. exotiques hors MVP, sauf exception documentee apres CP/SP robuste.
12. replay_match doit etre controle en audit.

ORDRE D'INTEGRATION V19.1.4
1. Document_11_Base_Historique_ZEturf_Calibration_Coefficients_GPI_V5_1_4_SOURCE.txt
2. Document_10_Formules_Proxy_CP_SG_ZEturf_GPI_V5_1_4_SOURCE.txt
3. Document_3_Tracker_Audit_Gouvernance_GPI_V19_1_4_SOURCE.txt
4. Document_0_Workflow_Operationnel_H5_GPI_ZEturf_V19_1_4_SOURCE.txt
5. Document_2_Matrice_ROI_Paris_ZEturf_GPI_V19_1_4_SOURCE.txt
6. Document_4_Historique_ZEturf_Seed_Reduit_GPI_V19_1_4_SOURCE.txt
7. Document_1_Prompt_Maitre_GPI_ZEturf_V19_1_4_ACTIF_MAJ_2026-05-23.txt
8. README_A_INTEGRER_SOURCE_PROJET_GPI_V19_1_4_2026-05-23.txt
9. manifest_GPI_V19_1_4.json

SOURCE DE VERITE PAR DOMAINE
- Comportement de l'analyste : Document 1.
- Workflow decisionnel rapide : Document 0.
- ROI, structures, seuils, exotiques, cout marginal : Document 2.
- Audit, tracker, niveaux de preuve, anti-overfitting : Document 3.
- Lecons anciennes non doctrinales : Document 4.
- Formules, P_event_market, C_prudent, R_prudent, moteur calculable : Document 10.
- Base CSV + SQLite, calibration, split, sorties moteur : Document 11.

DONNEES A CONSERVER HORS PROMPT
Ces fichiers sont des donnees de calibration, pas des instructions conversationnelles :
- calibration_segments.csv
- proxy_error_by_segment.csv
- backtest_cp_sec_vs_cp3.csv
- backtest_cp_sec_vs_cp3_excluded.csv
- backtest_decisions.csv
- backtest_kpi.csv
- paper_trading_50_decisions.csv
- odds_movement_snapshots.csv
- proba_basse_calibration.csv
- train_test_split.csv
- decision_engine_outputs.csv
- events_sp.csv
- sp_calibration_segments.csv
- user_ticket_history.csv
- zeturf_calibration.sqlite
- run_metrics.json
- audit_calibration_cp.md
- Template_Base_Historique_Rapports_ZEturf_GPI_V2_0.xlsx
- Tracker_ROI_GPI_auto_completable_MAJ_2026-05-17_CORRIGE.xlsx

DOCUMENTS A SUPPRIMER / NE PAS REINTEGRER
- anciens prompts V14 a V19.1.3 si doublon ;
- anciennes doctrines CP3 separees ;
- modules Monte Carlo comme source active ;
- modules Doc8 separes si leur contenu est deja condense ;
- captures, previews, conversations brutes, regrets post-course ;
- exports Excel ou Sheets comme source de verite ;
- documents commerciaux PMU non audites ;
- toute regle martingale, rattrapage, bonus, presse seule, top driver seul, deferrage seul ;
- tout document qui impose rapport CP visible comme condition bloquante ;
- tout document qui transforme ZE4, Trio, Z5, Ordre, ZE234, ZE345 ou CHANCE en pari standard ;
- tout dashboard ou tracker qui presente un ROI global non segmente ;
- tout fichier qui utilise l'historique utilisateur pour recalibrer les rapports publics.

REGLE MERE V19.1.4
Un pari n'est pas une prediction d'arrivee.
Il est defendable seulement si :
donnees fiables, SG ZEturf propres, NP/statut traites, moteur rejouable, split valide, leakage_check_ok, rapport visible ou proxy prudent audite, SP calibre si SP standard, proba_basse standardisee, CP_SEC vs CP3 teste, couverture reelle, variance acceptable, aucun kill-switch.
Sinon NO BET.

PRIORITE MVP V19.1.4
1. Generer et maintenir decision_engine_outputs.csv.
2. Generer et maintenir train_test_split.csv.
3. Controler proxy_error_by_segment.csv sur segments CP sample_n >=50.
4. Backtester CP_SEC vs CP3 sur segments CP comparables.
5. Alimenter paper_trading_50_decisions.csv.
6. Produire events_sp.csv et sp_calibration_segments.csv avant tout SP standard.
7. Segmenter STRICT, MICRO-SP, SP, CP_SEC, CP3, CP_PROXY_SG, 2SUR4_EXP, HORS_DOCTRINE, SIMULATION, PAPER_TRADING, NO_BET.
8. Mesurer drawdown, ratio median, ROI median, CP3 remboursants, taux de surestimation proxy, replay_fail_count et leakage_fail_count.
9. Interdire tout dashboard affichant un ROI global comme preuve.
10. Auditer l'ecart conseille vs joue sans l'utiliser pour calibrer les rapports.

CRITERES DE REFUS IMMEDIAT
- decision_engine_outputs absent ou incomplet ;
- input_hash absent ;
- ruleset_version absente ;
- split absent ;
- calibration_version absente ;
- leakage_check_ok = false ;
- decision_before_race_oui_non = non ;
- proxy ROUGE ou ND ;
- fallback non declare ;
- CP3 non compare au CP_SEC ;
- SP standard sans segment_SP UTILISABLE ;
- ROI simulation melange au ROI reel ;
- arrivee utilisee pour decider ;
- exotique sans CP/SP robuste ;
- dashboard global non segmente.

CONFORMITE
Le projet reste une aide a la decision.
Il ne doit jamais automatiser la prise de jeu, remplir un panier operateur, contourner ZEturf ou declencher un pari sans action humaine.
L'utilisateur reste seul decideur de toute mise reelle.

PHRASE SOURCE
Un pari GPI doit etre fiable, rejouable, hors echantillon, calibre, couvert et segmente. Sinon, NO BET.
