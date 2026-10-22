# Independent review of V24 (results_drift_v1), 2026-10-22, before the first run

The reviewer did not write the code, did not run the family and computed no outcome statistics.

## BLOCKERS (fix before running)

### 1. Some anchors are results-date notices, not results (`features/results.py:38-42`)
FINANCIAL_RESULTS includes notices ("WIPRO LIMITED TO ANNOUNCE RESULTS ... ON OCTOBER 17",
ICICIBANK "Call with media", HCLTECH notices) filed under subjects "Updates" / "General Updates"
/ "Press Release", 3-27 days before the results. The 45-day clustering then folds the real
results into the notice, so the "reaction" happens BEFORE the news.
- 26 usable events (21 DEV, 5 VAL) have the first real results filing after the reaction close.
  Affected: WIPRO (every quarter), ICICIBANK (6 quarters), HCLTECH, CANBK, UNIONBANK, GAIL,
  KOTAKBANK, HDFCLIFE, BAJFINANCE.
- The 45-day chain is measured from the previous filing each time, so a notice can also swallow the
  next quarter's real results (RECLTD 2024-01-23, 83 days after its anchor; BAJAJHLDNG and
  BAJFINANCE Q1 FY26).
- Fix: an anchor must say the results were submitted or approved (e.g. "submitted ... financial
  results", "Outcome of Board Meeting", "Financial Result Updates"). Exclude notice wording:
  "to be held", "scheduled", "intimation", "call with media", "to announce results".
  Cluster from the anchor, not as a chain. Also: CHOLAFIN 2025-07-31's board outcome at 14:41
  was categorised BOARD_MEETING, while the anchor is 21:12 (the base day is one session late).
  Consider allowing "Outcome of Board Meeting" + results text as an anchor whatever its category.

### 2. Prices are not adjusted for dividends (blocker for H4B, should-fix for H4A)
Example: COALINDIA ex-date 2023-11-21, previous close 324.2, open 314.6. About 65-75 usable events
have a dividend record date between entry and exit (COALINDIA, ONGC, NTPC, POWERGRID, TCS,
HCLTECH, GODREJCP). The spot-return proxy credits a short with the ex-dividend drop; a futures
short does NOT earn it. That is about 1-4% per affected event, biasing H4B toward PASS.
- Fix: exclude events whose hold window contains an ex-dividend date (parse dividend /
  record-date announcements, CORPORATE_ACTION category), or add the dividend back.

## SHOULD-FIX
3. Special sessions (2024-03-02, 2024-05-18 with 21 bars; 2025-10-21 Muhurat with 12 bars) make every
   event window that touches them unusable. May 2024: 66 anchors become 18 usable; Oct 2025: 54
   become 33. Treat those days as non-sessions (remove them from both the stock and NIFTY calendars).
4. April results seasons are under-covered (April 2024: 21 anchors, April 2025: 9). The feed-hole
   filter catches only zero-filing days and only drops anchors AFTER a hole. Document this; also
   drop anchors filed shortly before a hole.

## NOTES
5. Costs match the settings (delivery 35.32 bps, futures 13.58 bps). Futures roll is not modelled
   (a second round trip if expiry falls inside the hold). Futures STT was 0.0125% before
   2024-10-01 (the 0.02% setting is conservative). The DP charge is about Rs 15.93 now.
6. Causality and indexing were verified on 15 events against the raw bars (14 matched exactly; the
   15th differed only because the reviewer's own checker mistakenly counted the Sunday Muhurat
   session).
7. Time lock, split handling, runner signs, per-hypothesis costs and the Bonferroni level 0.975
   are all correct.
8. Survivorship: the universe is today's 100 large caps, including TMPV, ENRIN and TATACAP. Note this in the report.

## Required order
fix #1 and #2 (and ideally #3) -> add tests -> revise `hypotheses/family_results_drift_v1.yaml`
(allowed: never run) with a revision note -> commit -> second independent review -> run once.
