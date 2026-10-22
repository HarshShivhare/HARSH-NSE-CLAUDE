"""Robust statistics with session-level clustering."""
from __future__ import annotations

import numpy as np
import pandas as pd


def trimmed_mean(x: np.ndarray, prop: float = 0.10) -> float:
    x = np.sort(np.asarray(x, float)[~np.isnan(x)])
    k = int(len(x) * prop)
    x = x[k:len(x) - k] if len(x) > 2 * k else x
    return float(x.mean()) if len(x) else np.nan


def cluster_bootstrap_ci(values: np.ndarray, clusters: np.ndarray, level: float = 0.95,
                         reps: int = 2000, seed: int = 7) -> tuple[float, float]:
    """Percentile CI of the event-weighted mean, resampling whole clusters (sessions)."""
    v = np.asarray(values, float)
    ok = ~np.isnan(v)
    v, c = v[ok], np.asarray(clusters)[ok]
    if len(v) < 2:
        return (np.nan, np.nan)
    codes, uniq = pd.factorize(c)
    sums = np.bincount(codes, weights=v)
    cnts = np.bincount(codes).astype(float)
    rng = np.random.default_rng(seed)
    G = len(uniq)
    draws = rng.integers(0, G, size=(reps, G))
    means = sums[draws].sum(1) / cnts[draws].sum(1)
    a = (1 - level) / 2
    return float(np.quantile(means, a)), float(np.quantile(means, 1 - a))


def top_share(values: np.ndarray, k: int | None = None) -> float:
    """Share of total P&L contributed by the best events (concentration check).

    Default k = max(3, ceil(5% of events)) so the rule scales with sample size.
    """
    v = np.asarray(values, float)
    v = v[~np.isnan(v)]
    tot = v.sum()
    if len(v) == 0 or tot <= 0:
        return np.nan
    if k is None:
        k = max(3, int(np.ceil(0.05 * len(v))))
    return float(np.sort(v)[::-1][:k].sum() / tot)


def summarize(values: np.ndarray, clusters: np.ndarray, cost: float, level: float,
              reps: int = 2000, seed: int = 7) -> dict:
    v = np.asarray(values, float)
    ok = ~np.isnan(v)
    v, c = v[ok], np.asarray(clusters)[ok]
    lo, hi = cluster_bootstrap_ci(v, c, level, reps, seed)
    net = v - cost
    return {
        "n_events": int(len(v)),
        "n_sessions": int(pd.Series(c).nunique()),
        "mean_gross_bps": float(v.mean() * 1e4) if len(v) else np.nan,
        "median_gross_bps": float(np.median(v) * 1e4) if len(v) else np.nan,
        "trimmed_gross_bps": trimmed_mean(v) * 1e4,
        "mean_net_bps": float(net.mean() * 1e4) if len(v) else np.nan,
        "trimmed_net_bps": trimmed_mean(net) * 1e4,
        "ci_lo_net_bps": (lo - cost) * 1e4,
        "ci_hi_net_bps": (hi - cost) * 1e4,
        "win_rate_net": float((net > 0).mean()) if len(v) else np.nan,
        "top5pct_share_net": top_share(net),
    }
