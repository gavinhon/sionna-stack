"""3-D signal volume: the radio map on many heights at once (for coverage shells and the height slider).

Method: Sionna's RadioMapSolver accepts any mesh as ``measurement_surface`` (a MeshRadioMap: one value per
triangle; the surface does not interact with the rays). We build ONE mesh made of horizontal sheets stacked at
several heights (each grid cell = 2 triangles) and solve it in one run per beam. Validated against separate planar
radio maps at 1.5 m and 30 m (3.5 GHz, 1e9 rays): median difference 0.03-0.04 dB, median |difference| 0.1 dB.
31 sheets x 125 x 125 cells take ~23 s with 1e9 rays on an RTX 5080, ~25x faster than 31 planar maps.

Cells inside buildings (below the roof at that x, y) are flagged ``indoor``; shells and slices treat them as
"not outdoor air". Values are best server / best beam, like the 2-D maps.

Outputs per band (band_<slug>/volume.npz): path_gain [nz, ny, nx] (linear), rx_power_dbm [nz, ny, nx],
indoor [nz, ny, nx], heights [nz], xs [nx], ys [ny], cell_size; run-level volume_slices.png.
"""

from __future__ import annotations

import time
from pathlib import Path

import matplotlib.pyplot as plt
import mitsuba as mi
import numpy as np

from . import metrics
from .config import ExperimentConfig, band_label, band_slug


def grid_axes(cfg: ExperimentConfig):
    v, r = cfg.volume, cfg.radio_map
    cx, cy = r.center[0], r.center[1]
    sx, sy = r.size
    xs = np.arange(cx - sx / 2 + v.cell_size / 2, cx + sx / 2, v.cell_size)
    ys = np.arange(cy - sy / 2 + v.cell_size / 2, cy + sy / 2, v.cell_size)
    return xs, ys, np.asarray(v.height_list(), float)


def build_stack_mesh(cfg: ExperimentConfig, path: Path):
    """PLY with one horizontal sheet per height; returns (sionna mesh, xs, ys, heights)."""
    import trimesh
    from sionna.rt import load_mesh
    xs, ys, hs = grid_axes(cfg)
    c = cfg.volume.cell_size / 2
    X, Y = np.meshgrid(xs, ys)                      # [ny, nx]
    xy = np.stack([X.ravel(), Y.ravel()], 1)
    corners = np.array([[-c, -c], [c, -c], [c, c], [-c, c]])
    per = (xy[:, None, :] + corners[None]).reshape(-1, 2)
    verts = np.concatenate([np.column_stack([per, np.full(len(per), z)]) for z in hs])
    b = np.arange(len(xy))[:, None] * 4
    faces = np.concatenate([np.concatenate([b + [0, 1, 2], b + [0, 2, 3]]) + k * len(per) for k in range(len(hs))])
    trimesh.Trimesh(vertices=verts, faces=faces, process=False).export(path, encoding="binary")
    return load_mesh(str(path)), xs, ys, hs


def roof_heights(scene, xs, ys) -> np.ndarray:
    """Height of the highest surface above each (x, y) column (0 where open ground) -> [ny, nx]."""
    X, Y = np.meshgrid(xs, ys)
    n = X.size
    f = lambda a: np.ascontiguousarray(a, dtype=np.float32)  # noqa: E731
    top = 2000.0
    ray = mi.Ray3f(mi.Point3f(f(X.ravel()), f(Y.ravel()), f(np.full(n, top))),
                   mi.Vector3f(f(np.zeros(n)), f(np.zeros(n)), f(-np.ones(n))))
    si = scene.mi_scene.ray_intersect(ray)
    h = np.where(np.array(si.is_valid()), top - np.array(si.t), 0.0).reshape(X.shape)
    return np.where(h < 0.5, 0.0, h)  # the ground plane itself is 0


