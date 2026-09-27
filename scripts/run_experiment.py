#!/usr/bin/env python
"""Run a frequency-band experiment from a TOML config.

Examples:
    uv run scripts/run_experiment.py configs/munich_frequency.toml
    uv run scripts/run_experiment.py configs/munich_frequency.toml --bands 3.5 28 0.7 --extrapolate-materials
    SIONNA_STACK_BACKEND=llvm uv run scripts/run_experiment.py configs/munich_frequency.toml --quick
"""

import argparse
import os
import sys

os.environ.setdefault("MPLBACKEND", "Agg")  # headless figure output


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config", help="experiment TOML (see configs/)")
    ap.add_argument("--bands", nargs="+", type=float, help="override band list [GHz]")
    ap.add_argument("--extrapolate-materials", action="store_true",
                    help="allow bands outside ITU-R P.2040 material validity (flagged in the manifest)")
    ap.add_argument("--quick", action="store_true", help="fewer samples / coarser grid (CPU backend, smoke runs)")
    ap.add_argument("--no-render", action="store_true", help="skip Sionna renders and figures")
    ap.add_argument("--no-mobility", action="store_true", help="skip the [mobility] (moving TX) part of the config")
    ap.add_argument("--no-volume", action="store_true", help="skip the [volume] (3-D shells / height slider) part")
    ap.add_argument("--backend", choices=["auto", "cuda", "llvm"], help="same as SIONNA_STACK_BACKEND")
    ap.add_argument("--out", default="outputs", help="output root (default: outputs/)")
    args = ap.parse_args()

    if args.backend:
        os.environ["SIONNA_STACK_BACKEND"] = args.backend
    from sionna_stack import backend
    info = backend.init(quiet=True)

    from sionna_stack.config import load_config
    from sionna_stack.experiment import run_experiment

    cfg = load_config(args.config)
    if args.bands:
        cfg.bands_ghz = args.bands
    if args.extrapolate_materials:
        cfg.extrapolate_materials = True
    quick = args.quick
    if info.variant.startswith("llvm") and not quick:
        print("NOTE: CPU (LLVM) backend active; full-quality settings can take a long time. "
              "Use --quick for a faster, coarser run.", file=sys.stderr)
    run_experiment(cfg, out_root=args.out, quick=quick, render=not args.no_render, mobility=not args.no_mobility,
                   volume=not args.no_volume)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
