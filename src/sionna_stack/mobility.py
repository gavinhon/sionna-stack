"""Moving-transmitter experiment: a sequence of ray-tracing snapshots along a route.

Sionna RT computes each snapshot independently (it is not a time-domain wave solver). "Dynamics" are obtained by
moving the TX along the route and re-running PathSolver + RadioMapSolver at every step. The TX velocity is also
set, so every path carries its Doppler shift (Paths.doppler) for that snapshot.

Outputs (in <run_dir>/mobility/):
    mobility.json                  route, times, TX positions, speed, bands, grid metadata
    mobility_metrics.csv           one row per (band, step, receiver)
    mobility_path_gain.png         total path gain vs time, one panel per receiver, one line per band
    mobility_radio_maps.gif        animated radio maps (all bands side by side, shared colour scale)
    band_<slug>/mobility_radio_maps.npz   path_gain [steps, y, x], cell_centers, times, tx_positions
    band_<slug>/mobility_paths.npz        per step: s{i:03d}_{a,tau,valid,interactions,vertices,doppler}
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import matplotlib.pyplot as plt
import mitsuba as mi
import numpy as np

from . import metrics
from .config import ExperimentConfig, band_label, band_slug
from .experiment import _release_gpu_cache, paths_to_numpy, set_band


def route_positions(waypoints, num_steps: int):
    """Equally spaced points along a polyline; returns positions [S, 3], cumulative distance [S], unit dirs [S, 3]."""
    w = np.asarray(waypoints, float)
    seg = np.diff(w, axis=0)
    seg_len = np.linalg.norm(seg, axis=1)
    cum = np.concatenate([[0.0], np.cumsum(seg_len)])
    s = np.linspace(0.0, cum[-1], num_steps)
    pos, dirs = [], []
    for d in s:
        k = min(np.searchsorted(cum, d, side="right") - 1, len(seg) - 1)
        t = (d - cum[k]) / seg_len[k]
        pos.append(w[k] + t * seg[k])
        dirs.append(seg[k] / seg_len[k])
    return np.array(pos), s, np.array(dirs)


def run_mobility(scene, cfg: ExperimentConfig, run_dir: Path) -> dict:
    from sionna.rt import PathSolver, RadioMapSolver

    m, rc, pc = cfg.mobility, cfg.radio_map, cfg.paths
    out = run_dir / "mobility"
    out.mkdir(parents=True, exist_ok=True)
    tx = scene.get(m.tx)
    if tx is None:
        raise ValueError(f"[mobility] tx = {m.tx!r} is not a transmitter in the config")
    tx_power = next(t.power_dbm for t in cfg.transmitters if t.name == m.tx)
    original_pos = [float(v) for v in np.array(tx.position).ravel()]

    positions, dist, dirs = route_positions(m.waypoints, m.num_steps)
    times = dist / m.speed_mps
    path_solver = PathSolver(deterministic=pc.deterministic)
    rm_solver = RadioMapSolver()
    rows, maps, timings = [], {}, {}

    for ghz in cfg.bands_ghz:
        set_band(scene, cfg, ghz)
        t0 = time.perf_counter()
        pg_steps, npz_paths = [], {}
        for i, (p, d) in enumerate(zip(positions, dirs)):
            tx.position = mi.Point3f(*map(float, p))
            tx.velocity = mi.Vector3f(*map(float, d * m.speed_mps))
            paths = path_solver(
                scene, max_depth=pc.max_depth, samples_per_src=m.samples_per_src, synthetic_array=pc.synthetic_array,
                los=pc.los, specular_reflection=pc.specular_reflection, diffuse_reflection=pc.diffuse_reflection,
                refraction=pc.refraction, diffraction=pc.diffraction, edge_diffraction=pc.edge_diffraction, seed=pc.seed)
            pn = paths_to_numpy(paths)
            pn["doppler"] = np.array(paths.doppler)
            for k, v in pn.items():
                npz_paths[f"s{i:03d}_{k}"] = v
            rm = rm_solver(
                scene, center=mi.Point3f(*rc.center), orientation=mi.Point3f(*rc.orientation), size=mi.Point2f(*rc.size),
                cell_size=mi.Point2f(*m.cell_size), samples_per_tx=m.samples_per_tx, max_depth=rc.max_depth,
                los=rc.los, specular_reflection=rc.specular_reflection, diffuse_reflection=rc.diffuse_reflection,
                refraction=rc.refraction, diffraction=rc.diffraction, edge_diffraction=rc.edge_diffraction, seed=rc.seed)
            pg = np.array(rm.path_gain)[0]
            pg_steps.append(pg.astype(np.float32))
            if i == 0:
                cell_centers = np.array(rm.cell_centers)
            del rm, paths
            _release_gpu_cache()
            ti = [t.name for t in cfg.transmitters].index(m.tx)
            for ri, r in enumerate(cfg.receivers):
                lm = metrics.link_metrics(pn["a"], pn["tau"], pn["valid"], pn["interactions"], ri, ti, tx_power)
                v = pn["valid"][ri, ti].astype(bool)
                dop = np.abs(pn["doppler"][ri, ti][v]) if v.any() else np.array([np.nan])
                rows.append({"band_ghz": ghz, "step": i, "time_s": float(times[i]),
                             "tx_x": float(p[0]), "tx_y": float(p[1]), "tx_z": float(p[2]), "rx": r.name, **lm,
                             "max_abs_doppler_hz": float(np.nanmax(dop))})
        timings[band_slug(ghz)] = time.perf_counter() - t0
        maps[ghz] = np.stack(pg_steps)
        bdir = run_dir / f"band_{band_slug(ghz)}"
        bdir.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(bdir / "mobility_radio_maps.npz", path_gain=maps[ghz], cell_centers=cell_centers,
                            times=times, tx_positions=positions, frequency_hz=ghz * 1e9)
        np.savez_compressed(bdir / "mobility_paths.npz", **npz_paths, times=times, tx_positions=positions,
                            rx_names=np.array([r.name for r in cfg.receivers]),
                            rx_positions=np.array([r.position for r in cfg.receivers], float), frequency_hz=ghz * 1e9)
        print(f"  mobility {band_label(ghz)}: {m.num_steps} snapshots in {timings[band_slug(ghz)]:.1f} s")

    # restore the static TX
    tx.position = mi.Point3f(*original_pos)
    tx.velocity = mi.Vector3f(0.0, 0.0, 0.0)

    import pandas as pd
    df = pd.DataFrame(rows)
    df.to_csv(out / "mobility_metrics.csv", index=False)
    _plot_gain_vs_time(df, cfg, out / "mobility_path_gain.png")
    _animate(maps, cell_centers, positions, times, cfg, out / "mobility_radio_maps.gif")

    info = {"tx": m.tx, "waypoints": m.waypoints, "num_steps": m.num_steps, "speed_mps": m.speed_mps,
            "duration_s": float(times[-1]), "route_length_m": float(dist[-1]),
            "times_s": times.tolist(), "tx_positions": positions.tolist(), "cell_size": m.cell_size,
            "bands_ghz": cfg.bands_ghz, "timings_s": timings}
    (out / "mobility.json").write_text(json.dumps(info, indent=2))
    return info


def _plot_gain_vs_time(df, cfg: ExperimentConfig, path: Path) -> None:
    rxs = [r.name for r in cfg.receivers]
    fig, axes = plt.subplots(len(rxs), 1, figsize=(9, 2.6 * len(rxs)), sharex=True, constrained_layout=True,
                             squeeze=False)
    for ax, rx in zip(axes[:, 0], rxs):
        for k, ghz in enumerate(cfg.bands_ghz):
            d = df[(df.rx == rx) & (df.band_ghz == ghz)]
            ax.plot(d.time_s, d.total_path_gain_db, f"C{k}-", label=band_label(ghz))
            los = d[d.los]
            ax.plot(los.time_s, los.total_path_gain_db, f"C{k}o", ms=3)
        ax.set_title(f"{rx} (dots = line of sight to the moving TX)")
        ax.set_ylabel("total path gain [dB]")
        ax.grid(alpha=0.3)
        ax.legend(loc="lower right")
    axes[-1, 0].set_xlabel("time [s] (TX moving along the route)")
    fig.savefig(path, dpi=120)
    plt.close(fig)


def _animate(maps: dict, cell_centers, positions, times, cfg: ExperimentConfig, path: Path) -> None:
    from matplotlib.animation import FuncAnimation, PillowWriter

    bands = list(maps)
    ext = [cell_centers[..., 0].min(), cell_centers[..., 0].max(), cell_centers[..., 1].min(), cell_centers[..., 1].max()]
    fig, axes = plt.subplots(1, len(bands), figsize=(5 * len(bands), 4.8), constrained_layout=True, squeeze=False)
    axes = axes[0]
    ims, markers = [], []
    for ax, ghz in zip(axes, bands):
        im = ax.imshow(metrics.db(maps[ghz][0]), origin="lower", extent=ext, vmin=cfg.radio_map.vmin_db,
                       vmax=cfg.radio_map.vmax_db, cmap="viridis")
        ax.plot(positions[:, 0], positions[:, 1], "w--", lw=0.8)
        mk, = ax.plot(*positions[0, :2], "r^", ms=10)
        for r in cfg.receivers:
            ax.plot(*r.position[:2], "wo", ms=5, mec="k")
        ax.set_title(band_label(ghz))
        ims.append(im)
        markers.append(mk)
    fig.colorbar(ims[-1], ax=axes, label="path gain [dB]", shrink=0.8)
    title = fig.suptitle("")

    def frame(i):
        with np.errstate(divide="ignore"):
            for im, ghz in zip(ims, bands):
                im.set_data(metrics.db(maps[ghz][i]))
        for mk in markers:
            mk.set_data([positions[i, 0]], [positions[i, 1]])
        title.set_text(f"moving TX, t = {times[i]:.1f} s")
        return ims + markers + [title]

    FuncAnimation(fig, frame, frames=len(times)).save(path, writer=PillowWriter(fps=6))
    plt.close(fig)
