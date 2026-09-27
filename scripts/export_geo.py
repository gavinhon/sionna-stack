#!/usr/bin/env python
"""Export a simulation run to web/geo formats for the CesiumJS viewer (and any GIS tool).

    uv run scripts/export_geo.py outputs/sg_raffles_place/latest
    uv run scripts/export_geo.py outputs/sg_raffles_place/20260927-165935 --out viewer/public/data

Input : a run directory written by scripts/run_experiment.py for a GEOREFERENCED scene
        (manifest.json has geo_origin; built-in Sionna scenes are not georeferenced -> refused).
Output: <out>/<experiment>/<run_id>/            (versioned: one folder per simulation run)
          export.json            viewer manifest: bands, files, rectangles, legend, metrics, mobility, provenance
          scene/buildings.glb    scene buildings as glTF 2.0 (local ENU, Y-up per glTF spec)
          radiomap_<band>.png    radio map, RGBA (transparent = no ray reached), shared colour scale
          paths_<band>.czml      ray paths per receiver as 3-D polylines (strongest N per receiver)
          devices.czml           TX/RX points; popups with per-band metrics
          devices.geojson        same points for GIS tools (properties = metrics)
          mobility_<band>.czml   time-dynamic: moving TX + paths per snapshot (availability intervals)
          mobility/<band>/rm_###.png   radio-map frame per snapshot
        and refreshes <out>/index.json (list of all exported runs, newest first).

Coordinates: local ENU metres -> WGS84 lon/lat/ellipsoidal height with sionna_stack.geo.GeoFrame (exact PROJ
transform). Heights are relative to the scene ground (h0 = 0); the viewer adds the terrain height at the
origin when real terrain is shown. Radio-map PNGs are placed on the lon/lat rectangle spanned by the grid's
corners; over a few hundred metres the ENU grid and the lon/lat rectangle differ by millimetres.
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

EPOCH = "2026-01-01T00:00:00Z"  # arbitrary simulation epoch for the Cesium timeline (not a real date)
TYPE_NAMES = {0: "none", 1: "specular", 2: "diffuse", 4: "refraction", 8: "diffraction"}


def _cmap(name: str):
    import matplotlib
    return matplotlib.colormaps[name]


def db(x):
    with np.errstate(divide="ignore"):
        return 10 * np.log10(x)


def radio_map_png(values: np.ndarray, reached: np.ndarray, vmin: float, vmax: float, out: Path,
                  cmap: str = "viridis") -> None:
    """values [y, x] in dB/dBm (row 0 = south) -> RGBA PNG (row 0 = north), transparent where no ray arrived."""
    from PIL import Image
    with np.errstate(invalid="ignore"):
        v = np.clip((np.nan_to_num(values, nan=vmin, neginf=vmin) - vmin) / (vmax - vmin), 0, 1)
    rgba = (_cmap(cmap)(v) * 255).astype(np.uint8)
    rgba[..., 3] = np.where(reached, 235, 0)
    Image.fromarray(np.flipud(rgba), "RGBA").save(out, optimize=True)


def map_values(rm: dict, metric: str) -> np.ndarray:
    """The displayed quantity: received power [dBm] (best server) or path gain [dB]."""
    if metric == "rx_power" and "rx_power_dbm" in rm:
        return rm["rx_power_dbm"]
    return db(rm["path_gain"][0])


def grid_rectangle(frame, cell_centers: np.ndarray, cell_size) -> dict:
    cx, cy = cell_centers[..., 0], cell_centers[..., 1]
    hx, hy = float(cell_size[0]) / 2, float(cell_size[1]) / 2
    return frame.rectangle_degrees(cx.min() - hx, cy.min() - hy, cx.max() + hx, cy.max() + hy)


def path_polylines(frame, p: dict, ti: int, ri: int, tx_pos, rx_pos, max_paths: int):
    """Yield dicts (positions lon/lat/h flat list, gain_db, delay_ns, types) for the strongest valid paths."""
    valid = p["valid"][ri, ti].astype(bool)
    if not valid.any():
        return []
    g = np.abs(p["a"][ri, 0, ti, 0, :, 0]) ** 2
    idx = np.where(valid)[0]
    idx = idx[np.argsort(-g[idx])][:max_paths]
    out = []
    for j in idx:
        inter = p["interactions"][:, ri, ti, j]
        verts = [tx_pos] + [p["vertices"][d, ri, ti, j] for d in range(len(inter)) if inter[d] != 0] + [rx_pos]
        llh = frame.to_geodetic_points(np.array(verts))
        types = [TYPE_NAMES.get(int(t), str(int(t))) for t in inter if t != 0]
        out.append({"positions": llh.ravel().round(9).tolist(), "gain_db": float(db(g[j])),
                    "delay_ns": float(p["tau"][ri, ti, j] * 1e9), "types": types or ["line of sight"],
                    "doppler_hz": float(p["doppler"][ri, ti, j]) if "doppler" in p else None})
    return out


def color_rgba(gain_db, vmin, vmax, cmap="viridis", alpha=230):
    c = _cmap(cmap)(float(np.clip((gain_db - vmin) / (vmax - vmin), 0, 1)))
    return [int(c[0] * 255), int(c[1] * 255), int(c[2] * 255), alpha]


# One colour per receiver, identical to the viewer's receiver swatches and chart lines (viewer/src/chart.js).
RX_COLORS = ["#4fb3ff", "#ffb14f", "#7ee07e", "#ff6b9a", "#c49bff", "#f5e663"]


def path_style(ri: int, gain_db: float, vmin: float, vmax: float) -> tuple[list[int], float]:
    """Receiver colour; strength -> opacity (60..255) and width (1..4 px). Strong = solid + thick."""
    h = RX_COLORS[ri % len(RX_COLORS)].lstrip("#")
    s = float(np.clip((gain_db - vmin) / (vmax - vmin), 0, 1))
    return [int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(60 + 195 * s)], round(1.0 + 3.0 * s, 2)


def paths_czml(frame, p: dict, tx_names, tx_positions, rx_names, rx_positions, vmin, vmax, band, max_paths):
    doc = [{"id": "document", "name": f"paths {band}", "version": "1.0"}]
    # New format (experiment v2): paths are already reduced to the serving (TX, beam) per receiver, with a single
    # TX axis and tx_positions[ri] = position of receiver ri's serving TX. Old format: one TX axis per transmitter.
    serving = "serving_tx" in p
    links = [(0, ri, tx_positions[ri]) for ri in range(len(rx_names))] if serving else \
        [(ti, ri, tx_positions[ti]) for ti in range(len(tx_names)) for ri in range(len(rx_names))]
    for ti, ri, txp in links:
        rname = rx_names[ri]
        parent = f"paths/{rname}"
        if not any(d["id"] == parent for d in doc):
            doc.append({"id": parent, "name": f"paths to {rname} ({band})"})
        for k, pl in enumerate(path_polylines(frame, p, ti, ri, txp, rx_positions[ri], max_paths)):
            rgba, width = path_style(ri, pl["gain_db"], vmin, vmax)
            doc.append({
                "id": f"{parent}/{ti}/{k}", "parent": parent, "name": f"{rname} path {k + 1}",
                "description": (f"<table><tr><td>band</td><td>{band}</td></tr>"
                                f"<tr><td>path gain</td><td>{pl['gain_db']:.1f} dB</td></tr>"
                                f"<tr><td>delay</td><td>{pl['delay_ns']:.1f} ns</td></tr>"
                                f"<tr><td>interactions</td><td>{', '.join(pl['types'])}</td></tr></table>"),
                "polyline": {"positions": {"cartographicDegrees": pl["positions"]},
                             "width": width, "arcType": "NONE",
                             "material": {"solidColor": {"color": {"rgba": rgba}}}},
                "properties": {"gain_db": pl["gain_db"], "delay_ns": pl["delay_ns"], "rx": rname},
            })
    return doc


def antenna_text(cfg) -> str:
    a = cfg.get("antennas") or {}
    pat = a.get("tx_pattern", cfg.get("tx_pattern", "iso"))
    rows_, cols_ = a.get("tx_rows", 1), a.get("tx_cols", 1)
    kind = {"iso": "isotropic (omni)", "tr38901": "3GPP TR 38.901 sector element", "dipole": "dipole",
            "hw_dipole": "half-wave dipole"}.get(pat, pat)
    s = f"{kind}" + (f", {rows_} x {cols_} array" if rows_ * cols_ > 1 else "")
    nb = len(a.get("beams", [[0, 0]]))
    return s + (f", {nb} beams (best beam per location)" if nb > 1 else "")


def devices(frame, cfg, metrics_rows, label_fn):
    """CZML + GeoJSON for TX sites (sectors grouped by position) and RX with per-band metrics."""
    by_rx = {}
    for r in metrics_rows:
        by_rx.setdefault(r["rx"], []).append(r)
    czml = [{"id": "document", "name": "devices", "version": "1.0"}]
    features = []
    sites = {}
    for t in cfg["transmitters"]:
        sites.setdefault(tuple(round(v, 2) for v in t["position"]), []).append(t)
    for si, (pos, txs) in enumerate(sites.items()):
        lon, lat, h = frame.to_geodetic(*pos)
        sect = "".join(
            f"<tr><td>{t['name']}</td><td>{t['power_dbm']:g} dBm</td>"
            f"<td>{'-' if t.get('azimuth_deg') is None else f'{t['azimuth_deg']:g}°'}</td>"
            f"<td>{t.get('downtilt_deg', 0):g}°</td></tr>" for t in txs)
        desc = (f"<p>Antenna site at {pos[2]:g} m, {len(txs)} sector(s). Antenna: {antenna_text(cfg)}.</p>"
                "<table><tr><th>sector</th><th>power</th><th>azimuth</th><th>downtilt</th></tr>" + sect + "</table>"
                f"<p>local ENU [m]: {list(pos)}</p>")
        name = txs[0]["name"] if len(txs) == 1 else f"site {si + 1} ({len(txs)} sectors)"
        # keep the id of single-TX sites as tx/<name> (the viewer hides it in moving-TX mode)
        ident = f"tx/{txs[0]['name']}" if len(txs) == 1 else f"txsite/{si}"
        czml.append({"id": ident, "name": f"TX {name}", "description": desc,
                     "position": {"cartographicDegrees": [float(lon), float(lat), float(h)]},
                     "point": {"pixelSize": 14, "color": {"rgba": [230, 30, 30, 255]},
                               "outlineColor": {"rgba": [255, 255, 255, 255]}, "outlineWidth": 2,
                               "disableDepthTestDistance": 1e9},
                     "label": {"text": f"TX {name}", "pixelOffset": {"cartesian2": [0, -24]},
                               "font": "14px sans-serif", "fillColor": {"rgba": [255, 255, 255, 255]},
                               "showBackground": True, "disableDepthTestDistance": 1e9}})
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [float(lon), float(lat), float(h)]},
                         "properties": {"kind": "tx_site", "sectors": [t["name"] for t in txs],
                                        "power_dbm": [t["power_dbm"] for t in txs],
                                        "azimuth_deg": [t.get("azimuth_deg") for t in txs]}})
    has_snr = any("snr_db" in x and x["snr_db"] == x["snr_db"] for x in metrics_rows)
    for r in cfg["receivers"]:
        lon, lat, h = frame.to_geodetic(*r["position"])
        rows = by_rx.get(r["name"], [])
        def cell(x, k, f="{:.1f}"):
            v = x.get(k)
            return "-" if v is None or v != v else f.format(v)
        table = "".join(
            f"<tr><td>{label_fn(x['band_ghz'])}</td><td>{'yes' if x['los'] else 'no'}</td><td>{x['num_paths']}</td>"
            f"<td>{cell(x, 'total_path_gain_db')}</td><td>{cell(x, 'rx_power_dbm')}</td>"
            f"<td>{cell(x, 'rms_delay_spread_ns')}</td>"
            + (f"<td>{cell(x, 'snr_db')}</td><td>{cell(x, 'shannon_capacity_mbps', '{:.0f}')}</td>" if has_snr else "")
            + "</tr>" for x in rows)
        desc = ("<table><tr><th>band</th><th>LOS</th><th>paths</th><th>gain [dB]</th>"
                "<th>RX power [dBm]</th><th>delay spread [ns]</th>"
                + ("<th>SNR [dB]</th><th>Shannon bound [Mbit/s]</th>" if has_snr else "") + "</tr>" + table + "</table>"
                + ("<p>SNR over the channel bandwidth without interference; the Shannon bound is an upper limit "
                   "(real 5G reaches roughly 50-75 % of it).</p>" if has_snr else "")
                + f"<p>local ENU [m]: {r['position']}</p>")
        czml.append({"id": f"rx/{r['name']}", "name": f"RX {r['name']}", "description": desc,
                     "position": {"cartographicDegrees": [float(lon), float(lat), float(h)]},
                     "point": {"pixelSize": 11, "color": {"rgba": [255, 255, 255, 255]},
                               "outlineColor": {"rgba": [20, 20, 20, 255]}, "outlineWidth": 2,
                               "disableDepthTestDistance": 1e9},
                     "label": {"text": r["name"], "pixelOffset": {"cartesian2": [0, -20]}, "font": "13px sans-serif",
                               "showBackground": True, "disableDepthTestDistance": 1e9}})
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [float(lon), float(lat), float(h)]},
                         "properties": {"kind": "rx", "name": r["name"],
                                        **{f"{k}_{x['band_ghz']:g}GHz": x.get(k) for x in rows
                                           for k in ("los", "num_paths", "total_path_gain_db", "strongest_path_gain_db",
                                                     "rx_power_dbm", "rms_delay_spread_ns", "snr_db",
                                                     "shannon_capacity_mbps")}}})
    return czml, {"type": "FeatureCollection", "features": features}


# Building colours by ITU material in the viewer (the legend in viewer/index.html uses the same colours)
MATERIAL_COLORS = {"concrete": [205, 205, 210], "glass": [120, 170, 225], "marble": [228, 212, 178],
                   "metal": [120, 122, 132], "brick": [190, 115, 85], "wood": [170, 130, 90]}


def buildings_glb(scene_xml: Path, out: Path) -> list[str]:
    """Scene meshes (except the ground) -> one GLB, one coloured primitive per material file (bld_<material>.ply).
    glTF is Y-up: (east, north, up) -> (east, up, -north). Returns the materials present."""
    import trimesh
    scene = trimesh.Scene()
    mats = []
    for p in sorted((scene_xml.parent / "meshes").glob("*.ply")):
        if p.stem == "ground":
            continue
        m = trimesh.load(p, force="mesh")
        if len(m.faces) == 0:
            continue
        v = m.vertices.copy()
        m.vertices = np.stack([v[:, 0], v[:, 2], -v[:, 1]], axis=1)
        mat = p.stem.removeprefix("bld_")
        rgb = MATERIAL_COLORS.get(mat, [205, 205, 210])
        m.visual = trimesh.visual.TextureVisuals(material=trimesh.visual.material.PBRMaterial(
            baseColorFactor=rgb + [255], metallicFactor=0.0, roughnessFactor=0.9, name=mat))
        scene.add_geometry(m, geom_name=mat)
        mats.append(mat)
    scene.export(out)
    return mats


def export_volume(frame, vol: dict, metric: str, vmin: float, vmax: float, levels: list[float], out_dir: Path,
                  rel: str) -> dict:
    """3-D volume -> height-layer PNGs (height slider) + one translucent GLB shell per level (coverage shells).

    Shells: iso-surfaces of the displayed quantity (marching cubes) after light smoothing, with cells inside
    buildings and cells no ray reached treated as 'no signal'. The volume is padded with 'no signal' on the sides
    and top (so shells close there) but not at the bottom (so shells stand open on the ground, like domes).
    """
    import trimesh
    from sionna_stack.volume import shell_meshes
    out_dir.mkdir(parents=True, exist_ok=True)
    hs, xs, ys = vol["heights"], vol["xs"], vol["ys"]
    raw = vol["rx_power_dbm"] if metric == "rx_power" else db(vol["path_gain"])
    reached = vol["path_gain"] > 0   # indoor cells keep their computed (through-wall) values, as in the 2-D maps
    # height layers
    frames = []
    for k in range(len(hs)):
        name = f"h_{k:03d}.png"
        radio_map_png(raw[k], reached[k], vmin, vmax, out_dir / name)
        frames.append(f"{rel}/{name}")
    # shells (same code as the run's volume_shells_3d.png)
    dx = float(xs[1] - xs[0])
    shells = []
    for lev, verts, faces in shell_meshes(raw, reached, hs, xs, ys, levels, vmin - 60.0, step=1):
        x, y, z = verts[:, 0], verts[:, 1], verts[:, 2]
        m = trimesh.Trimesh(vertices=np.column_stack([x, z, -y]), faces=faces[:, ::-1], process=True)  # glTF Y-up
        rgba = color_rgba(lev, vmin, vmax, alpha=255)
        m.visual = trimesh.visual.TextureVisuals(material=trimesh.visual.material.PBRMaterial(
            baseColorFactor=rgba[:3] + [110], alphaMode="BLEND", doubleSided=True, metallicFactor=0.0,
            roughnessFactor=1.0))
        name = f"shell_{int(round(lev)):+d}.glb".replace("+", "p").replace("-", "m")
        m.export(out_dir / name)
        shells.append({"level": lev, "glb": f"{rel}/{name}", "rgba": rgba, "triangles": int(len(m.faces))})
    rect = frame.rectangle_degrees(xs[0] - dx / 2, ys[0] - dx / 2, xs[-1] + dx / 2, ys[-1] + dx / 2)
    return {"heights_m": [float(h) for h in hs], "frames": frames, "rectangle": rect, "shells": shells}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir")
    ap.add_argument("--out", default="viewer/public/data")
    ap.add_argument("--max-paths", type=int, default=60, help="strongest paths per receiver (static)")
    ap.add_argument("--max-paths-mobility", type=int, default=8, help="strongest paths per receiver per snapshot")
    ap.add_argument("--vmin", type=float, help="override the colour scale minimum (dB or dBm) without re-running")
    ap.add_argument("--vmax", type=float, help="override the colour scale maximum (dB or dBm) without re-running")
    args = ap.parse_args()

    from sionna_stack.config import band_label, band_slug
    from sionna_stack.geo import GeoFrame
    import pandas as pd

    run = Path(args.run_dir).resolve()
    man = json.loads((run / "manifest.json").read_text())
    if not man.get("geo_origin"):
        raise SystemExit(
            f"{man['scene']} is not georeferenced (no geo_origin in manifest.json). Built-in Sionna scenes have no "
            "geographic reference; create a scene with scripts/make_osm_scene.py (see README, section 13).")
    o = man["geo_origin"]
    frame = GeoFrame(o["lat"], o["lon"], o.get("height", 0.0))
    cfg = man["config"]
    rc = cfg["radio_map"]
    metric = rc.get("display_metric", "path_gain")
    metric_label = "received power [dBm]" if metric == "rx_power" else "path gain [dB]"
    vmin = args.vmin if args.vmin is not None else rc["vmin_db"]
    vmax = args.vmax if args.vmax is not None else rc["vmax_db"]
    # path line strength is always judged on a path-gain scale
    pvmin, pvmax = (vmin, vmax) if metric == "path_gain" else (-150.0, -60.0)
    dest = Path(args.out).resolve() / man["experiment"] / man["run_id"]
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "scene").mkdir(parents=True)
    metrics_rows = pd.read_csv(run / "metrics.csv").to_dict("records")

    materials_present = buildings_glb(Path(man["scene"]), dest / "scene" / "buildings.glb")

    bands = []
    bws = cfg.get("bandwidth_mhz")
    for bi, ghz in enumerate(man["bands_ghz"]):
        slug, bdir = band_slug(ghz), run / f"band_{band_slug(ghz)}"
        rm = dict(np.load(bdir / "radio_map.npz"))
        p = dict(np.load(bdir / "paths.npz"))
        radio_map_png(map_values(rm, metric), rm["path_gain"][0] > 0, vmin, vmax, dest / f"radiomap_{slug}.png")
        doc = paths_czml(frame, p, list(p["tx_names"]), p["tx_positions"], list(p["rx_names"]), p["rx_positions"],
                         pvmin, pvmax, band_label(ghz), args.max_paths)
        (dest / f"paths_{slug}.czml").write_text(json.dumps(doc))
        bands.append({"ghz": ghz, "slug": slug, "label": band_label(ghz),
                      "bandwidth_mhz": bws[bi] if bws else None,
                      "radio_map": {"png": f"radiomap_{slug}.png",
                                    "rectangle": grid_rectangle(frame, rm["cell_centers"], rm["cell_size"])},
                      "paths_czml": f"paths_{slug}.czml",
                      "metrics": [{k: (None if isinstance(v, float) and v != v else v) for k, v in r.items()}
                                  for r in metrics_rows if r["band_ghz"] == ghz]})

    volume = None
    if man.get("volume"):
        vinfo = man["volume"]
        levels = vinfo.get("shell_levels") or [vmin + 0.25 * (vmax - vmin), vmin + 0.5 * (vmax - vmin), vmin + 0.75 * (vmax - vmin)]
        vbands = []
        for ghz in vinfo["bands_ghz"]:
            slug = band_slug(ghz)
            f = run / f"band_{slug}" / "volume.npz"
            if not f.is_file():
                continue
            vol = dict(np.load(f))
            e = export_volume(frame, vol, metric, vmin, vmax, levels, dest / "volume" / slug, f"volume/{slug}")
            vbands.append({"ghz": ghz, "slug": slug, "label": band_label(ghz), **e})
            print(f"  volume {band_label(ghz)}: {len(e['frames'])} height layers, shells "
                  + ", ".join(f"{s['level']:g} ({s['triangles']} tri)" for s in e["shells"]))
        volume = {"metric": metric_label, "shell_levels": levels, "bands": vbands,
                  "cell_size_m": vinfo.get("cell_size_m"),
                  "note": "outdoor air only (cells inside buildings excluded); best server / best beam"}

    dev_czml, dev_geojson = devices(frame, cfg, metrics_rows, band_label)
    (dest / "devices.czml").write_text(json.dumps(dev_czml))
    (dest / "devices.geojson").write_text(json.dumps(dev_geojson))

    mobility = None
    if man.get("mobility"):
        mi_ = man["mobility"]
        mdf = pd.read_csv(run / "mobility" / "mobility_metrics.csv")
        times = np.array(mi_["times_s"])
        dt = float(times[1] - times[0]) if len(times) > 1 else 1.0
        stop = float(times[-1] + dt)
        from datetime import timedelta
        epoch = datetime.fromisoformat(EPOCH.replace("Z", "+00:00"))

        def iso(t):
            return (epoch + timedelta(seconds=float(t))).isoformat().replace("+00:00", "Z")

        mob_bands = []
        for ghz in man["bands_ghz"]:
            slug, bdir = band_slug(ghz), run / f"band_{band_slug(ghz)}"
            rms = np.load(bdir / "mobility_radio_maps.npz")
            mp = np.load(bdir / "mobility_paths.npz")
            fdir = dest / "mobility" / slug
            fdir.mkdir(parents=True)
            for i in range(len(times)):
                pgi = rms["path_gain"][i]
                # mobility maps are path gain; for an rx_power display add the moving TX power
                val = db(pgi) + (next(t["power_dbm"] for t in cfg["transmitters"] if t["name"] == mi_["tx"])
                                 if metric == "rx_power" else 0.0)
                radio_map_png(val, pgi > 0, vmin, vmax, fdir / f"rm_{i:03d}.png")
            txp = mp["tx_positions"]
            llh = frame.to_geodetic_points(txp)
            doc = [{"id": "document", "name": f"mobility {band_label(ghz)}", "version": "1.0",
                    "clock": {"interval": f"{EPOCH}/{iso(stop)}", "currentTime": EPOCH, "multiplier": 1,
                              "range": "LOOP_STOP", "step": "SYSTEM_CLOCK_MULTIPLIER"}},
                   {"id": "mobile_tx", "name": "moving TX", "availability": f"{EPOCH}/{iso(stop)}",
                    "description": f"<p>Transmitter moving at {mi_['speed_mps']} m/s along the route.</p>",
                    "position": {"epoch": EPOCH, "interpolationAlgorithm": "LINEAR",
                                 "cartographicDegrees": np.column_stack([times, llh]).ravel().round(9).tolist()},
                    "point": {"pixelSize": 14, "color": {"rgba": [255, 60, 60, 255]},
                              "outlineColor": {"rgba": [255, 255, 255, 255]}, "outlineWidth": 2,
                              "disableDepthTestDistance": 1e9},
                    "path": {"show": True, "leadTime": 0, "trailTime": stop, "width": 2,
                             "material": {"solidColor": {"color": {"rgba": [255, 90, 90, 200]}}}}}]
            rx_names = list(mp["rx_names"])
            for ri, rname in enumerate(rx_names):
                doc.append({"id": f"mpaths/{rname}", "name": f"paths to {rname} (moving TX)"})
            for i in range(len(times)):
                p = {k: mp[f"s{i:03d}_{k}"] for k in ("a", "tau", "valid", "interactions", "vertices", "doppler")}
                avail = f"{iso(times[i])}/{iso(times[i] + dt)}"
                for ri, rname in enumerate(rx_names):
                    for k, pl in enumerate(path_polylines(frame, p, 0, ri, txp[i], mp["rx_positions"][ri],
                                                          args.max_paths_mobility)):
                        rgba, width = path_style(ri, pl["gain_db"], pvmin, pvmax)
                        doc.append({"id": f"mpaths/{rname}/{i}/{k}", "parent": f"mpaths/{rname}", "availability": avail,
                                    "polyline": {"positions": {"cartographicDegrees": pl["positions"]},
                                                 "width": width, "arcType": "NONE",
                                                 "material": {"solidColor": {"color": {"rgba": rgba}}}},
                                    "description": (f"<p>t = {times[i]:.1f} s, {band_label(ghz)}, gain {pl['gain_db']:.1f} dB, "
                                                    f"delay {pl['delay_ns']:.1f} ns, Doppler {pl['doppler_hz']:.1f} Hz, "
                                                    f"{', '.join(pl['types'])}</p>")})
            (dest / f"mobility_{slug}.czml").write_text(json.dumps(doc))
            d = mdf[mdf.band_ghz == ghz]
            series = {rn: [None if pd.isna(v) else round(float(v), 2)
                           for v in d[d.rx == rn].sort_values("step").total_path_gain_db] for rn in rx_names}
            los = {rn: [bool(v) for v in d[d.rx == rn].sort_values("step").los] for rn in rx_names}
            mob_bands.append({"ghz": ghz, "slug": slug, "czml": f"mobility_{slug}.czml",
                              "frames": [f"mobility/{slug}/rm_{i:03d}.png" for i in range(len(times))],
                              "rectangle": grid_rectangle(frame, rms["cell_centers"], mi_["cell_size"]),
                              "total_path_gain_db": series, "los": los})
        mobility = {"epoch": EPOCH, "times_s": times.tolist(), "dt_s": dt, "speed_mps": mi_["speed_mps"],
                    "route_length_m": mi_["route_length_m"], "bands": mob_bands}

    export = {
        "format": "sionna-stack-export/1",
        "experiment": man["experiment"], "run_id": man["run_id"], "description": man.get("description", ""),
        "created_utc": man["created_utc"], "exported_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "origin": {"lat": o["lat"], "lon": o["lon"], "height": o.get("height", 0.0),
                   "note": "local z=0 (scene ground) at this ellipsoidal height; viewer adds terrain height at origin"},
        "scene": {"glb": "scene/buildings.glb", "source": man["scene"]},
        "legend": {"metric": metric_label, "metric_key": metric, "vmin": vmin, "vmax": vmax, "cmap": "viridis",
                   "stops": [[round(float(s), 3), color_rgba(vmin + s * (vmax - vmin), vmin, vmax, alpha=255)]
                             for s in np.linspace(0, 1, 11)]},
        "bands": bands,
        "path_style": {"color": "per receiver (rx_colors, same order as receivers)",
                       "opacity_and_width": "path gain on the legend scale: strong = solid + thick, weak = faint + thin"},
        "rx_colors": RX_COLORS[:len(cfg["receivers"])],
        "transmitters": cfg["transmitters"], "receivers": cfg["receivers"],
        "antennas": cfg.get("antennas"), "antenna_text": antenna_text(cfg),
        "noise_figure_db": cfg.get("noise_figure_db"),
        "materials": {m: MATERIAL_COLORS.get(m, [205, 205, 210]) for m in materials_present},
        "scene_meta": {k: (man.get("scene_meta") or {}).get(k) for k in
                       ("generator_version", "wall_materials", "material_sources", "material_props", "volumes")},
        "devices_czml": "devices.czml", "devices_geojson": "devices.geojson",
        "mobility": mobility,
        "volume": volume,
        "backend": man["backend"]["backend"], "versions": man["backend"]["versions"],
        "warnings": man.get("warnings", []),
        "accuracy": ["exact ENU<->WGS84 transform (PROJ); errors dominated by the data:",
                     "OSM footprints ~1-5 m; heights from tags/levels/default; building parts modelled; flat roofs",
                     "materials from OSM tags where present, otherwise the default material (see scene_meta)",
                     "flat ground at the origin height; no vegetation, vehicles, people or interiors"],
    }
    (dest / "export.json").write_text(json.dumps(export, indent=1))

    # refresh index.json: every exported run, newest first
    root = Path(args.out).resolve()
    entries = []
    for f in root.glob("*/*/export.json"):
        e = json.loads(f.read_text())
        entries.append({"experiment": e["experiment"], "run_id": e["run_id"], "path": str(f.parent.relative_to(root)),
                        "description": e.get("description", ""), "bands": [b["label"] for b in e["bands"]],
                        "mobility": e.get("mobility") is not None})
    entries.sort(key=lambda e: (e["run_id"], e["experiment"]), reverse=True)
    (root / "index.json").write_text(json.dumps({"exports": entries}, indent=1))
    size = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file()) / 1e6
    print(f"Exported {man['experiment']}/{man['run_id']} -> {dest} ({size:.1f} MB); index has {len(entries)} run(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
