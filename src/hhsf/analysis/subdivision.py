"""Whether a homestead piece could be split off each turbine parcel: FR-9 (PART 22).

FR-9 asks for road frontage, buildable area, access feasibility and hydro retention, and a
Subdivision Feasibility Score. Many turbine parcels are far bigger than a homestead, so
the question is whether a piece holding the stretch, a house site and a driveway could be
cut from the turbine parcel alone. For each corridor of a hydro run with a turbine parcel:

- road frontage: public road (TIGER, as for buildability) inside the parcel or within
  `edge_tolerance_ft` of its edge;
- hydro retention: the share of the stretch on the parcel (within the same tolerance);
- the house site: the parcel's buildable ground nearest the turbine end (as for privacy,
  hhsf.analysis.privacy.home_site), else the turbine end;
- access: the turbine end, the house site and some frontage are on one part of the parcel,
  so a wire and a driveway can join them without crossing another parcel; the driveway's
  length is measured straight, to the nearest such frontage (a winding one is longer);
- the piece: strips `strip_width_ft` wide along the stretch, wire and driveway plus a
  `home_lot_acres` lot around the house site, trimmed to the parcel; its acres, rounded up
  to `min_piece_acres`.

The "homestead cut" is possible when all four checks pass. The score is four parts,
each 0-1 times its weight; a part with no data is left out. Not in the overall score
(FR-7 doesn't list it). Settings: config/subdivision.yaml. Saved per corridor in
corridor_subdivision (`hhsf analyze subdivision`); see docs/ASSUMPTIONS.md,
"Subdivision feasibility".
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import shapely
import shapely.ops
import yaml
from pydantic import BaseModel, Field
from shapely.geometry import LineString, MultiPolygon, Point
from shapely.geometry.base import BaseGeometry
from sqlalchemy import Connection, text

from hhsf.analysis import privacy
from hhsf.core import crs, units
from hhsf.core.config import AOI, CONFIG_DIR

M2_PER_ACRE = 1e6 / units.ACRES_PER_SQKM

# Flags, saved with each corridor. Not a filter.
NO_ROAD_FRONTAGE = "no_road_frontage"  # under min_frontage_ft of public road
STRETCH_CROSSES_PARCELS = "stretch_crosses_parcels"  # under min_stretch_share on the parcel
NO_HOUSE_SITE = "no_house_site"  # no buildable ground on the parcel
ACCESS_CROSSES_PARCELS = "access_crosses_parcels"  # the driveway or wire leaves the parcel
LONG_DRIVEWAY = "long_driveway"  # longer than driveway_full_ft
NO_ROADS_LOADED = "no_roads_loaded"  # no public roads loaded around the parcels at all
# Any of these and the piece can't be cut from the turbine parcel alone.
BLOCKERS = {NO_ROAD_FRONTAGE, STRETCH_CROSSES_PARCELS, NO_HOUSE_SITE, ACCESS_CROSSES_PARCELS}


class Weights(BaseModel):
    frontage: float = Field(ge=0)
    hydro_retention: float = Field(ge=0)
    buildable: float = Field(ge=0)
    access: float = Field(ge=0)


class Settings(BaseModel):
    edge_tolerance_ft: float = Field(ge=0)
    min_frontage_ft: float = Field(gt=0)
    min_stretch_share: float = Field(gt=0, le=1)
    home_lot_acres: float = Field(gt=0)
    strip_width_ft: float = Field(gt=0)
    min_piece_acres: float = Field(ge=0)
    driveway_full_ft: float = Field(ge=0)
    driveway_zero_ft: float = Field(gt=0)
    weights: Weights

    @property
    def tolerance_m(self) -> float:
        return self.edge_tolerance_ft / units.FT_PER_M

    @property
    def min_frontage_m(self) -> float:
        return self.min_frontage_ft / units.FT_PER_M


def load_settings(path: Path = CONFIG_DIR / "subdivision.yaml") -> Settings:
    return Settings.model_validate(yaml.safe_load(path.read_text()))


@dataclass(frozen=True)
class Site:
    """One corridor and its turbine parcel (analysis CRS)."""

    corridor_id: int
    stretch: BaseGeometry
    turbine_end: Point
    gislink: str
    parcel: BaseGeometry
    parcel_acres: float
    buildable: BaseGeometry | None  # its buildable ground; None: none, or not computed
    buildable_acres: float | None  # None: not computed


@dataclass(frozen=True)
class CorridorSubdivision:
    corridor_id: int
    gislink: str
    frontage_m: float | None  # None: no public roads loaded near the parcel
    stretch_share: float  # of the stretch on the parcel, 0-1
    home_site_basis: str  # buildable | turbine_end
    home_site: Point  # analysis CRS
    road_point: Point | None  # where the driveway meets the road; None: no frontage
    driveway_m: float | None
    wire_m: float  # turbine end to the house site
    # The turbine end, house site and frontage are on one part of the parcel. None: no
    # frontage.
    access_on_parcel: bool | None
    piece: BaseGeometry  # analysis CRS
    piece_acres: float  # rounded up to min_piece_acres, at most the parcel
    homestead_cut: bool | None  # None: no roads loaded, or buildable ground not computed
    subdivision_score: float | None
    flags: tuple[str, ...]


def ramp(value: float, full: float, zero: float) -> float:
    """1 at `full` or less, falling straight to 0 at `zero`."""
    if value <= full:
        return 1.0
    if value >= zero:
        return 0.0
    return (zero - value) / (zero - full)


def same_part(edge: BaseGeometry, *points: Point) -> BaseGeometry | None:
    """The part of `edge` (the parcel and its tolerance) holding all `points`, so a
    driveway or wire between them can wind across the parcel without leaving it. None:
    they're on separate parts (a parcel split by another one), or one is off it."""
    for part in shapely.get_parts(edge):
        if all(part.covers(p) for p in points):
            return part
    return None


