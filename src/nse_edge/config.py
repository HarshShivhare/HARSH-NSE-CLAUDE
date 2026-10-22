"""Project settings loader. Single source of truth for paths, splits and the time lock."""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TZ = "Asia/Kolkata"


@dataclass(frozen=True)
class Split:
    name: str
    start: pd.Timestamp
    end: pd.Timestamp

    def contains(self, ts) -> bool:
        return self.start <= ts <= self.end


@dataclass(frozen=True)
class Settings:
    raw: dict
    root: Path
    splits: tuple[Split, ...]
    research_end: pd.Timestamp
    costs: dict = field(default_factory=dict)

    def path(self, key: str) -> Path:
        p = Path(self.raw["paths"][key])
        return p if p.is_absolute() else (self.root / p).resolve()

    @property
    def stock_glob(self) -> str:
        return self.raw["paths"]["stock_glob"]

    def split_of(self, ts) -> str | None:
        t = pd.Timestamp(ts)
        t = t.tz_localize(TZ) if t.tzinfo is None else t.tz_convert(TZ)
        for s in self.splits:
            if s.contains(t):
                return s.name
        return None


def _day_start(s: str) -> pd.Timestamp:
    return pd.Timestamp(s).tz_localize(TZ)


def _day_end(s: str) -> pd.Timestamp:
    return pd.Timestamp(s).tz_localize(TZ) + pd.Timedelta(hours=23, minutes=59, seconds=59)


@lru_cache(maxsize=4)
def load_settings(path: str | None = None) -> Settings:
    cfg_path = Path(path) if path else PROJECT_ROOT / "config" / "settings.yaml"
    raw = yaml.safe_load(cfg_path.read_text())
    research_end = pd.Timestamp(raw["research_end"]).tz_localize(TZ)
    splits = tuple(Split(k, _day_start(v[0]), _day_end(v[1])) for k, v in raw["splits"].items())
    for s in splits:
        if s.end > research_end:
            raise ValueError(f"split {s.name} ends after research_end; the 2026 lock would be broken")
    return Settings(raw=raw, root=cfg_path.parent.parent, splits=splits,
                    research_end=research_end, costs=raw.get("costs", {}))
