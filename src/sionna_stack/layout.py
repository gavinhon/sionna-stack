"""TX/RX layout helpers: the same receiver-selection logic for every scene.

Receiver classes (default): one line-of-sight receiver 60-120 m from the TX, one non-line-of-sight receiver
60-120 m away ("behind a building"), one deeper NLOS receiver 120-200 m away. Candidates are:
  1. on a regular grid at receiver height,
  2. outdoors (a ray cast down from high above hits nothing taller than 2 m),
  3. classified LOS/NLOS with a visibility ray to the TX,
  4. confirmed with the real PathSolver at a reference frequency (>= min_paths paths),
then the candidate closest to the middle of each distance band wins (ties: lowest x, then y).
Deterministic for a given scene + TX, so re-running gives the same layout.
"""

from __future__ import annotations

from dataclasses import dataclass

import mitsuba as mi
import numpy as np

DEFAULT_CLASSES = [
    ("rx_los", "LOS", (60.0, 120.0)),
    ("rx_nlos", "NLOS", (60.0, 120.0)),
    ("rx_nlos_far", "NLOS", (120.0, 200.0)),
]


def _f32(a):
    return np.ascontiguousarray(a, dtype=np.float32)


def outdoor_mask(scene, xy: np.ndarray, top: float = 1000.0, clearance: float = 2.0) -> np.ndarray:
    """True where nothing higher than `clearance` metres stands above the point (x, y)."""
    n = len(xy)
    ray = mi.Ray3f(mi.Point3f(_f32(xy[:, 0]), _f32(xy[:, 1]), _f32(np.full(n, top))),
                   mi.Vector3f(_f32(np.zeros(n)), _f32(np.zeros(n)), _f32(-np.ones(n))))
    si = scene.mi_scene.ray_intersect(ray)
    hit = np.array(si.is_valid()).astype(bool)
    h = top - np.array(si.t)
    return ~hit | (h < clearance)


def los_mask(scene, tx: np.ndarray, pts: np.ndarray) -> np.ndarray:
    d = pts - tx[None, :]
    dist = np.linalg.norm(d, axis=1)
    u = d / dist[:, None]
    n = len(pts)
    ray = mi.Ray3f(mi.Point3f(*[_f32(np.full(n, v)) for v in tx]), mi.Vector3f(_f32(u[:, 0]), _f32(u[:, 1]), _f32(u[:, 2])))
    ray.maxt = mi.Float(_f32(dist - 0.05))
    return ~np.array(scene.mi_scene.ray_test(ray)).astype(bool)


def segment_outdoor(scene, p0, p1, step: float = 1.0) -> bool:
    """True if the straight segment p0->p1 (x, y) stays outdoors (for mobility routes)."""
    p0, p1 = np.asarray(p0[:2], float), np.asarray(p1[:2], float)
    n = max(2, int(np.linalg.norm(p1 - p0) / step) + 1)
    xy = p0[None, :] + np.linspace(0, 1, n)[:, None] * (p1 - p0)[None, :]
    return bool(outdoor_mask(scene, xy).all())


@dataclass
class RxChoice:
    name: str
    expect: str
    position: list[float]
    distance_m: float
    num_paths: int


def suggest_receivers(scene, tx_position, extent: float = 250.0, grid: float = 5.0, rx_height: float = 1.5,
                      classes=DEFAULT_CLASSES, ref_frequency_hz: float = 3.5e9, min_paths: int = 3,
                      max_depth: int = 5, batch_size: int = 16,
                      samples_per_src: int = 200_000) -> list[RxChoice]:
    from sionna.rt import PathSolver, PlanarArray, Receiver, Transmitter

    tx = np.asarray(tx_position, float)
    g = np.arange(-extent, extent + 1e-6, grid)
    X, Y = np.meshgrid(g + tx[0], g + tx[1], indexing="ij")
    xy = np.stack([X.ravel(), Y.ravel()], 1)
    pts = np.concatenate([xy, np.full((len(xy), 1), rx_height)], 1)
    dist = np.linalg.norm(pts - tx[None, :], axis=1)
    keep = outdoor_mask(scene, xy) & (dist > 1.0)
    pts, dist = pts[keep], dist[keep]
    los = los_mask(scene, tx, pts)

    # shortlist per class: up to 40 candidates nearest the band centre
    shortlist = {}
    for name, expect, (lo, hi) in classes:
        m = (dist >= lo) & (dist <= hi) & (los if expect == "LOS" else ~los)
        idx = np.where(m)[0]
        mid = 0.5 * (lo + hi)
        order = np.lexsort((pts[idx, 1], pts[idx, 0], np.abs(dist[idx] - mid)))
        shortlist[name] = idx[order][:40]

    # confirm with the real path solver, in small batches (memory grows with #receivers x samples)
    allidx = np.unique(np.concatenate([v for v in shortlist.values() if len(v)])) if shortlist else np.array([], int)
    counts = {}
    if len(allidx):
        saved_f = float(scene.frequency[0]) if hasattr(scene.frequency, "__len__") else float(scene.frequency)
        scene.frequency = ref_frequency_hz
        scene.tx_array = PlanarArray(num_rows=1, num_cols=1, pattern="iso", polarization="V")
        scene.rx_array = scene.tx_array
        scene.add(Transmitter("_layout_tx", position=mi.Point3f(*map(float, tx))))
        solver = PathSolver(deterministic=True)
        for start in range(0, len(allidx), batch_size):
            batch = allidx[start:start + batch_size]
            names = []
            for k, i in enumerate(batch):
                nm = f"_layout_rx{k}"
                scene.add(Receiver(nm, position=mi.Point3f(*map(float, pts[i]))))
                names.append(nm)
            paths = solver(scene, max_depth=max_depth, diffraction=True, samples_per_src=samples_per_src)
            valid = np.array(paths.valid)[:, 0, :]
            for k, i in enumerate(batch):
                counts[int(i)] = int(valid[k].sum())
            for nm in names:
                scene.remove(nm)
        scene.remove("_layout_tx")
        scene.frequency = saved_f

    out = []
    for name, expect, _ in classes:
        chosen = next((i for i in shortlist[name] if counts.get(int(i), 0) >= min_paths), None)
        if chosen is None:
            raise RuntimeError(f"No candidate for {name} ({expect}); try another TX position or distance band")
        out.append(RxChoice(name, expect, [round(float(v), 2) for v in pts[chosen]], round(float(dist[chosen]), 1),
                            counts[int(chosen)]))
    return out
