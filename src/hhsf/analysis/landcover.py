"""What each corridor's turbine parcel is now: open field, forest or built up (PART 20).

The farmland step (hhsf.analysis.farmland) says what the soils could do; this says
what's on them, from USGS NLCD 2021 land cover and tree canopy cover (30 m cells,
hhsf.ingest.landcover). For each turbine parcel, counting the cells whose center is
inside it:

- open land: grassland, pasture/hay and cultivated crops (NLCD 71, 81, 82), ground
  that could be grazed, hayed or planted without clearing trees first;
- forest (41-43), developed land (21-24), and the rest (water, wetlands, barren, shrub);
- open farmland: open land on prime farmland (or farmland of statewide importance)
  soils, and open land on cropland soils (capability class 1 to `max_capability_class`,
  config/farmland.yaml): good soil that's already cleared;
- mean tree canopy cover, in percent.

It's raster work, so it's saved per parcel in parcel_landcover (`hhsf analyze
landcover`); see docs/ASSUMPTIONS.md, "Land cover". Not a filter.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
import shapely
from rasterio.windows import Window
from shapely.geometry.base import BaseGeometry
from sqlalchemy import Connection, text

from hhsf.analysis import buildable, farmland
from hhsf.core import crs, units
from hhsf.core.config import AOI

CELL_ACRES = units.sqkm_to_acres(30 * 30 / 1e6)
# NLCD land cover classes (https://www.mrlc.gov/data/legends/national-land-cover-database-class-legend-and-description)
OPEN = (71, 81, 82)  # grassland/herbaceous, pasture/hay, cultivated crops
PASTURE_HAY, CROPS = 81, 82
FOREST = (41, 42, 43)  # deciduous, evergreen, mixed
DEVELOPED = (21, 22, 23, 24)  # open space (lawns, roadsides), low, medium, high intensity
NO_CANOPY_DATA = 100  # tree canopy cover is 0-100; 254 and 255 mean no data


@dataclass(frozen=True)
class ParcelLandcover:
    gislink: str
    parcel_acres: float  # cells whose center is in the parcel
    open_acres: float
    pasture_hay_acres: float
    crop_acres: float
    forest_acres: float
    developed_acres: float
    open_prime_acres: float  # open land on prime (or statewide) farmland soils
    open_cropland_acres: float  # open land on capability class 1-max soils
    canopy_pct: float | None  # mean tree canopy cover; None: no canopy data there


def summarize(
    gislink: str,
    land_cover: np.ndarray,
    canopy: np.ndarray,
    parcel: np.ndarray,
    prime: np.ndarray,
    cropland: np.ndarray,
) -> ParcelLandcover:
    """One parcel's land cover from aligned grids (`parcel`, `prime` and `cropland`
    True/False; `land_cover` NLCD classes; `canopy` percent)."""

    def acres(mask: np.ndarray) -> float:
        return int((parcel & mask).sum()) * CELL_ACRES

    open_land = np.isin(land_cover, OPEN)
    tree = canopy[parcel & (canopy <= NO_CANOPY_DATA)]
    return ParcelLandcover(
        gislink=gislink,
        parcel_acres=int(parcel.sum()) * CELL_ACRES,
        open_acres=acres(open_land),
        pasture_hay_acres=acres(land_cover == PASTURE_HAY),
        crop_acres=acres(land_cover == CROPS),
        forest_acres=acres(np.isin(land_cover, FOREST)),
        developed_acres=acres(np.isin(land_cover, DEVELOPED)),
        open_prime_acres=acres(open_land & prime),
        open_cropland_acres=acres(open_land & cropland),
        canopy_pct=float(tree.mean()) if tree.size else None,
    )


def window(ds: rasterio.DatasetReader, bounds: tuple[float, float, float, float]) -> Window:
    """The whole cells of `ds` covering `bounds` (its CRS), one cell of margin."""
    t = ds.transform
    col0 = math.floor((bounds[0] - t.c) / t.a) - 1
    col1 = math.ceil((bounds[2] - t.c) / t.a) + 1
    row0 = math.floor((t.f - bounds[3]) / -t.e) - 1
    row1 = math.ceil((t.f - bounds[1]) / -t.e) + 1
    return Window(col0, row0, col1 - col0, row1 - row0)


def analyze_parcel(
    gislink: str,
    parcel: BaseGeometry,
    land_cover: rasterio.DatasetReader,
    canopy: rasterio.DatasetReader,
    prime: shapely.STRtree,
    cropland: shapely.STRtree,
) -> ParcelLandcover:
    """One parcel. `parcel` and the trees' polygons are in the analysis CRS."""
    win = window(land_cover, parcel.bounds)  # the two files share one grid (run_landcover)
    lc = land_cover.read(1, window=win, boundless=True, fill_value=0)
    tcc = canopy.read(1, window=win, boundless=True, fill_value=255)
    transform = land_cover.window_transform(win)
    hw = lc.shape
    box = shapely.box(*parcel.bounds).buffer(60)
    return summarize(
        gislink,
        lc,
        tcc,
        buildable.burn([parcel], transform, hw),
        buildable.burn(prime.geometries.take(prime.query(box)), transform, hw),
        buildable.burn(cropland.geometries.take(cropland.query(box)), transform, hw),
    )


