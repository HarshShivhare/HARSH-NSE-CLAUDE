"""Run a pre-registered hypothesis family and write a report + verdicts.

A family names a `dataset` (default: gap_events). Each dataset provides
  build(settings)              -> (events DataFrame, audit dict), cached and keyed on code+settings
  prepare(ev, spec, h, s)      -> frame with columns: split, cluster, primary, sec_* , pos
Everything after that (stats, verdicts, report, registry) is shared.
"""
from __future__ import annotations

import json
import pickle
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from ..backtest.costs import model_cost
from ..config import load_settings
from ..data.panel import cached_panels
from ..features.gaps import announcement_coverage_gaps, build_gap_events
from ..features.results import build_results_events
from ..io.loaders import load_announcements
from ..stats.robust import summarize
from ..timelock import assert_locked
from . import registry

SPLITS = ("DEV", "VALIDATION")


# ------------------------------------------------------------------ dataset builders

def _cached(name: str, builder, settings, rebuild: bool):
    s = settings
    cache = s.path("cache_dir") / f"{name}.pkl"
    key = registry.code_hash() + registry.file_hash(s.root / "config" / "settings.yaml")
    if cache.exists() and not rebuild:
        with cache.open("rb") as fh:
            ckey, ev, audit = pickle.load(fh)
        if ckey == key:
            return ev, audit
        rebuild = True  # code or settings changed -> cache is stale
    ev, audit = builder(s, rebuild)
    cache.parent.mkdir(parents=True, exist_ok=True)
    with cache.open("wb") as fh:
        pickle.dump((key, ev, audit), fh)
    return ev, audit


def _build_gap(s, rebuild):
    stock, bench, audit = cached_panels(s, rebuild=rebuild)
    ann_audit: dict = {}
    ann = load_announcements(ann_audit, s)
    ev = build_gap_events(stock, bench, ann)
    return ev, {**audit, **{f"announcements_{k}": v for k, v in ann_audit.items()},
                "announcements": len(ann), "gap_events_eligible": len(ev)}


def _build_results(s, rebuild):
    stock, bench, audit = cached_panels(s, rebuild=rebuild)
    ann = load_announcements(None, s)
    holes = announcement_coverage_gaps(pd.DatetimeIndex(bench.sessions["date"]), ann)
    ev_audit: dict = {}
    ev = build_results_events(stock, bench, ann, holes, s, audit=ev_audit)
    return ev, {**audit, **ev_audit, "feed_hole_days": [str(d.date()) for d in holes],
                "results_events_usable": len(ev),
                "by_split": ev["split"].value_counts().to_dict()}


def gap_events(settings=None, rebuild: bool = False):
    return _cached("gap_events", _build_gap, settings or load_settings(), rebuild)


def results_events(settings=None, rebuild: bool = False):
    return _cached("results_events", _build_results, settings or load_settings(), rebuild)


# ------------------------------------------------------------------ dataset preparers

def _gap_population(ev: pd.DataFrame, pop: dict) -> pd.Series:
    m = pd.Series(True, index=ev.index)
    news = pop.get("news")
    cols = {"overnight": "news_overnight", "none_24h": "no_news_24h",
            "results": "results_overnight", "material": "material_overnight"}
    if news is not None:
        if news not in cols:
            raise ValueError(f"unknown news filter {news!r}")
        m &= ev[cols[news]]
    if pop.get("first15_confirms"):
        rel15 = ev["first15_ret"] - ev["nifty_first15_ret"]
        m &= np.sign(rel15) == np.sign(ev["excess_gap"])
    return m


def _prepare_gap(ev, spec, h, cutoff):
    d = spec["defaults"]
    col = d["gap_cutoff"]["column"]
    e = ev[_gap_population(ev, h.get("population", {})) & (ev[col] >= cutoff)].copy()
    side = 1.0 if h["side"] == "continuation" else -1.0
    e["pos"] = side * np.sign(e["excess_gap"])
    tag = h["entry"].replace(":", "")
    e["primary"] = e["pos"] * e[f"ret_eod_{tag}"]
    e["sec_relative_eod"] = e["pos"] * (e[f"ret_eod_{tag}"] - e[f"nifty_eod_{tag}"])
    for k in d["secondary_horizons"]:
        e[f"sec_raw_{k}m"] = e["pos"] * e[f"ret_{k}m_{tag}"]
    e["cluster"] = e["date"]
    return e


