#!/usr/bin/env python
"""Convergence study: how many rays and how many interactions are enough?

    uv run scripts/convergence_study.py configs/sg_raffles_place.toml --bands 3.5 28
    uv run scripts/convergence_study.py configs/sg_raffles_place.toml --bands 3.5 28 --samples 1e6 1e7 1e8 --depths 3 5 8

Ray tracing is a Monte-Carlo method: too few rays -> noisy maps (random speckle, missing cells); too few
interactions (max_depth) -> missing multi-bounce energy (systematically too low in shadowed streets). The study
compares each setting against the most expensive one (max samples AND max depth), over outdoor cells, using the
experiment's own solver settings (diffuse scattering, diffraction, ...):

    median |delta| [dB], 90th percentile |delta| [dB], change of the covered area, and lost cells.

Criterion used by the project: median |delta| < 0.5 dB and p90 |delta| < 3 dB and coverage change < 1 point.
Results: outputs/convergence/<experiment>/<run_id>/convergence.csv, convergence.png, recommendation.txt
"""

import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path

os.environ.setdefault("MPLBACKEND", "Agg")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config")
    ap.add_argument("--bands", nargs="+", type=float, default=[3.5, 28.0])
    ap.add_argument("--samples", nargs="+", type=float, default=[1e6, 3e6, 1e7, 3e7, 1e8])
    ap.add_argument("--depths", nargs="+", type=int, default=[3, 4, 5, 6, 8])
    ap.add_argument("--out", default="outputs/convergence")
    args = ap.parse_args()

    from sionna_stack import backend
    backend.init(quiet=True)
    import numpy as np
    import pandas as pd
    import matplotlib.pyplot as plt
    from sionna_stack import experiment as ex, layout, metrics
    from sionna_stack.config import band_label, load_config

    cfg = load_config(args.config)
    cfg.extrapolate_materials = True
    scene = ex.build_scene(cfg)
    samples = sorted(int(s) for s in args.samples)
    depths = sorted(args.depths)
    ref_s, ref_d = samples[-1], depths[-1]
    run_dir = Path(args.out) / cfg.name / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    rc = cfg.radio_map
    weights = ex.beam_weights(scene, cfg)
    w0 = weights[0]

    def rm_db(ghz, s, d):
        ex.set_band(scene, cfg, ghz)
        rc.samples_per_tx, rc.max_depth = s, d
        t0 = time.perf_counter()
        rm = ex.compute_radio_map(scene, cfg, w0)
        pg = np.array(rm.path_gain).max(axis=0)
        return pg, time.perf_counter() - t0, np.array(rm.cell_centers)

    rows = []
    for ghz in args.bands:
        print(f"=== {band_label(ghz)}: reference samples={ref_s:.0e} depth={ref_d}")
        ref, t_ref, cc = rm_db(ghz, ref_s, ref_d)
        outdoor = layout.outdoor_mask(scene, cc[..., :2].reshape(-1, 2)).reshape(cc.shape[:2])
        sel = outdoor & (ref > 0)
        ref_db = metrics.db(ref)
        thr = rc.coverage_threshold_db
        cov_ref = 100 * ((ref_db > thr) & outdoor).sum() / outdoor.sum()
        grid = [(s, ref_d) for s in samples[:-1]] + [(ref_s, d) for d in depths[:-1]]
        for s, d in grid:
            try:
                pg, t, _ = rm_db(ghz, s, d)
            except RuntimeError as e:
                print(f"  samples={s:.0e} depth={d}: FAILED ({str(e).splitlines()[-1][:90]})")
                continue
            with np.errstate(divide="ignore", invalid="ignore"):
                dd = np.abs(np.where(pg > 0, metrics.db(pg), thr - 30) - ref_db)[sel]
            cov = 100 * ((metrics.db(pg) > thr) & outdoor).sum() / outdoor.sum()
            row = {"band_ghz": ghz, "samples_per_tx": s, "max_depth": d, "time_s": round(t, 2),
                   "median_abs_delta_db": float(np.median(dd)), "p90_abs_delta_db": float(np.percentile(dd, 90)),
                   "coverage_pct": float(cov), "coverage_delta_pts": float(cov - cov_ref),
                   "lost_cells_pct": float(100 * ((pg <= 0) & sel).sum() / sel.sum())}
            rows.append(row)
            print(f"  samples={s:.0e} depth={d}: median|d|={row['median_abs_delta_db']:.2f} dB  "
                  f"p90|d|={row['p90_abs_delta_db']:.2f} dB  coverage {cov:.1f}% ({cov - cov_ref:+.1f})  "
                  f"lost {row['lost_cells_pct']:.1f}%  [{t:.1f} s]")
        rows.append({"band_ghz": ghz, "samples_per_tx": ref_s, "max_depth": ref_d, "time_s": round(t_ref, 2),
                     "median_abs_delta_db": 0.0, "p90_abs_delta_db": 0.0, "coverage_pct": float(cov_ref),
                     "coverage_delta_pts": 0.0, "lost_cells_pct": 0.0})
    df = pd.DataFrame(rows)
    df.to_csv(run_dir / "convergence.csv", index=False)

    ok = (df.median_abs_delta_db < 0.5) & (df.p90_abs_delta_db < 3.0) & (df.coverage_delta_pts.abs() < 1.0)
    rec_s = max(int(df[(df.max_depth == ref_d) & ok].groupby("band_ghz").samples_per_tx.min().max()), samples[0]) \
        if ok.any() else ref_s
    rec_d = int(df[(df.samples_per_tx == ref_s) & ok].groupby("band_ghz").max_depth.min().max()) \
        if ok.any() else ref_d
    rec = (f"Recommendation (all bands meet median<0.5 dB, p90<3 dB, coverage change<1 pt): "
           f"samples_per_tx >= {rec_s:.0e}, max_depth >= {rec_d}")
    (run_dir / "recommendation.txt").write_text(rec + "\n")
    (run_dir / "settings.json").write_text(json.dumps({"config": args.config, "bands": args.bands,
                                                       "samples": samples, "depths": depths}, indent=1))

    fig, axes = plt.subplots(1, 2, figsize=(11, 4), constrained_layout=True)
    for k, ghz in enumerate(args.bands):
        d = df[(df.band_ghz == ghz) & (df.max_depth == ref_d)].sort_values("samples_per_tx")
        axes[0].plot(d.samples_per_tx, d.median_abs_delta_db, "o-", color=f"C{k}", label=f"{band_label(ghz)} median")
        axes[0].plot(d.samples_per_tx, d.p90_abs_delta_db, "s--", color=f"C{k}", label=f"{band_label(ghz)} p90")
        d = df[(df.band_ghz == ghz) & (df.samples_per_tx == ref_s)].sort_values("max_depth")
        axes[1].plot(d.max_depth, d.median_abs_delta_db, "o-", color=f"C{k}", label=f"{band_label(ghz)} median")
        axes[1].plot(d.max_depth, d.p90_abs_delta_db, "s--", color=f"C{k}", label=f"{band_label(ghz)} p90")
    axes[0].set_xscale("log")
    axes[0].set_xlabel(f"rays per transmitter (max_depth={ref_d})")
    axes[1].set_xlabel(f"max_depth (rays={ref_s:.0e})")
    for ax in axes:
        ax.set_ylabel("|difference to reference| [dB]")
        ax.axhline(0.5, color="gray", lw=0.8, ls=":")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
    fig.suptitle(f"{cfg.name}: convergence of the radio map (outdoor cells)")
    fig.savefig(run_dir / "convergence.png", dpi=130)
    print("\n" + rec + f"\n-> {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
