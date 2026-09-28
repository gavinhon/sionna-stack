"""Frequency-band propagation experiment, reusable for any scene, any list of bands and any antenna setup.

Pipeline per band (same scene geometry, TX power, TX/RX positions and antennas; only the frequency changes):
    scene.frequency = f -> PathSolver (paths to the receivers) -> RadioMapSolver per beam (coverage grid)
    -> best server / best beam -> metrics (+ SNR, capacity if bandwidths are given) -> figures + raw arrays
Then cross-band figures (shared colour scale), a frequency-sweep summary and a manifest.json.

Antennas: all transmitters share one planar array (Sionna constraint). Sectors = several transmitters at one
site with different azimuths. Beams = precoding vectors steering the array (``[antennas] beams``); coverage and
link metrics use the best (transmitter, beam) pair per location, like a UE attaching to the strongest
sector/SSB beam.

Output layout (one directory per run; ``latest`` symlink points at the newest):
    outputs/<experiment>/<run_id>/
        manifest.json               config, scene metadata, backend, versions, timings, files, warnings
        metrics.csv / metrics.md    one row per (band, receiver): serving TX/beam, LOS, paths, gains, delay, SNR
        radio_map_stats.csv         one row per band (percentages of OUTDOOR cells)
        comparison_radio_maps.png   all bands, same colour scale (+ difference map for <= 3 bands)
        band_summary.png            per-receiver gain / RX power / SNR / capacity and coverage vs frequency
        path_gain_cdf.png, power_delay_profiles.png
        band_<slug>/
            radio_map.npz           path_gain [1, y, x] best server (linear), rx_power_dbm, serving_tx/beam,
                                    outdoor mask, cell_centers [y, x, 3], grid metadata
            paths.npz               serving-link paths: a [rx,1,1,1,P,1], tau, valid, interactions, vertices, ...
            radio_map_sionna.png    RadioMap.show()  (Sionna's 2-D map; first beam if several)
            scene_radio_map.png     Scene.render(radio_map=...) (3-D scene with coverage overlay)
            scene_paths.png         Scene.render(paths=...)
                                    (these three are skipped for batched runs: many transmitters, see is_batched)
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import backend
from .config import ExperimentConfig, band_label, band_slug

backend.init(quiet=True)  # must precede `import sionna.rt`

import matplotlib.pyplot as plt  # noqa: E402
import mitsuba as mi  # noqa: E402
import sionna.rt as rt  # noqa: E402
from sionna.rt import (Camera, PathSolver, PlanarArray, RadioMapSolver,  # noqa: E402
                       Receiver, Transmitter, load_scene)

from . import layout, materials, metrics  # noqa: E402

RENDER_PATH_DEPTH = 3  # interactions drawn in scene_paths.png (metrics always use [paths] max_depth)


# --------------------------------------------------------------------------------------------- scene
def load_scene_from_cfg(cfg: ExperimentConfig):
    if cfg.scene.startswith("builtin:"):
        name = cfg.scene.split(":", 1)[1]
        if not hasattr(rt.scene, name):
            raise ValueError(f"Unknown built-in scene {name!r}")
        return load_scene(getattr(rt.scene, name))
    return load_scene(cfg.scene)


def build_scene(cfg: ExperimentConfig):
    scene = load_scene_from_cfg(cfg)
    a = cfg.antennas
    scene.tx_array = PlanarArray(num_rows=a.tx_rows, num_cols=a.tx_cols, vertical_spacing=a.tx_vertical_spacing,
                                 horizontal_spacing=a.tx_horizontal_spacing, pattern=a.tx_pattern,
                                 polarization=a.polarization)
    scene.rx_array = PlanarArray(num_rows=1, num_cols=1, pattern=a.rx_pattern, polarization=a.polarization)
    for t in cfg.transmitters:
        tx = Transmitter(name=t.name, position=mi.Point3f(*t.position), power_dbm=t.power_dbm, display_radius=3)
        scene.add(tx)
        aim = t.aim_point()
        if aim is not None:
            tx.look_at(mi.Point3f(*aim))
    for r in cfg.receivers:
        scene.add(Receiver(name=r.name, position=mi.Point3f(*r.position), display_radius=3))
    materials.attach_water_model(scene)  # no-op for scenes without water
    if cfg.extrapolate_materials:
        materials.enable_extrapolation(scene)
    return scene


def beam_weights(scene, cfg: ExperimentConfig) -> list[np.ndarray]:
    """Unit-norm steering vectors w = exp(-j 2pi p.u)/sqrt(N) for each configured beam.

    p = element positions in wavelengths (array's own frame: x = boresight, y = left, z = up), u = beam
    direction. Sign convention verified empirically (a beam steered to +30 deg peaks at +28.7 deg on a 4 m grid).
    Positions in wavelengths make the weights frequency-independent (the array scales with the wavelength).
    """
    pos = np.array(scene.tx_array.normalized_positions)
    pos = pos.T if pos.ndim == 2 and pos.shape[0] == 3 and pos.shape[1] != 3 else pos
    pos = pos.reshape(-1, 3)
    out = []
    for az, el in cfg.antennas.beams:
        az, el = np.radians(az), np.radians(el)
        u = np.array([np.cos(el) * np.cos(az), np.cos(el) * np.sin(az), np.sin(el)])
        out.append(np.exp(-1j * 2 * np.pi * (pos @ u)) / np.sqrt(len(pos)))
    return out


def set_band(scene, cfg: ExperimentConfig, ghz: float) -> list[str]:
    """Set scene.frequency; returns the list of materials that are extrapolated at this band."""
    bad = materials.out_of_range(scene, ghz * 1e9)
    if bad and not cfg.extrapolate_materials:
        raise ValueError(
            f"{band_label(ghz)}: no ITU-R P.2040 material data for: {', '.join(bad)}. "
            "Set [bands] extrapolate_materials = true to extrapolate (results are then labelled)."
        )
    scene.frequency = ghz * 1e9
    return bad


def _release_gpu_cache() -> None:
    """Return Dr.Jit's cached allocations to the driver between heavy steps.

    Without this, a large PathSolver result followed by a 1e9-ray RadioMapSolver call filled the 16 GB GPU and
    Dr.Jit kept flushing/re-allocating ("jit_flush_malloc_cache(): Dr.Jit exhausted the available memory"),
    turning a 5 s step into an apparent hang (observed during the accuracy study)."""
    import drjit as dr
    dr.sync_thread()
    dr.flush_malloc_cache()


def band_bandwidth_hz(cfg: ExperimentConfig, ghz: float) -> float | None:
    if not cfg.bandwidth_mhz:
        return None
    return cfg.bandwidth_mhz[cfg.bands_ghz.index(ghz)] * 1e6


# ------------------------------------------------------------------------------------------- solvers
def compute_paths(scene, cfg: ExperimentConfig):
    p = cfg.paths
    solver = PathSolver(deterministic=p.deterministic)
    return solver(
        scene, max_depth=p.max_depth, max_num_paths_per_src=p.max_num_paths_per_src,
        samples_per_src=p.samples_per_src, synthetic_array=p.synthetic_array, los=p.los,
        specular_reflection=p.specular_reflection, diffuse_reflection=p.diffuse_reflection,
        refraction=p.refraction, diffraction=p.diffraction, edge_diffraction=p.edge_diffraction, seed=p.seed,
    )


def compute_radio_map(scene, cfg: ExperimentConfig, weights: np.ndarray | None = None):
    r = cfg.radio_map
    pv = None
    if weights is not None and len(weights) > 1:
        pv = (mi.TensorXf(weights.real.astype(np.float32)), mi.TensorXf(weights.imag.astype(np.float32)))
    return RadioMapSolver()(
        scene, center=mi.Point3f(*r.center), orientation=mi.Point3f(*r.orientation),
        size=mi.Point2f(*r.size), cell_size=mi.Point2f(*r.cell_size), samples_per_tx=r.samples_per_tx,
        max_depth=r.max_depth, los=r.los, specular_reflection=r.specular_reflection,
        diffuse_reflection=r.diffuse_reflection, refraction=r.refraction, diffraction=r.diffraction,
        edge_diffraction=r.edge_diffraction, seed=r.seed, rr_depth=r.rr_depth, precoding_vec=pv,
    )


def paths_to_numpy(paths) -> dict:
    a, tau = paths.cir(normalize_delays=False, out_type="numpy")
    return {
        "a": a,
        "tau": tau,
        "valid": np.array(paths.valid),
        "interactions": np.array(paths.interactions),
        "vertices": np.array(paths.vertices),
    }


# GPU memory limits (RTX 5080, 16 GB), found on the 27-sector / 12-receiver Downtown Core run:
TX_BATCH = 6     # transmitters per RadioMapSolver call (at most; see map_batch_size)
PATH_LINKS = 12  # transmitter x receiver pairs per PathSolver call (depth 10, 3e6 samples): 72 pairs asked for 34 GB
MAX_RAYS_PER_CALL = 2**32 - 1  # RadioMapSolver seeds its sampler with a 32-bit wavefront size (samples x TX)


def map_batch_size(cfg: ExperimentConfig) -> int:
    """Transmitters per RadioMapSolver call: <= TX_BATCH and samples_per_tx x n <= 2^32 - 1 (1e9 rays -> 4)."""
    return max(1, min(TX_BATCH, MAX_RAYS_PER_CALL // max(1, cfg.radio_map.samples_per_tx)))


def is_batched(cfg: ExperimentConfig) -> bool:
    n_tx, n_rx = len(cfg.transmitters), len(cfg.receivers)
    return n_tx > map_batch_size(cfg) or n_tx * n_rx > PATH_LINKS


def tx_batches(scene, cfg: ExperimentConfig, size: int = TX_BATCH):
    """Yield lists of transmitter indices (cfg order); during each step only those transmitters are in the scene.

    Scenes that are not batched (is_batched) yield one batch and are not touched. Otherwise all transmitters are
    removed and each batch of `size` is added in turn; afterwards all are re-added in config order, so solver
    outputs keep their usual TX order."""
    names = [t.name for t in cfg.transmitters]
    if not is_batched(cfg):
        yield list(range(len(names)))
        return
    objs = {n: scene.get(n) for n in names}
    for n in names:
        scene.remove(n)
    try:
        for s in range(0, len(names), size):
            idx = list(range(s, min(s + size, len(names))))
            for i in idx:
                scene.add(objs[names[i]])
            try:
                yield idx
            finally:
                for i in idx:
                    scene.remove(names[i])
    finally:
        for n in names:
            scene.add(objs[n])


def _pad_paths(parts: list[dict]) -> dict:
    """Concatenate per-receiver serving paths (single TX axis) along the receiver axis, padding the path axis."""
    P = max(p["valid"].shape[-1] for p in parts)

    def pad(a, axis, fill):
        w = [(0, 0)] * a.ndim
        w[axis] = (0, P - a.shape[axis])
        return np.pad(a, w, constant_values=fill)
    return {
        "a": np.concatenate([pad(p["a"], 4, 0) for p in parts], 0),
        "tau": np.concatenate([pad(p["tau"], 2, 0) for p in parts], 0),
        "valid": np.concatenate([pad(p["valid"], 2, False) for p in parts], 0),
        "interactions": np.concatenate([pad(p["interactions"], 3, 0) for p in parts], 1),
        "vertices": np.concatenate([pad(p["vertices"], 3, 0) for p in parts], 1),
    }


def serving_paths(p: dict, weights: list[np.ndarray], tx_power_dbm: list[float],
                  return_power: bool = False):
    """Pick, per receiver, the (tx, beam) with the highest received power and return its paths.

    Returns (paths dict with a single TX axis, serving_tx [rx], serving_beam [rx]) and, with return_power,
    also the serving power [dBm] per receiver.
    """
    a = p["a"]                       # [rx, 1, tx, tx_ant, P, 1]
    valid = p["valid"].astype(bool)  # [rx, tx, P]
    n_rx = a.shape[0]
    best = np.full((n_rx,), -np.inf)
    s_tx = np.zeros(n_rx, int)
    s_b = np.zeros(n_rx, int)
    effs = [metrics.effective_paths(a, w) for w in weights]  # each [rx, 1, tx, 1, P, 1]
    for b, ae in enumerate(effs):
        g = np.sum(np.where(valid, np.abs(ae[:, 0, :, 0, :, 0]) ** 2, 0.0), axis=-1)  # [rx, tx]
        with np.errstate(divide="ignore"):
            pw = 10 * np.log10(g) + np.asarray(tx_power_dbm)[None, :]
        for r in range(n_rx):
            t = int(np.argmax(pw[r]))
            if pw[r, t] > best[r]:
                best[r], s_tx[r], s_b[r] = pw[r, t], t, b
    rr = np.arange(n_rx)
    out = {
        "a": np.stack([effs[s_b[r]][r, :, s_tx[r]] for r in rr])[:, :, None],     # [rx, 1, 1, 1, P, 1]
        "tau": p["tau"][rr, s_tx][:, None],                                        # [rx, 1, P]
        "valid": p["valid"][rr, s_tx][:, None],
        "interactions": p["interactions"][:, rr, s_tx][:, :, None],                # [D, rx, 1, P]
        "vertices": p["vertices"][:, rr, s_tx][:, :, None],                        # [D, rx, 1, P, 3]
    }
    return (out, s_tx, s_b, best) if return_power else (out, s_tx, s_b)


def batched_serving_paths(scene, cfg: ExperimentConfig, weights, powers) -> tuple[dict, np.ndarray, np.ndarray]:
    """serving_paths over all transmitters, solving a few transmitters at a time (same result, less memory)."""
    best = None
    for idx in tx_batches(scene, cfg, size=max(1, PATH_LINKS // max(1, len(cfg.receivers)))):
        p_b = paths_to_numpy(compute_paths(scene, cfg))
        out, s_tx, s_b, pw = serving_paths(p_b, weights, [powers[i] for i in idx], return_power=True)
        del p_b
        _release_gpu_cache()
        rows = [{k: v[r:r + 1] if k in ("a", "tau", "valid") else v[:, r:r + 1] for k, v in out.items()}
                for r in range(len(pw))]
        if best is None:
            best = {"pw": pw.copy(), "tx": np.array(idx)[s_tx], "beam": s_b.copy(), "rows": rows}
            continue
        for r in np.where(pw > best["pw"])[0]:
            best["pw"][r], best["tx"][r], best["beam"][r] = pw[r], idx[s_tx[r]], s_b[r]
            best["rows"][r] = rows[r]
    return _pad_paths(best["rows"]), best["tx"], best["beam"]


def radio_map_grid(rm) -> dict:
    return {
        "cell_centers": np.array(rm.cell_centers),  # [cells_y, cells_x, 3], scene coordinates [m]
        "center": np.array(rm.center).ravel(),
        "size": np.array(rm.size).ravel(),
        "cell_size": np.array(rm.cell_size).ravel(),
        "orientation": np.array(rm.orientation).ravel(),
    }


def display_map(cfg: ExperimentConfig, m: dict) -> tuple[np.ndarray, str]:
    """The map shown in figures/viewer: path gain [dB] or received power [dBm] (best server)."""
    if cfg.radio_map.display_metric == "rx_power":
        return m["rx_power_dbm"], "received power [dBm]"
    return metrics.db(m["path_gain"][0]), "path gain [dB]"


# ------------------------------------------------------------------------------------------- figures
def _camera(pos, look_at):
    return Camera(position=mi.Point3f(*pos), look_at=mi.Point3f(*look_at))


def render_figures(scene, cfg: ExperimentConfig, rm, paths, band_dir: Path, ghz: float, n_beams: int) -> None:
    r, rc = cfg.render, cfg.radio_map
    note = " (Sionna view, first beam)" if n_beams > 1 else ""
    # Sionna's own figures always show path gain; pick a scale that fits antenna gains
    vmin, vmax = (rc.vmin_db, rc.vmax_db) if rc.display_metric == "path_gain" else (-150.0, -50.0)
    title = f"{band_label(ghz)} - path gain [dB]{note}"

    fig = rm.show(metric="path_gain", vmin=vmin, vmax=vmax, show_tx=True, show_rx=True)
    fig.suptitle(title)
    fig.savefig(band_dir / "radio_map_sionna.png", dpi=130, bbox_inches="tight")
    plt.close(fig)

    cam = _camera(r.camera_position, r.camera_look_at)
    fig = scene.render(camera=cam, radio_map=rm, rm_metric="path_gain", rm_vmin=vmin, rm_vmax=vmax,
                       rm_show_color_bar=True, resolution=tuple(r.resolution), num_samples=r.num_samples)
    fig.suptitle(title)
    fig.savefig(band_dir / "scene_radio_map.png", dpi=130, bbox_inches="tight")
    plt.close(fig)

    pcam = _camera(r.paths_camera_position or r.camera_position, r.paths_camera_look_at or r.camera_look_at)
    fig = scene.render(camera=pcam, paths=paths, resolution=tuple(r.resolution), num_samples=r.num_samples)
    shown = min(cfg.paths.max_depth, RENDER_PATH_DEPTH)
    fig.suptitle(f"{band_label(ghz)} - ray paths to receivers (up to {shown} interactions shown)")
    fig.savefig(band_dir / "scene_paths.png", dpi=130, bbox_inches="tight")
    plt.close(fig)


def _extent(cc):
    return [cc[..., 0].min(), cc[..., 0].max(), cc[..., 1].min(), cc[..., 1].max()]


def comparison_figures(cfg: ExperimentConfig, results: dict, run_dir: Path) -> None:
    bands = list(results)
    rc = cfg.radio_map
    with_diff = 2 <= len(bands) <= 3
    n = len(bands) + (1 if with_diff else 0)
    ncols = min(n, 4)
    nrows = int(np.ceil(n / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(4.6 * ncols, 4.3 * nrows), constrained_layout=True, squeeze=False)
    axes = axes.ravel()
    label = ""
    for ax, ghz in zip(axes, bands):
        m = results[ghz]["rm"]
        dm, label = display_map(cfg, m)
        ext = _extent(m["cell_centers"])
        im = ax.imshow(dm, origin="lower", extent=ext, vmin=rc.vmin_db, vmax=rc.vmax_db, cmap="viridis")
        ax.set_title(band_label(ghz))
        ax.set_xlabel("x [m]")
        ax.set_ylabel("y [m]")
        for t in cfg.transmitters:
            ax.plot(*t.position[:2], "r^", ms=8)
        for r in cfg.receivers:
            ax.plot(*r.position[:2], "wo", ms=5, mec="k")
    fig.colorbar(im, ax=axes[: len(bands)].tolist(), label=f"{label} (shared scale)", shrink=0.8)
    if with_diff:
        lo, hi = bands[0], bands[-1]
        a, b = results[lo]["rm"]["path_gain"][0], results[hi]["rm"]["path_gain"][0]
        with np.errstate(invalid="ignore", divide="ignore"):
            diff = np.where((a > 0) & (b > 0), metrics.db(b) - metrics.db(a), np.nan)
        ax = axes[len(bands)]
        im2 = ax.imshow(diff, origin="lower", extent=ext, cmap="magma", vmin=-50, vmax=0)
        ax.set_title(f"{band_label(hi)} minus {band_label(lo)} [dB]")
        fig.colorbar(im2, ax=ax, label="difference [dB]", shrink=0.8)
    for ax in axes[n:]:
        ax.axis("off")
    fig.suptitle(f"{cfg.name}: coverage per band (same site, same antennas, same colour scale)")
    fig.savefig(run_dir / "comparison_radio_maps.png", dpi=120)
    plt.close(fig)

    # CDF over outdoor cells reached by rays
    fig, ax = plt.subplots(figsize=(7, 4.4), constrained_layout=True)
    for k, ghz in enumerate(bands):
        m = results[ghz]["rm"]
        dm, label = display_map(cfg, m)
        sel = (m["path_gain"][0] > 0) & m["outdoor"]
        v = np.sort(dm[sel])
        ax.plot(v, np.linspace(0, 1, v.size), label=band_label(ghz), color=plt.cm.plasma(k / max(len(bands) - 1, 1)))
    ax.set_xlabel(label)
    ax.set_ylabel("CDF over outdoor cells reached by rays")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, ncol=2)
    fig.savefig(run_dir / "path_gain_cdf.png", dpi=130)
    plt.close(fig)

    # Power-delay profiles (at most 4 bands, spread over the list, to stay readable)
    show = bands if len(bands) <= 4 else [bands[i] for i in np.linspace(0, len(bands) - 1, 4).round().astype(int)]
    rxs = [r.name for r in cfg.receivers]
    fig, axes = plt.subplots(len(rxs), 1, figsize=(8, 2.6 * len(rxs)), constrained_layout=True, squeeze=False)
    for i, (ax, name) in enumerate(zip(axes[:, 0], rxs)):
        for k, ghz in enumerate(show):
            p = results[ghz]["paths"]
            v = p["valid"][i, 0].astype(bool)
            if not v.any():
                continue
            g = metrics.db(np.abs(p["a"][i, 0, 0, 0, v, 0]) ** 2)
            t = p["tau"][i, 0, v] * 1e9
            color, marker = f"C{k}", "os^Dv"[k % 5]
            ml, sl, _ = ax.stem(t, g, bottom=-200, linefmt=color, markerfmt=color + marker, basefmt=" ",
                                label=band_label(ghz))
            plt.setp(sl, alpha=0.45)
            plt.setp(ml, markersize=4)
        ax.set_ylim(-200, -40)
        ax.set_title(name)
        ax.set_ylabel("path gain [dB]")
        ax.grid(alpha=0.3)
        ax.legend(loc="upper right", fontsize=8)
    axes[-1, 0].set_xlabel("absolute delay [ns]")
    fig.savefig(run_dir / "power_delay_profiles.png", dpi=130)
    plt.close(fig)


def band_summary_figure(cfg: ExperimentConfig, rows: list[dict], stats: list[dict], run_dir: Path) -> None:
    """How each quantity changes with frequency: the 'which band is best where' chart."""
    import pandas as pd
    df, st = pd.DataFrame(rows), pd.DataFrame(stats)
    has_bw = cfg.bandwidth_mhz is not None
    panels = [("total_path_gain_db", "total path gain [dB]"), ("rx_power_dbm", "received power [dBm]")]
    if has_bw:
        panels += [("snr_db", "SNR over channel bandwidth [dB]"), ("shannon_capacity_mbps", "Shannon bound [Mbit/s]")]
    cov_key = "coverage_pct_snr_above_0dB" if has_bw else [c for c in st.columns if c.startswith("coverage_pct_above")][0]
    fig, axes = plt.subplots(1, len(panels) + 1, figsize=(4.3 * (len(panels) + 1), 4.0), constrained_layout=True)

    def band_ticks(ax):
        from matplotlib.ticker import FixedLocator, NullLocator
        ax.xaxis.set_major_locator(FixedLocator(cfg.bands_ghz))
        ax.xaxis.set_minor_locator(NullLocator())
        ax.set_xticklabels([band_label(g).replace(" ", "\n") for g in cfg.bands_ghz], fontsize=7)

    for ax, (col, lab) in zip(axes, panels):
        for k, rx in enumerate(r.name for r in cfg.receivers):
            d = df[df.rx == rx].sort_values("band_ghz")
            ax.plot(d.band_ghz, d[col], "o-", color=f"C{k}", label=rx, ms=4)
        ax.set_xscale("log")
        band_ticks(ax)
        ax.set_xlabel("band")
        ax.set_ylabel(lab)
        if col == "shannon_capacity_mbps":
            ax.set_yscale("log")
        ax.grid(alpha=0.3, which="both")
    axes[0].legend(fontsize=8)
    ax = axes[-1]
    ax.plot(st.band_ghz, st[cov_key], "ks-", ms=4)
    ax.set_xscale("log")
    band_ticks(ax)
    ax.set_ylim(0, 100)
    ax.set_xlabel("band")
    ax.set_ylabel("outdoor area covered [%]" + (" (SNR > 0 dB)" if has_bw else ""))
    ax.grid(alpha=0.3, which="both")
    fig.suptitle(f"{cfg.name}: frequency sweep (per receiver and over the whole area)")
    fig.savefig(run_dir / "band_summary.png", dpi=130)
    plt.close(fig)


# ----------------------------------------------------------------------------------------- run
def _git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              check=True, cwd=Path(__file__).parent).stdout.strip() or None
    except Exception:
        return None


def _markdown_table(rows: list[dict], cols: list[str]) -> str:
    def fmt(v):
        if isinstance(v, float):
            return "n/a" if np.isnan(v) else f"{v:.1f}"
        return str(v)
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join(fmt(r.get(c, "")) for c in cols) + " |" for r in rows]
    return "\n".join(out) + "\n"


def run_band(scene, cfg: ExperimentConfig, ghz: float, band_dir: Path, render: bool = True,
             outdoor_cache: dict | None = None) -> dict:
    band_dir.mkdir(parents=True, exist_ok=True)
    extrapolated = set_band(scene, cfg, ghz)
    timings = {}
    weights = beam_weights(scene, cfg)
    powers = [t.power_dbm for t in cfg.transmitters]
    bw = band_bandwidth_hz(cfg, ghz)

    t0 = time.perf_counter()
    batched = is_batched(cfg)
    if batched:
        p, s_tx, s_b = batched_serving_paths(scene, cfg, weights, powers)
        paths = None
    else:
        paths = compute_paths(scene, cfg)
        p_all = paths_to_numpy(paths)  # forces evaluation
        p, s_tx, s_b = serving_paths(p_all, weights, powers)
        del p_all
    _release_gpu_cache()  # 1e9-ray radio maps need the memory; see _release_gpu_cache
    timings["paths_s"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    best_pw, best_pg = None, None
    first_rm = None
    for idx in tx_batches(scene, cfg, size=map_batch_size(cfg)):
        for b, w in enumerate(weights):
            rm = compute_radio_map(scene, cfg, w)
            if first_rm is None:
                first_rm = rm
            pg = np.array(rm.path_gain)                                     # [tx in batch, y, x]
            with np.errstate(divide="ignore"):
                pw = 10 * np.log10(pg) + np.asarray([powers[i] for i in idx])[:, None, None]  # [dBm]
            k = np.argmax(pw, axis=0)
            pw_max = np.take_along_axis(pw, k[None], 0)[0]
            pg_max = np.take_along_axis(pg, k[None], 0)[0]
            k = np.asarray(idx)[k]                                          # batch index -> config index
            if first_rm is not rm:
                del rm
            _release_gpu_cache()
            if best_pw is None:
                best_pw, best_pg, serv_tx, serv_b = pw_max, pg_max, k, np.zeros_like(k)
            else:
                better = pw_max > best_pw
                best_pw = np.where(better, pw_max, best_pw)
                best_pg = np.where(better, pg_max, best_pg)
                serv_tx = np.where(better, k, serv_tx)
                serv_b = np.where(better, b, serv_b)
    grid = radio_map_grid(first_rm)
    key = tuple(np.round(grid["cell_centers"][0, :2, :2].ravel(), 3)) + grid["cell_centers"].shape
    if outdoor_cache is not None and key in outdoor_cache:
        outdoor = outdoor_cache[key]
    else:
        cc = grid["cell_centers"]
        outdoor = layout.outdoor_mask(scene, cc[..., :2].reshape(-1, 2)).reshape(cc.shape[:2])
        if outdoor_cache is not None:
            outdoor_cache[key] = outdoor
    m = {**grid, "path_gain": best_pg[None], "rx_power_dbm": best_pw, "serving_tx": serv_tx,
         "serving_beam": serv_b, "outdoor": outdoor}
    timings["radio_map_s"] = time.perf_counter() - t0

    np.savez_compressed(band_dir / "paths.npz", **p, serving_tx=s_tx, serving_beam=s_b,
                        rx_names=np.array([r.name for r in cfg.receivers]),
                        rx_positions=np.array([r.position for r in cfg.receivers], dtype=float),
                        tx_names=np.array([t.name for t in cfg.transmitters]),
                        tx_positions=np.array([cfg.transmitters[t].position for t in s_tx], dtype=float),
                        frequency_hz=ghz * 1e9)
    np.savez_compressed(band_dir / "radio_map.npz", **m, frequency_hz=ghz * 1e9,
                        bandwidth_hz=bw if bw is not None else np.nan)

    # Sionna's 3-D renders are skipped for batched runs (see is_batched): its radio-map figures can show
    # one solver result (= one batch) only, and over a 2 km scene the path picture alone took ~9 min per band. The
    # merged maps are in the comparison figures and in the viewer, which also draws the paths.
    if render and not batched:
        t0 = time.perf_counter()
        # The path picture: Mitsuba builds a tube per path segment on the CPU, so thousands of depth-10 paths make
        # it slow and unreadable. Draw the dominant paths (<= RENDER_PATH_DEPTH interactions) from a separate,
        # cheap solve; all metrics above use the full-depth paths.
        vis = paths
        if cfg.paths.max_depth > RENDER_PATH_DEPTH:
            vcfg = dataclasses.replace(cfg, paths=dataclasses.replace(cfg.paths, max_depth=RENDER_PATH_DEPTH,
                                                                        samples_per_src=1_000_000))
            vis = compute_paths(scene, vcfg)
        render_figures(scene, cfg, first_rm, vis, band_dir, ghz, len(weights))
        timings["render_s"] = time.perf_counter() - t0

    rows = []
    for ri, r in enumerate(cfg.receivers):
        t = cfg.transmitters[s_tx[ri]]
        lm = metrics.link_metrics(p["a"], p["tau"], p["valid"], p["interactions"], ri, 0, t.power_dbm)
        # Signal level at the receiver from the (converged, diffuse-inclusive) radio map: mean of the 3 x 3 cells
        # around the RX (6 m x 6 m at 2 m cells), in linear power. This is the headline number; the path-based
        # totals (specular + refraction + diffraction paths) are kept for comparison and delay statistics.
        map_pg, map_pw = metrics.value_at(m, r.position)
        lm["paths_total_gain_db"] = lm.pop("total_path_gain_db")
        lm["paths_rx_power_dbm"] = lm.pop("rx_power_dbm")
        lm["total_path_gain_db"] = map_pg
        lm["rx_power_dbm"] = map_pw
        lm.update(metrics.snr_and_capacity(map_pw, bw, cfg.noise_figure_db))
        rows.append({"band_ghz": ghz, "rx": r.name, "expected": r.expect or "", "serving_tx": t.name,
                     "serving_beam": int(s_b[ri]), **lm})
    stats = {"band_ghz": ghz, **metrics.radio_map_stats(best_pg, cfg.radio_map.coverage_threshold_db,
                                                          max(powers), bw, cfg.noise_figure_db, outdoor)}
    return {"paths": p, "rm": m, "rows": rows, "stats": stats, "timings": timings,
            "extrapolated_materials": extrapolated}


def _scene_meta(cfg: ExperimentConfig) -> dict | None:
    if cfg.scene.startswith("builtin:"):
        return None
    f = Path(cfg.scene).parent / "scene_meta.json"
    return json.loads(f.read_text()) if f.is_file() else None


def run_experiment(cfg: ExperimentConfig, out_root: str | Path = "outputs", quick: bool = False,
                   render: bool = True, run_id: str | None = None, mobility: bool = True,
                   volume: bool = True) -> Path:
    info = backend.info()
    if quick:
        cfg.apply_quick_profile()
    run_id = run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    exp_dir = Path(out_root) / cfg.name
    run_dir = exp_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    print(info.summary())
    print(f"Run directory  : {run_dir}")

    t_start = time.perf_counter()
    scene = build_scene(cfg)
    results, rows, stats, warnings = {}, [], [], []
    outdoor_cache: dict = {}
    for ghz in cfg.bands_ghz:
        print(f"\n=== {band_label(ghz)} ===")
        res = run_band(scene, cfg, ghz, run_dir / f"band_{band_slug(ghz)}", render=render, outdoor_cache=outdoor_cache)
        results[ghz] = res
        rows += res["rows"]
        stats.append(res["stats"])
        if res["extrapolated_materials"]:
            warnings.append(f"{band_label(ghz)}: ITU material properties EXTRAPOLATED for "
                            + ", ".join(res["extrapolated_materials"]))
        print("  timings: " + ", ".join(f"{k}={v:.2f}" for k, v in res["timings"].items()))

    if render:
        comparison_figures(cfg, results, run_dir)
        band_summary_figure(cfg, rows, stats, run_dir)

    volume_info = None
    if volume and cfg.volume is not None:
        from .volume import run_volumes
        print("\n=== 3-D signal volume ===")
        volume_info = run_volumes(scene, cfg, run_dir)

    mobility_info = None
    if mobility and cfg.mobility is not None:
        if not cfg.antennas.is_single_antenna:
            warnings.append("[mobility] skipped: only supported with a single-antenna, single-beam TX")
        else:
            from .mobility import run_mobility
            print("\n=== mobility (moving TX) ===")
            mobility_info = run_mobility(scene, cfg, run_dir)

    cols = ["band_ghz", "rx", "expected", "serving_tx", "los", "num_paths", "total_path_gain_db", "rx_power_dbm",
            "rms_delay_spread_ns"]
    if cfg.bandwidth_mhz:
        cols += ["snr_db", "shannon_capacity_mbps"]
    import pandas as pd
    pd.DataFrame(rows).to_csv(run_dir / "metrics.csv", index=False)
    pd.DataFrame(stats).to_csv(run_dir / "radio_map_stats.csv", index=False)
    table = _markdown_table(rows, cols)
    scols = [c for c in stats[0].keys() if c not in ("cells_total",)]
    stable = _markdown_table(stats, scols)
    (run_dir / "metrics.md").write_text(f"# {cfg.name}\n\n## Links\n\n{table}\n## Radio maps (outdoor cells)\n\n{stable}")
    print("\n" + table + "\n" + stable)

    manifest = {
        "experiment": cfg.name,
        "description": cfg.description,
        "run_id": run_id,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scene": cfg.scene,
        "scene_meta": _scene_meta(cfg),
        "georeferenced": cfg.geo_origin is not None,
        "geo_origin": dataclasses.asdict(cfg.geo_origin) if cfg.geo_origin else None,
        "bands_ghz": cfg.bands_ghz,
        "quick_profile": quick,
        "backend": info.as_dict(),
        "git_commit": _git_commit(),
        "config": dataclasses.asdict(cfg),
        "timings_s": {band_slug(g): r["timings"] for g, r in results.items()} | {
            "total": time.perf_counter() - t_start},
        "mobility": mobility_info,
        "volume": volume_info,
        "warnings": warnings,
        "files": sorted(str(p.relative_to(run_dir)) for p in run_dir.rglob("*") if p.is_file()),
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str))

    latest = exp_dir / "latest"
    if latest.is_symlink() or latest.exists():
        latest.unlink()
    os.symlink(run_id, latest)
    print(f"\nDone in {manifest['timings_s']['total']:.1f} s -> {run_dir}")
    for w in warnings:
        print("WARNING:", w)
    return run_dir