def _prepare_results(ev, spec, h, cutoff):
    d = spec["defaults"]
    col = d["reaction_cutoff"]["column"]
    e = ev[ev[col] >= cutoff].copy()
    sign = h.get("population", {}).get("reaction")
    if sign == "positive":
        e = e[e["reaction_excess"] > 0]
    elif sign == "negative":
        e = e[e["reaction_excess"] < 0]
    elif sign is not None:
        raise ValueError(f"unknown reaction filter {sign!r}")
    side = 1.0 if h["side"] == "continuation" else -1.0
    e["pos"] = side * np.sign(e["reaction_excess"])
    hold = int(h["hold"])
    e["primary"] = e["pos"] * e[f"ret_{hold}d"]
    e[f"sec_relative_{hold}d"] = e["pos"] * (e[f"ret_{hold}d"] - e[f"nifty_{hold}d"])
    for k in d["secondary_holds"]:
        e[f"sec_raw_{k}d"] = e["pos"] * e[f"ret_{k}d"]
    e["cluster"] = e["entry_date"]
    return e


DATASETS = {
    "gap_events": (gap_events, _prepare_gap, "gap_cutoff", "date"),
    "results_events": (results_events, _prepare_results, "reaction_cutoff", "entry_date"),
}


# ------------------------------------------------------------------ verdicts

def _verdict(stats_by_split: dict, rules: dict) -> tuple[str, list[str]]:
    reasons, insufficient = [], False
    for sp in SPLITS:
        st = stats_by_split[sp]
        if st["n_sessions"] < rules["min_sessions"]:
            insufficient = True
            reasons.append(f"{sp}: only {st['n_sessions']} sessions (< {rules['min_sessions']})")
        checks = {
            "mean_net_bps": (st["mean_net_bps"], rules["mean_net_bps_gt"], ">"),
            "trimmed_net_bps": (st["trimmed_net_bps"], rules["trimmed_net_bps_gt"], ">"),
            "ci_lo_net_bps": (st["ci_lo_net_bps"], rules["ci_lo_net_bps_gt"], ">"),
            "top5pct_share_net": (st["top5pct_share_net"], rules["top5pct_share_net_lt"], "<"),
        }
        for k, (val, thr, op) in checks.items():
            if np.isnan(val):
                # top-5% share is undefined when net total <= 0; the mean check already fails then
                if k != "top5pct_share_net":
                    reasons.append(f"{sp}: {k} is NaN")
                continue
            ok = val > thr if op == ">" else val < thr
            if not ok:
                reasons.append(f"{sp}: {k}={val:.2f} not {op} {thr}")
    if insufficient:
        return "INSUFFICIENT", reasons
    return ("PASS" if not reasons else "FAIL"), reasons


# ------------------------------------------------------------------ run

