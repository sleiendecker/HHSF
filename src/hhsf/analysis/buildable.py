"""Buildable ground on each corridor's turbine parcel: the first step of FR-4.

For each parcel this lays a 10 m grid over it in the analysis CRS (equal-area meters)
and keeps the cells that are:

1. gently sloped: at most `max_slope_pct`, from the USGS 3DEP 10 m elevation model
   (Horn's method, as GIS slope tools use);
2. outside FEMA's 100-year floodplain (Special Flood Hazard Area);
3. on soils whose septic rating isn't excluded (SSURGO, dominant condition);

then keeps only patches of at least `min_patch_acres` (cells joined edge to edge).
Gentle, dry ground where no soils are loaded (a parcel reaching into a county that
hasn't been ingested) doesn't count either; `no_soil_acres` says how much there was.
The parcel's buildable acres are those patches, and their outline is saved, so the
map can draw them and the distance from the turbine end to the nearest one is
measured when read (view corridor_turbine_buildable). Settings are in
config/buildability.yaml; see docs/ASSUMPTIONS.md, "Buildable ground".

Flagged only: nothing is filtered or re-ranked on it (D-02, D-12).
"""

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
import shapely
import yaml
from pydantic import BaseModel, field_validator
from rasterio import features
from rasterio.transform import Affine, from_origin
from rasterio.warp import Resampling, reproject
from shapely.geometry import MultiPolygon, shape
from shapely.geometry.base import BaseGeometry
from sqlalchemy import Connection, text

from hhsf.core import crs, units
from hhsf.core.config import AOI, CONFIG_DIR

CELL_M = 10.0  # the 3DEP 1/3 arc-second DEM is ~10 m
CELL_ACRES = units.sqkm_to_acres(CELL_M * CELL_M / 1e6)
# The grid reaches this many cells past the parcel, so its edge cells have a slope.
PAD_CELLS = 2
SEPTIC_RATINGS = ("septic_tn", "septic_national")  # soils table columns


class Settings(BaseModel):
    max_slope_pct: float
    septic_rating: str
    septic_excluded: list[str]
    min_patch_acres: float

    @field_validator("septic_rating")
    @classmethod
    def _known_rating(cls, v: str) -> str:
        if v not in SEPTIC_RATINGS:
            raise ValueError(f"septic_rating must be one of {', '.join(SEPTIC_RATINGS)}")
        return v


def load_settings(
    path: Path = CONFIG_DIR / "buildability.yaml", state: str | None = None
) -> Settings:
    """The thresholds, with the `states` entry for `state` (a FIPS code) laid over them."""
    raw = yaml.safe_load(path.read_text())
    overrides = raw.pop("states", None) or {}
    return Settings.model_validate({**raw, **overrides.get(state, {})})


@dataclass(frozen=True)
class ParcelBuildable:
    gislink: str
    parcel_acres: float  # cells whose center is in the parcel
    analyzed_acres: float  # of those, with a slope (inside the elevation model)
    gentle_acres: float
    gentle_dry_acres: float
    septic_ok_acres: float
    no_soil_acres: float  # gentle and dry, but no soil polygon loaded there
    buildable_acres: float  # septic_ok ground in patches of min_patch_acres or more
    patches: int
    geom: MultiPolygon | None  # the patches, in storage CRS; None: none


# --- Raster steps (pure, unit-tested) --------------------------------------------


def horn_slope_pct(elev: np.ndarray, cell_m: float = CELL_M) -> np.ndarray:
    """Slope in percent of each cell, from its 8 neighbors (Horn 1981).

    Edge cells, and cells next to a missing elevation (NaN), get NaN.
    """
    z = np.pad(elev.astype("float64"), 1, constant_values=np.nan)
    a, b, c = z[:-2, :-2], z[:-2, 1:-1], z[:-2, 2:]
    d, f = z[1:-1, :-2], z[1:-1, 2:]
    g, h, i = z[2:, :-2], z[2:, 1:-1], z[2:, 2:]
    dz_dx = ((c + 2 * f + i) - (a + 2 * d + g)) / (8 * cell_m)
    dz_dy = ((g + 2 * h + i) - (a + 2 * b + c)) / (8 * cell_m)
    return 100 * np.hypot(dz_dx, dz_dy)


