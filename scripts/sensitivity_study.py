#!/usr/bin/env python
"""Sensitivity study: how much do uncertain modelling assumptions move the results?

    uv run scripts/sensitivity_study.py configs/sg_raffles_place.toml --bands 0.7 3.5 28 \
        --variants baseline=scenes/sg_raffles_place untagged_glass=scenes/sg_raffles_place_glass \
                   specular=scenes/sg_raffles_place_specular rough_x2=scenes/sg_raffles_place_rough2

Each variant is a scene folder made by make_osm_scene.py from the SAME OSM snapshot with one assumption changed
(e.g. --default-material glass, --scattering-scale 0 or 2). The experiment's TX/RX/solver settings are reused.
For every band and variant it reports, relative to the first variant (baseline):
    per-cell median / p90 |difference| over outdoor cells, coverage change, and each receiver's total path gain.
Results: outputs/sensitivity/<experiment>/<run_id>/sensitivity.csv, links.csv, sensitivity.png
"""

import argparse
import os
from datetime import datetime
from pathlib import Path

os.environ.setdefault("MPLBACKEND", "Agg")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config")
    ap.add_argument("--bands", nargs="+", type=float, default=[3.5, 28.0])
    ap.add_argument("--variants", nargs="+", required=True, help="name=scene_folder (first = baseline)")
    ap.add_argument("--out", default="outputs/sensitivity")
    args = ap.parse_args()

    from sionna_stack import backend
    backend.init(quiet=True)
    import numpy as np
    import pandas as pd
    import matplotlib.pyplot as plt
    from sionna_stack import experiment as ex, metrics
    from sionna_stack.config import band_label, load_config

    variants = [v.split("=", 1) for v in args.variants]
    base_cfg = load_config(args.config)
    run_dir = Path(args.out) / base_cfg.name / datetime.now().strftime("%Y%m%d-%H%M%S")
    results, link_rows = {}, []
    for name, folder in variants:
        cfg = load_config(args.config)
        cfg.scene = str((Path(folder) / "scene.xml").resolve())
        cfg.bands_ghz = args.bands
        cfg.extrapolate_materials = True
        scene = ex.build_scene(cfg)
        cache: dict = {}
        for ghz in args.bands:
            print(f"--- {name}: {band_label(ghz)}")
            r = ex.run_band(scene, cfg, ghz, run_dir / name / f"band_{ghz:g}", render=False, outdoor_cache=cache)
            results[(name, ghz)] = r["rm"]
            for row in r["rows"]:
                link_rows.append({"variant": name, **{k: row[k] for k in ("band_ghz", "rx", "los", "total_path_gain_db")}})

    base = variants[0][0]
    rows = []
    thr = base_cfg.radio_map.coverage_threshold_db
    for ghz in args.bands:
        b = results[(base, ghz)]
        out = b["outdoor"]
        bdb = metrics.db(b["path_gain"][0])
        cov_b = 100 * ((bdb > thr) & out).sum() / out.sum()
        for name, _ in variants:
            v = results[(name, ghz)]
            vdb = metrics.db(v["path_gain"][0])
            sel = out & (b["path_gain"][0] > 0) & (v["path_gain"][0] > 0)
            d = (vdb - bdb)[sel]
            cov = 100 * ((vdb > thr) & out).sum() / out.sum()
            rows.append({"band_ghz": ghz, "variant": name, "median_delta_db": float(np.median(d)),
                         "median_abs_delta_db": float(np.median(np.abs(d))), "p90_abs_delta_db": float(np.percentile(np.abs(d), 90)),
                         "coverage_pct": float(cov), "coverage_delta_pts": float(cov - cov_b)})
    df = pd.DataFrame(rows)
    links = pd.DataFrame(link_rows)
    run_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(run_dir / "sensitivity.csv", index=False)
    piv = links.pivot_table(index=["band_ghz", "rx"], columns="variant", values="total_path_gain_db")
    piv = piv[[n for n, _ in variants]]
    piv.to_csv(run_dir / "links.csv")
    print("\nArea (outdoor cells), relative to", base)
    print(df.round(2).to_string(index=False))
    print("\nReceiver total path gain [dB] per variant")
    print(piv.round(1).to_string())

    fig, axes = plt.subplots(1, 2, figsize=(11, 4), constrained_layout=True)
    for k, (name, _) in enumerate(variants[1:], start=1):
        d = df[df.variant == name]
        axes[0].plot(d.band_ghz, d.median_delta_db, "o-", color=f"C{k}", label=name)
        axes[1].plot(d.band_ghz, d.coverage_delta_pts, "o-", color=f"C{k}", label=name)
    for ax, lab in zip(axes, ("median change of path gain [dB]", "change of covered area [% points]")):
        ax.set_xscale("log")
        ax.set_xlabel("frequency [GHz]")
        ax.set_ylabel(lab)
        ax.axhline(0, color="gray", lw=0.8)
        ax.grid(alpha=0.3, which="both")
        ax.legend(fontsize=8)
    fig.suptitle(f"Sensitivity to modelling assumptions (vs '{base}', outdoor cells)")
    fig.savefig(run_dir / "sensitivity.png", dpi=130)
    print(f"\n-> {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
