"""How private each corridor's home site is: FR-6 (PART 21).

FR-6 asks for nearby structures, road visibility and development density. For each
corridor of a hydro run this picks a home site (the turbine parcel's buildable ground
nearest the turbine end, hhsf.analysis.buildable; the turbine end itself where there's
none) and measures around it:

- neighbors: other buildings (Microsoft footprints of `min_footprint_sqft` or more, not
  on the turbine parcel) within 1/4 and 1/2 mile, and the nearest one;
- development density: those buildings per square mile within 1 mile;
- road visibility: how far it is to the nearest public road (TIGER, as for buildability),
  and how many feet of woods (NLCD 2021 tree canopy of `woods_canopy_pct` or more) stand
  on the straight line between, over its first `sightline_max_ft`.

Plus, for information: the buildings on the turbine parcel itself, and the neighbors
within 1/4 mile of the turbine end. Settings: config/privacy.yaml. Saved per corridor in
corridor_privacy (`hhsf analyze privacy`); see docs/ASSUMPTIONS.md, "Privacy".
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
import shapely
import shapely.ops
import yaml
from pydantic import BaseModel, Field
from rasterio.transform import rowcol
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry
from sqlalchemy import Connection, text

from hhsf.analysis import buildable
from hhsf.core import crs, units
from hhsf.core.config import AOI, CONFIG_DIR

M_PER_MI = units.KM_PER_MI * 1000
QUARTER_MILE_M, HALF_MILE_M, MILE_M = M_PER_MI / 4, M_PER_MI / 2, M_PER_MI
SQFT_PER_M2 = units.FT_PER_M**2
SAMPLE_M = 10.0  # sightline sample spacing; canopy cells are 30 m
NO_CANOPY_DATA = 100  # tree canopy cover is 0-100; 254 and 255 mean no data
BUILDABLE, TURBINE_END = "buildable", "turbine_end"

Bounds = tuple[float, float, float, float]


class Settings(BaseModel):
    min_footprint_sqft: float = Field(gt=0)
    woods_canopy_pct: float = Field(gt=0, le=100)
    sightline_max_ft: float = Field(gt=0)
    search_miles: float = Field(ge=1)  # at least the density radius

    @property
    def search_m(self) -> float:
        return self.search_miles * M_PER_MI

    @property
    def sightline_max_m(self) -> float:
        return self.sightline_max_ft / units.FT_PER_M


def load_settings(path: Path = CONFIG_DIR / "privacy.yaml") -> Settings:
    return Settings.model_validate(yaml.safe_load(path.read_text()))


@dataclass(frozen=True)
class Site:
    """What one corridor's privacy is measured from (analysis CRS)."""

    corridor_id: int
    turbine_end: Point
    gislink: str | None  # turbine parcel; None: none found
    parcel: BaseGeometry | None
    buildable: BaseGeometry | None  # its buildable ground; None: none, or not computed


@dataclass(frozen=True)
class CorridorPrivacy:
    corridor_id: int
    gislink: str | None
    home_site_basis: str  # buildable | turbine_end
    home_site: Point  # analysis CRS
    buildings_quarter_mile: int
    buildings_half_mile: int
    buildings_mile: int
    buildings_per_sqmi: float
    nearest_building_m: float | None  # None: none within search_miles
    parcel_buildings: int | None  # on the turbine parcel; None: no parcel
    turbine_buildings_quarter_mile: int
    public_road_m: float | None  # None: no public road loaded nearby
    sightline_m: float | None  # the stretch of the line to the road examined for woods
    road_woods_m: float | None  # None: no road, or no canopy data on the line


def home_site(turbine_end: Point, ground: BaseGeometry | None) -> tuple[Point, str]:
    """Where a house would go: the buildable ground nearest the turbine end (the shortest
    wire, as buildability scores it), else the turbine end."""
    if ground is None or ground.is_empty:
        return turbine_end, TURBINE_END
    return shapely.ops.nearest_points(ground, turbine_end)[0], BUILDABLE


class Buildings:
    """Building footprints (analysis CRS) indexed for distance queries."""

    def __init__(self, footprints: list[BaseGeometry]):
        self.geoms = np.array(footprints, dtype=object)
        self.points = shapely.point_on_surface(self.geoms)  # which parcel a building is on
        self.tree = shapely.STRtree(self.geoms)

    def neighbors(self, at: Point, within_m: float, parcel: BaseGeometry | None) -> np.ndarray:
        """Distances from `at` to each building within `within_m`, except those on `parcel`."""
        idx = self.tree.query(at, predicate="dwithin", distance=within_m)
        if parcel is not None and len(idx):
            idx = idx[~shapely.contains(parcel, self.points[idx])]
        return shapely.distance(at, self.geoms[idx])

    def on(self, parcel: BaseGeometry) -> int:
        idx = self.tree.query(parcel)
        return int(shapely.contains(parcel, self.points[idx]).sum())


