# nse-edge

Evidence-first research system for NSE intraday equities. **Research only. This code
places no orders and has no broker order code.**

It replaces the V1–V22 patch-by-patch workflow with one modular package, a fail-closed
2026 lock enforced in code, and a pre-registration registry so results can't be
retuned after the fact.

## Layout: one module per pipeline stage

```
config/settings.yaml          paths, DEV/VALIDATION splits, research_end lock, costs
hypotheses/*.yaml             pre-registered hypothesis families (immutable once run)
registry/runs.jsonl           append-only log of every family run + file hash + verdicts
src/nse_edge/
  config.py                   settings loader; refuses splits that cross research_end
  timelock.py                 enforce_lock / assert_locked (fail closed)
  io/loaders.py               stock bars, NIFTY 50, announcements (all IST, all locked)
  io/parquet_lite.py          pure-Python parquet fallback when pyarrow is missing
  data/panel.py               symbol-session panel + (N, 75) bar matrices, quarantine rules
  features/gaps.py            opening-gap event table with a written causality contract
  stats/robust.py             trimmed means, session-cluster bootstrap, concentration
  backtest/costs.py           round-trip cost hurdle (charges + slippage)
  research/registry.py        pre-registration hash check
  research/runner.py          runs a family -> reports/<family>_<ts>/ + verdicts
  cli.py                      python -m nse_edge {status,build,run}
tests/test_integrity.py       lock, quarantine, causality, stats, registry tests
```

Each stage has one job and a narrow contract (panel → events → hypothesis → verdict).
You can rewrite or extend a stage without touching the others, and the tests guard the
contracts between them.

## Setup (Mac)

The project expects to sit next to the old repo on your Desktop. It reads that repo's
data and never writes into it.

```bash
cd ~/Desktop/nse-edge
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q                         # must be green before trusting any run
python -m nse_edge status
python -m nse_edge build          # builds cache/ (≈20 s)
```

## Research workflow (the only allowed loop)

1. Write a **new** family YAML in `hypotheses/`: rationale, population, side, entry,
   pass rules. Commit it **before** running.
2. `python -m nse_edge run hypotheses/<family>.yaml`
3. Read `reports/<family>_<ts>/REPORT.md`. Verdicts are PASS / FAIL / INSUFFICIENT.
4. Don't edit a family after it has run. The registry refuses a changed hash. A new idea
   needs a new family file, and that file counts as another test (multiple-testing budget).
5. Only a family that PASSES in both DEV and VALIDATION goes on to portfolio simulation,
   then forward paper trading, then a single final-OOS (2026) check.

## Rules encoded in code

- Bars after `research_end` (2025-12-31) are dropped at load time and asserted absent
  in every output. There is no unlock flag.
- Bar timestamps are bar **starts**. A horizon uses the close of the last bar that has
  fully closed by the horizon.
- Announcements count only if broadcast before 09:15 on the trading day.
- Impossible-OHLC sessions and incomplete sessions are quarantined, never repaired.
- Verdicts are net of a ~21 bps round-trip cost (Zerodha MIS charges at ₹50k notional
  plus 5 bps slippage per side), with Bonferroni-adjusted session-cluster bootstrap CIs.

## Security

Credentials stay in the old repo's `.env`. This project doesn't read them. Rotate the
Kite key and token that were previously pasted into chat.
