#!/usr/bin/env python3
from __future__ import annotations

import importlib
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from phase67c_common import patch_aloft, install_pressure_palette, render_500_exact

TARGETS = {
    "500hpa": {"module": "synoptic_500_full_phase66j", "phase": "66J", "input": "phase66j-input", "selector": "h", "config": "config"},
    "850hpa": {"module": "synoptic_850_full_phase66k", "phase": "66K", "input": "phase66k-input", "selector": "h", "config": "config"},
    "700hpa": {"module": "synoptic_700_full_phase66l", "phase": "66L", "input": "phase66l-input", "selector": "h", "config": "config"},
    "925hpa": {"module": "synoptic_925_full_phase66m", "phase": "66M", "input": "phase66m-input", "selector": "l.h", "config": "l.config"},
    "300hpa": {"module": "synoptic_300_full_phase66n", "phase": "66N", "input": "phase66n-input", "selector": "h", "config": "config"},
    "250hpa": {"module": "synoptic_250_full_phase66o", "phase": "66O", "input": "phase66o-input", "selector": "n.h", "config": "n.config"},
    "200hpa": {"module": "synoptic_200_full_phase66p", "phase": "66P", "input": "phase66p-input", "selector": "o.n.h", "config": "o.n.config"},
    "jet300": {"module": "jet_300_full_phase66q", "phase": "66Q", "input": "phase66q-input", "selector": "self"},
    "jet250": {"module": "jet_250_full_phase66r", "phase": "66R", "input": "phase66r-input", "selector": "self"},
    "jet200": {"module": "jet_200_full_phase66s", "phase": "66S", "input": "phase66s-input", "selector": "self"},
}
PRESSURE = {"500hpa","850hpa","700hpa","925hpa","300hpa","250hpa","200hpa"}


def parse_run(text: str) -> datetime:
    dt=datetime.fromisoformat(text.replace("Z","+00:00"))
    if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def resolve_attr(obj,path: str):
    for part in path.split("."): obj=getattr(obj,part)
    return obj


def find_h(mod):
    for path in ("h","l.h","n.h","o.n.h","r.n.h","r.q.n.h"):
        try:
            obj=resolve_attr(mod,path)
            if hasattr(obj,"p66e"): return obj
        except Exception:
            pass
    # Los Jets importan módulos anidados; buscar recursivamente por nombres conocidos.
    stack=[mod]; seen=set()
    while stack:
        obj=stack.pop()
        if id(obj) in seen: continue
        seen.add(id(obj))
        if hasattr(obj,"p66e"): return obj
        for name in ("h","l","n","o","q","r"):
            child=getattr(obj,name,None)
            if child is not None: stack.append(child)
    raise RuntimeError("67C: no se pudo localizar motor 66H")


def main():
    if len(sys.argv)!=4: raise SystemExit("Uso: phase67c_run_layer.py <capa> <ecmwf|gfs|icon> <run_utc>")
    slug,model,run_text=sys.argv[1],sys.argv[2].lower(),sys.argv[3]
    if slug not in TARGETS or model not in {"ecmwf","gfs","icon"}: raise SystemExit("Capa o modelo no válido")
    target=TARGETS[slug]; run_dt=parse_run(run_text)
    sys.argv=[target["module"]+".py",model]
    mod=importlib.import_module(target["module"])
    mod.OUT=ROOT/target["input"]
    h=find_h(mod)
    patch_aloft(h,model)
    if slug in PRESSURE:
        install_pressure_palette()
        if slug=="500hpa":
            h._render_premium=lambda t,z,p,bounds,out: render_500_exact(h,model,t,z,p,bounds,out)

    if target["selector"]=="self":
        if hasattr(mod,"_getter"): getter=mod._getter()
        elif hasattr(mod,"r") and hasattr(mod.r,"_getter"): getter=mod.r._getter()
        else: raise RuntimeError(f"{slug}: getter Jet no resuelto")
        validate=mod._validate
        def selected(max_step):
            speed,gh,_bounds,_sources=getter(run_dt,max_step); validate(speed,gh); return run_dt
        mod._select_run=selected
    else:
        holder=resolve_attr(mod,target["selector"]); config_fn=resolve_attr(mod,target["config"])
        def selected(steps):
            cfg=config_fn(); cfg["getter"](run_dt,max(steps)); return run_dt
        holder._select_run=selected

    print(f"67C {slug} {model}: ciclo {run_dt.isoformat()} · dominio amplio ECMWF/GFS · motor {target['phase']}",flush=True)
    mod.main()

if __name__=="__main__": main()
