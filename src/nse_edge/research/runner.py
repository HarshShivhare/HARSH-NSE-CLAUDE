"""Run a pre-registered hypothesis family and write a report + verdicts."""
from __future__ import annotations

import json
import pickle
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from ..backtest.costs import round_trip_cost
from ..config import load_settings
from ..data.panel import cached_panels
from ..features.gaps import build_gap_events
from ..io.loaders import load_announcements
from ..stats.robust import summarize
from ..timelock import assert_locked
from . import registry

SPLITS = ("DEV", "VALIDATION")


def gap_events(settings=None, rebuild: bool = False) -> tuple[pd.DataFrame, dict]:
    s = settings or load_settings()
    cache = s.path("cache_dir") / "gap_events.pkl"
    key = registry.code_hash() + registry.file_hash(s.root / "config" / "settings.yaml")
    if cache.exists() and not rebuild:
        with cache.open("rb") as fh:
            ckey, ev, audit = pickle.load(fh)
        if ckey == key:
            return ev, audit
        rebuild = True  # code or settings changed -> cache is stale
    stock, bench, audit = cached_panels(s, rebuild=rebuild)
    ann_audit: dict = {}
    ann = load_announcements(ann_audit, s)
    ev = build_gap_events(stock, bench, ann)
    audit = {**audit, **{f"announcements_{k}": v for k, v in ann_audit.items()},
             "announcements": len(ann), "gap_events_eligible": len(ev)}
    with cache.open("wb") as fh:
        pickle.dump((key, ev, audit), fh)
    return ev, audit


def _population(ev: pd.DataFrame, pop: dict) -> pd.Series:
    m = pd.Series(True, index=ev.index)
    news = pop.get("news")
    if news == "overnight":
        m &= ev["news_overnight"]
    elif news == "none_24h":
        m &= ev["no_news_24h"]
    elif news == "results":
        m &= ev["results_overnight"]
    elif news == "material":
        m &= ev["material_overnight"]
    elif news is not None:
        raise ValueError(f"unknown news filter {news!r}")
    if pop.get("first15_confirms"):
        rel15 = ev["first15_ret"] - ev["nifty_first15_ret"]
        m &= np.sign(rel15) == np.sign(ev["excess_gap"])
    return m


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


def run_family(path: Path, settings=None, reps: int = 2000, rerun_reason: str | None = None) -> Path:
    s = settings or load_settings()
    path = Path(path)
    fp = registry.check_preregistration(path, s, rerun_reason)
    spec = registry.load_family(path)
    d, rules = spec["defaults"], spec["pass_rules"]
    level = 1 - spec["family_alpha"] / len(spec["hypotheses"])
    cost = round_trip_cost(s)

    ev, audit = gap_events(s)
    assert_locked(ev.assign(t=ev["date"]), ["t"], s)
    ev["abs_excess_gap"] = ev["excess_gap"].abs()
    gc = d["gap_cutoff"]
    cutoff = float(ev.loc[ev["split"] == gc["fit_on"], gc["column"]].quantile(gc["quantile"]))

    out = s.path("reports_dir") / f"{spec['family']}_{datetime.now():%Y%m%d_%H%M%S}"
    out.mkdir(parents=True, exist_ok=False)
    rows, sec_rows, verdicts, detail = [], [], {}, {}
    for h in spec["hypotheses"]:
        m = _population(ev, h.get("population", {})) & (ev[gc["column"]] >= cutoff)
        e = ev[m].copy()
        side = 1.0 if h["side"] == "continuation" else -1.0
        e["pos"] = side * np.sign(e["excess_gap"])
        tag = h["entry"].replace(":", "")
        e["primary"] = e["pos"] * e[f"ret_eod_{tag}"]
        e["relative"] = e["pos"] * (e[f"ret_eod_{tag}"] - e[f"nifty_eod_{tag}"])
        by = {}
        for sp in SPLITS:
            g = e[e["split"] == sp]
            st = summarize(g["primary"].to_numpy(), g["date"].to_numpy(), cost, level, reps)
            by[sp] = st
            rows.append({"id": h["id"], "name": h["name"], "split": sp, **st})
            # secondary (reported, never used for the verdict)
            for lab, col in [("relative_eod", "relative")] + [
                    (f"raw_{k}m", None) for k in d["secondary_horizons"]]:
                vals = g[col] if col else g["pos"] * g[f"ret_{lab.split('_')[1][:-1]}m_{tag}"]
                st2 = summarize(vals.to_numpy(), g["date"].to_numpy(), cost, level, 500)
                sec_rows.append({"id": h["id"], "split": sp, "measure": lab, **st2})
            for side_lab, sg in (("long", g[g["pos"] > 0]), ("short", g[g["pos"] < 0])):
                st3 = summarize(sg["primary"].to_numpy(), sg["date"].to_numpy(), cost, level, 500)
                sec_rows.append({"id": h["id"], "split": sp, "measure": f"primary_{side_lab}", **st3})
        v, reasons = _verdict(by, rules)
        verdicts[h["id"]] = v
        detail[h["id"]] = {"verdict": v, "reasons": reasons, "n_total": int(len(e))}
        e["hyp"] = h["id"]
        e.attrs = {}
        e.to_csv(out / f"events_{h['id']}.csv.gz", index=False)
    summary = pd.DataFrame(rows)
    secondary = pd.DataFrame(sec_rows)
    summary.to_csv(out / "summary.csv", index=False)
    secondary.to_csv(out / "secondary.csv", index=False)
    meta = {"family": spec["family"], **fp, "rerun_reason": rerun_reason, "cost_round_trip_bps": cost * 1e4,
            "ci_level": level, "gap_cutoff_abs_excess_gap": cutoff, "audit": audit,
            "verdicts": detail}
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
    cols = ["id", "split", "n_events", "n_sessions", "mean_gross_bps", "median_gross_bps",
            "mean_net_bps", "trimmed_net_bps", "ci_lo_net_bps", "ci_hi_net_bps", "win_rate_net", "top5pct_share_net"]
    lines = [f"# {spec['family']} - research report", "",
             f"- Round-trip cost hurdle: **{meta['cost_round_trip_bps']:.1f} bps** (charges + slippage)",
             f"- CI level (Bonferroni): {meta['ci_level']:.4f}, session-cluster bootstrap",
             f"- Gap cutoff (|excess gap| >= DEV 80th pct): {meta['gap_cutoff_abs_excess_gap']*100:.2f}%",
             f"- Fingerprint: family `{meta['family_sha256'][:12]}`, settings `{meta['settings_sha256'][:12]}`, code `{meta['code_sha256'][:12]}`",
             f"- Data audit: {meta['audit']}", "", "## Verdicts", ""]
    for hid, v in meta["verdicts"].items():
        lines.append(f"- **{hid}: {v['verdict']}** ({v['n_total']} events)" +
                     ("" if not v["reasons"] else " - " + "; ".join(v["reasons"][:4])))
    lines += ["", "## Primary results (raw stock return, entry -> 15:20)", "", _fmt(summary, cols), "",
              "## Secondary measures (not used for verdicts)", "",
              _fmt(secondary, ["id", "split", "measure", "n_events", "mean_gross_bps", "mean_net_bps", "ci_lo_net_bps"]), ""]
    return "\n".join(lines)
