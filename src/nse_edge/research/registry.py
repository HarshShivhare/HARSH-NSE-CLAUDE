"""Pre-registration registry (one-shot runs).

Each run records a fingerprint of: the family YAML, settings.yaml and every source file
under src/nse_edge. Rules:
  * a family that already has a recorded run is NOT run again, unless an explicit
    `rerun_reason` is given (e.g. a bug fix found by review); the reason is logged and
    the family file itself must be unchanged.
  * a family file whose hash differs from its recorded run is always refused - a changed
    idea needs a new family name, and counts as an additional test.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from ..config import PROJECT_ROOT, load_settings


class RegistryError(RuntimeError):
    pass


def file_hash(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def code_hash() -> str:
    h = hashlib.sha256()
    for p in sorted((PROJECT_ROOT / "src" / "nse_edge").rglob("*.py")):
        h.update(p.relative_to(PROJECT_ROOT).as_posix().encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def fingerprint(path: Path, settings=None) -> dict:
    s = settings or load_settings()
    return {
        "family_sha256": file_hash(path),
        "settings_sha256": file_hash(s.root / "config" / "settings.yaml"),
        "code_sha256": code_hash(),
    }


def load_family(path: Path) -> dict:
    spec = yaml.safe_load(Path(path).read_text())
    ids = [h["id"] for h in spec["hypotheses"]]
    if len(ids) != len(set(ids)):
        raise RegistryError("duplicate hypothesis ids")
    for h in spec["hypotheses"]:
        if h["side"] not in ("continuation", "reversal"):
            raise RegistryError(f"{h['id']}: side must be continuation|reversal")
    return spec


def _registry_path(settings=None) -> Path:
    return (settings or load_settings()).path("registry_file")


def history(settings=None) -> list[dict]:
    p = _registry_path(settings)
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


def check_preregistration(path: Path, settings=None, rerun_reason: str | None = None) -> dict:
    spec = load_family(path)
    fp = fingerprint(path, settings)
    prior = [r for r in history(settings) if r["family"] == spec["family"]]
    if any(r.get("family_sha256", r.get("sha256")) != fp["family_sha256"] for r in prior):
        raise RegistryError(
            f"family {spec['family']!r} was already run with a different file. Do not edit a "
            "pre-registered family after seeing results; copy it to a new family name.")
    if prior and not rerun_reason:
        raise RegistryError(
            f"family {spec['family']!r} already has {len(prior)} recorded run(s). Runs are one-shot; "
            "pass an explicit rerun reason (e.g. a reviewed bug fix) to run it again.")
    return fp


def record_run(path: Path, fp: dict, verdicts: dict, report_dir: Path, settings=None,
               rerun_reason: str | None = None) -> None:
    spec = load_family(path)
    p = _registry_path(settings)
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "family": spec["family"],
        "file": Path(path).name,
        **fp,
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rerun_reason": rerun_reason,
        "verdicts": verdicts,
        "report_dir": str(report_dir),
    }
    with p.open("a") as fh:
        fh.write(json.dumps(rec) + "\n")
