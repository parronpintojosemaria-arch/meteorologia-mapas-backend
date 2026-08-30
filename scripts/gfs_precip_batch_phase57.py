#!/usr/bin/env python3
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np

import gfs_surface_phase21 as g21
import precip_type_intensity_phase30 as p30

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data-gfs-precip-batch57"
RAW.mkdir(exist_ok=True)

BATCH_VARS = ("PRATE", "CRAIN", "CSNOW", "CFRZR", "CICEP")
SHORT_NAMES = {
    "precipitation_rate": "prate",
    "rain": "crain",
    "snow": "csnow",
    "freezing_rain": "cfrzr",
    "ice_pellets": "cicep",
}


def _main_manifest() -> dict:
    path = ROOT / "public-phase29" / "gfs" / "manifest-phase29-gfs.json"
    if not path.exists():
        raise RuntimeError(f"Falta el manifiesto principal GFS: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("status") != "ok" or data.get("horizon_hours") != 384:
        raise RuntimeError(f"El GFS principal no está validado: {data.get('status')} / {data.get('horizon_hours')}")
    if not data.get("run_utc"):
        raise RuntimeError("El manifiesto principal GFS no contiene run_utc")
    return data


def _batch_url(run_dt: datetime, step: int, leftlon: float, rightlon: float) -> str:
    cycle = run_dt.strftime("%H")
    params = {
        "file": f"gfs.t{cycle}z.pgrb2.0p25.f{step:03d}",
        "lev_surface": "on",
        "subregion": "",
        "leftlon": str(leftlon),
        "rightlon": str(rightlon),
        "toplat": str(g21.NORTH),
        "bottomlat": str(g21.SOUTH),
        "dir": f"/gfs.{run_dt:%Y%m%d}/{cycle}/atmos",
    }
    for var in BATCH_VARS:
        params[f"var_{var}"] = "on"
    return g21.BASE + "?" + urlencode(params)


def _download(url: str, target: Path, label: str) -> None:
    last = None
    for attempt in range(1, 6):
        try:
            req = Request(url, headers={"User-Agent": "Meteorologia-Interactiva/1.0"})
            with urlopen(req, timeout=120) as response:
                data = response.read()
            if len(data) < 100 or not data.startswith(b"GRIB"):
                preview = data[:240].decode("utf-8", errors="ignore")
                raise RuntimeError(f"NOMADS no devolvió GRIB válido: {preview}")
            target.write_bytes(data)
            return
        except (HTTPError, URLError, TimeoutError, RuntimeError) as exc:
            last = exc
            if attempt == 5:
                break
            time.sleep(3 * attempt)
    raise RuntimeError(f"No se pudo descargar {label} tras 5 intentos: {last}")


def _download_step(run_dt: datetime, step: int) -> tuple[Path, Path, list[str]]:
    west = RAW / f"gfs_batch_{run_dt:%Y%m%d%H}_f{step:03d}_west.grib2"
    east = RAW / f"gfs_batch_{run_dt:%Y%m%d%H}_f{step:03d}_east.grib2"
    url_w = _batch_url(run_dt, step, 335, 359.999)
    url_e = _batch_url(run_dt, step, 0, 45)
    if not west.exists() or west.stat().st_size < 100:
        _download(url_w, west, f"GFS f{step:03d} oeste")
    time.sleep(0.35)
    if not east.exists() or east.stat().st_size < 100:
        _download(url_e, east, f"GFS f{step:03d} este")
    return west, east, [url_w, url_e]


def _field(west: Path, east: Path, short_name: str):
    filters = {"shortName": short_name, "stepType": "instant"}
    west_da = g21.open_single(west, filters)
    east_da = g21.open_single(east, filters)
    return g21.join_west_east(west_da, east_da)


def _read_step(run_dt: datetime, step: int) -> dict:
    west, east, urls = _download_step(run_dt, step)
    values = {}
    for key, short_name in SHORT_NAMES.items():
        vals, units, bounds = _field(west, east, short_name)
        p30.check_bounds(bounds, f"GFS {short_name} f{step:03d}")
        values[key] = {"values": vals, "units": units, "bounds": bounds}

    reference = values["precipitation_rate"]
    for key in ("rain", "snow", "freezing_rain", "ice_pellets"):
        item = values[key]
        if item["values"].shape != reference["values"].shape or not p30.same_bounds(item["bounds"], reference["bounds"]):
            raise RuntimeError(f"Malla GFS incompatible para {key} en f{step:03d}")
    values["urls"] = urls
    return values


def generate(steps: tuple[int, ...]) -> None:
    main = _main_manifest()
    run_dt = datetime.fromisoformat(main["run_utc"].replace("Z", "+00:00"))
    base = p30.PUBLIC / "gfs"

    manifest = {
        "schema": 30,
        "model": "NOAA GFS",
        "data_provider": "NOAA/NCEP NOMADS",
        "run_utc": run_dt.isoformat(),
        "projection": "EPSG:3857",
        "steps_tested": list(steps),
        "download_strategy": "PRATE+CRAIN+CSNOW+CFRZR+CICEP agrupados por mitad geográfica; misma pasada que Fase 29",
        "variables": {
            "precipitation_rate": {"source_parameter": "PRATE", "units": "mm/h", "step_type": "instant"},
            "precipitation_type": {
                "source_parameters": ["CRAIN", "CSNOW", "CFRZR", "CICEP"],
                "encoding": "bitmask: rain=1, snow=2, freezing_rain=4, ice_pellets=8",
                "code_table": {str(k): v for k, v in p30.GFS_BITS.items()},
                "note": "No se fuerza una categoría única: las combinaciones oficiales GFS se conservan en una máscara de bits.",
            },
        },
        "steps": {},
        "status": "ok",
    }

    successes = 0
    failures: list[str] = []
    prefetched: dict[int, dict] = {}

    last_step = max(steps)
    try:
        prefetched[last_step] = _read_step(run_dt, last_step)
    except Exception as exc:
        raise RuntimeError(f"La pasada GFS {run_dt.isoformat()} no tiene completa la precipitación en f{last_step:03d}: {exc}") from exc

    for step in steps:
        sk = f"f{step:03d}"
        manifest["steps"][sk] = {}
        try:
            data = prefetched.pop(step) if step in prefetched else _read_step(run_dt, step)
            rate = data["precipitation_rate"]
            mmh = p30.rate_to_mmh(rate["values"], rate["units"])
            rate_out = base / "precipitation_rate" / f"{sk}.webp"
            p30.render_precip_rate(mmh, rate["bounds"], rate_out, g21.project)
            manifest["steps"][sk]["precipitation_rate"] = {
                "status": "ok",
                "image": p30.rel(rate_out),
                "bounds": rate["bounds"],
                "units": "mm/h",
                "range": g21.finite_range(mmh),
                "raw_units": rate["units"],
                "step_type": "instant",
                "source_requests": data["urls"],
            }
            successes += 1

            fields = {
                "rain": data["rain"]["values"] >= 0.5,
                "snow": data["snow"]["values"] >= 0.5,
                "freezing_rain": data["freezing_rain"]["values"] >= 0.5,
                "ice_pellets": data["ice_pellets"]["values"] >= 0.5,
            }
            code = (
                fields["rain"].astype("int16")
                + 2 * fields["snow"].astype("int16")
                + 4 * fields["freezing_rain"].astype("int16")
                + 8 * fields["ice_pellets"].astype("int16")
            )
            type_out = base / "precipitation_type" / f"{sk}.webp"
            p30.render_types(code.astype("float32"), rate["bounds"], type_out, p30.TYPE_COLORS)
            manifest["steps"][sk]["precipitation_type"] = {
                "status": "ok",
                "image": p30.rel(type_out),
                "bounds": rate["bounds"],
                "distribution": p30.distribution(code.astype("float32"), p30.GFS_BITS),
                "step_type": "instant",
                "source_requests": data["urls"],
            }
            successes += 1
        except Exception as exc:
            failures.append(f"GFS {sk}: {exc}")

        time.sleep(0.45)

    expected = len(steps) * 2
    manifest["summary"] = {"successes": successes, "failures": len(failures), "expected": expected}
    if failures or successes != expected:
        manifest["status"] = "error"
        manifest["failure_notes"] = failures

    base.mkdir(parents=True, exist_ok=True)
    (base / "manifest-phase30-gfs.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest["summary"], ensure_ascii=False))
    print("run_utc=", run_dt.isoformat())
    print("requests_teoricas=", len(steps) * 2, "frente a 190 del generador anterior")

    if manifest["status"] != "ok":
        raise RuntimeError("GFS precipitación agrupada incompleta: " + " | ".join(failures[:6]))