def woods_on_line(
    line: shapely.LineString, canopy: np.ndarray, transform, woods_pct: float
) -> float | None:
    """Meters of `line` (analysis CRS) under tree canopy of `woods_pct` or more, sampled
    every SAMPLE_M; samples without canopy data are left out and the rest scaled up to the
    whole line. None: no sample has data."""
    if line.length == 0:
        return 0.0
    n = max(1, math.ceil(line.length / SAMPLE_M))
    points = shapely.line_interpolate_point(line, (np.arange(n) + 0.5) / n, normalized=True)
    xs, ys = shapely.get_x(points), shapely.get_y(points)
    rows, cols = rowcol(transform, xs, ys)
    rows, cols = np.asarray(rows), np.asarray(cols)
    inside = (rows >= 0) & (rows < canopy.shape[0]) & (cols >= 0) & (cols < canopy.shape[1])
    values = np.full(n, 255, dtype=canopy.dtype)
    values[inside] = canopy[rows[inside], cols[inside]]
    known = values <= NO_CANOPY_DATA
    if not known.any():
        return None
    return float((values[known] >= woods_pct).sum() / known.sum() * line.length)


def measure(
    site: Site,
    buildings: Buildings,
    roads: shapely.STRtree | None,
    canopy: tuple[np.ndarray, object] | None,
    settings: Settings,
) -> CorridorPrivacy:
    """One corridor's privacy. `roads`: public road lines (analysis CRS); `canopy`: the
    tree canopy grid and its transform (analysis CRS)."""
    home, basis = home_site(site.turbine_end, site.buildable)
    d = buildings.neighbors(home, settings.search_m, site.parcel)
    mile = int((d <= MILE_M).sum())
    road_m = sightline = woods = None
    if roads is not None and len(roads.geometries):
        road = roads.geometries[roads.nearest(home)]
        target = shapely.ops.nearest_points(home, road)[1]
        road_m = float(home.distance(target))
        sightline = min(road_m, settings.sightline_max_m)
        if canopy is not None:
            line = shapely.LineString([home, target])
            if road_m > sightline:
                line = shapely.LineString([home, line.interpolate(sightline)])
            woods = woods_on_line(line, canopy[0], canopy[1], settings.woods_canopy_pct)
    return CorridorPrivacy(
        corridor_id=site.corridor_id,
        gislink=site.gislink,
        home_site_basis=basis,
        home_site=home,
        buildings_quarter_mile=int((d <= QUARTER_MILE_M).sum()),
        buildings_half_mile=int((d <= HALF_MILE_M).sum()),
        buildings_mile=mile,
        buildings_per_sqmi=mile / math.pi,  # a 1-mile circle is pi square miles
        nearest_building_m=float(d.min()) if len(d) else None,
        parcel_buildings=None if site.parcel is None else buildings.on(site.parcel),
        turbine_buildings_quarter_mile=len(
            buildings.neighbors(site.turbine_end, QUARTER_MILE_M, site.parcel)
        ),
        public_road_m=road_m,
        sightline_m=sightline,
        road_woods_m=woods,
    )


# --- Database -----------------------------------------------------------------


def load_sites(conn: Connection, hydro_run_id: int) -> list[Site]:
    """Every corridor of a hydro run with its turbine end, turbine parcel and that
    parcel's buildable ground (analysis CRS)."""
    rows = conn.execute(
        text(
            """
            SELECT c.corridor_id,
                   ST_AsBinary(ST_EndPoint(ST_GeometryN(c.geom, ST_NumGeometries(c.geom)))),
                   tp.gislink, ST_AsBinary(p.geom), ST_AsBinary(b.geom)
            FROM hydro_corridors c
            LEFT JOIN corridor_turbine_parcels tp ON tp.corridor_id = c.corridor_id
            LEFT JOIN parcels p ON p.gislink = tp.gislink
            LEFT JOIN parcel_buildable b ON b.gislink = tp.gislink
            WHERE c.run_id = :r
            ORDER BY c.corridor_id
            """
        ),
        {"r": hydro_run_id},
    ).all()
    parcels: dict[str, BaseGeometry] = {}

    def geom(wkb) -> BaseGeometry | None:
        return None if wkb is None else crs.to_analysis(shapely.from_wkb(bytes(wkb)))

    sites = []
    for corridor_id, end, gislink, parcel, ground in rows:
        if gislink is not None and gislink not in parcels:
            parcels[gislink] = geom(parcel)
            shapely.prepare(parcels[gislink])
        sites.append(Site(corridor_id, geom(end), gislink, parcels.get(gislink), geom(ground)))
    return sites


def _geoms(conn: Connection, sql: str, params: dict) -> list[BaseGeometry]:
    """Analysis-CRS geometries from a query returning WKB."""
    found = [shapely.from_wkb(bytes(wkb)) for wkb in conn.execute(text(sql), params).scalars()]
    return list(crs.to_analysis(np.array(found, dtype=object))) if found else []


