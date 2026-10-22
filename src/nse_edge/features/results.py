"""Quarterly-results event table for multi-day drift research.

Event definition (fixed before any outcome was computed; revised after review V24, still
before any outcome was computed):
  * anchor = an announcement that IS a results filing (`is_results_filing`):
      - subject "Financial Result Updates" (exchange-generated "submitted ... the financial results"), or
      - subject "Outcome of Board Meeting" whose text mentions results, whatever its category, or
      - category FINANCIAL_RESULTS whose text says results were submitted / approved,
    never an after-the-fact filing (newspaper copy, call transcript, re-submission, ...) and
    never a notice ("to be held", "scheduled", "intimation", "call with media", "to announce
    results") unless the same text says the results were submitted / approved.
  * the first such filing per symbol is an anchor; filings within 45 days AFTER THE ANCHOR are
    folded into it (measured from the anchor, not chained from filing to filing).
  * events within 14 days after an announcement-feed hole are dropped (the true first
    filing may be missing, so the anchor could be late).
  * events whose holding window (entry date .. longest exit date, inclusive) contains a dividend
    ex-date are dropped: prices are not dividend-adjusted, so the spot return would credit a
    short with the dividend drop (a futures short does not earn it). Under T+1 settlement the
    ex-date is the record date (book closure: its first day). Dates come from announcements.
  * special sessions (Saturday DR drills, Muhurat trading: `SPECIAL_SESSIONS`) are not sessions
    for this dataset: they are removed from both the stock and the NIFTY calendar before indexing.

Causality contract (session indices are per symbol, consecutive on the NIFTY calendar):
  B  = last session that CLOSED (15:30) at or before the broadcast  -> base close
  R  = B + 1, the reaction session (contains or follows the broadcast)
  reaction_excess = (close_R / close_B - 1) - same for NIFTY       known at 15:30 on R
  entry  = open of the 09:20 bar on R + 1                          (decision made overnight)
  exit_h = close of session R + h (h >= 1)                          hold h sessions
Trades whose exit falls in a different split than the entry, or after research_end, are
dropped (no DEV trade may use VALIDATION prices, and nothing may touch 2026).
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from ..config import TZ
from ..data.panel import Panel, slot_of
from ..timelock import assert_locked

LAGGED_SUBJECTS = {
    "Copy of Newspaper Publication",
    "Analysts/Institutional Investor Meet/Con. Call Updates",
    "Clarification - Financial Results",
    "Reply to Clarification- Financial results",
}
RESULTS_TEXT = r"financial\s+results?|(?:un)?audited\s+results|quarterly\s+results|\bresults?\s+for\s+the\s+(?:quarter|period|half|year)"
SUBMITTED_TEXT = r"(?:submitted|approved|took\s+on\s+record|adopted)\b.{0,150}?financial\s+results?"
NOTICE_TEXT = r"to\s+be\s+held|scheduled|intimation|call\s+with\s+(?:the\s+)?media|to\s+announce\s+(?:\w+\s+){0,4}results?"
LAGGED_TEXT = (r"re-?submission|machine\s+readable|legible|corrigendum|clarification|newspaper|audio\s+recording"
               r"|transcript|presentation|integrated\s+filing|further\s+to")
OUTCOME_SUBJECT = r"(?i)outcome\s+of\s+(?:the\s+)?board\s+meeting"

# Short, non-standard sessions inside the research window (checked against the bar data:
# 21, 21 and 12 bars). 2024-01-20 and 2025-02-01 were full Saturday sessions and stay.
SPECIAL_SESSIONS = ("2024-03-02", "2024-05-18", "2025-10-21")

CLUSTER_DAYS = 45
HOLE_LOOKBACK_DAYS = 14
ENTRY_SLOT = slot_of("09:20")


def _has(text: pd.Series, pattern: str) -> pd.Series:
    return text.str.contains(pattern, case=False, regex=True)


def _col(ann: pd.DataFrame, c: str) -> pd.Series:
    return (ann[c] if c in ann else pd.Series("", index=ann.index)).fillna("").astype(str)


def is_results_filing(ann: pd.DataFrame) -> pd.Series:
    subj, det = _col(ann, "subject"), _col(ann, "details")
    txt = subj + " | " + det
    submitted = _has(det, SUBMITTED_TEXT)
    fru = subj.eq("Financial Result Updates")
    outcome = subj.str.match(OUTCOME_SUBJECT) & _has(det, RESULTS_TEXT)
    other = ann["catalyst_category"].eq("FINANCIAL_RESULTS") & submitted & ~_has(txt, LAGGED_TEXT)
    notice = _has(txt, NOTICE_TEXT) & ~submitted
    return (fru | outcome | other) & ~notice & ~subj.isin(LAGGED_SUBJECTS)


def results_anchors(ann: pd.DataFrame) -> pd.DataFrame:
    fr = ann[is_results_filing(ann)].sort_values(["symbol", "broadcast_time"])
    keep = np.zeros(len(fr), dtype=bool)
    window = pd.Timedelta(days=CLUSTER_DAYS)
    last_sym, anchor_t = None, None
    for i, (sym, t) in enumerate(zip(fr["symbol"], fr["broadcast_time"])):
        if sym != last_sym or t - anchor_t > window:
            keep[i] = True
            last_sym, anchor_t = sym, t
    return fr[keep][["symbol", "broadcast_time", "subject"]].reset_index(drop=True)


# ------------------------------------------------------------------ dividend ex-dates

_MON = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_DATE = (rf"\d{{1,2}}[-./ ]{_MON}[-./ ]\d{{4}}"                              # 25-Jul-2025
         r"|\d{1,2}[./-]\d{1,2}[./-]\d{4}"                                   # 17.02.2024
         rf"|\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?{_MON},?\s+\d{{4}}"       # 6th November, 2024
         rf"|{_MON}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}")                # August 19, 2024
DATE_RE = re.compile(_DATE, re.I)
RECORD_RE = re.compile(r"record\s+date", re.I)
CLOSURE_RE = re.compile(rf"closed\s+from\s+({_DATE})\s+to\s+(?:{_DATE})", re.I)
SUPERSEDED_RE = re.compile(rf"had\s+intimated\s+(?:the\s+)?({_DATE})\s+as\s+the\s+record\s+date", re.I)
DIVIDEND_RE = re.compile(r"dividend|purpose\s+of\s+di\b", re.I)


def _parse_date(s: str) -> pd.Timestamp | None:
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", s, flags=re.I).replace(",", " ")
    s = re.sub(r"\bof\b", " ", s, flags=re.I)
    t = pd.to_datetime(s, dayfirst=True, errors="coerce")
    return None if pd.isna(t) else t.normalize()


def dividend_ex_dates(ann: pd.DataFrame, horizon_days: int = 180) -> pd.DataFrame:
    """(symbol, ex_date) for every dividend record date / book closure found in the announcements.

    A date counts if it is near a "record date" mention (or opens a book closure) in an announcement
    that mentions a dividend, and lies within `horizon_days` after the broadcast. Dates a later
    filing says were revised away ("had intimated X as the record date") are removed.
    Deliberately inclusive: an extra date only drops an event, a missed one biases returns.
    """
    det = _col(ann, "subject") + " | " + _col(ann, "details")
    m = det.map(lambda t: bool(DIVIDEND_RE.search(t) and (RECORD_RE.search(t) or CLOSURE_RE.search(t))))
    found, superseded = [], set()
    for sym, bt, text in zip(ann.loc[m, "symbol"], ann.loc[m, "broadcast_time"], det[m]):
        day = pd.Timestamp(bt).tz_convert(TZ).tz_localize(None).normalize()
        old = {_parse_date(x) for x in SUPERSEDED_RE.findall(text)}
        superseded |= {(sym, d) for d in old if d is not None}
        cands = [x.group(1) for x in CLOSURE_RE.finditer(text)]
        for rm in RECORD_RE.finditer(text):
            cands += DATE_RE.findall(text[max(0, rm.start() - 60): rm.end() + 220])
        for c in cands:
            d = _parse_date(c)
            if d is not None and d not in old and day <= d <= day + pd.Timedelta(days=horizon_days):
                found.append((sym, d))
    out = pd.DataFrame(sorted(set(found) - superseded), columns=["symbol", "ex_date"])
    out["ex_date"] = pd.to_datetime(out["ex_date"]).dt.tz_localize(TZ)
    return out


# ------------------------------------------------------------------ events

def _drop_special(p: Panel, special: tuple[str, ...]) -> Panel:
    keep = ~p.sessions["date"].dt.strftime("%Y-%m-%d").isin(special).to_numpy()
    return Panel(p.sessions[keep].reset_index(drop=True), {k: v[keep] for k, v in p.bars.items()})


def build_results_events(stock: Panel, bench: Panel, ann: pd.DataFrame, feed_holes: pd.DatetimeIndex,
                         settings, holds=(1, 3, 5, 10), special_sessions=SPECIAL_SESSIONS,
                         ex_dates: pd.DataFrame | None = None, outcomes: bool = True,
                         audit: dict | None = None) -> pd.DataFrame:
    """`outcomes=False` builds the same rows without any ret_/nifty_ holding-period columns
    (for counting and review without peeking). `ex_dates` defaults to `dividend_ex_dates(ann)`."""
    stock, bench = _drop_special(stock, special_sessions), _drop_special(bench, special_sessions)
    if ex_dates is None:
        ex_dates = dividend_ex_dates(ann)
    s = stock.sessions.reset_index(drop=True)
    O = stock.bars["open"]
    close = s["close_1525"].to_numpy()
    bs = bench.sessions.reset_index(drop=True)
    bclose = bs["close_1525"].to_numpy()
    bopen = bench.bars["open"][:, ENTRY_SLOT]
    bpos = pd.Index(bs["date"]).get_indexer(s["date"])          # NIFTY calendar position
    s_end = pd.DatetimeIndex(s["date"] + pd.Timedelta(hours=15, minutes=30)).as_unit("ns").asi8  # UTC ns, unit pinned
    sym_start = s.groupby("symbol").cumcount().to_numpy() == 0
    sym_of = s["symbol"].to_numpy()
    maxh = max(holds)
    bad = s["bad_ohlc"].to_numpy()
    full = s["complete"].to_numpy()

    rows = []
    anchors = results_anchors(ann)
    for sym, g in anchors.groupby("symbol"):
        idx = np.flatnonzero(sym_of == sym)
        if not len(idx):
            continue
        ends = s_end[idx]
        for bt, subj in zip(g["broadcast_time"], g["subject"]):
            k = np.searchsorted(ends, pd.Timestamp(bt).as_unit("ns").value, side="right") - 1   # last close <= broadcast
            if k < 0 or k + 1 >= len(idx):
                continue
            b = idx[k]
            r = b + 1
            if r >= len(s) or sym_of[r] != sym or sym_start[r]:
                continue
            rec = {"symbol": sym, "broadcast_time": bt, "subject": subj,
                   "base_date": s.at[b, "date"], "reaction_date": s.at[r, "date"]}
            last = r + maxh
            span_ok = last < len(s) and sym_of[min(last, len(s) - 1)] == sym
            # consecutive on the NIFTY calendar and clean through the longest hold
            rng = slice(b, (last if span_ok else idx[-1]) + 1)
            bp = bpos[rng]
            rec["calendar_ok"] = bool(span_ok and (bp >= 0).all() and (np.diff(bp) == 1).all())
            # every session from base to longest exit must be a full, normal session
            rec["clean"] = bool(span_ok and not bad[rng].any() and full[rng].all())
            if not (rec["calendar_ok"] and rec["clean"]):
                rows.append(rec)
                continue
            bb, br = bpos[b], bpos[r]
            rec["reaction"] = close[r] / close[b] - 1
            rec["nifty_reaction"] = bclose[br] / bclose[bb] - 1
            e = O[r + 1, ENTRY_SLOT]
            be = bopen[bpos[r + 1]]
            rec["entry_date"] = s.at[r + 1, "date"]
            rec["entry"] = e
            rec["hold_end_date"] = s.at[last, "date"]
            for h in holds:
                x = r + h
                rec[f"exit_date_{h}"] = s.at[x, "date"]
                if outcomes:
                    rec[f"ret_{h}d"] = close[x] / e - 1
                    rec[f"nifty_{h}d"] = bclose[bpos[x]] / be - 1
            rows.append(rec)

    ev = pd.DataFrame(rows)
    for c in ("entry_date", "hold_end_date", "entry", "reaction", "nifty_reaction"):
        if c not in ev:
            ev[c] = pd.NaT if c.endswith("date") else np.nan
    ev["usable"] = ev["calendar_ok"] & ev["clean"] & ev["entry"].notna() & ev["reaction"].notna()
    ev["reaction_excess"] = ev["reaction"] - ev["nifty_reaction"]
    ev["split"] = [settings.split_of(d) if pd.notna(d) else None for d in ev["entry_date"]]
    # feed holes: anchor could be a late filing
    hole = np.zeros(len(ev), dtype=bool)
    for h in feed_holes:
        hole |= ((ev["broadcast_time"] >= h) & (ev["broadcast_time"] < h + pd.Timedelta(days=HOLE_LOOKBACK_DAYS))).to_numpy()
    ev["near_feed_hole"] = hole
    # dividend ex-date anywhere in entry .. longest exit (inclusive)
    exd = {sym: np.sort(pd.DatetimeIndex(x).as_unit("ns").asi8) for sym, x in ex_dates.groupby("symbol")["ex_date"]}   # UTC ns, like Timestamp.value
    div = np.zeros(len(ev), dtype=bool)
    for i, (sym, a, z) in enumerate(zip(ev["symbol"], ev["entry_date"], ev["hold_end_date"])):
        if pd.notna(a) and sym in exd:
            d = exd[sym]
            div[i] = np.searchsorted(d, z.value, side="right") > np.searchsorted(d, a.value, side="left")
    ev["ex_div_in_hold"] = div
    # exits must stay inside the entry's split (and therefore before research_end)
    if outcomes:
        for h in holds:
            col = f"exit_date_{h}"
            same = np.array([settings.split_of(x) if pd.notna(x) else None for x in ev[col]], dtype=object) == ev["split"].to_numpy()
            ev.loc[~same, [f"ret_{h}d", f"nifty_{h}d"]] = np.nan
    base = ev["usable"] & ev["split"].notna()
    if audit is not None:
        audit.update({
            "results_anchors": len(anchors),
            "results_anchors_with_bars": len(ev),
            "results_unusable_window": int((~ev["usable"]).sum()),
            "results_outside_splits": int((ev["usable"] & ev["split"].isna()).sum()),
            "results_near_feed_hole": int((base & ev["near_feed_hole"]).sum()),
            "results_ex_dividend_in_hold": int((base & ~ev["near_feed_hole"] & ev["ex_div_in_hold"]).sum()),
            "special_sessions_removed": list(special_sessions),
        })
    out = ev[base & ~ev["near_feed_hole"] & ~ev["ex_div_in_hold"]].reset_index(drop=True)
    assert_locked(out, [f"exit_date_{h}" for h in holds] + ["entry_date", "hold_end_date"])
    return out
