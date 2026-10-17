# GPI LITE — Seven synthetic days, campaign candidate (2026-10-17)

**Scope: OFFLINE_SYNTHETIC_DEVELOPMENT_ONLY.** Not prospective, not live odds, not a daily production scheduler, not real Android remote access, not a ROI assessment, and never a real bet.

## Technical contract

The campaign loops across seven *fictional logical dates*, 2030-01-02 through 2030-01-08, and creates two fictitious race decisions per day. Each day's program, 09H / H-30 / H-5 snapshots, decisions, and manifest must agree on the logical date. Outcomes cannot be used to create or alter the decision. All decisions must be `NO_BET`, and all stakes are 0 EUR.

The daily business pipeline remains in `tools/synthetic_daily_pipeline.py` **unchanged**. Every actual day is run in a fresh Python child process. The child modifies only the daily module's `DAY` fixture constant for this process, then invokes the existing `run(...)` function and the existing GPI engines. Each child first installs seccomp (blocking socket/syscalls, including AF_UNIX); inability to install seccomp is fatal. No external network, Cloud, data collection, production side effects, or wagers.

The orchestration is **not** a timed scheduler. The seven dates are executed sequentially during a local one-shot test; this does **not** prove that an autonomous daily trigger works on seven real days.

## Source and environment gate (CLI)

Supply an exact complete Git checkout and exact source commit. The gate requires HEAD equals the expected commit and independently compares every tracked regular file's Git blob SHA1 and executable mode with actual bytes/mode on disk. Extra ignored/untracked files, symlinks, absent files or changes hidden by `assume-unchanged` are rejected. No source update or Git write operation is performed. Checkout must contain existing authentic engine modules.

The output root MUST be outside the checkout and must use a `synthetic_` prefix. The tool rejects operational destination paths, symlink parents, overwrites and traversal `..`.

```bash
# PREPARATION: dependencies installed separately, from an approved offline wheelhouse.
# NO Git fetch, package install or external access occurs in this invocation.
cd /EXACT/READ_ONLY/CHECKOUT
python -m pytest -q -o addopts= \
  tests/test_synthetic_daily_pipeline.py \
  tests/test_synthetic_daily_hardening.py \
  tests/test_synthetic_week.py

# ONE SHOT: 7 fictional dates, with seccomp in each child.
python -m tools.synthetic_week \
  --checkout /EXACT/READ_ONLY/CHECKOUT \
  --expected-commit ACTUAL_40_CHAR_SHA_FROM_THIS_CHECKOUT \
  --outdir /ISOLATED/OUTPUT/synthetic_week

# AFTER INTERRUPTION ONLY: verifies all previously committed days.
python -m tools.synthetic_week \
  --checkout /EXACT/READ_ONLY/CHECKOUT \
  --expected-commit ACTUAL_40_CHAR_SHA_FROM_THIS_CHECKOUT \
  --outdir /ISOLATED/OUTPUT/synthetic_week --resume
```

The command invoking the 25 historical tests must also be run behind seccomp when claiming full offline test qualification. Running pytest without seccomp does NOT prove kernel-level network isolation.

## Failure / resume contract

A `source_lock.json` written before the first day pins the campaign to the source commit. Each day's 10-file bundle is written to a separate `_pending` staging directory, validated using its manifest and domain invariants, then atomically promoted to the final day's directory. A failed child never creates a successful final day's directory. An abandoned staging directory is not erased automatically; resumption refuses it. A previously completed day is *not* executed twice. Tampered completed days, wrong source commit or partial final index all fail closed. The dashboard and `week_index.json` completion marker are written only after all 7 days validate.

`verify_week(root)` tests all 7 daily manifests, provenance, day dates, NO_BETs and stakes, SHA256 references in the immutable completion index, and byte identity of the local HTML index. It does not cryptographically authenticate the index to an external signer.

## Evidence distinctions

1. `tests/test_synthetic_week.py`: stub emitter tests are **FAKE_ENGINE_ONLY**, testing week orchestration, fault injection, idempotence, manifest tampering, source-lock and Git preflight. They are not tests of the 25 original engine/business cases.
2. `tools/synthetic_week_offline_exec.py`: seccomp kernel socket denial must be observed and recorded on the executing host.
3. Full integrated PASS requires a complete authentically checked out source tree, the original 25/25 tests actually passing **and** all seven child runs passing under seccomp, with full logs, environment package provenance and file-level hashes.
4. The installed pytest environment and all required Python distributions must be separately qualified; provenance of the user-provided reconditioned wheels remains **NOT_PROVEN** without additional evidence.

### Known limits / no governance promotion

A seven-date accelerated replay cannot prove true autonomous scheduling, actual data collection, source freshness, Android access, economic edge or ROI. It must not feed live calibration or any TEST_LIVE or real-bet KPI. A successful local synthetic demo does not alter Phase C or retrospective OOS blocks.

`STRICT_ALLOWED=FALSE`

`REAL_BET_ALLOWED=FALSE`

`NO_BET=ACTIVE`

`END_TO_END_PROSPECTIVE=NOT_PROVEN`

`GOVERNANCE_TRANSITION=NONE`