def frontage(parcel_edge: BaseGeometry, roads: shapely.STRtree) -> BaseGeometry:
    """The public road inside `parcel_edge`, overlaps (a road in two county files) merged."""
    idx = roads.query(parcel_edge, predicate="intersects")
    if not len(idx):
        return shapely.LineString()
    return shapely.union_all(shapely.intersection(roads.geometries[idx], parcel_edge))


def score(parts: dict[str, float | None], weights: Weights) -> float | None:
    """0-100 from parts of 0-1; parts with no data are left out and the rest rescaled."""
    w = weights.model_dump()
    known = {k: v for k, v in parts.items() if v is not None and w[k] > 0}
    total = sum(w[k] for k in known)
    if not total:
        return None
    return 100 * sum(w[k] * v for k, v in known.items()) / total


def measure(site: Site, roads: shapely.STRtree | None, s: Settings) -> CorridorSubdivision:
    """One corridor's homestead cut. `roads`: public road lines (analysis CRS)."""
    edge = site.parcel.buffer(s.tolerance_m)
    shapely.prepare(edge)
    share = min(1.0, site.stretch.intersection(edge).length / site.stretch.length)
    home, basis = privacy.home_site(site.turbine_end, site.buildable)
    wire = LineString([site.turbine_end, home])
    lines = [site.stretch, wire]
    frontage_m = road_point = driveway_m = on = None
    part = same_part(edge, home, site.turbine_end)
    if roads is not None:
        road = frontage(edge, roads)
        frontage_m = road.length
        if frontage_m > 0:
            # The nearest frontage the house site can reach without leaving the parcel.
            reachable = None if part is None else road.intersection(part)
            on = reachable is not None and reachable.length > 0
            road_point = shapely.ops.nearest_points(reachable if on else road, home)[0]
            driveway = LineString([home, road_point])
            driveway_m = driveway.length
            lines.append(driveway)

    half = s.strip_width_ft / units.FT_PER_M / 2
    lot = home.buffer(math.sqrt(s.home_lot_acres * M2_PER_ACRE / math.pi))
    piece = shapely.union_all([*(g.buffer(half) for g in lines), lot]).intersection(site.parcel)
    piece_acres = min(site.parcel_acres, max(piece.area / M2_PER_ACRE, s.min_piece_acres))

    flags = []
    if frontage_m is None:
        flags.append(NO_ROADS_LOADED)
    elif frontage_m < s.min_frontage_m:
        flags.append(NO_ROAD_FRONTAGE)
    if share < s.min_stretch_share:
        flags.append(STRETCH_CROSSES_PARCELS)
    if site.buildable_acres is not None and basis != privacy.BUILDABLE:
        flags.append(NO_HOUSE_SITE)
    if on is False:
        flags.append(ACCESS_CROSSES_PARCELS)
    driveway_ft = None if driveway_m is None else units.m_to_ft(driveway_m)
    if driveway_ft is not None and driveway_ft > s.driveway_full_ft:
        flags.append(LONG_DRIVEWAY)

    cut = None
    if frontage_m is not None and site.buildable_acres is not None:
        cut = not BLOCKERS & set(flags)
    parts = {
        "frontage": None if frontage_m is None else min(1.0, frontage_m / s.min_frontage_m),
        "hydro_retention": share,
        "buildable": None
        if site.buildable_acres is None
        else min(1.0, site.buildable_acres / s.home_lot_acres),
        "access": None
        if frontage_m is None
        else 0.0
        if not on
        else ramp(driveway_ft, s.driveway_full_ft, s.driveway_zero_ft),
    }
    return CorridorSubdivision(
        corridor_id=site.corridor_id,
        gislink=site.gislink,
        frontage_m=frontage_m,
        stretch_share=share,
        home_site_basis=basis,
        home_site=home,
        road_point=road_point,
        driveway_m=driveway_m,
        wire_m=wire.length,
        access_on_parcel=on,
        piece=piece,
        piece_acres=piece_acres,
        homestead_cut=cut,
        subdivision_score=score(parts, s.weights),
        flags=tuple(flags),
    )


# --- Database -----------------------------------------------------------------


