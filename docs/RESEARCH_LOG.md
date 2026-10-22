# Research log

Newest entry first. Each entry names its registry record and report folder.

---

## 2026-10-22 - gap_reaction_v1 (V23) - ALL FAIL

**Report:** `reports/gap_reaction_v1_20261022_090712/` · **Registry:** first line of `registry/runs.jsonl`
**Data:** 100 symbols, 52,845 eligible symbol-sessions (DEV Sep-2023 → Jun-2025, VALIDATION Jul → Dec-2025).
2026 locked. 869 sessions excluded because the announcement feed has holes
(late-Apr 2024/2025).
**Hurdle:** 20.6 bps round trip (Zerodha MIS charges at ₹50k notional + 5 bps slippage per side).
**Test:** large stock-specific gap (|stock gap − NIFTY gap| ≥ 0.63%, DEV 80th pct), entry 09:20
(H3: 09:30), exit 15:20, Bonferroni 98.75% session-cluster CIs.

| Hyp | Idea | DEV gross / net (bps) | VAL gross / net (bps) | Verdict |
|---|---|---|---|---|
| H1A | material-news gap continues | −13.2 / −33.9 | −2.1 / −22.8 | FAIL |
| H1B | no-news gap reverses | **+18.1** / −2.6 | **+9.2** / −11.4 | FAIL (below cost) |
| H2 | results gap continues | +8.7 / −12.0 | +25.0 / +4.4 | FAIL (noisy, n=87 VAL) |
| H3 | news gap + first-15-min confirmation continues | +4.7 / −15.9 | +0.6 / −20.0 | FAIL |

**What we learned**
1. Material-news gaps do **not** continue intraday in NIFTY-100 stocks. If anything they drift
   slightly back. Large caps price the news at the 09:15 auction.
2. No-news gaps do fade (gross positive in both splits, and the DEV gross CI excludes 0). But the
   effect is about 10–18 bps, below the round-trip cost. This is the same pattern as V22-A:
   **real but not monetisable at intraday costs.**
3. First-15-minute confirmation adds nothing.
4. Together with V1–V22, this closes the **intraday-direction-from-OHLCV/announcement-labels**
   line for this universe. Decision-gate rule: no more threshold variants of these families.

**Do not:** rerun with a lower gap cutoff, other horizons or per-category subsets chosen
from this report. That is the forking-paths problem this registry exists to prevent.

**Next options (each needs a new pre-registered family):**
- **A. Multi-day results drift (PEAD):** results-gap stocks held 1–5 sessions (delivery costs about
  25–30 bps, but the target move is much larger). Needs daily bars only.
- **B. Cost-structure attack on H1B:** the fade is about 18 bps gross. Test it only at execution
  costs a real setup could achieve (limit orders, larger notional), and only if a paper-trading
  slippage measurement supports it. Otherwise drop it.
- **C. Volatility monetisation (options):** V22-A plus H1B both say we predict *magnitude*,
  not direction. Requires historical option/IV data (not available from Kite for expired
  contracts). Decide on a data source first.
