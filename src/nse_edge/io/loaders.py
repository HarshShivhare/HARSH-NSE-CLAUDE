"""Raw-data loaders. Every loader returns IST timestamps and applies the time lock."""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from ..config import TZ, load_settings
from ..timelock import enforce_lock


def read_parquet(path: str | Path) -> pd.DataFrame:
    try:
        import pyarrow  # noqa: F401
        return pd.read_parquet(path)
    except ImportError:
        from .parquet_lite import read_parquet_lite
        return read_parquet_lite(path)


def _ist(s: pd.Series) -> pd.Series:
    s = pd.to_datetime(s)
    return s.dt.tz_localize(TZ) if s.dt.tz is None else s.dt.tz_convert(TZ)


def symbol_from_path(p: Path) -> str:
    m = re.match(r"NSE_(.+?)_5minute_", p.name)
    return m.group(1).upper() if m else p.stem.upper()


def stock_files(settings=None) -> dict[str, Path]:
    s = settings or load_settings()
    files = sorted(s.path("stock_dir").glob(s.stock_glob))
    return {symbol_from_path(f): f for f in files}


def load_bars(path: Path, audit: dict | None = None, settings=None) -> pd.DataFrame:
    df = read_parquet(path)
    df = df[["date", "open", "high", "low", "close", "volume"]].copy()
    df["date"] = _ist(df["date"])
    return enforce_lock(df.sort_values("date").reset_index(drop=True), "date", audit, settings)


def load_benchmark(audit: dict | None = None, settings=None) -> pd.DataFrame:
    s = settings or load_settings()
    return load_bars(s.path("index_dir") / s.raw["paths"]["benchmark_file"], audit, s)


def load_announcements(audit: dict | None = None, settings=None) -> pd.DataFrame:
    s = settings or load_settings()
    a = read_parquet(s.path("announcements"))
    a["broadcast_time"] = _ist(a["broadcast_time"])
    a = a[a["broadcast_time"].notna()]
    a = a.drop(columns=[c for c in ("received_time", "source_file", "split") if c in a.columns])
    a["symbol"] = a["symbol"].astype(str).str.upper().str.strip()
    a = a.drop_duplicates(["symbol", "broadcast_time", "subject", "details"])
    return enforce_lock(a.sort_values("broadcast_time").reset_index(drop=True), "broadcast_time", audit, s)
