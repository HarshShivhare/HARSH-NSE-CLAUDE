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


# ------------------------------------------------------------------ results-drift dataset

from nse_edge.backtest.costs import model_cost
from nse_edge.features.results import build_results_events, results_anchors


def _results_setup(days, ann_rows, short_days=()):
    short = {str(pd.Timestamp(d).date()) for d in short_days}
    cut = lambda df, d: df.iloc[:21] if str(d.date()) in short else df     # DR-drill style 21-bar session
    frames = [cut(_bars(str(d.date()), base=100 + i, drift=0.01), d) for i, d in enumerate(days)]
    bfr = [cut(_bars(str(d.date()), base=1000), d) for d in days]
    ss, sa = _symbol_panel("A", pd.concat(frames), S)
    bs, ba = _symbol_panel("NIFTY50", pd.concat(bfr), S)
    cols = ["symbol", "broadcast_time", "subject", "catalyst_category", "details"]
    ann = pd.DataFrame(ann_rows, columns=cols[:len(ann_rows[0])])
    ann["broadcast_time"] = pd.to_datetime(ann["broadcast_time"]).dt.tz_localize(TZ)
    return Panel(ss, sa), Panel(bs, ba), ann


def test_results_reaction_and_entry_are_causal():
    days = pd.bdate_range("2024-03-01", periods=40)
    d10, d20 = days[10], days[20]
    rows = [("A", f"{d10.date()} 18:30", "Financial Result Updates", "FINANCIAL_RESULTS"),   # after hours
            ("A", f"{d20.date()} 15:30", "Outcome of Board Meeting", "FINANCIAL_RESULTS")]  # at the close
    stock, bench, ann = _results_setup(days, rows)
    # the bare "Outcome of Board Meeting" (no results text) is not a results filing -> one anchor
    assert len(results_anchors(ann)) == 1
    ev = build_results_events(stock, bench, ann, pd.DatetimeIndex([]), S)
    r = ev.iloc[0]
    assert r["base_date"].date() == d10.date()            # last close at/before broadcast
    assert r["reaction_date"].date() == days[11].date()   # first close after broadcast
    assert r["entry_date"].date() == days[12].date()      # trade only the session after the reaction
    assert r["exit_date_5"].date() == days[16].date()     # R + 5


def test_results_broadcast_exactly_at_close_uses_that_close_as_base():
    days = pd.bdate_range("2024-03-01", periods=30)
    rows = [("A", f"{days[5].date()} 15:30", "Financial Result Updates", "FINANCIAL_RESULTS")]
    ev = build_results_events(*_results_setup(days, rows), pd.DatetimeIndex([]), S)
    assert ev.iloc[0]["base_date"].date() == days[5].date()


def test_results_exit_crossing_split_is_dropped():
    days = pd.bdate_range("2025-06-02", periods=40)       # DEV ends 2025-06-30
    t = days[17]                                           # reaction ~ 26-Jun, 5d exit lands in July
    rows = [("A", f"{t.date()} 18:00", "Financial Result Updates", "FINANCIAL_RESULTS")]
    ev = build_results_events(*_results_setup(days, rows), pd.DatetimeIndex([]), S)
    r = ev.iloc[0]
    assert r["split"] == "DEV" and np.isnan(r["ret_5d"])


def test_lagged_results_subjects_never_anchor():
    ann = pd.DataFrame({"symbol": ["A", "A"], "catalyst_category": ["FINANCIAL_RESULTS"] * 2,
                        "subject": ["Copy of Newspaper Publication", "Financial Result Updates"],
                        "broadcast_time": pd.to_datetime(["2024-05-01 09:00", "2024-05-03 17:00"]).tz_localize(TZ)})
    a = results_anchors(ann)
    assert len(a) == 1 and a.iloc[0]["subject"] == "Financial Result Updates"


def test_cost_models():
    assert 30 < model_cost("delivery") * 1e4 < 40
    assert 10 < model_cost("futures") * 1e4 < 20


# ------------------------------------------------------------------ V24 review fixes

from nse_edge.features.results import SPECIAL_SESSIONS, dividend_ex_dates, is_results_filing

FRU = "Financial Result Updates"
SUBMITTED = "A Ltd has submitted to the Exchange, the financial results for the period ended December 31, 2023."


