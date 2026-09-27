#!/usr/bin/env python
"""Suggest receiver positions (LOS / NLOS / far NLOS) for a TX in any scene, and check mobility routes.

    uv run scripts/suggest_layout.py scenes/sg_raffles_place/scene.xml --tx 10 0 15
    uv run scripts/suggest_layout.py builtin:munich --tx 8.5 21 27
    uv run scripts/suggest_layout.py scenes/sg_raffles_place/scene.xml --tx 10 0 15 --route -100 -5 2  150 20 2

Prints a TOML snippet to paste into a config. The selection logic is documented in src/sionna_stack/layout.py.
"""

import argparse

from sionna_stack import backend


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scene", help="scene XML path or builtin:<name>")
    ap.add_argument("--tx", nargs=3, type=float, required=True, metavar=("X", "Y", "Z"))
    ap.add_argument("--rx-height", type=float, default=1.5)
    ap.add_argument("--route", nargs="+", type=float, help="waypoints x y z x y z ... to check (mobility)")
    args = ap.parse_args()

    backend.init(quiet=True)
    import sionna.rt as rt
    from sionna.rt import load_scene
    from sionna_stack import layout

    scene = load_scene(getattr(rt.scene, args.scene.split(":", 1)[1])) if args.scene.startswith("builtin:") \
        else load_scene(args.scene)

    import numpy as np
    tx = np.array(args.tx)
    if not layout.outdoor_mask(scene, tx[None, :2], clearance=tx[2] - 0.5)[0]:
        print(f"# WARNING: something stands above the TX at {args.tx}; is it inside a building?")

    for c in layout.suggest_receivers(scene, args.tx, rx_height=args.rx_height):
        print(f"[[receivers]]\nname = \"{c.name}\"\nposition = {c.position}\nexpect = \"{c.expect}\"\n"
              f"# distance {c.distance_m} m, {c.num_paths} paths at 3.5 GHz\n")

    if args.route:
        w = np.array(args.route).reshape(-1, 3)
        for a, b in zip(w[:-1], w[1:]):
            ok = layout.segment_outdoor(scene, a, b)
            print(f"# route segment {a.tolist()} -> {b.tolist()}: {'outdoors OK' if ok else 'CROSSES A BUILDING'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
