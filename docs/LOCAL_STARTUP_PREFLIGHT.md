# GPI LITE — Pré-démarrage local sans réseau

**Périmètre** : analyse statique du code de l'API et de ses imports internes.
**Ne réalise pas** : import de `api_main`, installation de paquets, appel HTTP, collecte hippique, accès GCP, modification d'une configuration ni transition de gouvernance.

## Commandes locales

Dans un checkout complet de cette branche :

```bash
python tools/offline_startup_preflight.py --root .
python -m pytest -q tests/test_offline_startup_preflight.py tests/test_results_main_offline.py
```

Le rapport `PASS_STATIC` signifie exclusivement que la syntaxe du graphe d'imports locaux atteignable depuis `api_main.py`, les chemins des modules et les déclarations statiques de routes n'ont pas de blocages détectés. `BLOCKED_STATIC` empêche de passer à un essai de démarrage. Les warnings de distributions manquantes ou non figées restent à traiter pour une installation reproductible.

**Limite** : l'analyse syntaxique n'est pas une qualification de l'initialisation FastAPI. Le test d'import complet, un véritable `GET /health` local, un essai métier et les accès réseau/GCP exigent une procédure séparée avec environnement et permissions qualifiés. Ce script ne fournit aucune autorisation d'exécution de collecte réelle.

État permanent : `STRICT_ALLOWED = FALSE`, `REAL_BET_ALLOWED = FALSE`, `NO_BET = ACTIVE`, `GOVERNANCE_TRANSITION = NONE`.
