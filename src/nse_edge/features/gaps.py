"""Opening-gap event table: one row per eligible symbol-session.

Causality contract (each field lists the instant it becomes known):
  prev_close        15:30 previous session
  open0             09:15 (opening auction price)
  gap, excess_gap   09:15
  news_* flags      09:15 (only announcements broadcast BEFORE 09:15 count)
  first15_ret       09:30 (close of the 09:25 bar)
  entry prices      open of the entry slot (the trade decision is made before it)
  exit / fwd prices close of the last bar that has fully closed by the horizon
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..data.panel import NSLOT, Panel, slot_of
from ..timelock import assert_locked

EOD_EXIT_SLOT = slot_of("15:15")  # close at 15:20, before broker auto square-off


def _news_flags(ev: pd.DataFrame, ann: pd.DataFrame) -> pd.DataFrame:
    """Per (symbol, date): announcements in the overnight window and in the prior 24h."""
    ev = ev[["symbol", "date", "prev_date"]].copy()
    ev["win_start"] = ev["prev_date"] + pd.Timedelta(hours=15, minutes=30)
    ev["day_start"] = ev["prev_date"] + pd.Timedelta(hours=9, minutes=15)
    ev["win_end"] = ev["date"] + pd.Timedelta(hours=9, minutes=15)
    a = ann[["symbol", "broadcast_time", "catalyst_category"]]
    m = ev.merge(a, on="symbol", how="inner")
    m = m[(m["broadcast_time"] >= m["day_start"]) & (m["broadcast_time"] < m["win_end"])]
    m["overnight"] = m["broadcast_time"] >= m["win_start"]
    m["results"] = m["overnight"] & (m["catalyst_category"] == "FINANCIAL_RESULTS")
    m["material"] = m["overnight"] & (m["catalyst_category"] != "OTHER")
    m["cat_overnight"] = m["catalyst_category"].where(m["overnight"])
    g = m.groupby(["symbol", "date"]).agg(
        n_ann_24h=("broadcast_time", "size"),
        n_ann_overnight=("overnight", "sum"),
        results_overnight=("results", "any"),
        material_overnight=("material", "any"),
        overnight_categories=("cat_overnight", lambda s: "|".join(sorted(set(s.dropna())))),
    ).reset_index()
    return g


def announcement_coverage_gaps(trading_days: pd.DatetimeIndex, ann: pd.DataFrame) -> pd.DatetimeIndex:
    """Trading days with zero announcements across the whole universe = feed gap, not 'no news'.

    With ~100 large caps there are filings on every real trading day, so a trading day with
    none means the source chunk was truncated (seen in late-April 2024 and 2025).
    """
    have = pd.DatetimeIndex(ann["broadcast_time"].dt.normalize().unique())
    return pd.DatetimeIndex(trading_days).sort_values().difference(have)


def _fwd(price_close: np.ndarray, entry_slot: int, minutes: int) -> np.ndarray:
    j = entry_slot + minutes // 5 - 1
    if j >= NSLOT:
        return np.full(price_close.shape[0], np.nan)
    return price_close[:, j]


def build_gap_events(stock: Panel, bench: Panel, ann: pd.DataFrame,
                     entry_slots=("09:20", "09:30"), horizons=(30, 60, 120)) -> pd.DataFrame:
    s = stock.sessions
    O, C = stock.bars["open"], stock.bars["close"]
    bs = bench.sessions.reset_index(drop=True)
    bidx = pd.Index(bs["date"]).get_indexer(s["date"])
    have_b = bidx >= 0
    bO = np.full_like(O, np.nan)
    bC = np.full_like(C, np.nan)
    bO[have_b] = bench.bars["open"][bidx[have_b]]
    bC[have_b] = bench.bars["close"][bidx[have_b]]
    b_prev_close = np.full(len(s), np.nan)
    b_prev_date = pd.Series(pd.NaT, index=s.index, dtype=s["date"].dtype)
    b_prev_close[have_b] = bs["prev_close"].to_numpy()[bidx[have_b]]
    b_prev_date[have_b] = bs["prev_date"].to_numpy()[bidx[have_b]]

    ev = s[["symbol", "date", "split", "prev_date", "prev_close", "vol20", "complete", "bad_ohlc"]].copy()
    ev["open0"] = O[:, 0]
    ev["gap"] = ev["open0"] / ev["prev_close"] - 1
    ev["nifty_gap"] = bO[:, 0] / b_prev_close - 1
    ev["excess_gap"] = ev["gap"] - ev["nifty_gap"]
    ev["gap_z"] = np.log1p(ev["excess_gap"]) / ev["vol20"]
    ev["first15_ret"] = C[:, 2] / O[:, 0] - 1
    ev["nifty_first15_ret"] = bC[:, 2] / bO[:, 0] - 1
    for hhmm in entry_slots:
        k = slot_of(hhmm)
        tag = hhmm.replace(":", "")
        e, be = O[:, k], bO[:, k]
        ev[f"ret_eod_{tag}"] = C[:, EOD_EXIT_SLOT] / e - 1
        ev[f"nifty_eod_{tag}"] = bC[:, EOD_EXIT_SLOT] / be - 1
        for h in horizons:
            ev[f"ret_{h}m_{tag}"] = _fwd(C, k, h) / e - 1
            ev[f"nifty_{h}m_{tag}"] = _fwd(bC, k, h) / be - 1

    # eligibility: clean, complete, consecutive with the benchmark calendar
    ev["eligible"] = (
        ev["complete"] & ~ev["bad_ohlc"] & have_b
        & ev["prev_close"].notna() & ev["vol20"].notna()
        & (ev["prev_date"].to_numpy() == b_prev_date.to_numpy())
        & ev["split"].notna()
    )
    ev = ev[ev["eligible"]].drop(columns=["complete", "bad_ohlc", "eligible"])

    flags = _news_flags(ev, ann)
    ev = ev.merge(flags, on=["symbol", "date"], how="left")
    for c in ("n_ann_24h", "n_ann_overnight"):
        ev[c] = ev[c].fillna(0).astype(int)
    for c in ("results_overnight", "material_overnight"):
        ev[c] = ev[c].astype("boolean").fillna(False).astype(bool)
    ev["overnight_categories"] = ev["overnight_categories"].fillna("")
    ev["news_overnight"] = ev["n_ann_overnight"] > 0
    ev["no_news_24h"] = ev["n_ann_24h"] == 0
    # news state is UNKNOWN when the announcement feed has a hole inside the event window
    gaps = announcement_coverage_gaps(pd.DatetimeIndex(ev["date"].unique()), ann)
    unknown = np.zeros(len(ev), dtype=bool)
    for g in gaps:
        unknown |= ((ev["prev_date"] <= g) & (ev["date"] >= g)).to_numpy()
    ev["news_unknown"] = unknown
    ev = ev[~ev["news_unknown"]]
    assert_locked(ev.assign(t=ev["date"] + pd.Timedelta(hours=15, minutes=30)), ["t"])
    return ev.reset_index(drop=True)
