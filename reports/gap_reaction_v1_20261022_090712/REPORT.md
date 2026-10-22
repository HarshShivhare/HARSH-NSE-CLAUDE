# gap_reaction_v1 - research report

- Round-trip cost hurdle: **20.6 bps** (charges + slippage)
- CI level (Bonferroni): 0.9875, session-cluster bootstrap
- Gap cutoff (|excess gap| >= DEV 80th pct): 0.63%
- Fingerprint: family `5a5dcb17a35c`, settings `19af13acfb05`, code `596c1fc91cd8`
- Data audit: {'lock_dropped_date': 1223886, 'symbols': 100, 'symbol_sessions': 55912, 'complete': 55613, 'bad_ohlc_sessions': 12, 'max_session': '2025-12-31', 'benchmark_sessions': 577, 'announcements_lock_dropped_broadcast_time': 0, 'announcements': 34863, 'gap_events_eligible': 52845}

## Verdicts

- **H1A: FAIL** (1439 events) - DEV: mean_net_bps=-33.85 not > 0; DEV: trimmed_net_bps=-35.44 not > 0; DEV: ci_lo_net_bps=-50.55 not > 0; VALIDATION: mean_net_bps=-22.75 not > 0
- **H1B: FAIL** (5287 events) - DEV: mean_net_bps=-2.58 not > 0; DEV: trimmed_net_bps=-2.16 not > 0; DEV: ci_lo_net_bps=-17.32 not > 0; VALIDATION: mean_net_bps=-11.41 not > 0
- **H2: FAIL** (407 events) - DEV: mean_net_bps=-11.95 not > 0; DEV: trimmed_net_bps=-15.41 not > 0; DEV: ci_lo_net_bps=-48.74 not > 0; VALIDATION: trimmed_net_bps=-3.87 not > 0
- **H3: FAIL** (572 events) - DEV: mean_net_bps=-15.90 not > 0; DEV: trimmed_net_bps=-17.95 not > 0; DEV: ci_lo_net_bps=-42.73 not > 0; VALIDATION: mean_net_bps=-20.00 not > 0

## Primary results (raw stock return, entry -> 15:20)

| id | split | n_events | n_sessions | mean_gross_bps | median_gross_bps | mean_net_bps | trimmed_net_bps | ci_lo_net_bps | ci_hi_net_bps | win_rate_net | top5pct_share_net |
|---|---|---|---|---|---|---|---|---|---|---|---|
| H1A | DEV | 1194 | 367 | -13.22 | -21.35 | -33.85 | -35.44 | -50.55 | -15.80 | 0.41 | nan |
| H1A | VALIDATION | 245 | 96 | -2.13 | -7.51 | -22.75 | -26.12 | -49.07 | 3.89 | 0.39 | nan |
| H1B | DEV | 4494 | 421 | 18.05 | 17.19 | -2.58 | -2.16 | -17.32 | 11.02 | 0.49 | nan |
| H1B | VALIDATION | 793 | 123 | 9.22 | 14.44 | -11.41 | -10.58 | -23.78 | 0.35 | 0.48 | nan |
| H2 | DEV | 320 | 154 | 8.68 | 9.10 | -11.95 | -15.41 | -48.74 | 27.18 | 0.48 | nan |
| H2 | VALIDATION | 87 | 45 | 24.98 | 16.55 | 4.35 | -3.87 | -41.64 | 57.93 | 0.49 | 5.74 |
| H3 | DEV | 469 | 255 | 4.73 | 3.19 | -15.90 | -17.95 | -42.73 | 9.56 | 0.46 | nan |
| H3 | VALIDATION | 103 | 61 | 0.62 | 19.24 | -20.00 | -20.05 | -57.49 | 15.07 | 0.50 | nan |

## Secondary measures (not used for verdicts)

