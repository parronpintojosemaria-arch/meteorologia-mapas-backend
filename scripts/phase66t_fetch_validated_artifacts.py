#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experimental-phase66t"
LAYERS = OUT / "layers"
TOKEN = os.environ.get("GITHUB_TOKEN", "")
REPO = os.environ.get("GITHUB_REPOSITORY", "parronpintojosemaria-arch/meteorologia-mapas-backend")
API = f"https://api.github.com/repos/{REPO}"

SPECS = (
    {"code": "66J", "branch": "phase66j-500-full-horizon", "slug": "500hpa", "label": "500 hPa"},
    {"code": "66K", "branch": "phase66k-850-full-horizon", "slug": "850hpa", "label": "850 hPa"},
    {"code": "66L", "branch": "phase66l-700-full-horizon", "slug": "700hpa", "label": "700 hPa"},
    {"code": "66M", "branch": "phase66m-925-full-horizon", "slug": "925hpa", "label": "925 hPa"},
    {"code": "66N", "branch": "phase66n-300-full-horizon", "slug": "300hpa", "label": "300 hPa"},
    {"code": "66O", "branch": "phase66o-250-full-horizon", "slug": "250hpa", "label": "250 hPa"},
    {"code": "66P", "branch": "phase66p-200-full-horizon", "slug": "200hpa", "label": "200 hPa"},
    {"code": "66Q", "branch": "phase66q-jet300-full-horizon", "slug": "jet300", "label": "Jet Stream 300 hPa"},
    {"code": "66R", "branch": "phase66r-jet250-full-horizon", "slug": "jet250", "label": "Jet Stream 250 hPa"},
    {"code": "66S", "branch": "phase66s-jet200-full-horizon", "slug": "jet200", "label": "Jet Stream 200 hPa"},
)


def _headers():
    h = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "phase66t-master-integration",
    }
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    return h


def _json(url: str):
    req = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))


def _download(url: str, path: Path):
    req = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(req, timeout=180) as r, path.open("wb") as f:
        shutil.copyfileobj(r, f, length=1024 * 1024)


def _latest_successful_run(spec):
    q = urllib.parse.urlencode({
        "branch": spec["branch"],
        "status": "success",
        "per_page": 30,
    })
    data = _json(f"{API}/actions/runs?{q}")
    token = spec["code"].lower()
    runs = [
        r for r in data.get("workflow_runs", [])
        if r.get("head_branch") == spec["branch"]
        and r.get("conclusion") == "success"
        and token in f"{r.get('name','')} {r.get('path','')}".lower()
    ]
    if not runs:
        raise RuntimeError(f"{spec['code']}: no hay run success localizable en {spec['branch']}")
    runs.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
    return runs[0]


def _maplibre_artifact(run_id: int, spec):
    data = _json(f"{API}/actions/runs/{run_id}/artifacts?per_page=100")
    arts = [
        a for a in data.get("artifacts", [])
        if not a.get("expired")
        and "maplibre" in a.get("name", "").lower()
    ]
    if not arts:
        raise RuntimeError(f"{spec['code']}: no existe artefacto MapLibre vigente en run {run_id}")
    arts.sort(key=lambda a: a.get("created_at", ""), reverse=True)
    return arts[0]


def _extract_normalized(zip_path: Path, final_dir: Path, report_name: str):
    with tempfile.TemporaryDirectory(prefix="phase66t-unzip-") as td:
        tmp = Path(td)
        with zipfile.ZipFile(zip_path) as z:
            root = tmp.resolve()
            for member in z.infolist():
                dest = (tmp / member.filename).resolve()
                if not dest.is_relative_to(root):
                    raise RuntimeError(f"ZIP inseguro: {member.filename}")
            z.extractall(tmp)

        reports = list(tmp.rglob(report_name))
        if len(reports) != 1:
            raise RuntimeError(f"{report_name}: se esperaba 1 copia y hay {len(reports)}")
        source_root = reports[0].parent
        if final_dir.exists():
            shutil.rmtree(final_dir)
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_root, final_dir)


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    LAYERS.mkdir(parents=True, exist_ok=True)

    sources = {
        "phase": "66T",
        "status": "ok",
        "repository": REPO,
        "source_policy": "último run success de cada rama validada; artefacto MapLibre no expirado",
        "layers": {},
    }

    for spec in SPECS:
        run = _latest_successful_run(spec)
        art = _maplibre_artifact(int(run["id"]), spec)
        report_name = f"report-phase{spec['code'].lower()}.json"
        final_dir = LAYERS / spec["slug"]

        with tempfile.TemporaryDirectory(prefix="phase66t-download-") as td:
            zip_path = Path(td) / f"{spec['slug']}.zip"
            print(f"{spec['code']} {spec['label']}: run {run['id']} · {art['name']} · {art['size_in_bytes']} bytes", flush=True)
            _download(art["archive_download_url"], zip_path)
            _extract_normalized(zip_path, final_dir, report_name)

        sources["layers"][spec["slug"]] = {
            "code": spec["code"],
            "label": spec["label"],
            "branch": spec["branch"],
            "run_id": int(run["id"]),
            "run_head_sha": run.get("head_sha"),
            "run_updated_at": run.get("updated_at"),
            "artifact_id": int(art["id"]),
            "artifact_name": art["name"],
            "artifact_size_bytes": int(art["size_in_bytes"]),
            "artifact_digest": art.get("digest"),
            "artifact_expires_at": art.get("expires_at"),
            "report": report_name,
        }

    (OUT / "sources-phase66t.json").write_text(
        json.dumps(sources, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"layers": len(sources["layers"]), "status": "ok"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
