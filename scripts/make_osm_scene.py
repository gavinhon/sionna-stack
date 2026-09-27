#!/usr/bin/env python
"""Build a georeferenced Sionna RT scene (Mitsuba XML + PLY) from OpenStreetMap buildings (generator v2).

    uv run scripts/make_osm_scene.py --name sg_raffles_place --lat 1.2840 --lon 103.8514 --size 600
    uv run scripts/make_osm_scene.py --name sg_raffles_place --from-cache          # rebuild from the saved snapshot
    uv run scripts/make_osm_scene.py --name sg_raffles_place_glass --osm-cache scenes/sg_raffles_place \
        --default-material glass                                                    # sensitivity variant

Pipeline:
  1. Overpass API query for `building` ways/relations and `building:part` ways in a square of --size metres
     around (lat, lon). The raw response is saved as osm_raw.json (a snapshot): --from-cache / --osm-cache
     rebuild bit-identical scenes later, even if OpenStreetMap changes.
  2. Footprints -> local East-North-Up metres around the origin (exact PROJ transform, sionna_stack.geo).
  3. Geometry, following OSM's "Simple 3D Buildings" convention:
     * an outline that contains building:part elements is replaced by those parts (podiums, towers, setbacks);
     * every volume is extruded from its base (`min_height` or `building:min_level` x 3.2 m; 0 by default) to its
       top (`height` or `building:levels` x 3.2 m; else the outline's height; else --default-height);
     * flat roofs; bottom faces lying on the ground are dropped (they coincide with the ground plane).
  4. Materials: `building:material` (walls) and `roof:material` (roofs) are mapped to ITU-R P.2040 materials
     (MATERIAL_MAP); untagged volumes use --default-material. Each ITU material has a thickness and a
     scattering coefficient (MATERIAL_PROPS), so diffuse scattering can be simulated.
  5. One PLY per ITU material (meshes/bld_<material>.ply) + a ground plane; scene.xml in Sionna's format;
     scene_meta.json with origin, statistics (height and material sources), OSM timestamp, licence, command.

Why not the Blender route? Sionna's documented approach (Blender + Mitsuba-Blender add-on + Blosm/OSM) is
interactive and needs extra installs; this script produces the same kind of scene (Mitsuba XML + PLY with ITU
materials) in one repeatable command and with an exactly known geographic origin. See README section 13.

Data: (c) OpenStreetMap contributors, ODbL 1.0 (https://www.openstreetmap.org/copyright).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests
import trimesh
from shapely.geometry import LineString, MultiPolygon, Polygon, box
from shapely.ops import linemerge, polygonize, unary_union

from sionna_stack.geo import GeoFrame

GENERATOR_VERSION = 2
OVERPASS = "https://overpass-api.de/api/interpreter"
# Public mirrors listed on https://wiki.openstreetmap.org/wiki/Overpass_API#Public_Overpass_API_instances
OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
LEVEL_HEIGHT = 3.2

# OSM material values -> ITU-R P.2040 material names available in Sionna (sionna.rt ITU_MATERIALS_PROPERTIES).
# ITU has no generic "stone": marble is the closest (dense natural stone). Unknown values -> --default-material.
MATERIAL_MAP = {
    "concrete": "concrete", "reinforced_concrete": "concrete", "cement_block": "concrete", "plaster": "concrete",
    "glass": "glass", "mirror": "glass",
    "stone": "marble", "sandstone": "marble", "granite": "marble", "limestone": "marble", "marble": "marble",
    "metal": "metal", "steel": "metal", "aluminium": "metal", "metal_sheet": "metal", "copper": "metal",
    "brick": "brick", "bricks": "brick", "roof_tiles": "brick", "tiles": "brick",
    "wood": "wood", "timber_framing": "wood",
    "grass": "concrete",  # green roofs: no valid ITU soil material above 10 GHz; concrete as a neutral stand-in
}
# Per ITU material: slab thickness [m] (used for transmission through walls) and scattering coefficient S in
# [0, 1] (fraction of reflected field energy scattered diffusely, Lambertian pattern). ITU-R P.2040 does not
# give S; the values are typical choices for building surfaces in ray-tracing studies (rough masonry ~0.3,
# smooth glass/metal ~0.1). They are assumptions, tested with --scattering-scale / the sensitivity runs.
MATERIAL_PROPS = {
    "concrete": (0.30, 0.30),
    "marble": (0.30, 0.30),
    "brick": (0.20, 0.30),
    "wood": (0.10, 0.20),
    "glass": (0.024, 0.10),   # double glazing; metallic low-e coatings are NOT modelled (would reflect more)
    "metal": (0.005, 0.10),
}
GROUND = ("concrete", 1.0, 0.20)  # roads/pavements; ITU "asphalt_concrete" is only valid 1-40 GHz

# Material ids deliberately do NOT start with "itu_": Sionna 2.1's XML pre-processing rewrites "itu_*" BSDFs
# keeping only type/thickness/colour, which silently drops scattering_coefficient (checked in scene_utils.py).
MATERIAL_XML = """	<bsdf type="itu-radio-material" id="{id}">
		<string name="type" value="{itu}"/>
		<float name="thickness" value="{thickness}"/>
		<float name="scattering_coefficient" value="{s}"/>
	</bsdf>