def grid(
    bounds: tuple[float, float, float, float], cell_m: float = CELL_M
) -> tuple[Affine, int, int]:
    """A grid of `cell_m` cells over analysis-CRS bounds, on whole multiples of the cell
    size (so neighboring parcels share cell edges), padded by PAD_CELLS."""
    xmin, ymin, xmax, ymax = bounds
    x0 = (math.floor(xmin / cell_m) - PAD_CELLS) * cell_m
    y1 = (math.ceil(ymax / cell_m) + PAD_CELLS) * cell_m
    width = math.ceil(xmax / cell_m) + PAD_CELLS - math.floor(xmin / cell_m) + PAD_CELLS
    height = math.ceil(ymax / cell_m) + PAD_CELLS - math.floor(ymin / cell_m) + PAD_CELLS
    return from_origin(x0, y1, cell_m, cell_m), width, height


def burn(geoms: Iterable[BaseGeometry], transform: Affine, shape_hw: tuple[int, int]) -> np.ndarray:
    """True for cells whose center is inside any of `geoms` (analysis CRS)."""
    geoms = [g for g in geoms if not g.is_empty]
    if not geoms:
        return np.zeros(shape_hw, dtype=bool)
    return features.rasterize(
        geoms, out_shape=shape_hw, transform=transform, fill=0, default_value=1, dtype="uint8"
    ).astype(bool)


def patches(mask: np.ndarray, transform: Affine, min_area_m2: float) -> list[shapely.Polygon]:
    """Patches of True cells joined edge to edge, as polygons, keeping those of at least
    `min_area_m2` (analysis CRS)."""
    if not mask.any():
        return []
    out = []
    for geom, _ in features.shapes(
        mask.astype("uint8"), mask=mask, transform=transform, connectivity=4
    ):
        polygon = shape(geom)
        if polygon.area >= min_area_m2 - 1e-6:
            out.append(polygon)
    return out


def classify(
    gislink: str,
    slope: np.ndarray,
    parcel: np.ndarray,
    sfha: np.ndarray,
    soil_ok: np.ndarray,
    transform: Affine,
    settings: Settings,
    soil_mapped: np.ndarray | None = None,
) -> ParcelBuildable:
    """Buildable ground from the grids (slope in percent; the rest True/False).

    `soil_mapped`: where any soil polygon is loaded (default: everywhere).
    """
    analyzed = parcel & np.isfinite(slope)
    gentle = analyzed & (slope <= settings.max_slope_pct)
    gentle_dry = gentle & ~sfha
    septic_ok = gentle_dry & soil_ok
    kept = patches(septic_ok, transform, settings.min_patch_acres / CELL_ACRES * CELL_M**2)
    area_m2 = sum(p.area for p in kept)
    geom = MultiPolygon([crs.to_storage(p) for p in kept]) if kept else None
    return ParcelBuildable(
        gislink=gislink,
        parcel_acres=int(parcel.sum()) * CELL_ACRES,
        analyzed_acres=int(analyzed.sum()) * CELL_ACRES,
        gentle_acres=int(gentle.sum()) * CELL_ACRES,
        gentle_dry_acres=int(gentle_dry.sum()) * CELL_ACRES,
        septic_ok_acres=int(septic_ok.sum()) * CELL_ACRES,
        no_soil_acres=(
            0.0 if soil_mapped is None else int((gentle_dry & ~soil_mapped).sum()) * CELL_ACRES
        ),
        buildable_acres=units.sqkm_to_acres(area_m2 / 1e6),
        patches=len(kept),
        geom=geom,
    )


# --- Per parcel ------------------------------------------------------------------


