"""Command line: python -m nse_edge <command>

  status                     data audit, lock status, registry history
  build [--rebuild]          build/refresh the cached session panel + gap events
  run <family.yaml>          run a pre-registered hypothesis family
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import load_settings


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="nse_edge")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    b = sub.add_parser("build")
    b.add_argument("--rebuild", action="store_true")
    r = sub.add_parser("run")
    r.add_argument("family", type=Path)
    r.add_argument("--reps", type=int, default=2000)
    r.add_argument("--rerun-reason", default=None,
                   help="required to run a family that already has a recorded run (logged)")
    a = p.parse_args(argv)
    s = load_settings()

    if a.cmd == "status":
        from .research import registry
        print(f"research_end : {s.research_end}  (final OOS {s.raw['final_oos']} is LOCKED)")
        for sp in s.splits:
            print(f"split {sp.name:<11}: {sp.start.date()} -> {sp.end.date()}")
        print("registry     :")
        for rec in registry.history(s):
            print(f"  {rec['run_at']}  {rec['family']}  {rec['verdicts']}  -> {rec['report_dir']}")
        if not registry.history(s):
            print("  (no runs yet)")
        return 0
    if a.cmd == "build":
        from .research.runner import gap_events
        _, audit = gap_events(s, rebuild=a.rebuild)
        print(json.dumps(audit, indent=2, default=str))
        return 0
    if a.cmd == "run":
        from .research.runner import run_family
        out = run_family(a.family, s, reps=a.reps, rerun_reason=a.rerun_reason)
        print((out / "REPORT.md").read_text())
        print(f"\nreport: {out}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