"""
SHAPE_XML = """	<shape type="ply" id="mesh-{name}">
		<string name="filename" value="meshes/{name}.ply"/>
		<boolean name="face_normals" value="true"/>
		<ref id="{mat}" name="bsdf"/>
	</shape>
"""


def parse_height(v: str | None) -> float | None:
    """'45', '45 m', '45.5m', "150'" -> metres."""
    if not v:
        return None
    m = re.match(r"\s*([0-9]+(?:\.[0-9]+)?)\s*(m|ft|')?", v)
    if not m:
        return None
    h = float(m.group(1))
    return h * 0.3048 if m.group(2) in ("ft", "'") else h


def parse_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def top_height(t: dict) -> tuple[float | None, str | None]:
    h = parse_height(t.get("height"))
    if h is not None:
        return h, "height_tag"
    lv = parse_float(t.get("building:levels"))
    if lv is not None:
        return lv * LEVEL_HEIGHT + (parse_float(t.get("roof:levels")) or 0) * LEVEL_HEIGHT, "levels"
    return None, None


def base_height(t: dict) -> float:
    h = parse_height(t.get("min_height"))
    if h is not None:
        return h
    lv = parse_float(t.get("building:min_level"))
    return lv * LEVEL_HEIGHT if lv is not None else 0.0


def itu_material(value: str | None, default: str) -> tuple[str, bool]:
    """-> (ITU material, came_from_tag)."""
    if value:
        m = MATERIAL_MAP.get(value.strip().lower())
        if m:
            return m, True
    return default, False


def element_polygons(el: dict, frame: GeoFrame) -> list[Polygon]:
    def ring(geom):
        x, y, _ = frame.to_local([p["lon"] for p in geom], [p["lat"] for p in geom])
        return list(zip(x.tolist(), y.tolist()))

    if el["type"] == "way":
        pts = ring(el.get("geometry", []))
        return [Polygon(pts).buffer(0)] if len(pts) >= 4 else []
    outers = [LineString(ring(m["geometry"])) for m in el.get("members", [])
              if m.get("role") == "outer" and len(m.get("geometry", [])) >= 2]
    inners = [LineString(ring(m["geometry"])) for m in el.get("members", [])
              if m.get("role") == "inner" and len(m.get("geometry", [])) >= 2]
    outer = unary_union(list(polygonize(linemerge(outers)))) if outers else None
    if outer is None or outer.is_empty:
        return []
    if inners:
        outer = outer.difference(unary_union(list(polygonize(linemerge(inners)))))
    geoms = outer.geoms if isinstance(outer, MultiPolygon) else [outer]
    return [g.buffer(0) for g in geoms if isinstance(g, Polygon)]


def fetch(lat, lon, size, endpoint) -> dict:
    frame = GeoFrame(lat, lon)
    r = frame.rectangle_degrees(-size / 2, -size / 2, size / 2, size / 2)
    bbox = f"{r['south']},{r['west']},{r['north']},{r['east']}"
    query = (f"[out:json][timeout:120];("
             f'way["building"]({bbox});relation["building"]["type"="multipolygon"]({bbox});'
             f'way["building:part"]({bbox});'
             f");out geom;")
    # Public Overpass servers are often busy (HTTP 429/504): try each endpoint with a few retries.
    endpoints = [endpoint] + [e for e in OVERPASS_MIRRORS if e != endpoint]
    data, errors = None, []
    for ep in endpoints:
        for attempt in range(3):
            print(f"Overpass query ({ep}, attempt {attempt + 1}) bbox={bbox}")
            try:
                resp = requests.post(ep, data={"data": query}, timeout=180,
                                     headers={"User-Agent": "sionna-stack/0.1 (research scene generator)"})
                resp.raise_for_status()
                data = resp.json()
                endpoint = ep
                break
            except (requests.RequestException, ValueError) as e:
                errors.append(f"{ep}: {e}")
                print(f"  failed: {e}", file=sys.stderr)
                time.sleep(10 * (attempt + 1))
        if data is not None:
            break
    if data is None:
        raise RuntimeError("All Overpass endpoints failed:\n  " + "\n  ".join(errors))
    data["_query"] = query
    data["_endpoint"] = endpoint
    data["_fetched_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return data


def extrude(poly: Polygon, z0: float, z1: float):
    """Prism from z0 to z1 -> (walls+bottom mesh, roof mesh). Bottom faces on the ground are dropped."""
    m = trimesh.creation.extrude_polygon(poly, height=z1 - z0)
    m.apply_translation([0, 0, z0])
    nz = m.face_normals[:, 2]
    roof = nz > 0.9
    bottom = nz < -0.9
    keep_walls = ~roof & ~(bottom & (z0 < 0.01))
    walls = m.submesh([np.where(keep_walls)[0]], append=True) if keep_walls.any() else None
    roofs = m.submesh([np.where(roof)[0]], append=True) if roof.any() else None
    return walls, roofs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True, help="scene name -> scenes/<name>/")
    ap.add_argument("--lat", type=float, help="origin latitude (WGS84)")
    ap.add_argument("--lon", type=float, help="origin longitude (WGS84)")
    ap.add_argument("--size", type=float, default=600.0, help="side of the square area [m] (default 600)")
    ap.add_argument("--default-height", type=float, default=12.0, help="height when OSM has none [m]")
    ap.add_argument("--default-material", default="concrete", choices=sorted(MATERIAL_PROPS),
                    help="ITU material for volumes without a (recognised) material tag")
    ap.add_argument("--scattering-scale", type=float, default=1.0,
                    help="multiply all scattering coefficients (0 = purely specular surfaces)")
    ap.add_argument("--ground-margin", type=float, default=100.0, help="ground plane extends this far beyond the area")
    ap.add_argument("--from-cache", action="store_true", help="rebuild from scenes/<name>/osm_raw.json")
    ap.add_argument("--osm-cache", help="rebuild from another scene folder's osm_raw.json (e.g. for variants)")
    ap.add_argument("--endpoint", default=OVERPASS)
    ap.add_argument("--out-root", default="scenes")
    args = ap.parse_args()

    out = Path(args.out_root) / args.name
    (out / "meshes").mkdir(parents=True, exist_ok=True)
    for old in (out / "meshes").glob("*.ply"):
        old.unlink()

    src_dir = Path(args.osm_cache) if args.osm_cache else (out if args.from_cache else None)
    if src_dir is not None:
        data = json.loads((src_dir / "osm_raw.json").read_text())
        meta_old = json.loads((src_dir / "scene_meta.json").read_text())
        lat, lon, size = meta_old["origin"]["lat"], meta_old["origin"]["lon"], meta_old["size_m"]
        if src_dir != out:
            (out / "osm_raw.json").write_text(json.dumps(data))
    else:
        if args.lat is None or args.lon is None:
            ap.error("--lat and --lon are required unless --from-cache / --osm-cache")
        lat, lon, size = args.lat, args.lon, args.size
        data = fetch(lat, lon, size, args.endpoint)
        (out / "osm_raw.json").write_text(json.dumps(data))
    frame = GeoFrame(lat, lon, 0.0)
    area = box(-size / 2, -size / 2, size / 2, size / 2)

    outlines, parts = [], []
    for el in data["elements"]:
        t = el.get("tags", {})
        if "building" in t:
            outlines += [(p, t) for p in element_polygons(el, frame) if not p.is_empty and p.area >= 4.0]
        elif "building:part" in t:
            parts += [(p, t) for p in element_polygons(el, frame) if not p.is_empty and p.area >= 1.0]

    # assign parts to the outline that contains them (Simple 3D Buildings)
    part_owner = [None] * len(parts)
    for pi, (pp, _) in enumerate(parts):
        rp = pp.representative_point()
        for oi, (op, _) in enumerate(outlines):
            if op.buffer(0.5).contains(rp):
                part_owner[pi] = oi
                break
    outline_has_parts = Counter(o for o in part_owner if o is not None)

    buckets = defaultdict(list)          # itu material -> list of meshes
    stats = Counter()
    heights = []

    def add_volume(poly, tags, fallback_tags, z_fallback, kind):
        z1, src = top_height(tags)
        if z1 is None and fallback_tags is not None:
            z1, src = top_height(fallback_tags)
            src = f"outline_{src}" if src else None
        if z1 is None:
            z1, src = z_fallback, "default"
        z0 = base_height(tags)
        if z1 - z0 < 1.0:
            z1 = z0 + 3.0 if z0 > 0 else max(z1, 3.0)
        wall_val = tags.get("building:material") or (fallback_tags or {}).get("building:material")
        roof_val = tags.get("roof:material") or (fallback_tags or {}).get("roof:material")
        wall_mat, wall_tag = itu_material(wall_val, args.default_material)
        roof_mat, roof_tag = itu_material(roof_val, wall_mat)
        try:
            walls, roofs = extrude(poly, z0, z1)
        except Exception as e:  # noqa: BLE001
            print(f"  skip volume: {e}", file=sys.stderr)
            return
        if walls is not None:
            buckets[wall_mat].append(walls)
        if roofs is not None:
            buckets[roof_mat].append(roofs)
        heights.append(z1)
        stats[f"{kind}s"] += 1
        stats[f"height_{src}"] += 1
        stats["wall_material_from_tag" if wall_tag else "wall_material_default"] += 1
        stats[f"wall_{wall_mat}"] += 1
        stats[f"roof_{roof_mat}"] += 1

    for oi, (poly, tags) in enumerate(outlines):
        if not area.contains(poly.centroid):
            continue
        if outline_has_parts[oi]:
            stats["outlines_replaced_by_parts"] += 1
            continue
        add_volume(poly, tags, None, args.default_height, "outline")
    for pi, (poly, tags) in enumerate(parts):
        if not area.contains(poly.centroid):
            continue
        owner = outlines[part_owner[pi]][1] if part_owner[pi] is not None else None
        add_volume(poly, tags, owner, args.default_height, "part")

    if not buckets:
        print("No buildings found - check coordinates/size.", file=sys.stderr)
        return 1

    scale = args.scattering_scale
    mats_used = sorted(buckets)
    xml = ['<scene version="2.1.0">\n', "<!-- Generated by scripts/make_osm_scene.py (v2) from OpenStreetMap "
           "(c) OpenStreetMap contributors, ODbL. Local frame: ENU at origin, see scene_meta.json -->\n\n"]
    for m in mats_used:
        th, s = MATERIAL_PROPS[m]
        xml.append(MATERIAL_XML.format(id=f"rm_{m}", itu=m, thickness=th, s=round(s * scale, 4)))
    gname, gth, gs = GROUND
    xml.append(MATERIAL_XML.format(id="rm_ground", itu=gname, thickness=gth, s=round(gs * scale, 4)))
    xml.append("\n")
    for m in mats_used:
        mesh = trimesh.util.concatenate(buckets[m])
        mesh.export(out / "meshes" / f"bld_{m}.ply", encoding="binary")
        xml.append(SHAPE_XML.format(name=f"bld_{m}", mat=f"rm_{m}"))
    g = size / 2 + args.ground_margin
    ground = trimesh.Trimesh(vertices=[[-g, -g, 0], [g, -g, 0], [g, g, 0], [-g, g, 0]], faces=[[0, 1, 2], [0, 2, 3]])
    ground.export(out / "meshes" / "ground.ply", encoding="binary")
    xml.append(SHAPE_XML.format(name="ground", mat="rm_ground"))
    xml.append("</scene>\n")
    (out / "scene.xml").write_text("".join(xml))

    hs = np.array(heights)
    meta = {
        "name": args.name,
        "generator": "scripts/make_osm_scene.py",
        "generator_version": GENERATOR_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "command": " ".join(sys.argv),
        "origin": frame.as_dict(),
        "size_m": size,
        "ground_extent_m": 2 * g,
        "volumes": {"outlines": stats["outlines"], "parts": stats["parts"],
                    "outlines_replaced_by_parts": stats["outlines_replaced_by_parts"]},
        "height_sources": {k[7:]: v for k, v in stats.items() if k.startswith("height_")},
        "height_stats_m": {"min": float(hs.min()), "median": float(np.median(hs)), "max": float(hs.max())},
        "wall_materials": {k[5:]: v for k, v in stats.items() if k.startswith("wall_") and "from" not in k
                           and "default" not in k},
        "roof_materials": {k[5:]: v for k, v in stats.items() if k.startswith("roof_")},
        "material_sources": {"from_osm_tag": stats["wall_material_from_tag"],
                             "default": stats["wall_material_default"], "default_material": args.default_material},
        "material_props": {m: {"itu": m, "thickness_m": MATERIAL_PROPS[m][0],
                               "scattering_coefficient": round(MATERIAL_PROPS[m][1] * scale, 4)} for m in mats_used},
        "ground": {"itu": gname, "thickness_m": gth, "scattering_coefficient": round(gs * scale, 4)},
        "scattering_scale": scale,
        "level_height_m": LEVEL_HEIGHT,
        "default_height_m": args.default_height,
        "osm": {"endpoint": data.get("_endpoint"), "fetched_utc": data.get("_fetched_utc"),
                "osm_base": data.get("osm3s", {}).get("timestamp_osm_base"), "query": data.get("_query"),
                "licence": "(c) OpenStreetMap contributors, ODbL 1.0"},
        "files": {"scene": "scene.xml", "meshes": sorted(f"meshes/{p.name}" for p in (out / "meshes").glob("*.ply")),
                  "osm_raw": "osm_raw.json"},
        "limitations": [
            "flat roofs (prisms); roof shapes (skillion, pyramidal, ...) ignored",
            "flat ground at z=0 (no terrain); water surfaces modelled as ground (concrete)",
            "untagged materials use the default material",
            "glass without metallic coatings; no windows/mullions/balconies; no vegetation, vehicles or people",
        ],
    }
    (out / "scene_meta.json").write_text(json.dumps(meta, indent=2))
    print(f"Scene written to {out}/: {stats['outlines']} outlines + {stats['parts']} parts "
          f"({stats['outlines_replaced_by_parts']} outlines replaced by parts), heights {hs.min():.0f}-{hs.max():.0f} m; "
          f"wall materials {meta['wall_materials']} ({meta['material_sources']['from_osm_tag']} from tags)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
