# GPI LITE — premier cycle quotidien intégral **synthétique**

Version : `SYNTHETIC_DAILY_V1` ; base GitHub historique de l'implantation : `13f6fd38f5fd0b71a4823a45d310bcc0ad735b3e`.

## Portée explicite

Un scénario fictif, entièrement hors réseau, représente deux courses imaginaires avec dix chevaux fictifs chacune. Il produit un programme, trois snapshots par course (`09H`, `H_MINUS_30`, `H_MINUS_5`), les mouvements des cotes SG, une watchlist, les décisions H-5, un règlement fictif sans mise, un fichier de suivi, un tableau HTML lisible sur mobile et un manifeste SHA256 des neuf livrables. Les heures sont des éléments **fixes de fixture**, pas des attestations de collecte avant départ.

Le pilote **réutilise les fonctions métier existantes** :

- `engine.gpi_lite.build_watchlist_row` (watchlist 09h),
- `engine.odds.odds_movement_builder.classify_market_status` et `.classify_direction` (variation SG),
- `engine.h5_decision.evaluate_h5_decision_from_brief` (décision H-5).

La comparaison à H-30 et H-5 est un traitement synthétique des snapshots : elle ne valide ni les branches automatiques H-30/H-5 de l'API ni la capture. La décision reçoit volontairement `decision_before_race_oui_non=non`, `np_ok=ND`, `statut_ok=ND`, `segment_roi=ND` et des kill-switches synthétiques. **Le moteur doit retourner `NO_BET`** ; sinon le programme lève une erreur avant écriture. Même les résultats fictifs connus du générateur n'entrent pas dans le moteur de décision. Aucune calibration n'est générée et aucun KPI réel n'est alimenté.

## Exécution dans le dépôt GPI

Depuis la racine d'un checkout complet du commit contenant ces fichiers, avec les dépendances Python déjà installées dans un environnement **isolé et sans réseau** :

```bash
python -m pytest -q -o addopts= tests/test_synthetic_daily_pipeline.py
python -m tools.synthetic_daily_pipeline --outdir data/synthetic_daily_demo
```

Le répertoire cible doit être absent et son nom commencer par `synthetic_`. Le script refuse les sorties vers les chemins opérationnels `data/output`, `data/morning`, `data/h5`, `data/results` et `sources/calibration`, les parents symlinks et tout écrasement. Les données résultantes sont immuables une fois écrites ; pour relancer, choisir un *nouveau* répertoire. `dashboard.html` peut s'ouvrir sur Android comme fichier local.

Le manifeste `audit_manifest.json` lie les neuf artefacts par empreintes SHA256 sans s'autoréférencer. `verify_bundle(Path(...))` permet de vérifier leurs octets. L'égalité bit à bit de deux exécutions est testée.

## Niveau de preuve (non franchissable)

Le prototype ne prouve **ni la collecte réelle, ni les 14 tests Phase C, ni le démarrage ASGI du commit courant, ni les H-30/H-5 opérationnels, ni l'exécution planifiée quotidienne, ni la livraison Android distante, ni un rendement financier**. Les données fictives et leurs règlements ne peuvent être utilisés comme preuve pré-course, paper trading ou étalonnage. Le test local de l'orchestrateur hors checkout exact, s'il emploie des *test doubles* de moteurs, ne qualifie pas le couplage avec les moteurs authentiques.

`STRICT_ALLOWED=FALSE ; REAL_BET_ALLOWED=FALSE ; NO_BET=ACTIVE ; END_TO_END_PROSPECTIVE=NOT_PROVEN ; GOVERNANCE_TRANSITION=NONE`.

## Corrections ciblées du 17 octobre 2026

Le vérificateur exige désormais les neuf noms d'artefacts, les métadonnées de scénario attendues et les drapeaux de gouvernance fermés. Il refuse les manifestes vides ou incomplets. Ce contrôle reste une vérification de structure et d'empreintes, sans authentification externe du manifeste.

Le pilote rejette tout composant de chemin `..`. Il confronte H-30 et H-5 au départ fictif et exige une arrivée de trois numéros entiers, présents et distincts. Ces validations ne constituent aucune preuve de collecte réelle.

Les six régressions complémentaires se trouvent dans `tests/test_synthetic_daily_hardening.py`. Elles sont compatibles avec unittest et pytest. Les 19 tests d'origine restent inchangés. Une campagne intégrée sur un environnement préalablement préparé, puis privé de réseau, utilise :

```bash
python -m pytest -q tests/test_synthetic_daily_pipeline.py tests/test_synthetic_daily_hardening.py
```

Le lancement de l'orchestrateur reste `python -m tools.synthetic_daily_pipeline --outdir DOSSIER_SYNTHETIC_NEUF`, depuis la racine du checkout. La préparation des dépendances est distincte des essais et ne modifie pas les moteurs métier.
