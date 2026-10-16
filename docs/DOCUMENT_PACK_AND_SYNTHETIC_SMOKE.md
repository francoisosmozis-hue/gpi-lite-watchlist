# GPI LITE — Réconciliation documentaire et essai métier synthétique (hors réseau)

Date : 16 octobre 2026. Branche isolée \`dev/gpi-lite-local-startup-20261016\`.

## Changement documentaire

\`/gpi/ruleset\` lit maintenant les neuf fichiers suivis dans \`project_sources/GPI_ZEturf_V19_1_4\` au lieu du dossier inexistant \`sources/docs\`. La liste des neuf comprend les huit documents recensés par le manifeste et le manifeste lui-même. La décision de qualification utilise \`engine.source_pack.build_source_pack_status()\` et refuse l'intégrité si une identité de document, un SHA256 ou une taille attendue ne concorde pas.

**Le statut \`strict_ready\` de \`/gpi/ruleset\` n'est qu'un prérequis local**, jamais une autorisation globale de pari. Il demeure faux dans le checkout observé, car les fichiers obligatoires \`sp_calibration_segments.csv\`, \`train_test_split.csv\` et \`decision_engine_outputs.csv\` restent absents de \`sources/calibration\`. Les quatre compléments absents ne sont pas fabriqués non plus.

## Contrôles locaux

Dans un checkout complet du commit contenant ces changements :

\`\`\`bash
python -m pytest -q tests/test_ruleset_docs_reconciliation.py
python -m pytest -q tests/test_synthetic_business_smoke.py
python tools/synthetic_business_smoke.py
\`\`\`

\`test_ruleset_docs_reconciliation.py\` compile et exécute uniquement les deux fonctions source \`list_existing_files\` et \`build_ruleset_status\` par extraction AST. Il injecte des états manifestes simulés et de fausses arborescences de test dans un répertoire temporaire. Il ne vérifie pas indépendamment les octets du vrai pack.

\`synthetic_business_smoke.py\` appelle deux vrais modules métier du dépôt, \`engine.gpi_lite.build_watchlist_row\` puis \`engine.h5_decision.evaluate_h5_decision_from_brief\`, sans import de \`api_main\`, sans HTTP, sans Cloud, sans fichiers de sortie, avec dix chevaux **FICTIFS** et des données volontairement non qualifiées. L'essai doit toujours aboutir à \`NO_BET\`. Il ne teste ni la collecte, ni les cotes authentiques, ni les PDF, ni H-30, ni la persistance, ni le règlement, ni le fonctionnement quotidien.

## Gates

\`\`\`text
STRICT_ALLOWED = FALSE
REAL_BET_ALLOWED = FALSE
NO_BET = ACTIVE
GCP_MUTATION_AUTHORIZED = FALSE
WEB_HIPPIQUE_COLLECTION_AUTHORIZED = FALSE
END_TO_END_PROSPECTIVE = NOT_PROVEN
GOVERNANCE_TRANSITION = NONE
\`\`\`

La preuve du démarrage Phase B demeure circonscrite au rapport fourni par l'utilisateur, sans archive ZIP Phase B vérifiable dans la présente session.
