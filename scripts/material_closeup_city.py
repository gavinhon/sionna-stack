#!/usr/bin/env python
"""Close-up of how building materials change the signal in the REAL scene (Raffles Place), from an existing
sensitivity study run (scripts/sensitivity_study.py) - no new simulation.

    uv run scripts/material_closeup_city.py outputs/sensitivity/sg_raffles_place/<run_id>

Two figures in the run folder:
    closeup_materials.png   untagged buildings as concrete (baseline) vs as glass, and the difference;
                            the buildings that changed material are outlined in cyan
    closeup_roughness.png   smooth walls (no diffuse scattering) vs twice as rough, and the difference
Rows = bands, window = 200 m x 200 m around the transmitter and receivers (--window to change).
"""

import argparse
import os
from pathlib import Path

os.environ.setdefault("MPLBACKEND", "Agg")
ROOT = Path(__file__).resolve().parents[1]


def roofs(ply: Path):
    """xy triangles of the upward-facing faces (roofs) of a building mesh."""
    import numpy as np
    import trimesh
    if not ply.exists():
        return np.zeros((0, 3, 2))
    m = trimesh.load(ply, process=False)
    up = m.face_normals[:, 2] > 0.9
    return m.vertices[m.faces[up]][:, :, :2]


def changed_roofs(base_dir: Path, variant_dir: Path, material: str):
    """Roof triangles that are `material` in the variant but not in the baseline (= buildings that changed)."""
    import numpy as np
    b = roofs(base_dir / "meshes" / f"bld_{material}.ply")
    v = roofs(variant_dir / "meshes" / f"bld_{material}.ply")
    key = lambda t: tuple(np.round(t.mean(axis=0), 1))  # noqa: E731
    have = {key(t) for t in b}
    return np.array([t for t in v if key(t) not in have]).reshape(-1, 3, 2)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run", help="sensitivity run folder (contains baseline/, untagged_glass/, ...)")
    ap.add_argument("--config", default=str(ROOT / "configs" / "sg_raffles_place.toml"))
    ap.add_argument("--window", nargs=4, type=float, default=[-100, 100, -140, 60], metavar=("X0", "X1", "Y0", "Y1"))
    args = ap.parse_args()

    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.collections import PolyCollection
    from matplotlib.colors import TwoSlopeNorm
    from sionna_stack.config import band_label, load_config

    run = Path(args.run)
    cfg = load_config(args.config)
    rc = cfg.radio_map
    x0, x1, y0, y1 = args.window
    scene_base = Path(cfg.scene).parent
    bands = sorted({float(p.name.split("_", 1)[1]) for p in (run / "baseline").glob("band_*")})

    def load(variant, g):
        d = np.load(run / variant / f"band_{g:g}" / "radio_map.npz")
        pg = d["path_gain"][0]
        with np.errstate(divide="ignore"):
            db = np.where(pg > 0, 10 * np.log10(np.maximum(pg, 1e-30)), np.nan)
        return d["cell_centers"], db, d["outdoor"]

    def decorate(ax):
        for t in cfg.transmitters:
            ax.plot(*t.position[:2], "o", color="red", ms=7, mec="white", mew=1.2)
        for r in cfg.receivers:
            ax.plot(*r.position[:2], "o", color="white", ms=5, mec="black", mew=1)
            ax.annotate(r.name, r.position[:2], xytext=(4, 4), textcoords="offset points", fontsize=7,
                        color="white", bbox=dict(fc="black", alpha=0.6, lw=0, pad=1))
        ax.set_xlim(x0, x1); ax.set_ylim(y0, y1); ax.set_aspect("equal")
        ax.set_xticks([]); ax.set_yticks([])

    def comparison(base, variant, titles, out, highlight=None, suptitle=""):
        fig, axes = plt.subplots(len(bands), 3, figsize=(12.5, 3.9 * len(bands)), constrained_layout=True,
                                 squeeze=False)
        cmap = plt.get_cmap("viridis").copy(); cmap.set_bad("#d9d9d9")
        dmap = plt.get_cmap("RdBu_r").copy(); dmap.set_bad("#d9d9d9")
        for i, g in enumerate(bands):
            cc, a, outdoor = load(base, g)
            _, b, _ = load(variant, g)
            X, Y = cc[..., 0], cc[..., 1]
            win = (X >= x0) & (X <= x1) & (Y >= y0) & (Y <= y1) & outdoor
            for j, (v, t) in enumerate(((a, titles[0]), (b, titles[1]))):
                im = axes[i, j].pcolormesh(X, Y, np.where(outdoor, v, np.nan), vmin=rc.vmin_db, vmax=rc.vmax_db,
                                           cmap=cmap, shading="nearest")
                axes[i, j].set_title(f"{band_label(g)}: {t}", fontsize=10)
                decorate(axes[i, j])
            d = np.where(outdoor, b - a, np.nan)
            dm = axes[i, 2].pcolormesh(X, Y, d, norm=TwoSlopeNorm(vmin=-15, vcenter=0, vmax=15), cmap=dmap,
                                       shading="nearest")
            if highlight is not None and len(highlight):
                axes[i, 2].add_collection(PolyCollection(highlight, facecolor="#00e5ff", alpha=0.35,
                                                         edgecolor="none"))
            sel = win & np.isfinite(d)
            axes[i, 2].set_title(f"{band_label(g)}: difference (median {np.median(d[sel]):+.1f} dB, "
                                 f"90 % within ±{np.percentile(np.abs(d[sel]), 90):.0f} dB)", fontsize=10)
            decorate(axes[i, 2])
        fig.colorbar(im, ax=axes[:, :2], shrink=0.4, location="bottom", label="path gain [dB] (grey = inside buildings)")
        fig.colorbar(dm, ax=axes[:, 2], shrink=0.8, location="bottom", label="difference [dB]: red = stronger, blue = weaker")
        fig.suptitle(suptitle, fontsize=12)
        fig.savefig(run / out, dpi=100)
        plt.close(fig)
        print(f"-> {run / out}")

    glass = changed_roofs(scene_base, scene_base.parent / f"{scene_base.name}_glass", "glass")
    comparison("baseline", "untagged_glass", ("untagged buildings = concrete", "untagged buildings = glass"),
               "closeup_materials.png", highlight=glass,
               suptitle="Raffles Place close-up: buildings without a material tag in OpenStreetMap as concrete "
                        "(left) or glass (middle).\nCyan (right) = buildings whose material changed; red = "
                        "stronger with glass, blue = weaker")
    comparison("specular", "rough_x2", ("smooth walls (no scattering)", "walls twice as rough"),
               "closeup_roughness.png",
               suptitle="Raffles Place close-up: smooth walls (left) vs twice the diffuse scattering (middle).\n"
                        "Red = stronger with rough walls, blue = weaker")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