def load_buildings(conn: Connection, bounds: Bounds, settings: Settings) -> Buildings:
    """Buildings of `min_footprint_sqft` or more over `bounds` (storage CRS)."""
    return Buildings(
        _geoms(
            conn,
            "SELECT ST_AsBinary(geom) FROM buildings WHERE area_m2 >= :min_m2"
            f" AND geom && {buildable.ENVELOPE}",
            {"min_m2": settings.min_footprint_sqft / SQFT_PER_M2, **_box(bounds)},
        )
    )


def load_public_roads(conn: Connection, bounds: Bounds) -> shapely.STRtree | None:
    """Public roads a driveway can join (as for corridor_turbine_roads), over `bounds`
    (storage CRS). None: no roads loaded there."""
    lines = _geoms(
        conn,
        "SELECT ST_AsBinary(geom) FROM roads WHERE public AND NOT limited_access"
        f" AND geom && {buildable.ENVELOPE}",
        _box(bounds),
    )
    return shapely.STRtree(lines) if lines else None


def _box(bounds: Bounds) -> dict[str, float]:
    return dict(zip(("xmin", "ymin", "xmax", "ymax"), bounds, strict=True))


def save(conn: Connection, aoi: AOI, results: list[CorridorPrivacy], settings: Settings) -> None:
    if not results:
        return
    conn.execute(
        text(
            """
            INSERT INTO corridor_privacy (corridor_id, aoi_key, gislink, home_site_basis,
                home_site, buildings_quarter_mile, buildings_half_mile, buildings_mile,
                buildings_per_sqmi, nearest_building_m, parcel_buildings,
                turbine_buildings_quarter_mile, public_road_m, sightline_m, road_woods_m,
                min_footprint_sqft, woods_canopy_pct)
            VALUES (:corridor_id, :aoi_key, :gislink, :home_site_basis,
                ST_GeomFromWKB(:wkb, 4326), :buildings_quarter_mile, :buildings_half_mile,
                :buildings_mile, :buildings_per_sqmi, :nearest_building_m, :parcel_buildings,
                :turbine_buildings_quarter_mile, :public_road_m, :sightline_m, :road_woods_m,
                :min_footprint_sqft, :woods_canopy_pct)
            ON CONFLICT (corridor_id) DO UPDATE SET
                aoi_key = EXCLUDED.aoi_key, gislink = EXCLUDED.gislink,
                home_site_basis = EXCLUDED.home_site_basis, home_site = EXCLUDED.home_site,
                buildings_quarter_mile = EXCLUDED.buildings_quarter_mile,
                buildings_half_mile = EXCLUDED.buildings_half_mile,
                buildings_mile = EXCLUDED.buildings_mile,
                buildings_per_sqmi = EXCLUDED.buildings_per_sqmi,
                nearest_building_m = EXCLUDED.nearest_building_m,
                parcel_buildings = EXCLUDED.parcel_buildings,
                turbine_buildings_quarter_mile = EXCLUDED.turbine_buildings_quarter_mile,
                public_road_m = EXCLUDED.public_road_m, sightline_m = EXCLUDED.sightline_m,
                road_woods_m = EXCLUDED.road_woods_m,
                min_footprint_sqft = EXCLUDED.min_footprint_sqft,
                woods_canopy_pct = EXCLUDED.woods_canopy_pct, computed_at = now()
            """
        ),
        [
            {
                **{k: v for k, v in r.__dict__.items() if k != "home_site"},
                "wkb": crs.to_storage(r.home_site).wkb,
                "aoi_key": aoi.key,
                "min_footprint_sqft": settings.min_footprint_sqft,
                "woods_canopy_pct": settings.woods_canopy_pct,
            }
            for r in results
        ],
    )


def needed_bounds(sites: list[Site], pad_m: float) -> Bounds:
    """Every turbine end and turbine parcel, and `pad_m` around them (analysis CRS)."""
    geoms = [g for s in sites for g in (s.turbine_end, s.parcel) if g is not None]
    xmin, ymin, xmax, ymax = shapely.GeometryCollection(geoms).bounds
    return xmin - pad_m, ymin - pad_m, xmax + pad_m, ymax + pad_m


def run_privacy(
    conn: Connection,
    aoi: AOI,
    sites: list[Site],
    canopy_path: Path | None,
    settings: Settings,
    echo: Callable[[str], None] = print,
) -> list[CorridorPrivacy]:
    """Measure and save privacy for `sites`. `canopy_path`: the NLCD tree canopy file
    (analysis CRS) covering them; None: woods aren't measured."""
    reach = crs.to_storage(shapely.box(*needed_bounds(sites, settings.search_m))).bounds
    buildings = load_buildings(conn, reach, settings)
    roads = load_public_roads(conn, reach)
    canopy = None
    if canopy_path is not None:
        with rasterio.open(canopy_path) as r:
            canopy = (r.read(1), r.transform)
    echo(f"  {len(buildings.geoms):,} buildings of {settings.min_footprint_sqft:g} sq ft or more")
    results = [measure(s, buildings, roads, canopy, settings) for s in sites]
    save(conn, aoi, results, settings)
    return results