def _ann(rows):
    a = pd.DataFrame(rows, columns=["symbol", "broadcast_time", "subject", "catalyst_category", "details"])
    a["broadcast_time"] = pd.to_datetime(a["broadcast_time"]).dt.tz_localize(TZ)
    return a


# fix 1: anchors are results filings, never notices; clustering is measured from the anchor

def test_results_notices_never_anchor():
    a = _ann([
        ("A", "2024-01-04 17:50", "Press Release", "FINANCIAL_RESULTS",
         'press release titled "A LIMITED TO ANNOUNCE RESULTS FOR THE THIRD QUARTER ENDED DECEMBER 31, 2023, ON JANUARY 12, 2024"'),
        ("A", "2024-01-05 18:00", "Updates", "FINANCIAL_RESULTS",
         "the meeting of the Board of Directors is scheduled on 12/01/2024 to consider financial results"),
        ("A", "2024-01-08 13:17", "Updates", "FINANCIAL_RESULTS", "'Call with media on financial results for the quarter ended December 31, 2023'"),
        ("A", "2024-01-09 11:00", "Board Meeting Intimation", "BOARD_MEETING", "Intimation of board meeting to be held to approve financial results"),
        ("A", "2024-01-12 15:36", FRU, "FINANCIAL_RESULTS", SUBMITTED),
    ])
    assert is_results_filing(a).tolist() == [False, False, False, False, True]
    anc = results_anchors(a)
    assert len(anc) == 1 and anc.iloc[0]["broadcast_time"] == pd.Timestamp("2024-01-12 15:36", tz=TZ)


def test_results_board_outcome_with_results_text_anchors_whatever_its_category():
    a = _ann([
        ("A", "2024-01-10 14:41", "Outcome of Board Meeting", "BOARD_MEETING", "outcome of Board meeting held on January 10, 2024."),
        ("B", "2024-01-10 14:41", "Outcome of Board Meeting", "BOARD_MEETING",
         "The Board at its meeting held today approved the unaudited financial results for the quarter ended December 31, 2023"),
        # real outcome that also mentions an AGM "to be held": the approval wording wins over the notice wording
        ("C", "2024-01-10 15:10", "Outcome of Board Meeting", "FINANCIAL_RESULTS",
         "Board has approved the un-audited financial results for the quarter and notice convening the AGM to be held on 28 August"),
    ])
    assert is_results_filing(a).tolist() == [False, True, True]


def test_results_cluster_is_measured_from_anchor_not_chained():
    t0 = pd.Timestamp("2024-01-01 18:00")
    rows = [("A", str(t0 + pd.Timedelta(days=d)), FRU, "FINANCIAL_RESULTS", SUBMITTED) for d in (0, 30, 60, 90, 100)]
    anc = results_anchors(_ann(rows))
    # chained <=30-day gaps would fold everything into day 0; from-anchor gives day 0 and day 60 (90, 100 fold into 60)
    days = [(t - anc["broadcast_time"].iloc[0]).days for t in anc["broadcast_time"]]
    assert days == [0, 60]


def test_results_notice_before_results_does_not_move_the_base_day():
    days = pd.bdate_range("2024-03-01", periods=40)
    rows = [("A", f"{days[5].date()} 17:50", "Press Release", "FINANCIAL_RESULTS", "A LIMITED TO ANNOUNCE RESULTS ON 15 MARCH"),
            ("A", f"{days[10].date()} 18:30", FRU, "FINANCIAL_RESULTS", SUBMITTED)]
    ev = build_results_events(*_results_setup(days, rows), pd.DatetimeIndex([]), S, outcomes=False)
    assert len(ev) == 1 and ev.iloc[0]["base_date"].date() == days[10].date()
    assert not any(c.startswith("ret_") for c in ev.columns)          # blind build has no holding returns


# fix 2: dividend ex-dates inside the holding window

