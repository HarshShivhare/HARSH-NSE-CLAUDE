# CLAUDE.md - working rules for this repo

Evidence-first NSE intraday/multi-day research. **Research only: never write order
placement code and never call broker order APIs** (a Kite MCP server may be connected;
do not use its place/modify/cancel tools).

## Commands
```bash
source .venv/bin/activate
pytest -q                                            # must pass before any run
python -m nse_edge status                            # lock + registry history
python -m nse_edge build --dataset gap_events|results_events
python -m nse_edge run hypotheses/<family>.yaml      # ONE-SHOT, see rules below
```
Data lives read-only in `../HARSH-NSE-TRADING-SYSTEM/data` (paths in `config/settings.yaml`).
Never write into that repo. Never read or print `.env`.

## Non-negotiable research rules
1. **2026 is locked.** `research_end` = 2025-12-31. Never weaken `timelock.py`, never add an
   unlock flag, never load 2026 bars for research.
2. **Pre-registration.** A hypothesis family YAML is written (and ideally git-committed)
   BEFORE it runs. After a run, never edit it: a changed idea = a new family file.
3. **Don't peek.** While building or reviewing a dataset, don't compute or print outcome
   statistics (forward/holding returns by population, win rates). Counts, dates and the
   signal inputs are fine.
4. **One-shot runs.** The registry refuses reruns. A rerun needs `--rerun-reason` and is
   only for a reviewed bug fix or a reproducibility check.
5. **Independent review before every run.** Use a separate subagent that didn't write the
   code to audit causality/leakage, with the same don't-peek rule.
6. **No rescue tuning.** A FAIL is a result. Never lower cutoffs, change horizons or pick
   subsets after seeing a report.

## Conventions
- Bar timestamps are bar STARTS (IST). A horizon uses the close of the last bar fully closed by
  it. Slot k = bar starting 09:15 + 5k min (0..74).
- Quarantine, never repair: impossible OHLC / incomplete / special sessions are excluded.
- Every result goes in `docs/RESEARCH_LOG.md` with its report folder and registry entry.

## Current state (2026-10-22)
- V23 `gap_reaction_v1`: run, ALL FAIL (see docs/RESEARCH_LOG.md).
- V24 `results_drift_v1`: written, **NOT run**. Independent review found BLOCKERS, listed in
  `docs/REVIEW_V24.md`. They must be fixed (and the family revised before its first run)
  before running.
