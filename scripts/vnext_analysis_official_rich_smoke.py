#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

import vnext_analysis_official_smoke as B
import vnext_gfs_preflight as G
import vnext_ecmwf_data as E
import icon_eu_surface_phase35 as I35
import icon_eu_pressure_jet_phase36 as I36

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "vnext-analysis-official-rich-smoke"
OUT.mkdir(parents=True, exist_ok=True)
POINT = B.POINTS["malaga"]
STEP = 24


def sample(values, bounds, units, convert=None):
    if convert:
        values = convert(values, units)
        units = convert.__name__.replace("to_", "")
    v, glat, glon = B.sample_regular(values, bounds, POINT["lat"], POINT["lon"])
    return {
        "value": None if v is None else round(float(v), 4),
        "units": units,
        "nearest_grid": {"lat": round(glat, 4), "lon": round(glon, 4)},
    }


def optional(fn):
    try:
        return {"ok": True, "data": fn()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def gfs_rich(run):
    out = {"provider": "NOAA/NCEP NOMADS", "cycle": run.isoformat(), "valid_step_h": STEP, "fields": {}}
    for lev in (700, 500):
        rh, rhu, rhb, _ = G.retrieve(run, STEP, f"lev_{lev}_mb", "var_RH")
        vv, vvu, vvb, _ = G.retrieve(run, STEP, f"lev_{lev}_mb", "var_VVEL")
        out["fields"][f"relative_humidity_{lev}hpa"] = sample(rh, rhb, rhu)
        out["fields"][f"vertical_velocity_{lev}hpa"] = sample(vv, vvb, vvu)

    def cape():
        a, u, b, urls = G.retrieve(run, STEP, "lev_surface", "var_CAPE")
        return {"sample": sample(a, b, u), "source_urls": urls}
    def cin():
        a, u, b, urls = G.retrieve(run, STEP, "lev_surface", "var_CIN")
        return {"sample": sample(a, b, u), "source_urls": urls}
    def zero():
        a, u, b, urls = G.retrieve(run, STEP, "lev_0C_isotherm", "var_HGT")
        return {"sample": sample(G.height_m(a, u), b, "m"), "source_urls": urls}
    def pwat():
        errors=[]
        for level in ("lev_entire_atmosphere_(considered_as_a_single_layer)", "lev_entire_atmosphere"):
            try:
                a,u,b,urls=G.retrieve(run, STEP, level, "var_PWAT")
                return {"sample": sample(a,b,u), "source_urls":urls, "level_key":level}
            except Exception as exc:
                errors.append(str(exc))
        raise RuntimeError("PWAT no disponible: "+" | ".join(errors[-2:]))

    out["fields"]["cape"] = optional(cape)
    out["fields"]["cin"] = optional(cin)
    out["fields"]["freezing_level_height"] = optional(zero)
    out["fields"]["precipitable_water"] = optional(pwat)
    return out


def ecmwf_rich(run):
    out = {"provider": "ECMWF Open Data", "cycle": run.isoformat(), "valid_step_h": STEP, "fields": {}}

    raw = E.RAW_ROOT / f"analysis_rich_pl_{run:%Y%m%d%H}_f{STEP:03d}.grib2"
    src, errs = E.retrieve("lower", run, STEP, "pl", ["r", "w", "vo", "d"], raw, levels=[700, 500])
    ds = E.open_grib(raw)
    try:
        for lev in (700, 500):
            for key, aliases in (("relative_humidity", {"r"}), ("vertical_velocity", {"w"}), ("vorticity", {"vo"}), ("divergence", {"d"})):
                da = E.find_da_level(ds, aliases, lev)
                vals, units, bounds = E.grid(da, lev)
                out["fields"][f"{key}_{lev}hpa"] = sample(vals, bounds, units)
    finally:
        raw.unlink(missing_ok=True)
    out["pressure_distribution"] = {"mirror": src, "warnings": errs[-3:]}

    def sfc(params):
        path=E.RAW_ROOT/f"analysis_rich_sfc_{run:%Y%m%d%H}_f{STEP:03d}_{'_'.join(params)}.grib2"
        source, errors=E.retrieve("surface",run,STEP,"sfc",params,path)
        dsets=E.open_grib(path)
        rows={}
        try:
            for param in params:
                da=E.find_da(dsets,{param})
                vals,units,bounds=E.grid(da)
                if param=="2d":
                    vals=E.celsius(vals,units); units="°C"
                rows[param]=sample(vals,bounds,units)
        finally:
            path.unlink(missing_ok=True)
        return {"fields":rows,"mirror":source,"warnings":errors[-3:]}

    surface=optional(lambda:sfc(["mucape","tcwv","2d"]))
    out["surface_extra"]=surface
    return out


def icon_rich(run):
    out = {"provider": "Deutscher Wetterdienst (DWD) Open Data", "cycle": run.isoformat(), "valid_step_h": STEP, "fields": {}}
    for lev in (700, 500):
        for key, directory, code in (
            ("relative_humidity", "relhum", "RELHUM"),
            ("vertical_velocity", "omega", "OMEGA"),
        ):
            path, url = I36.download_pressure(run, STEP, lev, directory, code)
            vals, units, bounds, *_ = I35.read_regular(path)
            out["fields"][f"{key}_{lev}hpa"] = {
                **sample(vals, bounds, units),
                "source_url": url,
            }

    for key, directory, code in (
        ("cape_ml", "cape_ml", "CAPE_ML"),
        ("cin_ml", "cin_ml", "CIN_ML"),
        ("freezing_level_height", "hzerocl", "HZEROCL"),
        ("total_column_water_vapour", "tqv", "TQV"),
        ("relative_humidity_2m", "relhum_2m", "RELHUM_2M"),
    ):
        def load(directory=directory,code=code):
            path,url=B.icon_download_single(run,STEP,directory,code)
            vals,units,bounds,*_=I35.read_regular(path)
            return {"sample":sample(vals,bounds,units),"source_url":url}
        out["fields"][key]=optional(load)
    return out


def main():
    base_path = ROOT / "vnext-analysis-official-smoke" / "analysis-official-smoke.json"
    if not base_path.exists():
        raise RuntimeError("Ejecuta primero vnext_analysis_official_smoke.py")
    base=json.loads(base_path.read_text(encoding="utf-8"))
    if base.get("status")!="ok":
        raise RuntimeError("Smoke base no está OK")

    grun=datetime.fromisoformat(base["models"]["gfs"]["cycle"])
    erun=datetime.fromisoformat(base["models"]["ecmwf"]["cycle"])
    irun=datetime.fromisoformat(base["models"]["icon_eu"]["cycle"])

    report={
        "schema":"mi-analysis-official-rich-smoke-1",
        "generated_at_utc":datetime.now(timezone.utc).isoformat(),
        "status":"ok",
        "point":POINT,
        "step_h":STEP,
        "models":{
            "gfs":gfs_rich(grun),
            "ecmwf":ecmwf_rich(erun),
            "icon_eu":icon_rich(irun),
        },
        "rules":{
            "no_open_meteo":True,
            "missing_fields":"se registran como no disponibles; nunca se inventan ni se rellenan",
            "interpretation":"los diagnósticos espaciales se harán con campos oficiales, no con un único punto",
        },
    }
    for key in ("gfs","ecmwf","icon_eu"):
        fields=report["models"][key]["fields"]
        assert fields["relative_humidity_700hpa"]["value"] is not None
        assert fields["vertical_velocity_700hpa"]["value"] is not None
    path=OUT/"analysis-official-rich-smoke.json"
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":"ok","file":str(path),"models":list(report["models"])},ensure_ascii=False))


if __name__=="__main__":
    main()
