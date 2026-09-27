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
