"""The steepest stretch of a reach (D-08): where one penstock would get the most drop.

A whole NHDPlus reach is the wrong unit for a homestead system: reaches run from a
few hundred feet to miles, and one penstock covers only part of one. So the
corridor is the stretch of the reach, no longer than the D-08 penstock length,
with the most drop:

1. Sample the 3DEP DEM every `sample_spacing_m` along the reach, upstream to
   downstream (NHDPlus draws flowlines that way).
2. Clean the profile so it never rises going downstream (a "carve": each sample
   is at most the one above it). That removes bumps where a road fill crosses the
   stream or the line strays onto a bank. Then keep it inside NHDPlus's smoothed
   elevations for the reach's two ends, so a stretch never has more drop than the
   whole reach does in NHDPlus.
3. Slide a window of the penstock length down the profile and keep the one with
   the most drop. Ties go to the most downstream window (more water there).
   A reach shorter than the penstock is one stretch, end to end.

The search (sections one and two) is pure numpy so it can be tested on synthetic
profiles; the DEM reading is at the bottom. Explained in docs/ASSUMPTIONS.md.
"""

import math
from dataclasses import dataclass

import numpy as np
import rasterio.transform
import shapely
from rasterio.io import DatasetReader
from rasterio.windows import Window
from shapely.geometry import LineString, MultiLineString
from shapely.geometry.base import BaseGeometry
from shapely.ops import substring

from hhsf.analysis import hydro
from hhsf.core import crs

HEAD_METHOD = "dem_3dep_steepest_stretch"
NO_TERRAIN = "no_terrain_data"  # head_flag: part of the reach is off the DEM or on a gap
NOT_ONE_LINE = "reach_not_one_line"  # head_flag: the flowline has separate pieces


@dataclass(frozen=True)
class Stretch:
    """Where the stretch sits on its reach, in meters from the reach's upstream end."""

    start_m: float  # top of the stretch (where the intake would go)
    end_m: float  # bottom (where the turbine would go)
    drop_m: float  # gross head over the stretch
    reach_length_m: float

    @property
    def length_m(self) -> float:
        return self.end_m - self.start_m

    @property
    def whole_reach(self) -> bool:
        return self.start_m <= 0 and self.end_m >= self.reach_length_m

    # NHDPlus measures are percent along the reach with 0 at the downstream end.
    @property
    def from_measure(self) -> float:
        return 100 * (1 - self.end_m / self.reach_length_m) if self.reach_length_m else 0.0

    @property
    def to_measure(self) -> float:
        return 100 * (1 - self.start_m / self.reach_length_m) if self.reach_length_m else 100.0


@dataclass(frozen=True)
class CorridorHead:
    """The head a corridor uses, how it was found, and (for stretches) where it is."""

    head: hydro.Head
    method: str
    stretch: Stretch | None = None


# --- The search (pure) ------------------------------------------------------------


def sample_distances(length_m: float, spacing_m: float) -> np.ndarray:
    """Evenly spaced distances from 0 to length_m, no more than spacing_m apart."""
    n = max(2, math.ceil(length_m / spacing_m) + 1)
    return np.linspace(0.0, length_m, n)


def clean_profile(
    z: np.ndarray, top_m: float | None = None, bottom_m: float | None = None
) -> np.ndarray:
    """Elevations that never rise downstream, kept within [bottom_m, top_m] when given."""
    z = np.minimum.accumulate(np.asarray(z, dtype=float))
    if top_m is not None or bottom_m is not None:
        z = np.clip(z, bottom_m, top_m)
    return z


