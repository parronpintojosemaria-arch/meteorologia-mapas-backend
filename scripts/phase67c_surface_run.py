#!/usr/bin/env python3
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))

from phase67c_common import patch_surface


def parse_run(raw: str):
    dt=datetime.fromisoformat(raw.replace("Z","+00:00"))
    if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def main():
    if len(sys.argv)!=3 or sys.argv[1] not in {"ecmwf","gfs","icon"}:
        raise SystemExit("Uso: phase67c_surface_run.py <ecmwf|gfs|icon> <run_utc>")
    model=sys.argv[1]; run_dt=parse_run(sys.argv[2])
    sys.argv=["phase66w_surface_full.py",model,run_dt.isoformat()]
    import phase66w_surface_full as w
    if model in {"ecmwf","gfs"}:
        patch_surface(w)
    w.OUT=ROOT/"candidate-phase66w-surface"
    {"ecmwf":w.ecmwf,"gfs":w.gfs,"icon":w.icon}[model](run_dt)
    print(f"67C superficie {model}: {run_dt.isoformat()} · {'105O..45E' if model!='icon' else 'dominio ICON-EU nativo'}",flush=True)

if __name__=="__main__": main()