def elevations(
    dem: rasterio.DatasetReader, transform: Affine, shape_hw: tuple[int, int]
) -> np.ndarray:
    """The DEM resampled onto an analysis-CRS grid (bilinear); NaN outside it."""
    out = np.full(shape_hw, np.nan, dtype="float32")
    reproject(
        source=rasterio.band(dem, 1),
        destination=out,
        src_nodata=dem.nodata,
        dst_transform=transform,
        dst_crs=f"EPSG:{crs.ANALYSIS_EPSG}",
        dst_nodata=np.nan,
        resampling=Resampling.bilinear,
    )
    return out


def analyze_parcel(
    gislink: str,
    parcel: BaseGeometry,
    dem: rasterio.DatasetReader,
    sfha: shapely.STRtree,
    soil_ok: shapely.STRtree,
    soil_any: shapely.STRtree,
    settings: Settings,
) -> ParcelBuildable:
    """One parcel. `parcel` and the trees' polygons are in the analysis CRS."""
    transform, width, height = grid(parcel.bounds)
    hw = (height, width)
    box = shapely.box(*parcel.bounds).buffer(PAD_CELLS * CELL_M)
    return classify(
        gislink,
        horn_slope_pct(elevations(dem, transform, hw)),
        burn([parcel], transform, hw),
        burn(sfha.geometries.take(sfha.query(box)), transform, hw),
        burn(soil_ok.geometries.take(soil_ok.query(box)), transform, hw),
        transform,
        settings,
        burn(soil_any.geometries.take(soil_any.query(box)), transform, hw),
    )


# --- Database --------------------------------------------------------------------


def turbine_parcels(conn: Connection, hydro_run_id: int) -> list[tuple[str, BaseGeometry]]:
    """The turbine parcels of a hydro run's corridors (storage CRS), each once."""
    rows = conn.execute(
        text(
            """
            SELECT p.gislink, ST_AsBinary(p.geom)
            FROM parcels p
            WHERE p.gislink IN (
                SELECT tp.gislink FROM hydro_corridors c
                JOIN corridor_turbine_parcels tp ON tp.corridor_id = c.corridor_id
                WHERE c.run_id = :r
            )
            ORDER BY p.gislink
            """
        ),
        {"r": hydro_run_id},
    ).all()
    return [(gislink, shapely.from_wkb(bytes(wkb))) for gislink, wkb in rows]


def by_state(
    conn: Connection, parcels: list[tuple[str, BaseGeometry]]
) -> dict[str, list[tuple[str, BaseGeometry]]]:
    """`parcels` grouped by their own state's FIPS code, for its septic rating (D-20).

    A stretch near the state line can end on a parcel in the next state: the rating is the
    parcel's state's, not the AOI's.
    """
    states = dict(
        conn.execute(
            text("SELECT gislink, left(county_geoid, 2) FROM parcels WHERE gislink = ANY(:g)"),
            {"g": [g for g, _ in parcels]},
        ).all()
    )
    out: dict[str, list[tuple[str, BaseGeometry]]] = {}
    for parcel in parcels:
        out.setdefault(states[parcel[0]], []).append(parcel)
    return dict(sorted(out.items()))


def latest_hydro_run(conn: Connection, aoi_key: str) -> int | None:
    return conn.execute(
        text(
            "SELECT max(run_id) FROM analysis_runs"
            " WHERE analysis = 'hydro' AND status = 'finished' AND aoi_key = :aoi"
        ),
        {"aoi": aoi_key},
    ).scalar_one()


def _polygons(
    conn: Connection, sql: str, bounds: tuple[float, float, float, float]
) -> shapely.STRtree:
    rows = conn.execute(
        text(sql),
        dict(zip(("xmin", "ymin", "xmax", "ymax"), bounds, strict=True)),
    ).scalars()
    return shapely.STRtree([crs.to_analysis(shapely.from_wkb(bytes(wkb))) for wkb in rows])


ENVELOPE = "ST_MakeEnvelope(:xmin, :ymin, :xmax, :ymax, 4326)"


def sfha_polygons(conn: Connection, bounds: tuple[float, float, float, float]) -> shapely.STRtree:
    """FEMA 100-year floodplain pieces over `bounds` (storage CRS), as analysis-CRS polygons."""
    return _polygons(
        conn, f"SELECT ST_AsBinary(geom) FROM flood_zones WHERE sfha AND geom && {ENVELOPE}", bounds
    )


