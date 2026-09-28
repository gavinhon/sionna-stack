#!/usr/bin/env python
"""Suggest macro base-station sites on rooftops for a generated scene and print them as TOML [[transmitters]].

    uv run scripts/suggest_sites.py scenes/sg_downtown_core --isd 500 --min-height 25 --max-height 100

How a planner would lay out a macro layer, simplified:
  1. a hexagonal grid of nominal site positions with inter-site distance --isd (dense urban: 300-500 m);
  2. each nominal position takes the nearest rooftop (flat roof part >= --min-roof-area m², height within
     [--min-height, --max-height]: tall enough to see over the street, not a 280 m skyscraper top) within
     0.4 x ISD; positions with no such roof nearby (e.g. over water) get no site;
  3. three sectors at azimuths 30 / 150 / 270 deg (the same orientation at every site, as in a regular hexagonal
     layout), each mounted 3 m above the roof at the roof EDGE it faces (1.5 m inside the edge), like real rooftop
     panels: an antenna in the middle of a large roof is hidden from the streets below by the roof itself (a first
     version did that: a site 70 m from a receiver delivered -96 dBm); downtilt = angle to the cell edge
     (ISD/2) + 2 deg, clamped 3-12 deg.
Sector names are <site>_<k> (s01_1, s01_2, ...): the exporter groups sectors into one site marker by that prefix.
Deterministic for a given scene and arguments.
"""

import argparse
import json
import math
from pathlib import Path


def roof_patches(scene_dir: Path, min_area: float):
    """Connected upward-facing patches of all building meshes -> [(x, y, z, area, footprint polygon)]."""
    import numpy as np
    import trimesh
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    out = []
    for p in sorted((scene_dir / "meshes").glob("bld_*.ply")):
        m = trimesh.load(p, process=False)
        up = np.where(m.face_normals[:, 2] > 0.9)[0]
        if not len(up):
            continue
        roof = m.submesh([up], append=True)
        roof.merge_vertices()
        for part in roof.split(only_watertight=False):
            if part.area >= min_area:
                poly = unary_union([Polygon(t) for t in part.vertices[part.faces][:, :, :2]]).buffer(0.01)
                c = part.centroid
                out.append((float(c[0]), float(c[1]), float(part.vertices[:, 2].max()), float(part.area), poly))
    return out


def edge_point(poly, azimuth_deg: float, inset: float = 1.5):
    """Point on the roof edge in the compass direction azimuth_deg (from the roof's centre), `inset` m inside."""
    from shapely.geometry import LineString, Point
    c = poly.centroid if poly.contains(poly.centroid) else poly.representative_point()
    a = math.radians(90.0 - azimuth_deg)                    # compass bearing -> angle from +x (east)
    u = (math.cos(a), math.sin(a))
    hit = LineString([(c.x, c.y), (c.x + 2000 * u[0], c.y + 2000 * u[1])]).intersection(poly.exterior)
    pts = [hit] if hit.geom_type == "Point" else list(getattr(hit, "geoms", []))
    if not pts:
        return c.x, c.y
    e = min(pts, key=lambda q: Point(c.x, c.y).distance(q))  # first edge crossing
    d = max(0.0, Point(c.x, c.y).distance(e) - inset)
    return c.x + d * u[0], c.y + d * u[1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scene_dir")
    ap.add_argument("--isd", type=float, default=500.0, help="inter-site distance [m]")
    ap.add_argument("--min-height", type=float, default=25.0)
    ap.add_argument("--max-height", type=float, default=100.0)
    ap.add_argument("--min-roof-area", type=float, default=150.0)
    ap.add_argument("--power-dbm", type=float, default=46.0)
    ap.add_argument("--margin", type=float, default=150.0, help="keep sites this far inside the scene edge [m]")
    args = ap.parse_args()

    import numpy as np
    scene_dir = Path(args.scene_dir)
    size = json.loads((scene_dir / "scene_meta.json").read_text())["size_m"]
    roofs = [r for r in roof_patches(scene_dir, args.min_roof_area) if args.min_height <= r[2] <= args.max_height]
    R = np.array([r[:4] for r in roofs])
    half = size / 2 - args.margin
    dy = args.isd * math.sqrt(3) / 2
    sites = []
    j = 0
    y = -half
    while y <= half + 1e-6:
        x = -half + (args.isd / 2 if j % 2 else 0.0)
        while x <= half + 1e-6:
            d = np.hypot(R[:, 0] - x, R[:, 1] - y)
            k = int(np.argmin(d))
            if d[k] <= 0.4 * args.isd and all(math.hypot(R[k, 0] - s[0], R[k, 1] - s[1]) > 0.5 * args.isd for s in sites):
                sites.append((*R[k], roofs[k][4]))
            x += args.isd
        y += dy
        j += 1

    print(f"# {len(sites)} sites x 3 sectors, ISD {args.isd:g} m, roofs {args.min_height:g}-{args.max_height:g} m "
          f"(scripts/suggest_sites.py {scene_dir} --isd {args.isd:g})")
    for n, (x, y, z, a, poly) in enumerate(sites, start=1):
        h = z + 3.0
        tilt = min(12.0, max(3.0, math.degrees(math.atan((h - 1.5) / (args.isd / 2))) + 2.0))
        for s, az in enumerate((30, 150, 270), start=1):
            ex, ey = edge_point(poly, az)
            note = f"   # site {n}: roof {z:.0f} m, {a:.0f} m², centre ({x:.0f}, {y:.0f})" if s == 1 else ""
            print(f'\n[[transmitters]]\nname = "s{n:02d}_{s}"\nposition = [{ex:.1f}, {ey:.1f}, {h:.1f}]{note}')
            print(f"azimuth_deg = {az:.1f}\ndowntilt_deg = {tilt:.1f}\npower_dbm = {args.power_dbm:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