| id | split | measure | n_events | mean_gross_bps | mean_net_bps | ci_lo_net_bps |
|---|---|---|---|---|---|---|
| H1A | DEV | relative_eod | 1194 | -7.75 | -28.38 | -43.73 |
| H1A | DEV | raw_30m | 1194 | -8.52 | -29.14 | -36.78 |
| H1A | DEV | raw_60m | 1194 | -9.26 | -29.89 | -40.23 |
| H1A | DEV | raw_120m | 1194 | -9.94 | -30.57 | -44.86 |
| H1A | DEV | primary_long | 829 | -12.72 | -33.35 | -53.93 |
| H1A | DEV | primary_short | 365 | -14.34 | -34.97 | -77.00 |
| H1A | VALIDATION | relative_eod | 245 | 0.37 | -20.26 | -45.20 |
| H1A | VALIDATION | raw_30m | 245 | -0.25 | -20.87 | -32.55 |
| H1A | VALIDATION | raw_60m | 245 | 1.63 | -19.00 | -33.05 |
| H1A | VALIDATION | raw_120m | 245 | 3.61 | -17.02 | -35.86 |
| H1A | VALIDATION | primary_long | 181 | -10.80 | -31.43 | -66.39 |
| H1A | VALIDATION | primary_short | 64 | 22.40 | 1.78 | -46.30 |
| H1B | DEV | relative_eod | 4494 | 16.18 | -4.45 | -15.67 |
| H1B | DEV | raw_30m | 4494 | 7.79 | -12.83 | -18.86 |
| H1B | DEV | raw_60m | 4494 | 8.61 | -12.02 | -19.09 |
| H1B | DEV | raw_120m | 4494 | 11.75 | -8.88 | -16.63 |
| H1B | DEV | primary_long | 1637 | 29.94 | 9.31 | -24.99 |
| H1B | DEV | primary_short | 2857 | 11.24 | -9.39 | -31.20 |
| H1B | VALIDATION | relative_eod | 793 | 7.87 | -12.76 | -24.66 |
| H1B | VALIDATION | raw_30m | 793 | 0.26 | -20.36 | -26.52 |
| H1B | VALIDATION | raw_60m | 793 | 1.63 | -19.00 | -26.19 |
| H1B | VALIDATION | raw_120m | 793 | 5.28 | -15.35 | -25.30 |
| H1B | VALIDATION | primary_long | 288 | 3.79 | -16.84 | -37.10 |
| H1B | VALIDATION | primary_short | 505 | 12.31 | -8.32 | -28.25 |
| H2 | DEV | relative_eod | 320 | 11.78 | -8.85 | -39.90 |
| H2 | DEV | raw_30m | 320 | 7.41 | -13.22 | -34.96 |
| H2 | DEV | raw_60m | 320 | 10.81 | -9.82 | -36.53 |
| H2 | DEV | raw_120m | 320 | 17.15 | -3.48 | -31.34 |
| H2 | DEV | primary_long | 208 | -2.20 | -22.82 | -67.79 |
| H2 | DEV | primary_short | 112 | 28.88 | 8.25 | -50.98 |
| H2 | VALIDATION | relative_eod | 87 | 30.15 | 9.52 | -32.28 |
| H2 | VALIDATION | raw_30m | 87 | 8.76 | -11.87 | -32.46 |
| H2 | VALIDATION | raw_60m | 87 | 19.90 | -0.73 | -27.62 |
| H2 | VALIDATION | raw_120m | 87 | 24.72 | 4.09 | -26.59 |
| H2 | VALIDATION | primary_long | 58 | -10.70 | -31.33 | -92.98 |
| H2 | VALIDATION | primary_short | 29 | 96.33 | 75.70 | 11.05 |
| H3 | DEV | relative_eod | 469 | 9.46 | -11.17 | -34.72 |
| H3 | DEV | raw_30m | 469 | -0.56 | -21.18 | -37.27 |
| H3 | DEV | raw_60m | 469 | 2.93 | -17.70 | -32.89 |
| H3 | DEV | raw_120m | 469 | 9.56 | -11.07 | -29.63 |
| H3 | DEV | primary_long | 320 | 2.68 | -17.95 | -48.32 |
| H3 | DEV | primary_short | 149 | 9.13 | -11.50 | -64.54 |
| H3 | VALIDATION | relative_eod | 103 | 9.28 | -11.34 | -44.28 |
| H3 | VALIDATION | raw_30m | 103 | -10.97 | -31.60 | -49.58 |
| H3 | VALIDATION | raw_60m | 103 | -7.78 | -28.41 | -48.61 |
| H3 | VALIDATION | raw_120m | 103 | -1.31 | -21.94 | -46.61 |
| H3 | VALIDATION | primary_long | 69 | -23.65 | -44.28 | -89.78 |
| H3 | VALIDATION | primary_short | 34 | 49.89 | 29.26 | -15.12 |
