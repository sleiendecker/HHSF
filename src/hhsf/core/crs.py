"""The CRS contract. This is the only module that should name an EPSG code.

- Storage: geometry is stored in WGS84 (EPSG:4326) so it works directly with web maps.
- Analysis: lengths, areas and distances are computed in NAD83 / CONUS Albers
  (EPSG:5070), an equal-area projection in meters that covers the whole region
  without zone boundaries. Degrees are never used for measurement.
"""

from functools import cache

import numpy as np
import shapely
from pyproj import CRS, Transformer
from shapely.geometry.base import BaseGeometry

STORAGE_EPSG = 4326
ANALYSIS_EPSG = 5070


@cache
def _transformer(src: int | str, dst: int) -> Transformer:
    return Transformer.from_crs(src, dst, always_xy=True)


def _project(geom: BaseGeometry, src: int | str, dst: int) -> BaseGeometry:
    t = _transformer(src, dst)
    return shapely.transform(geom, lambda xy: np.column_stack(t.transform(xy[:, 0], xy[:, 1])))


def to_analysis(geom: BaseGeometry) -> BaseGeometry:
    """Project a storage-CRS geometry into the analysis CRS (meters)."""
    return _project(geom, STORAGE_EPSG, ANALYSIS_EPSG)


def to_storage(geom: BaseGeometry) -> BaseGeometry:
    """Project an analysis-CRS geometry back to storage (WGS84)."""
    return _project(geom, ANALYSIS_EPSG, STORAGE_EPSG)


def file_to_storage(geom: BaseGeometry, file_crs: str) -> BaseGeometry:
    """Project a geometry read from a downloaded file into storage (2D WGS84).

    `file_crs` is the file's own CRS as WKT or "EPSG:nnnn" (NHDPlus HR files are
    NAD83, EPSG:4269). Z and M values are dropped.
    """
    horizontal = CRS.from_user_input(file_crs)
    if horizontal.is_compound:  # e.g. "NAD83 + NAVD88 height": keep the NAD83 part
        horizontal = horizontal.sub_crs_list[0]
    return _project(shapely.force_2d(geom), horizontal.to_wkt(), STORAGE_EPSG)


def length_m(geom: BaseGeometry) -> float:
    """Length in meters of a storage-CRS line geometry."""
    return to_analysis(geom).length
