#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))

from phase67c_common import WIDE, EXPECTED, patch_surface


def main():
    if len(sys.argv)!=2 or sys.argv[1] not in {"ecmwf","gfs","icon"}:
        raise SystemExit("Uso: phase67c_extra_run.py <ecmwf|gfs|icon>")
    model=sys.argv[1]
    sys.argv=["phase66y_generate_extra.py",model]
    import phase66y_generate_extra as y

    y.GLOBAL_REQUESTED_BOUNDS.clear(); y.GLOBAL_REQUESTED_BOUNDS.update(WIDE)
    y.GLOBAL_EXPECTED_CELL_BOUNDS.clear(); y.GLOBAL_EXPECTED_CELL_BOUNDS.update(EXPECTED)

    def apply_wide(es,g20,g21,g23):
        # patch_surface espera el contenedor 66W; crear un adaptador mínimo.
        class W: pass
        w=W(); w.es=es; w.g20=g20; w.g21=g21; w.g23=g23
        w.GLOBAL_REQUESTED_BOUNDS=dict(WIDE); w.EXPECTED_BOUNDS=dict(EXPECTED)
        patch_surface(w)
    y.apply_global_surface_domain=apply_wide

    if model=="gfs":
        original_batch_url=y.g57._batch_url
        def batch_url_wide(run,step,left,right):
            if abs(float(left)-315.0)<1e-6: left=255.0
            return original_batch_url(run,step,left,right)
        y.g57._batch_url=batch_url_wide

    {"ecmwf":y.run_ecmwf,"gfs":y.run_gfs,"icon":y.run_icon}[model]()
    print(f"67C extras {model}: dominio {'105O..45E' if model!='icon' else 'ICON-EU nativo'}",flush=True)

if __name__=="__main__": main()