def test_dividend_ex_dates_are_parsed_from_announcements():
    a = _ann([
        ("A", "2025-07-10 15:00", "Record Date", "CORPORATE_ACTION",
         "A Ltd has informed the Exchange that Record date for the purpose of Dividend  is 25-Jul-2025."),
        ("B", "2025-01-05 15:00", "Record Date", "CORPORATE_ACTION", "Record date for the purpose of Dividend  is 27-Jan-2025."),
        ("B", "2025-01-15 15:24", "Revised Record date", "CORPORATE_ACTION",
         "B Ltd had intimated the 27-Jan-2025 as the record date. The Company has now informed the Exchange that the "
         "Record date has been revised to 31-Jan-2025 for the purpose of Dividend."),
        ("C", "2024-07-11 15:22", "Book Closure", "CORPORATE_ACTION",
         "Register of Members will remain closed from 17-Jul-2024 to 19-Jul-2024 for the purpose of Dividend."),
        ("D", "2024-08-05 12:00", "Record Date", "CORPORATE_ACTION",
         "The Company has fixed Monday, August 19, 2024 as the Record Date for the purpose of determining the members eligible to receive dividend"),
        ("E", "2024-08-05 12:00", "Record Date", "CORPORATE_ACTION", "Record date for the purpose of Bonus is 10-Aug-2024."),
    ])
    got = {(s, str(d.date())) for s, d in zip(*dividend_ex_dates(a).T.to_numpy())}
    assert got == {("A", "2025-07-25"), ("B", "2025-01-31"), ("C", "2024-07-17"), ("D", "2024-08-19")}


def test_results_events_with_ex_dividend_in_hold_are_dropped():
    days = pd.bdate_range("2024-03-01", periods=40)
    rows = [("A", f"{days[10].date()} 18:30", FRU, "FINANCIAL_RESULTS", SUBMITTED)]   # R = d11, entry d12, R+10 = d21
    setup = _results_setup(days, rows)

    def n_events(ex_day):
        ex = pd.DataFrame({"symbol": ["A"], "ex_date": [days[ex_day]]})
        ex["ex_date"] = ex["ex_date"].dt.tz_localize(TZ)
        return len(build_results_events(*setup, pd.DatetimeIndex([]), S, ex_dates=ex, outcomes=False))

    assert n_events(11) == 1                     # ex-date on the reaction day, before entry: kept
    assert n_events(12) == 0                     # entry day (window is inclusive)
    assert n_events(16) == 0                     # inside the 5-session hold
    assert n_events(21) == 0                     # longest exit (R + 10)
    assert n_events(22) == 1                     # after the longest exit: kept
    # default path: ex-dates come from the announcements themselves
    stock, bench, ann = setup
    rec = _ann([("A", f"{days[3].date()} 15:00", "Record Date", "CORPORATE_ACTION",
                 f"Record date for the purpose of Dividend  is {days[14]:%d-%b-%Y}.")])
    audit = {}
    ev = build_results_events(stock, bench, pd.concat([ann, rec], ignore_index=True), pd.DatetimeIndex([]), S,
                              outcomes=False, audit=audit)
    assert len(ev) == 0 and audit["results_ex_dividend_in_hold"] == 1


# fix 3: special sessions are non-sessions for the results dataset only

def test_special_sessions_list():
    assert SPECIAL_SESSIONS == ("2024-03-02", "2024-05-18", "2025-10-21")


def test_results_special_session_is_skipped_not_fatal():
    sat = pd.Timestamp("2024-03-02")
    days = pd.bdate_range("2024-02-12", periods=30).append(pd.DatetimeIndex([sat])).sort_values()
    rows = [("A", "2024-02-28 18:30", FRU, "FINANCIAL_RESULTS", SUBMITTED)]   # B = 28-Feb, R = 29-Feb, entry 1-Mar
    stock, bench, ann = _results_setup(days, rows, short_days=[sat])
    n_stock, n_bench = len(stock), len(bench)
    old = build_results_events(stock, bench, ann, pd.DatetimeIndex([]), S, special_sessions=(), outcomes=False)
    assert len(old) == 0                                     # a 21-bar session inside the window kills the event
    ev = build_results_events(stock, bench, ann, pd.DatetimeIndex([]), S, outcomes=False)
    r = ev.iloc[0]
    assert r["entry_date"].date() == pd.Timestamp("2024-03-01").date()
    assert r["exit_date_1"].date() == pd.Timestamp("2024-03-01").date()
    assert r["exit_date_3"].date() == pd.Timestamp("2024-03-05").date()       # Sat 2-Mar is not a session
    assert sat not in set(ev[[c for c in ev.columns if c.startswith("exit_date_")]].stack().dt.tz_localize(None))
    # the inputs (shared with the gap dataset) are not modified
    assert len(stock) == n_stock and len(bench) == n_bench
    assert sat in set(stock.sessions["date"].dt.tz_localize(None))