def run_family(path: Path, settings=None, reps: int = 2000, rerun_reason: str | None = None) -> Path:
    s = settings or load_settings()
    path = Path(path)
    fp = registry.check_preregistration(path, s, rerun_reason)
    spec = registry.load_family(path)
    dataset = spec.get("dataset", "gap_events")
    build, prepare, cut_key, date_col = DATASETS[dataset]
    d, rules = spec["defaults"], spec["pass_rules"]
    level = 1 - spec["family_alpha"] / len(spec["hypotheses"])

    ev, audit = build(s)
    assert_locked(ev.assign(_t=ev[date_col]), ["_t"], s)
    gc = d[cut_key]
    base = gc["column"].removeprefix("abs_")
    ev[gc["column"]] = ev[base].abs()
    cutoff = float(ev.loc[ev["split"] == gc["fit_on"], gc["column"]].quantile(gc["quantile"]))

    out = s.path("reports_dir") / f"{spec['family']}_{datetime.now():%Y%m%d_%H%M%S}"
    out.mkdir(parents=True, exist_ok=False)
    rows, sec_rows, verdicts, detail, costs = [], [], {}, {}, {}
    for h in spec["hypotheses"]:
        cost = model_cost(h.get("cost_model", d.get("cost_model", "intraday")), s)
        costs[h["id"]] = cost * 1e4
        e = prepare(ev, spec, h, cutoff)
        sec_cols = [c for c in e.columns if c.startswith("sec_")]
        by = {}
        for sp in SPLITS:
            g = e[(e["split"] == sp) & e["primary"].notna()]
            st = summarize(g["primary"].to_numpy(), g["cluster"].to_numpy(), cost, level, reps)
            by[sp] = st
            rows.append({"id": h["id"], "name": h["name"], "split": sp, "cost_bps": cost * 1e4, **st})
            # secondary (reported, never used for the verdict)
            for c in sec_cols:
                st2 = summarize(g[c].to_numpy(), g["cluster"].to_numpy(), cost, level, 500)
                sec_rows.append({"id": h["id"], "split": sp, "measure": c[4:], **st2})
            if (g["pos"] > 0).any() and (g["pos"] < 0).any():
                for lab, sg in (("long", g[g["pos"] > 0]), ("short", g[g["pos"] < 0])):
                    st3 = summarize(sg["primary"].to_numpy(), sg["cluster"].to_numpy(), cost, level, 500)
                    sec_rows.append({"id": h["id"], "split": sp, "measure": f"primary_{lab}", **st3})
        v, reasons = _verdict(by, rules)
        verdicts[h["id"]] = v
        detail[h["id"]] = {"verdict": v, "reasons": reasons, "n_total": int(e["primary"].notna().sum())}
        e.assign(hyp=h["id"]).to_csv(out / f"events_{h['id']}.csv.gz", index=False)

    summary = pd.DataFrame(rows)
    secondary = pd.DataFrame(sec_rows)
    summary.to_csv(out / "summary.csv", index=False)
    secondary.to_csv(out / "secondary.csv", index=False)
    meta = {"family": spec["family"], "dataset": dataset, **fp, "rerun_reason": rerun_reason,
            "cost_round_trip_bps": costs, "ci_level": level, "cutoff_column": gc["column"],
            "cutoff": cutoff, "audit": audit, "verdicts": detail}
    (out / "run.json").write_text(json.dumps(meta, indent=2, default=str))
    (out / "REPORT.md").write_text(_report_md(spec, meta, summary, secondary))
    registry.record_run(path, fp, verdicts, out, s, rerun_reason)
    return out


def _fmt(df: pd.DataFrame, cols: list[str]) -> str:
    d = df[cols].copy()
    for c in d.columns:
        if d[c].dtype.kind == "f":
            d[c] = d[c].map(lambda x: f"{x:.2f}")
    head = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n"
    return head + "\n".join("| " + " | ".join(map(str, r)) + " |" for r in d.to_numpy())


def _report_md(spec, meta, summary, secondary) -> str:
    cols = ["id", "split", "n_events", "n_sessions", "cost_bps", "mean_gross_bps", "median_gross_bps",
            "mean_net_bps", "trimmed_net_bps", "ci_lo_net_bps", "ci_hi_net_bps", "win_rate_net", "top5pct_share_net"]
    costs = ", ".join(f"{k} {v:.1f}" for k, v in meta["cost_round_trip_bps"].items())
    lines = [f"# {spec['family']} - research report", "",
             f"- Dataset: `{meta['dataset']}`",
             f"- Round-trip cost hurdle (bps): {costs}",
             f"- CI level (Bonferroni): {meta['ci_level']:.4f}, session-cluster bootstrap",
             f"- Cutoff: {meta['cutoff_column']} >= {meta['cutoff']*100:.2f}% (fit on DEV)",
             f"- Fingerprint: family `{meta['family_sha256'][:12]}`, settings `{meta['settings_sha256'][:12]}`, code `{meta['code_sha256'][:12]}`",
             f"- Data audit: {meta['audit']}", "", "## Verdicts", ""]
    for hid, v in meta["verdicts"].items():
        lines.append(f"- **{hid}: {v['verdict']}** ({v['n_total']} events)" +
                     ("" if not v["reasons"] else " - " + "; ".join(v["reasons"][:4])))
    lines += ["", "## Primary results", "", _fmt(summary, cols), "",
              "## Secondary measures (not used for verdicts)", "",
              _fmt(secondary, ["id", "split", "measure", "n_events", "mean_gross_bps", "mean_net_bps", "ci_lo_net_bps"]), ""]
    return "\n".join(lines)
