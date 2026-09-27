"""Per-link and per-map metrics computed from Sionna RT outputs (NumPy only).

Definitions (single-antenna TX/RX, ``synthetic_array=True``):

* path gain of path i:        G_i = |a_i|^2   (includes free-space loss, material losses and the
  antenna patterns; with "iso" patterns this is the pure propagation gain)
* strongest path gain:        max_i G_i                         [dB]
* total (wideband) path gain: sum_i G_i  (incoherent, i.e. power sum over paths) [dB]
* received power:             P_tx[dBm] + total path gain       [dBm]
* mean excess delay:          tau_bar = sum G_i tau_i / sum G_i
* RMS delay spread:           sqrt( sum G_i (tau_i - tau_bar)^2 / sum G_i )   [ns]
* LOS:                        a valid path with no interaction at any depth (InteractionType.NONE)
"""

from __future__ import annotations

import numpy as np

# sionna.rt.InteractionType constants (checked against the installed 2.1.0 package)
NONE, SPECULAR, DIFFUSE, REFRACTION, DIFFRACTION = 0, 1, 2, 4, 8


def db(x):
    with np.errstate(divide="ignore"):
        return 10.0 * np.log10(x)


def effective_paths(a: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Combine the TX array elements with a (unit-norm) precoding/beam vector.

    a: [rx, rx_ant, tx, tx_ant, paths, t] (synthetic array: per-element phases included)
    weights: [tx_ant] complex -> returns [rx, rx_ant, tx, 1, paths, t] with a_eff = sum_k w_k a_k,
    the same combination RadioMapSolver uses for `precoding_vec`.
    """
    return np.tensordot(a, weights, axes=([3], [0]))[:, :, :, None, :, :]


def noise_dbm(bandwidth_hz: float, noise_figure_db: float) -> float:
    """Thermal noise kTB at 290 K plus receiver noise figure."""
    return -174.0 + 10.0 * np.log10(bandwidth_hz) + noise_figure_db


def snr_and_capacity(rx_power_dbm: float, bandwidth_hz: float | None, noise_figure_db: float) -> dict:
    """SNR over the full channel bandwidth (no interference) and the Shannon bound B*log2(1+SNR).

    The capacity is an UPPER bound: real 5G reaches roughly 50-75 % of it at high SNR and saturates at
    ~7-8 bit/s/Hz per layer (256-QAM); it ignores interference, MIMO layers and protocol overhead.
    """
    if bandwidth_hz is None or not np.isfinite(rx_power_dbm):
        return {"noise_dbm": np.nan, "snr_db": np.nan, "shannon_capacity_mbps": np.nan}
    n = noise_dbm(bandwidth_hz, noise_figure_db)
    snr = rx_power_dbm - n
    cap = bandwidth_hz * np.log2(1.0 + 10 ** (snr / 10.0)) / 1e6
    return {"noise_dbm": float(n), "snr_db": float(snr), "shannon_capacity_mbps": float(cap)}


def link_metrics(a: np.ndarray, tau: np.ndarray, valid: np.ndarray, interactions: np.ndarray,
                 rx: int, tx: int, tx_power_dbm: float) -> dict:
    """a: [rx, rx_ant, tx, tx_ant(=1 or combined), paths, t]; tau/valid: [rx, tx, paths];
    interactions: [depth, rx, tx, paths]."""
    v = valid[rx, tx].astype(bool)
    g = np.abs(a[rx, 0, tx, 0, :, 0]) ** 2
    g = np.where(v, g, 0.0)
    t = tau[rx, tx]
    inter = interactions[:, rx, tx, :]
    n = int(v.sum())
    out = {
        "num_paths": n,
        "los": bool(np.any(v & np.all(inter == NONE, axis=0))),
        "num_paths_with_diffraction": int(np.sum(v & np.any(inter == DIFFRACTION, axis=0))),
        "num_paths_with_reflection": int(np.sum(v & np.any(inter == SPECULAR, axis=0))),
    }
    if n == 0 or g.sum() == 0:
        out.update(strongest_path_gain_db=np.nan, total_path_gain_db=np.nan, rx_power_dbm=np.nan,
                   first_arrival_ns=np.nan, mean_excess_delay_ns=np.nan, rms_delay_spread_ns=np.nan)
        return out
    p = g / g.sum()
    t0 = t[v].min()
    mean = float(np.sum(p * t))
    rms = float(np.sqrt(np.sum(p * (t - mean) ** 2)))
    total_db = float(db(g.sum()))
    out.update(
        strongest_path_gain_db=float(db(g.max())),
        total_path_gain_db=total_db,
        rx_power_dbm=tx_power_dbm + total_db,
        first_arrival_ns=float(t0 * 1e9),
        mean_excess_delay_ns=float((mean - t0) * 1e9),
        rms_delay_spread_ns=rms * 1e9,
    )
    return out


def value_at(m: dict, position, half: int = 1) -> tuple[float, float]:
    """Radio-map value at a position: linear mean over the (2*half+1)^2 cells around it.

    m: radio-map dict with path_gain [1, y, x] (linear), rx_power_dbm [y, x], cell_centers [y, x, 3].
    Returns (path gain [dB], received power [dBm]); NaN if no ray reached any of those cells.
    """
    cc = m["cell_centers"]
    iy = int(np.argmin(np.abs(cc[:, 0, 1] - position[1])))
    ix = int(np.argmin(np.abs(cc[0, :, 0] - position[0])))
    sl = (slice(max(iy - half, 0), iy + half + 1), slice(max(ix - half, 0), ix + half + 1))
    pg = m["path_gain"][0][sl]
    pw = np.power(10.0, m["rx_power_dbm"][sl] / 10.0)
    if not np.any(pg > 0):
        return float("nan"), float("nan")
    return float(db(pg.mean())), float(db(np.nanmean(np.where(np.isfinite(pw), pw, 0.0))))


def radio_map_stats(path_gain: np.ndarray, threshold_db: float, tx_power_dbm: float | None = None,
                    bandwidth_hz: float | None = None, noise_figure_db: float = 7.0,
                    outdoor: np.ndarray | None = None) -> dict:
    """path_gain: linear [cells_y, cells_x]; zero = no ray reached the cell (or cell inside a building).

    outdoor: optional boolean mask of cells that are outdoors; percentages then refer to OUTDOOR cells
    (more meaningful than "all cells", which include building interiors that a 1.5 m plane cuts through).
    """
    pg_db = db(path_gain)
    reached = path_gain > 0
    base = outdoor if outdoor is not None else np.ones_like(reached)
    n = max(int(base.sum()), 1)
    out = {
        "cells_total": int(path_gain.size),
        "cells_outdoor": int(base.sum()),
        "cells_reached_pct": float(100 * (reached & base).sum() / n),
        f"coverage_pct_above_{threshold_db:g}dB": float(100 * ((pg_db > threshold_db) & base).sum() / n),
        "median_path_gain_db_reached": float(np.median(pg_db[reached & base])) if (reached & base).any() else np.nan,
        "p10_path_gain_db_reached": float(np.percentile(pg_db[reached & base], 10)) if (reached & base).any() else np.nan,
    }
    if tx_power_dbm is not None and bandwidth_hz is not None:
        rxp = tx_power_dbm + pg_db
        snr = rxp - noise_dbm(bandwidth_hz, noise_figure_db)
        with np.errstate(invalid="ignore"):
            out["median_rx_power_dbm_reached"] = float(np.median(rxp[reached & base])) if (reached & base).any() else np.nan
            out["coverage_pct_snr_above_0dB"] = float(100 * ((snr > 0) & base).sum() / n)
            out["coverage_pct_snr_above_10dB"] = float(100 * ((snr > 10) & base).sum() / n)
    return out
