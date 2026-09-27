#!/usr/bin/env python
"""Add the 3-D signal volume (coverage shells + height slider data) to an EXISTING run, without re-running it.

    uv run scripts/run_volume.py configs/sg_macro.toml outputs/sg_macro/latest
    uv run scripts/run_volume.py configs/sg_raffles_place.toml outputs/sg_raffles_place/latest --bands 0.7 3.5 28 140
    uv run scripts/run_volume.py configs/sg_macro.toml outputs/sg_macro/latest --figures-only   # new shell_levels

Uses the config's [volume] section (defaults if absent) and the same scene, transmitters, antennas and physics
as the run. Writes band_<slug>/volume.npz and volume_slices.png into the run folder and records a "volume"
section in its manifest.json. Then export again: uv run scripts/export_geo.py <run>
"""

import argparse
import json
import os
from pathlib import Path

os.environ.setdefault("MPLBACKEND", "Agg")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config")
    ap.add_argument("run_dir")
    ap.add_argument("--bands", nargs="+", type=float, help="subset of bands (default: [volume] bands_ghz or all)")
    ap.add_argument("--figures-only", action="store_true",
                    help="don't recompute: redraw the figures from the saved volumes and record the config's "
                         "current [volume] shell_levels (seconds, no GPU)")
    args = ap.parse_args()

    from sionna_stack.config import VolumeCfg, load_config

    run = Path(args.run_dir).resolve()
    man_path = run / "manifest.json"
    man = json.loads(man_path.read_text())
    cfg = load_config(args.config)
    if cfg.name != man["experiment"]:
        raise SystemExit(f"config {cfg.name!r} does not match run {man['experiment']!r}")
    if [t.position for t in cfg.transmitters] != [t["position"] for t in man["config"]["transmitters"]]:
        raise SystemExit("transmitters in the config differ from the run's; the volume would not match the run")
    cfg.volume = cfg.volume or VolumeCfg()
    if args.bands:
        cfg.volume.bands_ghz = args.bands
    if args.figures_only:
        from sionna_stack.volume import redraw_figures
        if "volume" not in man:
            raise SystemExit("this run has no volume yet; run without --figures-only first")
        redraw_figures(cfg, run)
        man["volume"]["shell_levels"] = cfg.volume.shell_levels
        man_path.write_text(json.dumps(man, indent=2, default=str))
        print(f"figures redrawn, shell_levels={cfg.volume.shell_levels}; now run: uv run scripts/export_geo.py {args.run_dir}")
        return 0
    from sionna_stack import backend
    backend.init()
    from sionna_stack import experiment as ex
    from sionna_stack.volume import run_volumes
    cfg.extrapolate_materials = True
    scene = ex.build_scene(cfg)
    info = run_volumes(scene, cfg, run)
    man["volume"] = info
    man["files"] = sorted(str(p.relative_to(run)) for p in run.rglob("*") if p.is_file())
    man_path.write_text(json.dumps(man, indent=2, default=str))
    print(f"volume added to {run}; now run: uv run scripts/export_geo.py {args.run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
