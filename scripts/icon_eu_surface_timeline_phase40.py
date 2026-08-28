#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import icon_eu_full_timeline_phase39 as p39
import icon_eu_horizon_phase37 as h37

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public-icon40" / "icon-eu"
PUBLIC.mkdir(parents=True, exist_ok=True)

EXPECTED_STEPS = p39.EXPECTED_STEPS

FIELDS = {
    "temperature_2m": {"directory": "t_2m", "kind": "single", "code": "T_2M"},
    "wind_u_10m": {"directory": "u_10m", "kind": "single", "code": "U_10M"},
    "wind_v_10m": {"directory": "v_10m", "kind": "single", "code": "V_10M"},
    "cloud_cover_total": {"directory": "clct", "kind": "single", "code": "CLCT"},
    "precipitation_total": {"directory": "tot_prec", "kind": "single", "code": "TOT_PREC"},
    "rain_gsp": {"directory": "rain_gsp", "kind": "single", "code": "RAIN_GSP"},
    "rain_con": {"directory": "rain_con", "kind": "single", "code": "RAIN_CON"},
    "snow_gsp": {"directory": "snow_gsp", "kind": "single", "code": "SNOW_GSP"},
    "snow_con": {"directory": "snow_con", "kind": "single", "code": "SNOW_CON"},
}


def main():
    run_dt = h37.choose_run()
    expected = set(EXPECTED_STEPS)
    manifest = {
        "schema": 40,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": "DWD ICON-EU",
        "data_provider": "Deutscher Wetterdienst (DWD) Open Data",
        "run_utc": run_dt.isoformat(),
        "purpose": "Validar todos los campos de superficie necesarios antes de generar la línea temporal completa.",
        "timeline_rule": "+0..+78 h cada 1 h; +81..+120 h cada 3 h",
        "expected_steps": list(EXPECTED_STEPS),
        "expected_step_count": len(EXPECTED_STEPS),
        "fields": {},
        "status": "ok",
    }

    failures = []
    successes = 0

    if len(EXPECTED_STEPS) != 93:
        failures.append(f"Calendario heredado de Fase 39 no tiene 93 pasos: {len(EXPECTED_STEPS)}")

    for key, spec in FIELDS.items():
        try:
            published, url = p39.published_steps(run_dt, spec)
            relevant = sorted(s for s in published if 0 <= s <= 120)
            relevant_set = set(relevant)
            missing = sorted(expected - relevant_set)
            unexpected = sorted(relevant_set - expected)
            ok = len(relevant) == 93 and not missing and not unexpected
            rec = {
                "directory_url": url,
                "published_steps_0_120": relevant,
                "published_step_count_0_120": len(relevant),
                "missing_expected_steps": missing,
                "unexpected_steps_0_120": unexpected,
                "first_step": relevant[0] if relevant else None,
                "last_step": relevant[-1] if relevant else None,
                "status": "ok" if ok else "error",
            }
            manifest["fields"][key] = rec
            if ok:
                successes += len(EXPECTED_STEPS)
            else:
                failures.append(
                    f"{key}: count={len(relevant)} missing={missing[:15]} unexpected={unexpected[:15]}"
                )
        except Exception as exc:
            manifest["fields"][key] = {"status": "error", "error": str(exc)}
            failures.append(f"{key}: {exc}")

    expected_checks = len(FIELDS) * len(EXPECTED_STEPS)
    manifest["summary"] = {
        "field_count": len(FIELDS),
        "steps_per_field": len(EXPECTED_STEPS),
        "successes": successes,
        "expected": expected_checks,
        "failures": len(failures),
    }
    if failures or successes != expected_checks:
        manifest["status"] = "error"
        manifest["failure_notes"] = failures

    out = PUBLIC / "manifest-icon-eu40.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(manifest["summary"], ensure_ascii=False))
    print("run_utc=", run_dt.isoformat())
    for key, rec in manifest["fields"].items():
        print(
            key,
            rec.get("status"),
            rec.get("published_step_count_0_120"),
            rec.get("missing_expected_steps"),
            rec.get("unexpected_steps_0_120"),
        )

    if manifest["status"] != "ok":
        raise RuntimeError("ICON-EU Fase 40 incompleta: " + " | ".join(failures[:12]))


if __name__ == "__main__":
    main()