def soil_polygons(
    conn: Connection, bounds: tuple[float, float, float, float], settings: farmland.Settings
) -> tuple[shapely.STRtree, shapely.STRtree]:
    """Prime (or statewide) farmland soils and cropland soils over `bounds` (storage CRS)."""
    envelope = buildable.ENVELOPE
    prime = conn.execute(
        text(
            "SELECT ST_AsBinary(geom) FROM soils WHERE (farmland_class = :prime"
            f" OR farmland_class LIKE :statewide) AND geom && {envelope}"
        ),
        {"prime": farmland.PRIME, "statewide": farmland.STATEWIDE, **_box(bounds)},
    ).scalars()
    crop = conn.execute(
        text(
            "SELECT ST_AsBinary(geom) FROM soils WHERE capability_class <= :top"
            f" AND geom && {envelope}"
        ),
        {"top": settings.max_capability_class, **_box(bounds)},
    ).scalars()

    def tree(rows) -> shapely.STRtree:
        return shapely.STRtree([crs.to_analysis(shapely.from_wkb(bytes(wkb))) for wkb in rows])

    return tree(prime), tree(crop)


def _box(bounds: tuple[float, float, float, float]) -> dict[str, float]:
    return dict(zip(("xmin", "ymin", "xmax", "ymax"), bounds, strict=True))


def save(conn: Connection, aoi: AOI, results: list[ParcelLandcover], year: int) -> None:
    conn.execute(
        text(
            """
            INSERT INTO parcel_landcover (gislink, aoi_key, parcel_acres, open_acres,
                pasture_hay_acres, crop_acres, forest_acres, developed_acres,
                open_prime_acres, open_cropland_acres, canopy_pct, nlcd_year)
            VALUES (:gislink, :aoi_key, :parcel_acres, :open_acres, :pasture_hay_acres,
                :crop_acres, :forest_acres, :developed_acres, :open_prime_acres,
                :open_cropland_acres, :canopy_pct, :nlcd_year)
            ON CONFLICT (gislink) DO UPDATE SET
                aoi_key = EXCLUDED.aoi_key, parcel_acres = EXCLUDED.parcel_acres,
                open_acres = EXCLUDED.open_acres,
                pasture_hay_acres = EXCLUDED.pasture_hay_acres,
                crop_acres = EXCLUDED.crop_acres, forest_acres = EXCLUDED.forest_acres,
                developed_acres = EXCLUDED.developed_acres,
                open_prime_acres = EXCLUDED.open_prime_acres,
                open_cropland_acres = EXCLUDED.open_cropland_acres,
                canopy_pct = EXCLUDED.canopy_pct, nlcd_year = EXCLUDED.nlcd_year,
                computed_at = now()
            """
        ),
        [{**r.__dict__, "aoi_key": aoi.key, "nlcd_year": year} for r in results],
    )


def run_landcover(
    conn: Connection,
    aoi: AOI,
    parcels: list[tuple[str, BaseGeometry]],
    rasters: dict[str, Path],
    settings: farmland.Settings,
    year: int,
    echo: Callable[[str], None] = print,
) -> list[ParcelLandcover]:
    """Compute and save land cover for `parcels` (storage CRS)."""
    prime, cropland = soil_polygons(conn, buildable.bounds_of(g for _, g in parcels), settings)
    results = []
    with rasterio.open(rasters["land_cover"]) as lc, rasterio.open(rasters["tree_canopy"]) as tcc:
        if lc.transform != tcc.transform or lc.shape != tcc.shape:
            raise RuntimeError("The land cover and tree canopy files don't line up: refetch them.")
        for n, (gislink, geom) in enumerate(parcels, 1):
            results.append(analyze_parcel(gislink, crs.to_analysis(geom), lc, tcc, prime, cropland))
            if n % 1000 == 0:
                echo(f"  {n:,} of {len(parcels):,} parcels")
    save(conn, aoi, results, year)
    return results
