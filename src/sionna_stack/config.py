"""Experiment configuration (TOML) -> typed dataclasses.

One config file fully describes a run: scene, bands, radio devices, solver settings, radio-map grid,
camera and colour scale. The same code runs any scene (built-in or custom XML) and any list of bands;
only the config changes. See ``configs/munich_frequency.toml`` for an annotated example.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Device:
    name: str
    position: list[float]
    power_dbm: float = 44.0  # transmitters only: conducted power into the antenna (all elements together)
    look_at: list[float] | None = None
    # Transmitters: antenna pointing as a compass bearing (0 = north, 90 = east, clockwise) and a downtilt
    # (positive = pointing below the horizon). Alternative to look_at.
    azimuth_deg: float | None = None
    downtilt_deg: float = 0.0
    expect: str | None = None  # receivers: "LOS" / "NLOS" (a label; the run reports the measured value)

    def aim_point(self) -> list[float] | None:
        """look_at target derived from azimuth/downtilt (100 m ahead), or the explicit look_at."""
        if self.look_at is not None:
            return self.look_at
        if self.azimuth_deg is None:
            return None
        import math
        math_az = math.radians(90.0 - self.azimuth_deg)  # compass bearing -> ENU angle from +x (east)
        tilt = math.radians(self.downtilt_deg)
        d = [math.cos(tilt) * math.cos(math_az), math.cos(tilt) * math.sin(math_az), -math.sin(tilt)]
        return [p + 100.0 * v for p, v in zip(self.position, d)]


@dataclass
class AntennaCfg:
    """Antennas. All transmitters of a scene share one array definition (a Sionna constraint)."""
    tx_pattern: str = "iso"          # "iso", "dipole", "hw_dipole" or "tr38901" (3GPP TR 38.901 sector element)
    tx_rows: int = 1                 # planar array: rows (vertical) x cols (horizontal), spacing in wavelengths
    tx_cols: int = 1
    tx_vertical_spacing: float = 0.5
    tx_horizontal_spacing: float = 0.5
    rx_pattern: str = "iso"
    polarization: str = "V"
    # Beams in the antenna's own frame, [azimuth, elevation] in degrees (azimuth positive = to the left of
    # boresight, elevation positive = up). Each point is evaluated separately; coverage uses the best beam
    # (like 5G beam sweeping). Default: one beam straight ahead.
    beams: list[list[float]] = field(default_factory=lambda: [[0.0, 0.0]])

    @property
    def is_single_antenna(self) -> bool:
        return self.tx_rows * self.tx_cols == 1 and len(self.beams) == 1


@dataclass
class GeoOrigin:
    """WGS84 location of the scene's local (0, 0, 0). Only for georeferenced (custom) scenes."""
    lat: float
    lon: float
    height: float = 0.0  # ellipsoidal height [m] of local z = 0
    crs_note: str = ""


@dataclass
class PathsCfg:
    max_depth: int = 5
    samples_per_src: int = 1_000_000
    max_num_paths_per_src: int = 1_000_000
    los: bool = True
    specular_reflection: bool = True
    diffuse_reflection: bool = False
    refraction: bool = True
    diffraction: bool = True
    edge_diffraction: bool = False
    synthetic_array: bool = True
    deterministic: bool = True
    seed: int = 42


@dataclass
class RadioMapCfg:
    center: list[float] = field(default_factory=lambda: [0.0, 0.0, 1.5])
    size: list[float] = field(default_factory=lambda: [500.0, 500.0])
    cell_size: list[float] = field(default_factory=lambda: [2.0, 2.0])
    orientation: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    samples_per_tx: int = 10_000_000
    max_depth: int = 5
    los: bool = True
    specular_reflection: bool = True
    diffuse_reflection: bool = False
    refraction: bool = True
    diffraction: bool = True
    edge_diffraction: bool = False
    seed: int = 42
    # Shared colour scale for every band. Same limits => maps are directly comparable.
    # display_metric: "path_gain" [dB] (propagation incl. antenna gains) or "rx_power" [dBm] (adds TX power).
    display_metric: str = "path_gain"
    vmin_db: float = -150.0
    vmax_db: float = -60.0
    coverage_threshold_db: float = -130.0   # path-gain threshold for the coverage statistic
    rr_depth: int = -1                      # Russian roulette start depth (-1 = off)


@dataclass
class RenderCfg:
    camera_position: list[float] = field(default_factory=lambda: [0.0, -450.0, 500.0])
    camera_look_at: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    resolution: list[int] = field(default_factory=lambda: [1200, 900])
    num_samples: int = 128
    paths_camera_position: list[float] | None = None
    paths_camera_look_at: list[float] | None = None


@dataclass
class VolumeCfg:
    """3-D signal volume (coverage shells + height slider). Uses the [radio_map] physics settings."""
    height_start: float = 1.5
    height_step: float = 5.0
    height_stop: float = 150.0
    heights: list[float] | None = None      # explicit list overrides start/step/stop
    cell_size: float = 4.0                  # horizontal cell [m]; 4 m cells collect 4x the rays of 2 m cells
    samples_per_tx: int = 300_000_000       # ~ same rays per cell as 1.2e9 on a 2 m grid
    bands_ghz: list[float] | None = None    # default: all bands of the experiment
    # Shell levels in the display metric's unit (dB path gain or dBm received power), strongest last
    shell_levels: list[float] = field(default_factory=lambda: [-120.0, -100.0, -80.0])

    def height_list(self) -> list[float]:
        if self.heights:
            return list(self.heights)
        import numpy as np
        rest = np.arange(max(self.height_step, 5.0), self.height_stop + 1e-6, self.height_step)
        return [self.height_start] + [float(h) for h in rest if h > self.height_start]


