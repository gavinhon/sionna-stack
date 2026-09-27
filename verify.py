#!/usr/bin/env python
"""Smoke test: load a built-in scene, compute paths and a small radio map, report backend + timings.

    uv run verify.py                  # auto backend (CUDA/OptiX if available, else LLVM/CPU)
    uv run verify.py --backend llvm   # force the CPU backend
    uv run verify.py --all-backends   # run CUDA and LLVM in separate processes and compare results

Exit code 0 = the stack works on the backend that was selected.
Timings under WSL2 are NOT benchmarks (Mitsuba: "do not collect performance data within WSL").
"""

import argparse
import json
import os
import subprocess
import sys
import time


def run_once() -> dict:
    from sionna_stack import backend
    info = backend.init(quiet=True)

    import numpy as np
    import mitsuba as mi
    import sionna.rt as rt
    from sionna.rt import PathSolver, PlanarArray, RadioMapSolver, Receiver, Transmitter, load_scene

    t = {}
    t0 = time.perf_counter()
    scene = load_scene(rt.scene.simple_street_canyon)
    t["load_scene_s"] = time.perf_counter() - t0
    scene.frequency = 3.5e9
    scene.tx_array = PlanarArray(num_rows=1, num_cols=1, pattern="iso", polarization="V")
    scene.rx_array = scene.tx_array
    scene.add(Transmitter("tx", position=mi.Point3f(-33, 11, 32), power_dbm=44))
    scene.add(Receiver("rx", position=mi.Point3f(27, -13, 1.5)))

    t0 = time.perf_counter()
    paths = PathSolver(deterministic=True)(scene, max_depth=3, diffraction=True)
    a, tau = paths.cir(normalize_delays=False, out_type="numpy")
    t["paths_s"] = time.perf_counter() - t0
    valid = np.array(paths.valid)
    g = np.abs(a[0, 0, 0, 0, :, 0]) ** 2

    t0 = time.perf_counter()
    rm = RadioMapSolver()(scene, center=mi.Point3f(0, 0, 1.5), orientation=mi.Point3f(0, 0, 0),
                          size=mi.Point2f(200, 120), cell_size=mi.Point2f(2, 2), samples_per_tx=10**6, max_depth=3)
    pg = np.array(rm.path_gain)
    t["radio_map_s"] = time.perf_counter() - t0

    return {
        "backend": info.backend,
        "variant": info.variant,
        "gpu": info.gpu,
        "driver": info.driver,
        "optix_source": info.optix_source,
        "fallback_reason": info.fallback_reason,
        "versions": info.versions,
        "num_paths": int(valid.sum()),
        "total_path_gain_db": float(10 * np.log10(g.sum())),
        "radio_map_shape": list(pg.shape),
        "radio_map_cells_reached_pct": float(100 * (pg > 0).mean()),
        "timings_s": {k: round(v, 3) for k, v in t.items()},
    }


def print_report(r: dict) -> None:
    print(f"Backend        : {r['backend']}  ({r['variant']})")
    if r["gpu"]:
        print(f"GPU / driver   : {r['gpu']} / {r['driver']}")
    if r["optix_source"]:
        print(f"OptiX          : {r['optix_source']}")
    if r["fallback_reason"]:
        print(f"CPU fallback   : {r['fallback_reason']}")
    print("Versions       : " + ", ".join(f"{k} {v}" for k, v in r["versions"].items()))
    print(f"Paths          : {r['num_paths']} valid, total path gain {r['total_path_gain_db']:.2f} dB")
    print(f"Radio map      : {r['radio_map_shape']} cells, {r['radio_map_cells_reached_pct']:.1f}% reached")
    print("Timings [s]    : " + ", ".join(f"{k}={v}" for k, v in r["timings_s"].items())
          + "   (first call includes JIT compilation; WSL timings are not benchmarks)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["auto", "cuda", "llvm"])
    ap.add_argument("--all-backends", action="store_true")
    ap.add_argument("--json", action="store_true", help=argparse.SUPPRESS)  # used by --all-backends
    args = ap.parse_args()

    if args.all_backends:
        results = {}
        for b in ["cuda", "llvm"]:
            env = dict(os.environ, SIONNA_STACK_BACKEND=b)
            p = subprocess.run([sys.executable, __file__, "--json"], env=env, capture_output=True, text=True)
            if p.returncode != 0:
                print(f"--- {b}: FAILED\n{p.stdout}\n{p.stderr}")
                continue
            results[b] = json.loads(p.stdout.strip().splitlines()[-1])
            print(f"--- {b}")
            print_report(results[b])
        if len(results) == 2:
            d = abs(results["cuda"]["total_path_gain_db"] - results["llvm"]["total_path_gain_db"])
            same_n = results["cuda"]["num_paths"] == results["llvm"]["num_paths"]
            print(f"\nCUDA vs LLVM: path count equal={same_n}, |delta total path gain|={d:.3f} dB")
        return 0 if results else 1

    if args.backend:
        os.environ["SIONNA_STACK_BACKEND"] = args.backend
    r = run_once()
    if args.json:
        print(json.dumps(r))
    else:
        print_report(r)
        print("\nverify: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