def load_sites(conn: Connection, hydro_run_id: int) -> list[Site]:
    """Every corridor of a hydro run that has a turbine parcel, with its stretch, turbine
    end and the parcel's buildable ground (analysis CRS)."""
    rows = conn.execute(
        text(
            """
            SELECT c.corridor_id, ST_AsBinary(c.geom),
                   ST_AsBinary(ST_EndPoint(ST_GeometryN(c.geom, ST_NumGeometries(c.geom)))),
                   tp.gislink, ST_AsBinary(p.geom), p.acres, ST_AsBinary(b.geom),
                   b.buildable_acres
            FROM hydro_corridors c
            JOIN corridor_turbine_parcels tp ON tp.corridor_id = c.corridor_id
            JOIN parcels p ON p.gislink = tp.gislink
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
    for corridor_id, stretch, end, gislink, parcel, acres, ground, ground_acres in rows:
        if gislink not in parcels:
            parcels[gislink] = geom(parcel)
            shapely.prepare(parcels[gislink])
        sites.append(
            Site(
                corridor_id,
                geom(stretch),
                geom(end),
                gislink,
                parcels[gislink],
                acres,
                geom(ground),
                ground_acres,
            )
        )
    return sites


def save(conn: Connection, aoi: AOI, results: list[CorridorSubdivision], s: Settings) -> None:
    if not results:
        return
    conn.execute(
        text(
            """
            INSERT INTO corridor_subdivision (corridor_id, aoi_key, gislink, frontage_m,
                stretch_share, home_site_basis, home_site, road_point, driveway_m, wire_m,
                access_on_parcel, piece, piece_acres, homestead_cut, subdivision_score,
                flags, min_frontage_ft, min_stretch_share)
            VALUES (:corridor_id, :aoi_key, :gislink, :frontage_m, :stretch_share,
                :home_site_basis, ST_GeomFromWKB(:home_wkb, 4326),
                ST_GeomFromWKB(:road_wkb, 4326), :driveway_m, :wire_m, :access_on_parcel,
                ST_Multi(ST_GeomFromWKB(:piece_wkb, 4326)), :piece_acres, :homestead_cut,
                :subdivision_score, :flags, :min_frontage_ft, :min_stretch_share)
            ON CONFLICT (corridor_id) DO UPDATE SET
                aoi_key = EXCLUDED.aoi_key, gislink = EXCLUDED.gislink,
                frontage_m = EXCLUDED.frontage_m, stretch_share = EXCLUDED.stretch_share,
                home_site_basis = EXCLUDED.home_site_basis, home_site = EXCLUDED.home_site,
                road_point = EXCLUDED.road_point, driveway_m = EXCLUDED.driveway_m,
                wire_m = EXCLUDED.wire_m, access_on_parcel = EXCLUDED.access_on_parcel,
                piece = EXCLUDED.piece, piece_acres = EXCLUDED.piece_acres,
                homestead_cut = EXCLUDED.homestead_cut,
                subdivision_score = EXCLUDED.subdivision_score, flags = EXCLUDED.flags,
                min_frontage_ft = EXCLUDED.min_frontage_ft,
                min_stretch_share = EXCLUDED.min_stretch_share, computed_at = now()
            """
        ),
        [
            {
                "corridor_id": r.corridor_id,
                "aoi_key": aoi.key,
                "gislink": r.gislink,
                "frontage_m": r.frontage_m,
                "stretch_share": r.stretch_share,
                "home_site_basis": r.home_site_basis,
                "home_wkb": crs.to_storage(r.home_site).wkb,
                "road_wkb": None if r.road_point is None else crs.to_storage(r.road_point).wkb,
                "driveway_m": r.driveway_m,
                "wire_m": r.wire_m,
                "access_on_parcel": r.access_on_parcel,
                "piece_wkb": _multipolygon(crs.to_storage(r.piece)).wkb,
                "piece_acres": r.piece_acres,
                "homestead_cut": r.homestead_cut,
                "subdivision_score": r.subdivision_score,
                "flags": list(r.flags),
                "min_frontage_ft": s.min_frontage_ft,
                "min_stretch_share": s.min_stretch_share,
            }
            for r in results
        ],
    )


def _multipolygon(g: BaseGeometry) -> MultiPolygon:
    """The polygons of `g` (an intersection can leave stray lines and points)."""
    parts = [p for p in shapely.get_parts(g) if p.geom_type in ("Polygon", "MultiPolygon")]
    polygons = [q for p in parts for q in shapely.get_parts(p)]
    return MultiPolygon(polygons)


def run_subdivision(
    conn: Connection,
    aoi: AOI,
    sites: list[Site],
    settings: Settings,
    echo: Callable[[str], None] = print,
) -> list[CorridorSubdivision]:
    """Measure and save the homestead cut for `sites`."""
    if not sites:
        return []
    geoms = np.array([s.parcel for s in sites], dtype=object)
    xmin, ymin, xmax, ymax = shapely.total_bounds(geoms)
    pad = settings.tolerance_m + 1
    reach = crs.to_storage(shapely.box(xmin - pad, ymin - pad, xmax + pad, ymax + pad)).bounds
    roads = privacy.load_public_roads(conn, reach)
    echo(f"  {0 if roads is None else len(roads.geometries):,} public road segments")
    results = [measure(site, roads, settings) for site in sites]
    save(conn, aoi, results, settings)
    return results
