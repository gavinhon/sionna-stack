"""Local scene coordinates <-> WGS84, used by the scene generator and the geo exporter.

Convention for georeferenced scenes (written by scripts/make_osm_scene.py into scene_meta.json):
    local frame = East-North-Up (ENU) tangent frame at the origin (lat0, lon0, h0)
    x = east [m], y = north [m], z = up [m];  local (0, 0, 0) == (lat0, lon0, h0)
    h0 = WGS84 *ellipsoidal* height of local z = 0 (the scene's ground). The generator writes h0 = 0 because the
    true ground height is not known offline; the viewer adds the terrain height at the origin when real terrain
    is shown (see docs/DEVELOPER_GUIDE.md, "Heights").

The conversion is the exact PROJ pipeline geodetic -> geocentric (ECEF) -> topocentric (ENU), not a flat-earth
approximation, so over a scene of a few hundred metres the numerical error is far below a millimetre. The
real accuracy limits come from the data (OSM footprints ~1-5 m, estimated heights), see README.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

import numpy as np
from pyproj import Transformer


@dataclass(frozen=True)
class GeoFrame:
    lat0: float
    lon0: float
    h0: float = 0.0

    @cached_property
    def _pipeline(self) -> str:
        return ("+proj=pipeline +step +proj=cart +ellps=WGS84 "
                f"+step +proj=topocentric +ellps=WGS84 +lat_0={self.lat0} +lon_0={self.lon0} +h_0={self.h0}")

    @cached_property
    def _fwd(self) -> Transformer:  # (lon, lat, h) -> (x, y, z)
        return Transformer.from_pipeline(self._pipeline)

    def to_local(self, lon, lat, h=0.0):
        """WGS84 degrees / ellipsoidal metres -> local ENU metres."""
        lon, lat, h = np.broadcast_arrays(np.asarray(lon, float), np.asarray(lat, float), np.asarray(h, float))
        x, y, z = self._fwd.transform(lon, lat, h)
        return np.asarray(x), np.asarray(y), np.asarray(z)

    def to_geodetic(self, x, y, z=0.0):
        """Local ENU metres -> (lon, lat, ellipsoidal height). Inverse of to_local."""
        x, y, z = np.broadcast_arrays(np.asarray(x, float), np.asarray(y, float), np.asarray(z, float))
        lon, lat, h = self._fwd.transform(x, y, z, direction="INVERSE")
        return np.asarray(lon), np.asarray(lat), np.asarray(h)

    def to_geodetic_points(self, xyz: np.ndarray) -> np.ndarray:
        """[..., 3] local -> [..., 3] (lon, lat, h)."""
        xyz = np.asarray(xyz, float)
        lon, lat, h = self.to_geodetic(xyz[..., 0], xyz[..., 1], xyz[..., 2])
        return np.stack([lon, lat, h], axis=-1)

    def rectangle_degrees(self, x_min, y_min, x_max, y_max) -> dict:
        """Lon/lat bounding rectangle of a local axis-aligned rectangle (for image overlays)."""
        lon, lat, _ = self.to_geodetic([x_min, x_max, x_max, x_min], [y_min, y_min, y_max, y_max])
        return {"west": float(lon.min()), "south": float(lat.min()),
                "east": float(lon.max()), "north": float(lat.max())}

    def as_dict(self) -> dict:
        return {"lat": self.lat0, "lon": self.lon0, "height": self.h0,
                "frame": "ENU tangent plane at origin; x=east, y=north, z=up [m]",
                "height_reference": "WGS84 ellipsoidal height of local z=0"}
