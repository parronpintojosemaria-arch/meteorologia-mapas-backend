#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_cycle(cycle_dir: Path) -> dict:
    manifest_path = cycle_dir / "manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError("falta manifest.json")

    manifest = load_json(manifest_path)
    if manifest.get("status") != "ready":
        raise RuntimeError("manifest.status no es ready")

    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise RuntimeError("manifest.files vacío o inválido")

    expected_count = manifest.get("expected_count")
    if expected_count is None:
        expected_count = len(files)
    if int(expected_count) != len(files):
        raise RuntimeError(f"expected_count={expected_count} pero manifest.files={len(files)}")

    seen = set()
    total_bytes = 0
    for item in files:
        rel = item.get("path")
        if not rel or rel in seen:
            raise RuntimeError(f"ruta inválida o duplicada: {rel!r}")
        seen.add(rel)
        p = (cycle_dir / rel).resolve()
        if cycle_dir.resolve() not in p.parents:
            raise RuntimeError(f"ruta fuera de la pasada: {rel}")
        if not p.is_file():
            raise RuntimeError(f"falta archivo: {rel}")
        size = p.stat().st_size
        if size <= 0:
            raise RuntimeError(f"archivo vacío: {rel}")
        total_bytes += size
        wanted_sha = item.get("sha256")
        if wanted_sha and sha256_file(p).lower() != wanted_sha.lower():
            raise RuntimeError(f"sha256 no coincide: {rel}")

    return {
        "manifest": manifest,
        "file_count": len(files),
        "total_bytes": total_bytes,
        "manifest_sha256": sha256_file(manifest_path),
    }


def build_current(model: str, cycle: str, base_url: str, validation: dict) -> dict:
    base_url = base_url.rstrip("/")
    return {
        "schema": 1,
        "model": model,
        "status": "ready",
        "cycle": cycle,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "manifest_url": f"{base_url}/cycles/{cycle}/manifest.json",
        "cycle_url": f"{base_url}/cycles/{cycle}",
        "file_count": validation["file_count"],
        "total_bytes": validation["total_bytes"],
        "manifest_sha256": validation["manifest_sha256"],
    }


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def self_test() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        current_path = root / "control" / "models" / "ecmwf" / "current.json"
        old = {"schema": 1, "model": "ecmwf", "cycle": "OLD", "status": "ready"}
        atomic_write_json(current_path, old)

        good = root / "cycles" / "GOOD"
        good.mkdir(parents=True)
        (good / "images").mkdir()
        img = good / "images" / "f000.webp"
        img.write_bytes(b"valid-test-payload")
        manifest = {
            "schema": 1,
            "model": "ecmwf",
            "cycle": "GOOD",
            "status": "ready",
            "expected_count": 1,
            "files": [{"path": "images/f000.webp", "sha256": sha256_file(img)}],
        }
        atomic_write_json(good / "manifest.json", manifest)
        val = validate_cycle(good)
        atomic_write_json(current_path, build_current("ecmwf", "GOOD", "https://example.test/ecmwf", val))
        assert load_json(current_path)["cycle"] == "GOOD"

        atomic_write_json(current_path, old)
        bad = root / "cycles" / "BAD"
        bad.mkdir(parents=True)
        atomic_write_json(bad / "manifest.json", {
            "schema": 1,
            "model": "ecmwf",
            "cycle": "BAD",
            "status": "ready",
            "expected_count": 1,
            "files": [{"path": "images/missing.webp"}],
        })
        failed = False
        try:
            validate_cycle(bad)
        except RuntimeError:
            failed = True
        assert failed, "la pasada incompleta tenía que fallar"
        assert load_json(current_path)["cycle"] == "OLD", "current.json cambió tras una pasada fallida"

    print("VNEXT CURRENT GUARD SELF-TEST OK")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model")
    ap.add_argument("--cycle")
    ap.add_argument("--cycle-dir")
    ap.add_argument("--base-url")
    ap.add_argument("--output")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        self_test()
        return

    required = [args.model, args.cycle, args.cycle_dir, args.base_url, args.output]
    if any(x is None for x in required):
        ap.error("faltan argumentos: --model --cycle --cycle-dir --base-url --output")

    cycle_dir = Path(args.cycle_dir)
    validation = validate_cycle(cycle_dir)
    payload = build_current(args.model, args.cycle, args.base_url, validation)
    atomic_write_json(Path(args.output), payload)
    print(json.dumps({"status": "ok", "model": args.model, "cycle": args.cycle, "files": validation["file_count"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
