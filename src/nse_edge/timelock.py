"""Fail-closed time lock for the final out-of-sample period.

Every research loader passes its frame through `enforce_lock`. Anything after
`research_end` is dropped and counted. `assert_locked` is called again on every
research output so that a bug upstream cannot leak 2026 data into a report.
"""
from __future__ import annotations

import pandas as pd

from .config import load_settings


class LockViolation(RuntimeError):
    pass


def enforce_lock(df: pd.DataFrame, col: str, audit: dict | None = None, settings=None) -> pd.DataFrame:
    s = settings or load_settings()
    if df.empty:
        return df
    mask = df[col] <= s.research_end
    dropped = int((~mask & df[col].notna()).sum())
    if audit is not None:
        audit[f"lock_dropped_{col}"] = audit.get(f"lock_dropped_{col}", 0) + dropped
    return df.loc[mask].copy()


def assert_locked(df: pd.DataFrame, cols: list[str], settings=None) -> None:
    s = settings or load_settings()
    for c in cols:
        if c not in df.columns or df.empty:
            continue
        mx = df[c].max()
        if pd.notna(mx) and pd.Timestamp(mx) > s.research_end:
            raise LockViolation(f"column {c!r} contains {mx} > research_end {s.research_end}")