@dataclass
class MobilityCfg:
    """A transmitter moving along a polyline; one snapshot (paths + radio map) per step and band."""
    tx: str                                   # name of the transmitter that moves
    waypoints: list[list[float]]              # [[x, y, z], ...] local metres; straight segments between them
    num_steps: int = 60
    speed_mps: float = 10.0                   # sets the timeline and the TX velocity (-> Doppler)
    cell_size: list[float] = field(default_factory=lambda: [4.0, 4.0])  # coarser than the static map
    samples_per_tx: int = 2_000_000
    samples_per_src: int = 500_000


@dataclass
class ExperimentConfig:
    name: str
    description: str
    scene: str  # "builtin:<name>" or a path to a Mitsuba XML file (relative to the config file)
    bands_ghz: list[float]
    transmitters: list[Device]
    receivers: list[Device]
    antennas: AntennaCfg = field(default_factory=AntennaCfg)
    bandwidth_mhz: list[float] | None = None  # per band (same order as bands_ghz); enables SNR / capacity
    noise_figure_db: float = 7.0              # receiver (handset) noise figure
    extrapolate_materials: bool = False
    geo_origin: GeoOrigin | None = None
    paths: PathsCfg = field(default_factory=PathsCfg)
    radio_map: RadioMapCfg = field(default_factory=RadioMapCfg)
    render: RenderCfg = field(default_factory=RenderCfg)
    mobility: MobilityCfg | None = None
    volume: VolumeCfg | None = None
    source_file: str | None = None

    # "quick" profile: same physics, fewer samples/coarser grid; for the CPU backend or smoke runs.
    def apply_quick_profile(self) -> None:
        self.radio_map.samples_per_tx = min(self.radio_map.samples_per_tx, 1_000_000)
        self.radio_map.cell_size = [max(c, 5.0) for c in self.radio_map.cell_size]
        self.paths.samples_per_src = min(self.paths.samples_per_src, 200_000)
        self.render.num_samples = min(self.render.num_samples, 32)
        self.render.resolution = [min(self.render.resolution[0], 800), min(self.render.resolution[1], 600)]
        if self.volume:
            self.volume.samples_per_tx = min(self.volume.samples_per_tx, 30_000_000)
            self.volume.cell_size = max(self.volume.cell_size, 8.0)
        if self.mobility:
            self.mobility.num_steps = min(self.mobility.num_steps, 20)
            self.mobility.samples_per_tx = min(self.mobility.samples_per_tx, 500_000)
            self.mobility.samples_per_src = min(self.mobility.samples_per_src, 100_000)
            self.mobility.cell_size = [max(c, 5.0) for c in self.mobility.cell_size]


def _sub(cls, d: dict | None):
    return cls(**(d or {}))


def load_config(path: str | Path) -> ExperimentConfig:
    path = Path(path).resolve()
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    exp = raw["experiment"]
    scene = raw["scene"]["source"]
    if not scene.startswith("builtin:"):
        scene = str((path.parent / scene).resolve())
    geo = raw["scene"].get("geo_origin")
    if geo is None and not scene.startswith("builtin:"):
        # scenes made by scripts/make_osm_scene.py carry their origin in scene_meta.json
        meta = Path(scene).parent / "scene_meta.json"
        if meta.is_file():
            o = json.loads(meta.read_text())["origin"]
            geo = {"lat": o["lat"], "lon": o["lon"], "height": o["height"], "crs_note": o.get("frame", "")}
    mob = raw.get("mobility")
    bands = raw["bands"]
    bw = bands.get("bandwidth_mhz")
    if bw is not None and len(bw) != len(bands["ghz"]):
        raise ValueError("[bands] bandwidth_mhz must have one entry per band in ghz")
    return ExperimentConfig(
        name=exp["name"],
        description=exp.get("description", ""),
        scene=scene,
        bands_ghz=[float(b) for b in bands["ghz"]],
        bandwidth_mhz=[float(b) for b in bw] if bw else None,
        noise_figure_db=float(bands.get("noise_figure_db", 7.0)),
        extrapolate_materials=bool(bands.get("extrapolate_materials", False)),
        transmitters=[Device(**t) for t in raw["transmitters"]],
        receivers=[Device(**r) for r in raw["receivers"]],
        antennas=_sub(AntennaCfg, raw.get("antennas")),
        geo_origin=GeoOrigin(**geo) if geo else None,
        paths=_sub(PathsCfg, raw.get("paths")),
        radio_map=_sub(RadioMapCfg, raw.get("radio_map")),
        render=_sub(RenderCfg, raw.get("render")),
        mobility=MobilityCfg(**mob) if mob else None,
        volume=VolumeCfg(**raw["volume"]) if raw.get("volume") else None,
        source_file=str(path),
    )


def band_label(ghz: float) -> str:
    """3.5 -> '3.5 GHz', 0.7 -> '700 MHz'; used in plots."""
    return f"{ghz * 1000:g} MHz" if ghz < 1 else f"{ghz:g} GHz"


def band_slug(ghz: float) -> str:
    """3.5 -> '3p5GHz'; used in file names (no dots)."""
    return f"{ghz:g}".replace(".", "p") + "GHz"
