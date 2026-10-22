"""Session panel: one row per symbol-session plus aligned (N, 75) bar matrices.

Slot k (0..74) is the 5-minute bar that STARTS at 09:15 + 5k minutes. A bar's
close is only known at slot start + 5 minutes. Downstream code must respect that;
`nse_edge.features` exposes helpers that encode it once.

Quality rules (quarantine, never repair):
  * bars outside 09:15-15:25 are discarded (Muhurat / special sessions vanish)
  * a session with any impossible OHLC bar -> bad_ohlc = True
  * a session with fewer than 75 regular bars -> complete = False
"""
from __future__ import annotations

import pickle
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import load_settings
from ..io.loaders import load_bars, load_benchmark, stock_files
from ..timelock import assert_locked

NSLOT = 75
FIELDS = ("open", "high", "low", "close", "volume")


@dataclass
class Panel:
    sessions: pd.DataFrame          # symbol, date, split, n_bars, complete, bad_ohlc, prev_close, ...
    bars: dict[str, np.ndarray]     # field -> (N, 75) float, NaN where missing

    def __len__(self):
        return len(self.sessions)


def slot_time(k: int) -> str:
    m = 9 * 60 + 15 + 5 * k
    return f"{m // 60:02d}:{m % 60:02d}"


def slot_of(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    k, r = divmod(h * 60 + m - (9 * 60 + 15), 5)
    if r or not 0 <= k < NSLOT:
        raise ValueError(f"{hhmm} is not a 5-minute slot start")
    return k


def _symbol_panel(sym: str, df: pd.DataFrame, settings) -> tuple[pd.DataFrame, dict]:
    mins = df["date"].dt.hour * 60 + df["date"].dt.minute
    df = df[(mins >= 555) & (mins <= 925)].copy()
    df["slot"] = ((df["date"].dt.hour * 60 + df["date"].dt.minute - 555) // 5).astype(int)
    df["day"] = df["date"].dt.normalize()
    df = df.drop_duplicates(["day", "slot"], keep="last")
    days = np.sort(df["day"].unique())
    di = pd.Index(days)
    r = di.get_indexer(df["day"])
    out = {f: np.full((len(days), NSLOT), np.nan) for f in FIELDS}
    for f in FIELDS:
        out[f][r, df["slot"].to_numpy()] = df[f].to_numpy(dtype=float)
    o, h, l, c = (out[k] for k in ("open", "high", "low", "close"))
    bad_bar = (h < np.fmax(o, c) - 1e-9) | (l > np.fmin(o, c) + 1e-9) | (l <= 0)
    bad_bar &= ~np.isnan(o)
    n_bars = (~np.isnan(c)).sum(1)
    last_close = c[:, NSLOT - 1]
    sess = pd.DataFrame({
        "symbol": sym,
        "date": pd.DatetimeIndex(days),
        "n_bars": n_bars,
        "complete": n_bars == NSLOT,
        "bad_ohlc": bad_bar.any(1),
        "prev_date": pd.DatetimeIndex(days).to_series().shift(1).to_numpy(),
        "prev_close": np.r_[np.nan, last_close[:-1]],
        "close_1525": last_close,
    })
    # causal daily volatility: std of prior 20 close-to-close returns (excludes today)
    cc = np.log(sess["close_1525"]).diff()
    sess["vol20"] = cc.shift(1).rolling(20, min_periods=15).std()
    sess["split"] = [settings.split_of(d) for d in sess["date"]]
    return sess, out


def build_panel(symbols: list[str] | None = None, settings=None, audit: dict | None = None) -> Panel:
    s = settings or load_settings()
    files = stock_files(s)
    if symbols:
        files = {k: v for k, v in files.items() if k in symbols}
    frames, mats = [], {f: [] for f in FIELDS}
    for sym, path in files.items():
        df = load_bars(path, audit, s)
        sess, arr = _symbol_panel(sym, df, s)
        frames.append(sess)
        for f in FIELDS:
            mats[f].append(arr[f])
    sessions = pd.concat(frames, ignore_index=True)
    bars = {f: np.vstack(mats[f]) for f in FIELDS}
    assert_locked(sessions.assign(date_end=sessions["date"] + pd.Timedelta(hours=15, minutes=30)), ["date_end"], s)
    return Panel(sessions, bars)


def build_benchmark_panel(settings=None, audit: dict | None = None) -> Panel:
    s = settings or load_settings()
    sess, arr = _symbol_panel("NIFTY50", load_benchmark(audit, s), s)
    return Panel(sess, arr)


def cached_panels(settings=None, rebuild: bool = False) -> tuple[Panel, Panel, dict]:
    s = settings or load_settings()
    cache = s.path("cache_dir") / "panels.pkl"
    if cache.exists() and not rebuild:
        with cache.open("rb") as fh:
            return pickle.load(fh)
    audit: dict = {}
    stock = build_panel(settings=s, audit=audit)
    bench = build_benchmark_panel(settings=s, audit=audit)
    audit.update({
        "symbols": int(stock.sessions["symbol"].nunique()),
        "symbol_sessions": len(stock),
        "complete": int(stock.sessions["complete"].sum()),
        "bad_ohlc_sessions": int(stock.sessions["bad_ohlc"].sum()),
        "max_session": str(stock.sessions["date"].max().date()),
        "benchmark_sessions": len(bench),
    })
    cache.parent.mkdir(parents=True, exist_ok=True)
    with cache.open("wb") as fh:
        pickle.dump((stock, bench, audit), fh)
    return stock, bench, audit