def compute_volume(scene, cfg: ExperimentConfig, weights: list[np.ndarray], mesh, xs, ys, hs, roofs) -> dict:
    """Best-server / best-beam signal volume for the current scene.frequency."""
    from sionna.rt import RadioMapSolver
    from .experiment import MAX_RAYS_PER_CALL, TX_BATCH, _release_gpu_cache, tx_batches
    r, v = cfg.radio_map, cfg.volume
    powers = np.asarray([t.power_dbm for t in cfg.transmitters])
    nz, ny, nx = len(hs), len(ys), len(xs)
    best_pw = None
    idx = None
    # many transmitters (wide-area runs): a few per call, <= 2^32 rays per call (see experiment.tx_batches)
    size = max(1, min(TX_BATCH, MAX_RAYS_PER_CALL // max(1, v.samples_per_tx)))
    for tx_idx in tx_batches(scene, cfg, size=size):
        for w in weights:
            pv = None
            if len(w) > 1:
                pv = (mi.TensorXf(w.real.astype(np.float32)), mi.TensorXf(w.imag.astype(np.float32)))
            rm = RadioMapSolver()(scene, measurement_surface=mesh, samples_per_tx=v.samples_per_tx,
                                  max_depth=r.max_depth, los=r.los, specular_reflection=r.specular_reflection,
                                  diffuse_reflection=r.diffuse_reflection, refraction=r.refraction,
                                  diffraction=r.diffraction, edge_diffraction=r.edge_diffraction, seed=r.seed,
                                  precoding_vec=pv)
            pg = np.array(rm.path_gain)                               # [tx in batch, triangles]
            if idx is None:                                           # triangle -> (z, y, x), once
                cc = np.array(rm.cell_centers)
                cc = cc.T if cc.shape[0] == 3 and cc.shape[-1] != 3 else cc
                iz = np.array([np.argmin(np.abs(hs - z)) for z in np.round(cc[:, 2], 3)])
                iy = np.clip(np.round((cc[:, 1] - ys[0]) / v.cell_size).astype(int), 0, ny - 1)
                ix = np.clip(np.round((cc[:, 0] - xs[0]) / v.cell_size).astype(int), 0, nx - 1)
                idx = (iz, iy, ix)
            del rm
            _release_gpu_cache()
            with np.errstate(divide="ignore"):
                pw = 10 * np.log10(pg) + powers[tx_idx][:, None]      # [tx in batch, triangles] dBm
            k = np.argmax(pw, axis=0)
            pw_best = np.take_along_axis(pw, k[None], 0)[0]
            pg_best = np.take_along_axis(pg, k[None], 0)[0]
            if best_pw is None:
                best_pw, best_pg = pw_best, pg_best
            else:
                better = pw_best > best_pw
                best_pw = np.where(better, pw_best, best_pw)
                best_pg = np.where(better, pg_best, best_pg)
    # 2 triangles per cell: average in linear power
    vol_pg = np.zeros((nz, ny, nx))
    cnt = np.zeros((nz, ny, nx))
    np.add.at(vol_pg, idx, best_pg)
    np.add.at(cnt, idx, 1)
    vol_pg /= np.maximum(cnt, 1)
    with np.errstate(divide="ignore"):
        # best server's power; same power for all TX in our configs, so dBm = P_max + path gain
        vol_pw = np.where(vol_pg > 0, powers.max() + 10 * np.log10(np.maximum(vol_pg, 1e-300)), -np.inf)
    indoor = hs[:, None, None] < roofs[None, :, :]
    return {"path_gain": vol_pg.astype(np.float32), "rx_power_dbm": vol_pw.astype(np.float32), "indoor": indoor,
            "heights": hs, "xs": xs, "ys": ys, "cell_size": v.cell_size, "roof_heights": roofs.astype(np.float32)}


def run_volumes(scene, cfg: ExperimentConfig, run_dir: Path) -> dict:
    """Compute the volume for every configured band into run_dir/band_<slug>/volume.npz (+ slices figure)."""
    from .experiment import beam_weights, set_band
    v = cfg.volume
    bands = v.bands_ghz or cfg.bands_ghz
    mesh, xs, ys, hs = build_stack_mesh(cfg, run_dir / "volume_measurement_mesh.ply")
    roofs = roof_heights(scene, xs, ys)
    timings, vols = {}, {}
    for ghz in bands:
        set_band(scene, cfg, ghz)
        t0 = time.perf_counter()
        vol = compute_volume(scene, cfg, beam_weights(scene, cfg), mesh, xs, ys, hs, roofs)
        bdir = run_dir / f"band_{band_slug(ghz)}"
        bdir.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(bdir / "volume.npz", **vol, frequency_hz=ghz * 1e9)
        timings[band_slug(ghz)] = round(time.perf_counter() - t0, 1)
        vols[ghz] = vol
        print(f"  volume {band_label(ghz)}: {len(hs)} heights x {len(ys)} x {len(xs)} cells in {timings[band_slug(ghz)]} s")
    slices_figure(cfg, vols, run_dir / "volume_slices.png")
    shells_figure(cfg, vols, run_dir / "volume_shells_3d.png")
    levels_figure(cfg, vols, run_dir / "volume_shell_levels.png")
    (run_dir / "volume_measurement_mesh.ply").unlink(missing_ok=True)
    return {"bands_ghz": bands, "heights_m": hs.tolist(), "cell_size_m": v.cell_size,
            "samples_per_tx": v.samples_per_tx, "shell_levels": v.shell_levels, "timings_s": timings}


def redraw_figures(cfg: ExperimentConfig, run_dir: Path) -> dict:
    """Rebuild volume_slices.png / volume_shells_3d.png from saved band_*/volume.npz (no GPU; e.g. after
    changing [volume] shell_levels). Returns the bands found."""
    bands = cfg.volume.bands_ghz or cfg.bands_ghz
    vols = {}
    for ghz in bands:
        f = run_dir / f"band_{band_slug(ghz)}" / "volume.npz"
        if f.exists():
            vols[ghz] = dict(np.load(f))
    if not vols:
        raise FileNotFoundError(f"no band_*/volume.npz in {run_dir}; run without --figures-only first")
    slices_figure(cfg, vols, run_dir / "volume_slices.png")
    shells_figure(cfg, vols, run_dir / "volume_shells_3d.png")
    levels_figure(cfg, vols, run_dir / "volume_shell_levels.png")
    return {"bands_ghz": list(vols)}


def shell_stats(values: np.ndarray, reached: np.ndarray, indoor: np.ndarray, xs, ys, levels,
                tx_xy=None) -> list[dict]:
    """What each coverage shell encloses, for explaining it: per level
    pct_inside    share [%] of the outdoor air (cells not inside buildings, all heights) at or above the level
    dist_inside_m / dist_outside_m   median horizontal distance of that stronger / weaker air to the nearest
                  transmitter (None without transmitters or when empty).
    A shell is only the boundary between the two. When pct_inside is close to 100 % the surface only wraps the
    few weaker pockets (usually far from the sites or behind towers) and nothing appears around the transmitters;
    when it is small the surface is a bubble around the transmitters."""
    air = ~np.asarray(indoor, bool)
    v = np.where(reached, values, -np.inf)
    d = None
    if tx_xy is not None and len(tx_xy):
        X, Y = np.meshgrid(np.asarray(xs, float), np.asarray(ys, float))        # [ny, nx]
        t = np.asarray(tx_xy, float)[:, :2]
        d2 = np.min(np.hypot(X[None] - t[:, 0, None, None], Y[None] - t[:, 1, None, None]), axis=0)
        d = np.broadcast_to(d2[None], v.shape)
    out = []
    n_air = max(1, int(air.sum()))
    for lev in sorted(float(x) for x in levels):
        inside = (v >= lev) & air
        outside = (v < lev) & air
        s = {"level": lev, "pct_inside": round(100.0 * inside.sum() / n_air, 1)}
        if d is not None:
            s["dist_inside_m"] = round(float(np.median(d[inside]))) if inside.any() else None
            s["dist_outside_m"] = round(float(np.median(d[outside]))) if outside.any() else None
        out.append(s)
    return out


def levels_figure(cfg: ExperimentConfig, vols: dict, path: Path) -> None:
    """The vertical cut with every shell level drawn as a line: shows why a shell sometimes appears only far away
    from the transmitters (the level is exceeded almost everywhere, so its boundary only wraps weak pockets)."""
    bands = list(vols)
    rc = cfg.radio_map
    wide = (vols[bands[0]]["xs"][-1] - vols[bands[0]]["xs"][0]) > 1000
    ncols = 1 if wide or len(bands) <= 4 else 2
    nrows = int(np.ceil(len(bands) / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(15 if wide else 7.5 * ncols + 1.5, 2.9 * nrows + 0.6),
                             constrained_layout=True, squeeze=False)
    axes = axes.ravel()
    for ax in axes[len(bands):]:
        ax.axis("off")
    axes = axes[: len(bands)]
    ty = cfg.volume.slice_y if cfg.volume.slice_y is not None else cfg.transmitters[0].position[1]
    near = [t for t in cfg.transmitters if abs(t.position[1] - ty) <= 50]
    levels = sorted(cfg.volume.shell_levels)
    colors = ["#ff4d6d", "#ffd166", "#ffffff", "#8ecae6", "#b5e48c"][: len(levels)]
    tx_xy = [t.position[:2] for t in cfg.transmitters]
    unit = "dBm" if cfg.radio_map.display_metric == "rx_power" else "dB"
    im = None
    for ax, ghz in zip(axes, bands):
        vol = vols[ghz]
        vals, label = display_volume(cfg, vol)
        reached = vol["path_gain"] > 0
        st = {s["level"]: s for s in shell_stats(vals, reached, vol["indoor"], vol["xs"], vol["ys"], levels, tx_xy)}
        j = int(np.argmin(np.abs(vol["ys"] - ty)))
        sl = np.where(reached[:, j, :], vals[:, j, :], rc.vmin_db - 60)
        cmap = plt.cm.viridis.copy()
        cmap.set_bad("#d9d9d9")
        im = ax.pcolormesh(vol["xs"], vol["heights"], np.where(vol["indoor"][:, j, :], np.nan, sl), vmin=rc.vmin_db,
                           vmax=rc.vmax_db, cmap=cmap, shading="nearest")
        handles = []
        from scipy.ndimage import gaussian_filter
        smooth = gaussian_filter(sl, sigma=(1.0, 1.4))  # same smoothing as the 3-D shells (shell_meshes)
        for lev, c in zip(levels, colors):
            cs = ax.contour(vol["xs"], vol["heights"], smooth, levels=[lev], colors=[c], linewidths=1.8,
                            linestyles="solid")
            p = st[float(lev)]["pct_inside"]
            ptxt = "<1" if 0 < p < 0.5 else ">99" if 99.5 <= p < 100 else f"{p:.0f}"
            handles.append(plt.Line2D([], [], color=c, lw=2,
                                      label=f"{lev:g} {unit}: {ptxt} % of the air is stronger"))
            del cs
        for t in near:
            ax.plot(t.position[0], t.position[2], "r^", ms=8, mec="white")
        leg = ax.legend(handles=handles, loc="upper right", fontsize=8, framealpha=0.85, title="shell = this line")
        leg.get_title().set_fontsize(8)
        ax.set_title(band_label(ghz), loc="left")
        ax.set_ylabel("height [m]")
    for ax in axes[-ncols:]:
        ax.set_xlabel("x (east) [m]")
    fig.colorbar(im, ax=list(axes), label=label, shrink=0.8)
    fig.suptitle(f"{cfg.name}: where each coverage shell runs (vertical cut at y = {ty:g} m, grey = inside buildings)"
                 f"\nA shell is only the BOUNDARY line. If almost all the air is stronger than its level, the line\n"
                 f"only circles the weak pockets, far from the transmitters (red triangles).", fontsize=10)
    fig.savefig(path, dpi=110)
    plt.close(fig)


def shell_meshes(values: np.ndarray, reached: np.ndarray, heights, xs, ys, levels, floor: float,
                 step: int = 1, smooth=(1.0, 1.4, 1.4),
                 min_fraction: float = 0.02) -> list[tuple[float, np.ndarray, np.ndarray]]:
    """Iso-surfaces ("coverage shells") of a signal volume -> [(level, vertices [n,3] local ENU m, faces [m,3])].

    values [nz, ny, nx] in dB/dBm; cells no ray reached count as `floor` (no signal). Cells inside buildings keep
    their computed values: the simulation includes transmission through walls (strong at low frequencies, weak at
    mmWave), so shells pass through buildings where the signal really does and wrap around them where it doesn't.
    (A first version set indoor cells to "no signal", which wrapped a fake shell around every building.)
    Gaussian smoothing (default sigma 1 cell vertically, 1.4 horizontally, i.e. ~5 m) removes Monte-Carlo speckle,
    and disconnected fragments smaller than `min_fraction` of the level's surface are dropped. Shells are
    left open where they meet the edges of the computed box (ground, sides, top) instead of being capped: a flat
    cap would look like a wall that isn't there. When a shell reaches the box edge, the coverage simply
    continues beyond the modelled area.
    """
    from scipy.ndimage import gaussian_filter
    from skimage.measure import marching_cubes
    hs, xs, ys = np.asarray(heights, float), np.asarray(xs, float), np.asarray(ys, float)
    v = np.where(reached, np.nan_to_num(values, neginf=floor, nan=floor), floor).astype(np.float32)
    v = gaussian_filter(v, sigma=smooth)
    dx = float(xs[1] - xs[0])
    out = []
    for lev in sorted(levels):
        if not (v.max() > lev > v.min()):
            continue
        try:
            verts, faces, _, _ = marching_cubes(v, level=lev, step_size=step, allow_degenerate=False)
        except RuntimeError:  # "No surface found": the level is (just) not reached anywhere
            continue
        z = np.interp(verts[:, 0], np.arange(len(hs)), hs)
        y = ys[0] + verts[:, 1] * dx
        x = xs[0] + verts[:, 2] * dx
        verts = np.column_stack([x, y, z])
        if min_fraction > 0 and len(faces):
            # drop small disconnected fragments (speckle / slivers) that clutter the view
            import trimesh
            parts = trimesh.Trimesh(vertices=verts, faces=faces, process=False).split(only_watertight=False)
            keep = [p for p in parts if len(p.faces) >= min_fraction * len(faces)]
            if keep:
                m = trimesh.util.concatenate(keep)
                verts, faces = np.asarray(m.vertices), np.asarray(m.faces)
        out.append((float(lev), verts, faces))
    return out


def shells_figure(cfg: ExperimentConfig, vols: dict, path: Path) -> None:
    """3-D view of the coverage shells for every band (same levels, same camera) - the 3-D pattern vs frequency."""
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    bands = list(vols)
    rc = cfg.radio_map
    # one fixed level for every band (the middle configured level): nested translucent shells are unreadable in a
    # static picture; the viewer offers all levels interactively
    srt = sorted(cfg.volume.shell_levels)
    levels = [srt[len(srt) // 2]]
    ncols = min(len(bands), 4)
    nrows = int(np.ceil(len(bands) / ncols))
    fig = plt.figure(figsize=(4.6 * ncols, 4.4 * nrows), constrained_layout=True)
    for i, ghz in enumerate(bands):
        vol = vols[ghz]
        vals, label = display_volume(cfg, vol)
        ax = fig.add_subplot(nrows, ncols, i + 1, projection="3d")
        reached = vol["path_gain"] > 0
        for lev, verts, faces in shell_meshes(vals, reached, vol["heights"], vol["xs"], vol["ys"], levels,
                                              rc.vmin_db - 60, step=2):
            pc = Poly3DCollection(verts[faces], alpha=0.35, facecolor="#2a9d8f", edgecolor="none")
            ax.add_collection3d(pc)
        for t in cfg.transmitters:
            ax.scatter(*t.position, color="red", s=30, depthshade=False)
        ax.set_xlim(vol["xs"][0], vol["xs"][-1])
        ax.set_ylim(vol["ys"][0], vol["ys"][-1])
        ax.set_zlim(0, vol["heights"][-1])
        # true proportions; wide scenes (2 km x 150 m) would be a flat sheet, so height is stretched to >= 1/4
        ratio = vol["heights"][-1] / (vol["xs"][-1] - vol["xs"][0])
        stretch = max(1.0, 0.25 / ratio)
        ax.set_box_aspect((1, 1, ratio * stretch))
        ax.view_init(elev=28, azim=-60)
        ax.set_title(band_label(ghz))
        ax.set_xticks([]); ax.set_yticks([])
        ax.set_zlabel("height [m]")
    unit = "dBm" if cfg.radio_map.display_metric == "rx_power" else "dB"
    note = f"; height stretched x{stretch:.1f}" if stretch > 1 else ""
    fig.suptitle(f"{cfg.name}: {levels[0]:g} {unit} coverage shell per band\n"
                 f"(inside the shape the signal is stronger than {levels[0]:g} {unit}; red dot = transmitter{note})")
    fig.savefig(path, dpi=110)
    plt.close(fig)


def display_volume(cfg: ExperimentConfig, vol: dict) -> tuple[np.ndarray, str]:
    if cfg.radio_map.display_metric == "rx_power":
        return vol["rx_power_dbm"], "received power [dBm]"
    return metrics.db(vol["path_gain"]), "path gain [dB]"


def slices_figure(cfg: ExperimentConfig, vols: dict, path: Path) -> None:
    """Vertical (x-z) cut for every band ([volume] slice_y, default: through the first transmitter)."""
    bands = list(vols)
    ncols = min(len(bands), 4)
    nrows = int(np.ceil(len(bands) / ncols))
    wide = (vols[bands[0]]["xs"][-1] - vols[bands[0]]["xs"][0]) > 1000
    fig, axes = plt.subplots(nrows, ncols, figsize=((9.5 if wide else 4.8) * ncols, 2.9 * nrows),
                             constrained_layout=True, squeeze=False)
    axes = axes.ravel()
    ty = cfg.volume.slice_y if cfg.volume.slice_y is not None else cfg.transmitters[0].position[1]
    near = [t for t in cfg.transmitters if abs(t.position[1] - ty) <= 50]  # transmitters close to the cut
    rc = cfg.radio_map
    im = None
    for ax, ghz in zip(axes, bands):
        vol = vols[ghz]
        vals, label = display_volume(cfg, vol)
        j = int(np.argmin(np.abs(vol["ys"] - ty)))
        sl = np.where(vol["indoor"][:, j, :], np.nan, vals[:, j, :])
        cmap = plt.cm.viridis.copy()
        cmap.set_bad("#d9d9d9")
        im = ax.pcolormesh(vol["xs"], vol["heights"], sl, vmin=rc.vmin_db, vmax=rc.vmax_db, cmap=cmap, shading="nearest")
        for t in near:
            ax.plot(t.position[0], t.position[2], "r^", ms=7)
        ax.set_title(band_label(ghz))
        ax.set_xlabel("x (east) [m]")
        ax.set_ylabel("height [m]")
    for ax in axes[len(bands):]:
        ax.axis("off")
    # the "grey = buildings" note goes in the title: a long colour-bar label is clipped on a single-row figure
    fig.colorbar(im, ax=axes[: len(bands)].tolist(), label=label, shrink=0.8)
    where = "through the transmitter" if cfg.volume.slice_y is None else "west-east"
    fig.suptitle(f"{cfg.name}: vertical cut {where} (y = {ty:g} m) - signal vs height, per band "
                 f"(grey = inside buildings; red = transmitters within 50 m of the cut)")
    fig.savefig(path, dpi=120)
    plt.close(fig)