def soil_polygons(
    conn: Connection, bounds: tuple[float, float, float, float], settings: Settings | None = None
) -> shapely.STRtree:
    """Soil pieces over `bounds`: with `settings`, only those whose septic rating isn't
    excluded."""
    where = "true"
    if settings is not None and settings.septic_excluded:
        column = settings.septic_rating  # validated against SEPTIC_RATINGS
        excluded = ", ".join("'" + r.replace("'", "''") + "'" for r in settings.septic_excluded)
        where = f"{column} NOT IN ({excluded})"
    return _polygons(
        conn, f"SELECT ST_AsBinary(geom) FROM soils WHERE {where} AND geom && {ENVELOPE}", bounds
    )


def save(conn: Connection, aoi: AOI, results: list[ParcelBuildable], settings: Settings) -> None:
    conn.execute(
        text(
            """
            INSERT INTO parcel_buildable (gislink, aoi_key, parcel_acres, analyzed_acres,
                gentle_acres, gentle_dry_acres, septic_ok_acres, no_soil_acres, buildable_acres,
                patches, geom, max_slope_pct, septic_rating, septic_excluded, min_patch_acres)
            VALUES (:gislink, :aoi_key, :parcel_acres, :analyzed_acres, :gentle_acres,
                :gentle_dry_acres, :septic_ok_acres, :no_soil_acres, :buildable_acres, :patches,
                ST_GeomFromWKB(:wkb, 4326), :max_slope_pct, :septic_rating,
                :septic_excluded, :min_patch_acres)
            ON CONFLICT (gislink) DO UPDATE SET
                aoi_key = EXCLUDED.aoi_key, parcel_acres = EXCLUDED.parcel_acres,
                analyzed_acres = EXCLUDED.analyzed_acres, gentle_acres = EXCLUDED.gentle_acres,
                gentle_dry_acres = EXCLUDED.gentle_dry_acres,
                septic_ok_acres = EXCLUDED.septic_ok_acres,
                no_soil_acres = EXCLUDED.no_soil_acres,
                buildable_acres = EXCLUDED.buildable_acres, patches = EXCLUDED.patches,
                geom = EXCLUDED.geom, max_slope_pct = EXCLUDED.max_slope_pct,
                septic_rating = EXCLUDED.septic_rating,
                septic_excluded = EXCLUDED.septic_excluded,
                min_patch_acres = EXCLUDED.min_patch_acres, computed_at = now()
            """
        ),
        [
            {
                **{k: v for k, v in r.__dict__.items() if k != "geom"},
                "wkb": None if r.geom is None else r.geom.wkb,
                "aoi_key": aoi.key,
                **settings.model_dump(),
            }
            for r in results
        ],
    )


def bounds_of(geoms: Iterable[BaseGeometry]) -> tuple[float, float, float, float]:
    return shapely.GeometryCollection(list(geoms)).bounds


def run_buildable(
    conn: Connection,
    aoi: AOI,
    parcels: list[tuple[str, BaseGeometry]],
    dem_path: Path,
    settings: Settings,
    echo: Callable[[str], None] = print,
) -> list[ParcelBuildable]:
    """Compute and save buildable ground for `parcels` (storage CRS)."""
    bounds = bounds_of(g for _, g in parcels)
    sfha = sfha_polygons(conn, bounds)
    soil_ok = soil_polygons(conn, bounds, settings)
    soil_any = soil_polygons(conn, bounds)
    echo(
        f"  {len(sfha):,} floodplain pieces, {len(soil_ok):,} of {len(soil_any):,} soil pieces "
        "septic-suitable nearby"
    )
    results = []
    with rasterio.open(dem_path) as dem:
        for n, (gislink, geom) in enumerate(parcels, 1):
            results.append(
                analyze_parcel(
                    gislink, crs.to_analysis(geom), dem, sfha, soil_ok, soil_any, settings
                )
            )
            if n % 500 == 0:
                echo(f"  {n:,} of {len(parcels):,} parcels")
    save(conn, aoi, results, settings)
    return results
