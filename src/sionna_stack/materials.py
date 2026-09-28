"""Frequency validity of ITU radio materials, with an opt-in extrapolation.

Sionna's ``ITURadioMaterial`` implements ITU-R P.2040 (Section 2.1.4):
    eta_r = a * f_GHz**b,   sigma = c * f_GHz**d   [S/m]
with (a, b, c, d) tabulated per material and *per validity range*. Outside every range Sionna raises
``ValueError`` when ``scene.frequency`` is set. Examples: concrete/metal 1-100 GHz, brick 1-40 GHz,
ground types only 1-10 GHz. So 700 MHz fails for every built-in urban scene, and 28 GHz fails for any
scene that uses ITU ground materials.

``enable_extrapolation(scene)`` swaps each ITU material's frequency callback (the public
``RadioMaterial.frequency_update_callback`` property) for one that uses the coefficients of the
*nearest* validity range. That is an extrapolation of the ITU power law, NOT validated data.
Results must be labelled as such (the experiment manifest lists every extrapolated material and band).
"""

from __future__ import annotations

import drjit as dr
from sionna.rt import ITURadioMaterial
from sionna.rt.radio_materials.itu import ITU_MATERIALS_PROPERTIES


def valid_ranges(itu_type: str) -> list[tuple[float, float]]:
    return sorted(ITU_MATERIALS_PROPERTIES[itu_type].keys())


def out_of_range(scene, f_hz: float) -> list[str]:
    """Names of ITU materials in `scene` that have no ITU-R P.2040 data at `f_hz`."""
    f = f_hz / 1e9
    bad = []
    for m in scene.radio_materials.values():
        if isinstance(m, ITURadioMaterial):
            if not any(lo <= f <= hi for lo, hi in valid_ranges(m.itu_type)):
                bad.append(f"{m.name} ({m.itu_type}, valid {valid_ranges(m.itu_type)} GHz)")
    return bad


def _nearest_params(itu_type: str, f_ghz: float):
    props = ITU_MATERIALS_PROPERTIES[itu_type]
    for (lo, hi), p in props.items():
        if lo <= f_ghz <= hi:
            return p
    (lo, hi), p = min(props.items(), key=lambda kv: min(abs(f_ghz - kv[0][0]), abs(f_ghz - kv[0][1])))
    return p


WATER_ID = "rm_water"   # generic radio material written by scripts/make_osm_scene.py for OSM water areas
EPS0 = 8.8541878128e-12


def water_permittivity(f_ghz, t_celsius: float = 29.0):
    """Complex relative permittivity (eps', eps'') of liquid water, ITU-R P.840 (double Debye; valid to 1 THz).

    Works on floats, numpy arrays and Dr.Jit arrays. 29 degC ~ Singapore surface water.
    Examples at 29 degC: 0.9 GHz eps' 76.8, eps'' 3.0; 3.5 GHz 75.0 / 11.5; 28 GHz 31.4 / 34.4.
    """
    th = 300.0 / (273.15 + t_celsius) - 1.0
    e0 = 77.66 + 103.3 * th
    e1, e2 = 5.48, 3.51
    fp = 20.09 - 142.0 * th + 294.0 * th * th     # primary relaxation [GHz]
    fs = 590.0 - 1500.0 * th                      # secondary relaxation [GHz]
    rp, rs = f_ghz / fp, f_ghz / fs
    re = (e0 - e1) / (1 + rp * rp) + (e1 - e2) / (1 + rs * rs) + e2
    im = f_ghz * (e0 - e1) / (fp * (1 + rp * rp)) + f_ghz * (e1 - e2) / (fs * (1 + rs * rs))
    return re, im


def attach_water_model(scene, t_celsius: float = 29.0, ionic_s_per_m: float = 0.01) -> bool:
    """Give the scene's water material (if any) the ITU-R P.840 frequency dependence.

    Sionna's RadioMaterial takes eps' and a conductivity sigma; the dielectric loss eps'' is converted with
    sigma = 2 pi f eps0 eps'' and a small ionic conductivity of fresh water (Marina Reservoir is a freshwater
    reservoir) is added. Returns True if a water material was found."""
    found = False
    for m in scene.radio_materials.values():
        if m.name == WATER_ID:
            def cb(f, t=t_celsius, ion=ionic_s_per_m):
                re, im = water_permittivity(f / 1e9, t)
                return re, 2 * 3.141592653589793 * f * EPS0 * im + ion
            m.frequency_update_callback = cb
            found = True
    return found


def enable_extrapolation(scene) -> None:
    for m in scene.radio_materials.values():
        if isinstance(m, ITURadioMaterial):
            itu_type = m.itu_type

            def cb(f, itu_type=itu_type):
                f_ghz = f / 1e9
                f_scalar = float(f_ghz[0]) if dr.is_array_v(f_ghz) else float(f_ghz)
                a, b, c, d = _nearest_params(itu_type, f_scalar)
                return a * dr.power(f_ghz, b), c * dr.power(f_ghz, d)

            m.frequency_update_callback = cb