def steepest_stretch(d: np.ndarray, z: np.ndarray, window_m: float) -> Stretch:
    """The window of length window_m with the most drop, on a cleaned profile.

    `d` are distances from the upstream end (increasing), `z` the elevations there.
    Windows start at every sample (and one ends exactly at the downstream end);
    the elevation at a window's bottom is interpolated between samples.
    """
    length = float(d[-1])
    if length <= window_m:
        return Stretch(0.0, length, float(z[0] - z[-1]), length)
    starts = np.append(d[d < length - window_m], length - window_m)
    drops = np.interp(starts, d, z) - np.interp(starts + window_m, d, z)
    i = len(drops) - 1 - int(np.argmax(drops[::-1]))  # last of equal drops: most downstream
    return Stretch(float(starts[i]), float(starts[i] + window_m), float(drops[i]), length)


def best_stretch(
    d: np.ndarray,
    z: np.ndarray,
    window_m: float,
    top_m: float | None = None,
    bottom_m: float | None = None,
) -> Stretch:
    """Clean a raw DEM profile, then find its steepest stretch."""
    return steepest_stretch(d, clean_profile(z, top_m, bottom_m), window_m)


# --- Geometry ---------------------------------------------------------------------


def reach_line(geom: BaseGeometry) -> LineString | None:
    """The reach as one analysis-CRS line (meters), or None if it has separate pieces."""
    line = shapely.line_merge(crs.to_analysis(geom))
    return line if line.geom_type == "LineString" else None


def stretch_geometry(geom: BaseGeometry, s: Stretch) -> MultiLineString:
    """The stretch's part of the reach, in storage CRS (the reach itself if it's all of it)."""
    if s.whole_reach:
        return geom if geom.geom_type == "MultiLineString" else MultiLineString([geom])
    line = reach_line(geom)
    if line is None:
        raise ValueError("a stretch needs a single-line reach")
    return MultiLineString([crs.to_storage(substring(line, s.start_m, s.end_m))])


# --- Reading the DEM --------------------------------------------------------------


def sample_profile(
    dem: DatasetReader, line: LineString, spacing_m: float
) -> tuple[np.ndarray, np.ndarray] | None:
    """Distances and DEM elevations (m) along an analysis-CRS line, upstream first.

    Each sample takes the DEM cell it falls in. Only the cells around the line are
    read. None if any sample is off the DEM or on a cell with no data.
    """
    d = sample_distances(line.length, spacing_m)
    xy = shapely.get_coordinates(shapely.line_interpolate_point(line, d))
    x, y = crs.analysis_xy_to_file(xy[:, 0], xy[:, 1], dem.crs.to_wkt())
    rows, cols = (np.asarray(a) for a in rasterio.transform.rowcol(dem.transform, x, y))
    if rows.min() < 0 or cols.min() < 0 or rows.max() >= dem.height or cols.max() >= dem.width:
        return None
    r0, c0 = rows.min(), cols.min()
    window = Window(c0, r0, cols.max() - c0 + 1, rows.max() - r0 + 1)
    z = dem.read(1, window=window)[rows - r0, cols - c0].astype(float)
    if not np.all(np.isfinite(z)) or (dem.nodata is not None and np.any(z == dem.nodata)):
        return None
    return d, z


def corridor_head(
    dem: DatasetReader,
    geom: BaseGeometry,
    max_elev_m: float | None,
    min_elev_m: float | None,
    s: hydro.CorridorSettings,
    net_head_factor: float,
) -> CorridorHead:
    """Head over the reach's steepest stretch, or NULL head with a flag if it can't be found."""
    line = reach_line(geom)
    if line is None:
        return CorridorHead(hydro.Head(None, None, NOT_ONE_LINE), HEAD_METHOD)
    profile = sample_profile(dem, line, s.sample_spacing_m)
    if profile is None:
        return CorridorHead(hydro.Head(None, None, NO_TERRAIN), HEAD_METHOD)
    if max_elev_m is not None and min_elev_m is not None and max_elev_m < min_elev_m:
        max_elev_m = min_elev_m = None  # NHDPlus says it rises downstream: don't trust its bounds
    found = best_stretch(*profile, s.penstock_length_m, max_elev_m, min_elev_m)
    head = hydro.Head(found.drop_m, found.drop_m * net_head_factor)
    return CorridorHead(head, HEAD_METHOD, found)
