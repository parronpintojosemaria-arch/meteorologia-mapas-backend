#!/usr/bin/env python3
"""Descarga el CTTH MTG más reciente y valida cloud_top_height real.

Fase de adquisición/validación únicamente. No interpola ni inventa alturas y no
publica todavía geometría 3D. Produce un informe JSON y una previsualización
para comprobar que el dato científico correcto llega desde EUMETSAT.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

import eumdac
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import netCDF4
import numpy as np

COLLECTION_ID = "EO:EUM:DAT:0681"  # FCI-2-CTTH, MTG 0 degree
OUTDIR = Path("output/cloud3d-ctth")


def fail(message: str, code: int = 1) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)


def recursive_variables(group: netCDF4.Group, prefix: str = ""):
    for name, var in group.variables.items():
        path = f"{prefix}/{name}" if prefix else name
        yield path, var
    for name, child in group.groups.items():
        child_prefix = f"{prefix}/{name}" if prefix else name
        yield from recursive_variables(child, child_prefix)


def choose_height_variable(ds: netCDF4.Dataset):
    exact = []
    candidates = []
    for path, var in recursive_variables(ds):
        leaf = path.split("/")[-1].lower()
        std_name = str(getattr(var, "standard_name", "")).lower()
        long_name = str(getattr(var, "long_name", "")).lower()
        if leaf == "cloud_top_height":
            exact.append((path, var))
        elif "cloud" in leaf and "height" in leaf:
            candidates.append((path, var))
        elif "cloud top" in long_name and "height" in long_name:
            candidates.append((path, var))
        elif "cloud_top" in std_name and "height" in std_name:
            candidates.append((path, var))
    pool = exact or candidates
    if not pool:
        names = [path for path, _ in recursive_variables(ds)]
        fail("No se encontró cloud_top_height. Variables disponibles: " + ", ".join(names[:120]))
    return pool[0]


def masked_to_float(var) -> np.ma.MaskedArray:
    data = np.ma.asarray(var[:], dtype=np.float64)
    if data.ndim > 2:
        # El producto debe contener una rejilla 2-D. Si hay dimensiones singleton,
        # se eliminan sin alterar valores; no se agregan ni interpolan datos.
        data = np.ma.squeeze(data)
    if data.ndim != 2:
        fail(f"cloud_top_height no es una rejilla 2-D después de squeeze: shape={data.shape}")
    return data


def stats_for(data: np.ma.MaskedArray):
    values = data.compressed()
    values = values[np.isfinite(values)]
    if values.size == 0:
        fail("cloud_top_height no contiene valores válidos")
    p = np.percentile(values, [1, 5, 25, 50, 75, 95, 99])
    return {
        "valid_pixels": int(values.size),
        "min": float(np.min(values)),
        "max": float(np.max(values)),
        "mean": float(np.mean(values)),
        "percentiles": {
            "p01": float(p[0]),
            "p05": float(p[1]),
            "p25": float(p[2]),
            "p50": float(p[3]),
            "p75": float(p[4]),
            "p95": float(p[5]),
            "p99": float(p[6]),
        },
    }


def save_preview(data: np.ma.MaskedArray, path: Path) -> None:
    # Solo vista de control en la rejilla nativa. No se usa todavía para el globo.
    h, w = data.shape
    stride = max(1, int(max(h, w) / 1800))
    view = data[::stride, ::stride]
    fig = plt.figure(figsize=(12, 9), dpi=150)
    ax = fig.add_subplot(111)
    im = ax.imshow(view, origin="upper", cmap="turbo", vmin=0, vmax=18000)
    ax.set_title("MTG FCI CTTH · cloud_top_height (rejilla nativa, control)")
    ax.set_xlabel("píxel X")
    ax.set_ylabel("píxel Y")
    cb = fig.colorbar(im, ax=ax, shrink=0.82)
    cb.set_label("altura del techo nuboso (m)")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def iso(value):
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def main() -> None:
    key = os.environ.get("EUMETSAT_CONSUMER_KEY", "").strip()
    secret = os.environ.get("EUMETSAT_CONSUMER_SECRET", "").strip()
    if not key or not secret:
        fail(
            "Faltan los secretos EUMETSAT_CONSUMER_KEY y/o EUMETSAT_CONSUMER_SECRET. "
            "Deben configurarse como GitHub Actions Secrets; no se escriben en el repositorio."
        )

    OUTDIR.mkdir(parents=True, exist_ok=True)
    token = eumdac.AccessToken((key, secret))
    datastore = eumdac.DataStore(token)
    collection = datastore.get_collection(COLLECTION_ID)

    now = dt.datetime.now(dt.timezone.utc)
    # Ventana holgada para soportar latencia o huecos puntuales del producto NRT.
    start = now - dt.timedelta(hours=6)
    products = list(collection.search(dtstart=start, dtend=now))
    if not products:
        fail(f"No hay productos CTTH en {COLLECTION_ID} entre {start.isoformat()} y {now.isoformat()}")

    def product_time(product):
        return getattr(product, "sensing_start", dt.datetime.min.replace(tzinfo=dt.timezone.utc))

    product = max(products, key=product_time)
    product_id = str(product)
    print(f"CTTH seleccionado: {product_id}")
    print(f"Sensing start: {getattr(product, 'sensing_start', None)}")

    with tempfile.TemporaryDirectory(prefix="ctth-") as tmp_name:
        tmp = Path(tmp_name)
        with product.open() as source:
            source_name = Path(getattr(source, "name", f"{product_id}.bin")).name
            downloaded = tmp / source_name
            with downloaded.open("wb") as dest:
                shutil.copyfileobj(source, dest)

        if zipfile.is_zipfile(downloaded):
            extract_dir = tmp / "extracted"
            extract_dir.mkdir()
            with zipfile.ZipFile(downloaded) as zf:
                zf.extractall(extract_dir)
            nc_files = sorted(extract_dir.rglob("*.nc")) + sorted(extract_dir.rglob("*.nc4"))
        else:
            nc_files = [downloaded] if downloaded.suffix.lower() in {".nc", ".nc4"} else []

        if not nc_files:
            # Algunos productos usan nombres sin extensión reconocible; intentamos abrir
            # el archivo descargado directamente antes de rendirnos.
            nc_files = [downloaded]

        selected_nc = None
        selected_path = None
        selected_var = None
        last_error = None
        for nc_path in nc_files:
            try:
                ds = netCDF4.Dataset(nc_path, "r")
                try:
                    var_path, var = choose_height_variable(ds)
                    selected_nc = nc_path
                    selected_path = var_path
                    selected_var = (ds, var)
                    break
                except BaseException:
                    ds.close()
                    raise
            except Exception as exc:
                last_error = exc

        if selected_var is None:
            fail(f"No se pudo abrir un NetCDF CTTH válido: {last_error}")

        ds, var = selected_var
        try:
            data = masked_to_float(var)
            units = str(getattr(var, "units", "")) or None
            info = stats_for(data)
            preview_path = OUTDIR / "ctth-cloud-top-height-preview.png"
            save_preview(data, preview_path)

            report = {
                "status": "ok",
                "source": "EUMETSAT Data Store",
                "collection_id": COLLECTION_ID,
                "product_family": "FCI-2-CTTH",
                "product_id": product_id,
                "sensing_start": iso(getattr(product, "sensing_start", None)),
                "sensing_end": iso(getattr(product, "sensing_end", None)),
                "variable": selected_path,
                "units": units,
                "shape": list(data.shape),
                "statistics": info,
                "scientific_rule": "Alturas tomadas exclusivamente de cloud_top_height; sin interpolación ni alturas inventadas.",
                "phase": "adquisicion_y_validacion; todavía no es geometría 3D",
            }
            (OUTDIR / "ctth-report.json").write_text(
                json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            print(json.dumps(report, indent=2, ensure_ascii=False))
        finally:
            ds.close()


if __name__ == "__main__":
    main()
