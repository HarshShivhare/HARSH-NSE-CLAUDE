"""Research-integrity tests. These must pass before any run is trusted."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from nse_edge.backtest.costs import round_trip_cost
from nse_edge.config import TZ, load_settings
from nse_edge.data.panel import NSLOT, Panel, _symbol_panel, slot_of
from nse_edge.features.gaps import EOD_EXIT_SLOT, _fwd, _news_flags, announcement_coverage_gaps, build_gap_events
from nse_edge.research import registry
from nse_edge.stats.robust import cluster_bootstrap_ci, summarize
from nse_edge.timelock import LockViolation, assert_locked, enforce_lock

S = load_settings()


def _bars(day: str, base: float = 100.0, drift: float = 0.0) -> pd.DataFrame:
    t = pd.date_range(f"{day} 09:15", periods=NSLOT, freq="5min", tz=TZ)
    o = base + drift * np.arange(NSLOT)
    return pd.DataFrame({"date": t, "open": o, "high": o + 0.5, "low": o - 0.5, "close": o + drift, "volume": 100})


# ------------------------------------------------------------------ time lock

def test_lock_drops_2026_and_counts():
    df = pd.concat([_bars("2025-12-31"), _bars("2026-01-01")])
    audit = {}
    out = enforce_lock(df, "date", audit)
    assert out["date"].max() < pd.Timestamp("2026-01-01", tz=TZ)
    assert audit["lock_dropped_date"] == NSLOT


def test_assert_locked_raises():
    df = pd.DataFrame({"t": [pd.Timestamp("2026-01-02 10:00", tz=TZ)]})
    with pytest.raises(LockViolation):
        assert_locked(df, ["t"])


def test_splits_never_reach_final_oos():
    for sp in S.splits:
        assert sp.end <= S.research_end
    assert S.split_of(pd.Timestamp("2026-01-05 10:00", tz=TZ)) is None


# ------------------------------------------------------------------ panel

def test_panel_slots_and_quarantine():
    a = _bars("2024-06-24")
    b = _bars("2024-06-25")
    b.loc[0, "high"] = b.loc[0, "low"] - 1          # impossible bar
    c = _bars("2024-06-26").iloc[:-1]                 # incomplete session
    muhurat = pd.DataFrame({"date": [pd.Timestamp("2024-11-01 18:00", tz=TZ)], "open": [1.0],
                            "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1]})
    sess, arr = _symbol_panel("X", pd.concat([a, b, c, muhurat]), S)
    assert list(sess["bad_ohlc"]) == [False, True, False]
    assert list(sess["complete"]) == [True, True, False]
    assert len(sess) == 3                              # Muhurat bar discarded
    assert sess["prev_close"].iloc[1] == arr["close"][0, NSLOT - 1]
    assert slot_of("09:15") == 0 and slot_of("15:25") == 74


# ------------------------------------------------------------------ causality of returns

def test_forward_horizon_uses_bar_closed_by_horizon():
    C = np.arange(NSLOT, dtype=float)[None, :]
    k = slot_of("09:20")
    # 30 minutes after the 09:20 open is 09:50 -> last bar fully closed is the 09:45 bar
    assert _fwd(C, k, 30)[0] == slot_of("09:45")
    assert EOD_EXIT_SLOT == slot_of("15:15")


def test_news_window_excludes_announcements_at_or_after_open():
    ev = pd.DataFrame({"symbol": ["A"], "date": [pd.Timestamp("2025-03-04", tz=TZ)],
                       "prev_date": [pd.Timestamp("2025-03-03", tz=TZ)]})
    ann = pd.DataFrame({"symbol": ["A", "A", "A"], "catalyst_category": ["OTHER", "FINANCIAL_RESULTS", "OTHER"],
                        "broadcast_time": pd.to_datetime(["2025-03-03 12:00", "2025-03-03 18:00",
                                                          "2025-03-04 09:15"]).tz_localize(TZ)})
    f = _news_flags(ev, ann).iloc[0]
    assert f["n_ann_24h"] == 2            # 09:15 announcement is NOT known at the open
    assert f["n_ann_overnight"] == 1      # 12:00 prior day is not overnight
    assert bool(f["results_overnight"])


def test_gap_events_end_to_end_synthetic():
    days = pd.bdate_range("2024-01-01", periods=40)
    frames, bframes = [], []
    for i, d in enumerate(days):
        frames.append(_bars(str(d.date()), base=100 + i, drift=0.01))
        bframes.append(_bars(str(d.date()), base=1000, drift=0.0))
    ss, sa = _symbol_panel("A", pd.concat(frames), S)
    bs, ba = _symbol_panel("NIFTY50", pd.concat(bframes), S)
    ann = pd.DataFrame({"symbol": ["A"], "catalyst_category": ["FINANCIAL_RESULTS"],
                        "broadcast_time": [days[30] - pd.Timedelta(hours=2)]}).assign(
        broadcast_time=lambda x: x["broadcast_time"].dt.tz_localize(TZ))
    # fill every other trading day with a filing for another symbol so the feed has no holes
    filler = pd.DataFrame({"symbol": "Z", "catalyst_category": "OTHER",
                           "broadcast_time": [pd.Timestamp(d, tz=TZ) + pd.Timedelta(hours=11) for d in days]})
    ev = build_gap_events(Panel(ss, sa), Panel(bs, ba), pd.concat([ann, filler], ignore_index=True))
    assert len(ev) > 0 and ev["prev_close"].notna().all()
    row = ev[ev["date"] == pd.Timestamp(days[30], tz=TZ)].iloc[0]
    assert row["results_overnight"]
    assert ev["nifty_gap"].abs().max() < 1e-9


# ------------------------------------------------------------------ stats & costs

def test_cost_hurdle_reasonable():
    c = round_trip_cost() * 1e4
    assert 15 < c < 30, c


def test_cluster_bootstrap_wider_than_naive_when_clustered():
    rng = np.random.default_rng(0)
    clusters = np.repeat(np.arange(50), 20)
    shock = rng.normal(0, 1, 50)[clusters]
    v = shock + rng.normal(0, 0.1, len(clusters))
    lo, hi = cluster_bootstrap_ci(v, clusters, 0.95, 1000)
    naive = 1.96 * v.std() / np.sqrt(len(v)) * 2
    assert (hi - lo) > 2 * naive


def test_summarize_net_is_gross_minus_cost():
    st = summarize(np.full(100, 0.003), np.arange(100), 0.002, 0.95, 200)
    assert abs(st["mean_net_bps"] - 10) < 1e-6


# ------------------------------------------------------------------ pre-registration

def test_registry_refuses_edited_family(tmp_path, monkeypatch):
    fam = tmp_path / "fam.yaml"
    fam.write_text(yaml.safe_dump({"family": "t", "hypotheses": [{"id": "X", "side": "reversal"}]}))
    reg = tmp_path / "runs.jsonl"
    monkeypatch.setattr(registry, "_registry_path", lambda settings=None: reg)
    fp = registry.check_preregistration(fam)
    registry.record_run(fam, fp, {"X": "FAIL"}, tmp_path)
    with pytest.raises(registry.RegistryError, match="one-shot"):
        registry.check_preregistration(fam)                      # same file: one-shot
    assert registry.check_preregistration(fam, rerun_reason="reviewed bug fix")
    fam.write_text(fam.read_text() + "\n# tweak\n")
    with pytest.raises(registry.RegistryError, match="different file"):
        registry.check_preregistration(fam, rerun_reason="anything")


# ------------------------------------------------------------------ real data (skipped if absent)

REAL = S.path("stock_dir")


@pytest.mark.skipif(not any(REAL.glob(S.stock_glob)) if REAL.exists() else True, reason="real data not present")
def test_real_file_reads_and_is_locked():
    from nse_edge.io.loaders import load_bars, stock_files
    f = next(iter(stock_files().values()))
    df = load_bars(f)
    assert df["date"].max() <= S.research_end
    assert str(df["date"].dt.tz) == TZ


def test_feed_holes_make_news_unknown():
    days = pd.DatetimeIndex(pd.bdate_range("2024-04-22", periods=8)).tz_localize(TZ)
    ann = pd.DataFrame({"symbol": "Z", "catalyst_category": "OTHER",
                        "broadcast_time": [d + pd.Timedelta(hours=11) for d in days if d.day not in (25, 26)]})
    gaps = announcement_coverage_gaps(days, ann)
    assert [g.day for g in gaps] == [25, 26]
