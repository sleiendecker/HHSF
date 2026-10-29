"""Exports for a score run: shortlist CSV + GeoJSON, every corridor as CSV, a report.

Pure functions, no database: export_run.py loads the rows and hands them over, so
every column and every line of the report can be unit-tested. Values are in units
people read (ft, W, cfs and gpm, sq mi, mi), rounded to what the estimates can carry.

Owner names are personal information (docs/ASSUMPTIONS.md, "Parcels and owners"):
they go into the CSVs and the report, which stay on this computer, and never into
the GeoJSON, which is meant for other tools and is easy to pass around.
"""

import csv
import io
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from hhsf.analysis import scoring, stretch, subdivision
from hhsf.core import units
from hhsf.ingest.roads import ROAD_LABELS

SHORTLIST_CSV = "shortlist.csv"
SHORTLIST_GEOJSON = "shortlist.geojson"
ALL_CORRIDORS_CSV = "all_corridors.csv"
REPORT_MD = "report.md"

FT_PER_MI = 5280
# The report sits in data/outputs/<aoi>/run_<id>/; links climb back to the repo root.
DOCS = "../../../../docs"
TIER_TITLES = {
    "medium": "Medium confidence (StreamStats Q90)",
    "low": "Low confidence (gauge transfer, regional yield or other fallback flow)",
    scoring.HEAD_ONLY: "Head only (basin too small for a flow estimate)",
    scoring.LOW_FLOW: "Low flow (below the power target on a low-confidence flow)",
}
STATUS_TITLES = {
    scoring.SCORED: "Scored (firm power at or above the target)",
    scoring.HEAD_ONLY: "Head only (no flow estimate)",
    scoring.LOW_FLOW: "Low flow (below the target on a low-confidence flow)",
    scoring.BELOW_POWER_TARGET: "Filtered: firm power below the target",
    scoring.BELOW_MIN_HEAD: "Filtered: head only or low flow, but too little head",
    scoring.NOT_SCORABLE: "Filtered: head or flow missing",
}

SHORTLIST_COLUMNS = [
    "rank",
    "tier",
    "tier_rank",
    "overall_score",
    "buildability_subscore",
    "agriculture_subscore",
    "privacy_subscore",
    "logistics_subscore",
    "hydro_score",
    "power_subscore",
    "head_subscore",
    "net_head_ft",
    "firm_power_w",
    "firm_flow_cfs",
    "firm_flow_gpm",
    "flow_method",
    "flow_confidence",
    "flow_flags",
    "drainage_area_sqmi",
    "corridor_length_ft",
    "reach_length_mi",
    "stream_name",
    "nhdplusid",
    "outlet_lat",
    "outlet_lon",
    "turbine_parcel_id",
    "turbine_parcel_county",
    "turbine_parcel_acres",
    "turbine_parcel_deed_acres",
    "turbine_parcel_distance_ft",
    "turbine_parcel_tpad_url",
    "stretch_parcel_count",
    "stretch_parcel_ids",
    "parcel_flags",
    "turbine_parcel_owner",
    "turbine_parcel_owner2",
    "turbine_parcel_address",
    "stretch_parcel_owners",
    "turbine_flood_zone",
    "turbine_flood_subtype",
    "turbine_in_sfha",
    "stretch_sfha_ft",
    "stretch_flood_zones",
    "flood_flags",
    "turbine_road_ft",
    "turbine_road_type",
    "turbine_road_name",
    "turbine_public_road_ft",
    "drive_minutes",
    "drive_miles",
    "drive_road_point_ft",
    "in_drive_area",
    "access_flags",
    "turbine_parcel_gentle_acres",
    "turbine_parcel_buildable_acres",
    "nearest_buildable_ft",
    "buildable_flags",
    "turbine_parcel_prime_acres",
    "turbine_parcel_prime_if_acres",
    "turbine_parcel_statewide_acres",
    "turbine_parcel_cropland_acres",
    "turbine_parcel_wet_acres",
    "turbine_parcel_pasture_aum",
    "farmland_flags",
    "turbine_parcel_open_acres",
    "turbine_parcel_open_cropland_acres",
    "turbine_parcel_forest_acres",
    "turbine_parcel_developed_acres",
    "turbine_parcel_canopy_pct",
    "landcover_flags",
    "home_site",
    "home_site_lat",
    "home_site_lon",
    "buildings_quarter_mile",
    "buildings_half_mile",
    "buildings_per_sqmi",
    "nearest_building_ft",
    "turbine_parcel_buildings",
    "home_public_road_ft",
    "road_woods_ft",
    "privacy_flags",
    "subdivision_score",
    "homestead_cut",
    "road_frontage_ft",
    "stretch_on_parcel_pct",
    "cut_driveway_ft",
    "cut_wire_ft",
    "homestead_piece_acres",
    "subdivision_flags",
    "explanation",
]
ALL_CORRIDORS_COLUMNS = ["status", *SHORTLIST_COLUMNS]
# Personal information: CSVs and the report only.
OWNER_COLUMNS = {
    "turbine_parcel_owner",
    "turbine_parcel_owner2",
    "turbine_parcel_address",
    "stretch_parcel_owners",
}
GEOJSON_COLUMNS = [c for c in SHORTLIST_COLUMNS if c not in OWNER_COLUMNS]

# parcel_flags values.
NO_PARCEL = "no_parcel_found"  # no parcel within 100 m of the turbine point
OUTSIDE_PARCEL = "turbine_outside_parcel"  # in a gap (road, river bed); nearest parcel shown
UNDER_MIN_ACRES = "parcel_under_min_acres"  # D-03 placeholder; flagged, not filtered

# flood_flags values. Flags only: nothing is filtered or re-ranked on flood zones.
FLOOD_NOT_MAPPED = "flood_zone_not_mapped"  # no FEMA zone at the turbine end
TURBINE_IN_FLOODWAY = "turbine_in_floodway"
TURBINE_IN_SFHA = "turbine_in_sfha"  # the 1% annual chance ("100-year") floodplain
STRETCH_IN_SFHA = "stretch_in_sfha"  # some of the stretch is in it

# access_flags values. Flags only: nothing is filtered or re-ranked on roads or drive time.
OUTSIDE_DRIVE_AREA = "outside_drive_area"  # over the D-02 drive's minutes

# Whether a turbine end is within the drive (SQL over `drive_time_areas a`, given its drive
# `minutes` and turbine end `pt`): its own drive time when it has one, so the flag agrees
# with the minutes shown; the area's outline only when it has none. The outline is drawn
# around the road network, and points near its edge fall on the wrong side of it.
IN_DRIVE_AREA_SQL = """
    CASE WHEN {minutes} IS NOT NULL AND count(a.*) > 0 THEN {minutes} <= max(a.minutes)
         ELSE bool_or(ST_Intersects(a.geom, {pt})) END
"""
NO_DRIVE_TIME = "no_drive_time"  # not computed yet, or Valhalla found no route
ROAD_NOT_PUBLIC = "nearest_road_not_public"  # a 4WD trail or private road is nearer
DRIVE_ENDS_ON_TRACK = "drive_ends_on_track"  # no route to the nearest public road; see below

# buildable_flags values. Flags only: nothing is filtered or re-ranked on buildable ground.
NO_BUILDABLE_GROUND = "no_buildable_ground"  # none on the turbine parcel
BUILDABLE_NOT_COMPUTED = "buildable_not_computed"  # the turbine parcel wasn't analyzed
# Gentle, dry ground on the parcel with no soils loaded (it reaches past the loaded soil
# surveys, usually into a county that hasn't been ingested): buildable acres may be
# understated.
SOILS_NOT_LOADED = "soils_not_loaded"

# farmland_flags values. Flags only: nothing is filtered or re-ranked on farmland.
# No prime farmland, farmland of statewide importance or cropland (capability class 1 to
# the configured maximum) on the turbine parcel.
NO_FARMLAND = "no_farmland"
FARMLAND_NOT_COMPUTED = "farmland_not_computed"  # the turbine parcel wasn't analyzed
# The parcel has at least this many acres with no soils loaded (soils_not_loaded, as for
# buildable ground): its farmland may be understated.
FARMLAND_UNSURVEYED_ACRES = 1.0

# landcover_flags values. Not a filter.
NO_OPEN_LAND = "no_open_land"  # no field, pasture or crops on the turbine parcel (NLCD 2021)
LANDCOVER_NOT_COMPUTED = "landcover_not_computed"  # the turbine parcel wasn't analyzed

# privacy_flags values. Flags only: privacy is a part of the overall score (hydro_v6 on),
# never a filter.
NEIGHBOR_CLOSE = "neighbor_within_660_ft"  # another building within 1/8 mile of the home site
NEIGHBOR_CLOSE_M = 660 / units.FT_PER_M
# A public road within 500 ft of the home site with under 100 ft of woods between.
OPEN_TO_ROAD = "open_to_road"
OPEN_TO_ROAD_M, OPEN_TO_ROAD_WOODS_M = 500 / units.FT_PER_M, 100 / units.FT_PER_M
BUILDINGS_ON_PARCEL = "buildings_on_parcel"  # the turbine parcel already has buildings
PRIVACY_NOT_COMPUTED = "privacy_not_computed"  # `hhsf analyze privacy` hasn't run

# subdivision_flags: hhsf.analysis.subdivision's flags, saved with each corridor, and this.
# Not a filter, and not in the overall score.
SUBDIVISION_NOT_COMPUTED = "subdivision_not_computed"  # `hhsf analyze subdivision` hasn't run


@dataclass(frozen=True)
class Parcel:
    """A parcel a corridor sits on, with its owners (personal information)."""

    parcel_id: str
    county_name: str | None
    acres: float  # measured from the outline
    deed_acres: float | None
    tpad_url: str | None
    owner: str | None = None
    owner2: str | None = None
    property_address: str | None = None


@dataclass(frozen=True)
class FloodZone:
    """A FEMA flood zone (National Flood Hazard Layer)."""

    zone: str  # A, AE, X, ...
    subtype: str | None  # FLOODWAY, 0.2 PCT ANNUAL CHANCE FLOOD HAZARD, ...
    sfha: bool  # Special Flood Hazard Area: the 1% annual chance floodplain
    floodway: bool = False

    @property
    def label(self) -> str:
        """'AE floodway', 'X (0.2% annual chance)', 'X (minimal hazard)', 'A'."""
        return flood_zone_label(self.zone, self.subtype)


@dataclass(frozen=True)
class Road:
    """The nearest TIGER road to a turbine end."""

    road_class: str  # hhsf.ingest.roads.ROAD_CLASSES key: local, 4wd_trail, ...
    name: str | None
    public: bool
    distance_m: float

    @property
    def label(self) -> str:
        return ROAD_LABELS.get(self.road_class, self.road_class)


@dataclass(frozen=True)
class DriveArea:
    """The drive-time area the corridors were checked against (D-02)."""

    origin_name: str
    minutes: int
    built: str  # date
    engine_version: str | None


@dataclass(frozen=True)
class Buildable:
    """Buildable ground on the turbine parcel (hhsf.analysis.buildable)."""

    gentle_acres: float  # slope at most the limit
    buildable_acres: float  # and outside the 100-year floodplain, on septic-suitable soils
    nearest_m: float | None  # turbine end to the nearest buildable ground; None: none
    no_soil_acres: float = 0.0  # gentle and dry, but no soils loaded there


@dataclass(frozen=True)
class BuildableInfo:
    """The settings the buildable ground was computed with (config/buildability.yaml)."""

    max_slope_pct: float
    septic_rating: str  # soils column: septic_tn or septic_national
    septic_excluded: tuple[str, ...]
    min_patch_acres: float
    computed: str  # date of the latest computation


@dataclass(frozen=True)
class Farmland:
    """Farmland on the turbine parcel (hhsf.analysis.farmland), in acres."""

    parcel_acres: float
    soil_acres: float  # of it, with soils loaded
    prime_acres: float  # 'All areas are prime farmland'
    prime_if_acres: float  # prime only if drained, protected from flooding, ...
    statewide_acres: float  # farmland of statewide importance
    cropland_acres: float  # capability class 1 to the maximum
    wet_acres: float  # somewhat poorly drained or wetter
    pasture_aum: float  # animal-unit months a year the soils could carry as pasture


@dataclass(frozen=True)
class Landcover:
    """What the turbine parcel is now (hhsf.analysis.landcover, NLCD 2021), in acres."""

    open_acres: float  # grassland, pasture/hay, crops
    open_cropland_acres: float  # open land on capability class 1-3 soils
    forest_acres: float
    developed_acres: float
    canopy_pct: float | None  # mean tree canopy cover


@dataclass(frozen=True)
class Privacy:
    """Around the corridor's home site (hhsf.analysis.privacy)."""

    home_site: str  # buildable (nearest buildable ground) | turbine_end
    lat: float
    lon: float
    buildings_quarter_mile: int  # other buildings, not on the turbine parcel
    buildings_half_mile: int
    buildings_per_sqmi: float  # within 1 mile
    nearest_building_m: float | None  # None: none within the search radius
    parcel_buildings: int | None  # on the turbine parcel; None: no turbine parcel
    public_road_m: float | None  # None: no roads loaded
    road_woods_m: float | None  # woods on the line toward that road; None: no data


@dataclass(frozen=True)
class PrivacyInfo:
    """The settings privacy was computed with (config/privacy.yaml) and the buildings."""

    min_footprint_sqft: float
    woods_canopy_pct: float
    buildings_version: str | None  # ms_buildings release and download date
    computed: str  # date of the latest computation


@dataclass(frozen=True)
class Subdivision:
    """Whether a homestead piece could be cut from the turbine parcel (FR-9,
    hhsf.analysis.subdivision)."""

    score: float | None  # 0-100; None: no part had data
    homestead_cut: bool | None  # None: no roads loaded, or buildable ground not computed
    frontage_m: float | None  # public road on or along the parcel; None: no roads loaded
    stretch_share: float  # of the stretch on the parcel, 0-1
    driveway_m: float | None  # straight, house site to that road; None: no frontage
    wire_m: float  # straight, turbine end to house site
    piece_acres: float
    flags: tuple[str, ...]


@dataclass(frozen=True)
class SubdivisionInfo:
    """The settings the homestead cuts were computed with (config/subdivision.yaml)."""

    min_frontage_ft: float
    min_stretch_share: float
    computed: str  # date of the latest computation


@dataclass(frozen=True)
class FarmlandInfo:
    """The settings farmland was computed with (config/farmland.yaml)."""

    max_capability_class: int
    wet_drainage: tuple[str, ...]
    computed: str  # date of the latest computation


@dataclass(frozen=True)
class ExportRow:
    """One corridor of a score run, joined to its hydro numbers (storage units)."""

    corridor_id: int
    nhdplusid: int
    stream_name: str | None
    status: str
    tier: str | None
    rank: int | None
    tier_rank: int | None
    hydro_score: float | None
    head_subscore: float | None
    power_subscore: float | None
    net_head_ft: float | None
    firm_power_w: float | None
    firm_flow_cfs: float | None
    flow_method: str | None
    flow_confidence: str
    flow_flags: tuple[str, ...]
    drainage_area_sqkm: float | None
    length_m: float
    outlet_lon: float | None
    outlet_lat: float | None
    explanation: dict
    geometry: dict | None = None  # GeoJSON, EPSG:4326
    reach_length_m: float | None = None  # the whole reach; length_m is the corridor (stretch)
    turbine_parcel: Parcel | None = None  # the parcel at the bottom of the stretch
    turbine_parcel_distance_m: float | None = None  # 0 when the point is inside it
    stretch_parcel_ids: tuple[str, ...] = ()  # every parcel the stretch crosses, most first
    stretch_parcel_owners: tuple[str, ...] = ()
    parcel_flags: tuple[str, ...] = ()
    turbine_flood_zone: FloodZone | None = None  # the zone at the bottom of the stretch
    stretch_flood_zones: tuple[str, ...] = ()  # zone labels along the stretch, longest first
    stretch_sfha_m: float | None = None  # None where no zone is mapped along the stretch
    flood_flags: tuple[str, ...] = ()
    turbine_road: Road | None = None  # None: no roads loaded
    turbine_public_road_m: float | None = None
    drive_minutes: float | None = None  # from the origin; None: not computed or no route
    drive_km: float | None = None
    drive_road_point_m: float | None = None  # turbine end to the road point the drive ends at
    in_drive_area: bool | None = None  # None: no drive-time area built
    access_flags: tuple[str, ...] = ()
    turbine_buildable: Buildable | None = None  # None: no turbine parcel, or not computed
    buildable_flags: tuple[str, ...] = ()
    turbine_farmland: Farmland | None = None  # None: no turbine parcel, or not computed
    farmland_flags: tuple[str, ...] = ()
    turbine_landcover: Landcover | None = None  # None: no turbine parcel, or not computed
    landcover_flags: tuple[str, ...] = ()
    privacy: Privacy | None = None  # None: not computed
    privacy_flags: tuple[str, ...] = ()
    subdivision: Subdivision | None = None  # None: no turbine parcel, or not computed
    subdivision_flags: tuple[str, ...] = ()
    overall_score: float | None = None  # FR-7; only for profiles with an overall section
    buildability_subscore: float | None = None  # None: no data, left out
    agriculture_subscore: float | None = None
    privacy_subscore: float | None = None
    logistics_subscore: float | None = None

    @property
    def ranked(self) -> bool:
        return self.rank is not None


@dataclass(frozen=True)
class RunInfo:
    """Provenance of a score run and the hydro run it scored."""

    aoi_key: str
    score_run_id: int
    hydro_run_id: int
    score_code_version: str
    hydro_code_version: str
    scoring_profile_version: str
    finished_at: str
    data_source_versions: dict
    hydro_params: dict
    score_params: dict
    aoi_name: str | None = None
    exported_with: str | None = None  # code version of the exporter
    parcels_version: str | None = None  # tn_parcels version and download date; None: not loaded
    flood_version: str | None = None  # fema_nfhl version and download date; None: not loaded
    roads_version: str | None = None  # tiger_roads version and download date; None: not loaded
    drive_area: DriveArea | None = None  # None: no drive-time area built
    soils_version: str | None = None  # usda_ssurgo version and download date; None: not loaded
    buildable: BuildableInfo | None = None  # None: buildable ground never computed
    farmland: FarmlandInfo | None = None  # None: farmland never computed
    landcover_year: int | None = None  # NLCD year; None: land cover never computed
    privacy: PrivacyInfo | None = None  # None: privacy never computed
    subdivision: SubdivisionInfo | None = None  # None: homestead cuts never computed


# --- Rows ---------------------------------------------------------------------


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def parcel_flags(
    turbine: Parcel | None, distance_m: float | None, min_acres: float
) -> tuple[str, ...]:
    """What to check about the turbine parcel (only when parcels are loaded)."""
    if turbine is None:
        return (NO_PARCEL,)
    flags = []
    if distance_m:
        flags.append(OUTSIDE_PARCEL)
    if turbine.acres < min_acres:
        flags.append(UNDER_MIN_ACRES)
    return tuple(flags)


def flood_zone_label(zone: str, subtype: str | None) -> str:
    """A zone as people read it on the map and in the report."""
    if subtype is None:
        return zone
    if "FLOODWAY" in subtype:
        return f"{zone} floodway"
    if "0.2 PCT" in subtype:
        return f"{zone} (0.2% annual chance)"
    if "MINIMAL" in subtype:
        return f"{zone} (minimal hazard)"
    return f"{zone} ({subtype.lower()})"


def flood_flags(turbine: FloodZone | None, stretch_sfha_m: float | None) -> tuple[str, ...]:
    """What to check about flooding (only when flood zones are loaded)."""
    flags = []
    if turbine is None:
        flags.append(FLOOD_NOT_MAPPED)
    elif turbine.floodway:
        flags.append(TURBINE_IN_FLOODWAY)
    if turbine is not None and turbine.sfha:
        flags.append(TURBINE_IN_SFHA)
    if stretch_sfha_m:
        flags.append(STRETCH_IN_SFHA)
    return tuple(flags)


def access_flags(
    road: Road | None,
    drive_minutes: float | None,
    in_area: bool | None,
    drive_area: bool,
    drive_any_road: bool = False,
) -> tuple[str, ...]:
    """What to check about getting there. `drive_area`: whether a drive-time area exists.

    `drive_any_road`: the drive ends on a track or driveway, because the nearest public
    road has no route from the origin in OpenStreetMap (a gated or private section).
    """
    flags = []
    if drive_area and in_area is False:
        flags.append(OUTSIDE_DRIVE_AREA)
    if drive_area and drive_minutes is None:
        flags.append(NO_DRIVE_TIME)
    if drive_any_road and drive_minutes is not None:
        flags.append(DRIVE_ENDS_ON_TRACK)
    if road is not None and not road.public:
        flags.append(ROAD_NOT_PUBLIC)
    return tuple(flags)


def buildable_flags(
    turbine: Parcel | None, b: Buildable | None, min_acres: float = 0.5
) -> tuple[str, ...]:
    """What to check about a house site (only when buildable ground has been computed).

    `min_acres`: the smallest patch that counts; less unsurveyed ground than that can't
    hide a building site.
    """
    if turbine is None:
        return ()
    if b is None:
        return (BUILDABLE_NOT_COMPUTED,)
    flags = []
    if b.buildable_acres == 0:
        flags.append(NO_BUILDABLE_GROUND)
    if b.no_soil_acres >= min_acres:
        flags.append(SOILS_NOT_LOADED)
    return tuple(flags)


def farmland_flags(turbine: Parcel | None, f: Farmland | None) -> tuple[str, ...]:
    """What to check about farming the turbine parcel (only when farmland is computed)."""
    if turbine is None:
        return ()
    if f is None:
        return (FARMLAND_NOT_COMPUTED,)
    flags = []
    if f.prime_acres + f.statewide_acres + f.cropland_acres == 0:
        flags.append(NO_FARMLAND)
    if f.parcel_acres - f.soil_acres >= FARMLAND_UNSURVEYED_ACRES:
        flags.append(SOILS_NOT_LOADED)
    return tuple(flags)


def landcover_flags(turbine: Parcel | None, lc: Landcover | None) -> tuple[str, ...]:
    """What to check about the turbine parcel's land cover (only when it's computed)."""
    if turbine is None:
        return ()
    if lc is None:
        return (LANDCOVER_NOT_COMPUTED,)
    return (NO_OPEN_LAND,) if lc.open_acres == 0 else ()


def privacy_flags(p: Privacy | None) -> tuple[str, ...]:
    """What to check about the home site's privacy."""
    if p is None:
        return (PRIVACY_NOT_COMPUTED,)
    flags = []
    if p.nearest_building_m is not None and p.nearest_building_m <= NEIGHBOR_CLOSE_M:
        flags.append(NEIGHBOR_CLOSE)
    if (
        p.public_road_m is not None
        and p.public_road_m <= OPEN_TO_ROAD_M
        and p.road_woods_m is not None
        and p.road_woods_m < OPEN_TO_ROAD_WOODS_M
    ):
        flags.append(OPEN_TO_ROAD)
    if p.parcel_buildings:
        flags.append(BUILDINGS_ON_PARCEL)
    return tuple(flags)


def subdivision_flags(turbine: Parcel | None, sd: Subdivision | None) -> tuple[str, ...]:
    """What stands in the way of cutting a homestead piece from the turbine parcel."""
    if turbine is None:
        return ()
    if sd is None:
        return (SUBDIVISION_NOT_COMPUTED,)
    return sd.flags


def _yes_no(b: bool | None) -> str | None:
    return None if b is None else "yes" if b else "no"


def _ft(m: float | None) -> int | None:
    return None if m is None else round(units.m_to_ft(m))


def record(r: ExportRow) -> dict:
    """One corridor as readable, rounded values (None where there's no estimate)."""
    flow = r.firm_flow_cfs
    t = r.turbine_parcel
    distance = r.turbine_parcel_distance_m
    fz = r.turbine_flood_zone
    sfha_m = r.stretch_sfha_m
    road = r.turbine_road
    b = r.turbine_buildable
    f = r.turbine_farmland
    lc = r.turbine_landcover
    pv = r.privacy
    sd = r.subdivision
    return {
        "status": r.status,
        "rank": r.rank,
        "tier": r.tier,
        "tier_rank": r.tier_rank,
        "overall_score": _round(r.overall_score, 1),
        "buildability_subscore": _round(r.buildability_subscore, 1),
        "agriculture_subscore": _round(r.agriculture_subscore, 1),
        "privacy_subscore": _round(r.privacy_subscore, 1),
        "logistics_subscore": _round(r.logistics_subscore, 1),
        "hydro_score": _round(r.hydro_score, 1),
        "power_subscore": _round(r.power_subscore, 1),
        "head_subscore": _round(r.head_subscore, 1),
        "net_head_ft": _round(r.net_head_ft, 0),
        "firm_power_w": _round(r.firm_power_w, 0),
        "firm_flow_cfs": _round(flow, 3),
        "firm_flow_gpm": None if flow is None else round(units.cfs_to_gpm(flow)),
        "flow_method": r.flow_method,
        "flow_confidence": r.flow_confidence,
        "flow_flags": "; ".join(r.flow_flags),
        "drainage_area_sqmi": (
            None
            if r.drainage_area_sqkm is None
            else round(units.sqkm_to_sqmi(r.drainage_area_sqkm), 2)
        ),
        "corridor_length_ft": round(units.m_to_ft(r.length_m)),
        "reach_length_mi": round(units.m_to_ft(r.reach_length_m or r.length_m) / FT_PER_MI, 2),
        "stream_name": r.stream_name,
        "nhdplusid": r.nhdplusid,
        "outlet_lat": _round(r.outlet_lat, 5),
        "outlet_lon": _round(r.outlet_lon, 5),
        "turbine_parcel_id": t and t.parcel_id,
        "turbine_parcel_county": t and t.county_name,
        "turbine_parcel_acres": t and round(t.acres, 1),
        "turbine_parcel_deed_acres": t and t.deed_acres,
        "turbine_parcel_distance_ft": None if distance is None else round(units.m_to_ft(distance)),
        "turbine_parcel_tpad_url": t and t.tpad_url,
        "stretch_parcel_count": len(r.stretch_parcel_ids) if t or r.stretch_parcel_ids else None,
        "stretch_parcel_ids": "; ".join(r.stretch_parcel_ids),
        "parcel_flags": "; ".join(r.parcel_flags),
        "turbine_parcel_owner": t and t.owner,
        "turbine_parcel_owner2": t and t.owner2,
        "turbine_parcel_address": t and t.property_address,
        "stretch_parcel_owners": "; ".join(r.stretch_parcel_owners),
        "turbine_flood_zone": fz and fz.zone,
        "turbine_flood_subtype": fz and fz.subtype,
        "turbine_in_sfha": None if fz is None else fz.sfha,
        "stretch_sfha_ft": None if sfha_m is None else round(units.m_to_ft(sfha_m)),
        "stretch_flood_zones": "; ".join(r.stretch_flood_zones),
        "flood_flags": "; ".join(r.flood_flags),
        "turbine_road_ft": road and _ft(road.distance_m),
        "turbine_road_type": road and road.label,
        "turbine_road_name": road and road.name,
        "turbine_public_road_ft": _ft(r.turbine_public_road_m),
        "drive_minutes": _round(r.drive_minutes, 0),
        "drive_miles": None if r.drive_km is None else round(units.km_to_mi(r.drive_km), 1),
        "drive_road_point_ft": _ft(r.drive_road_point_m),
        "in_drive_area": r.in_drive_area,
        "access_flags": "; ".join(r.access_flags),
        "turbine_parcel_gentle_acres": b and round(b.gentle_acres, 1),
        "turbine_parcel_buildable_acres": b and round(b.buildable_acres, 1),
        "nearest_buildable_ft": b and _ft(b.nearest_m),
        "buildable_flags": "; ".join(r.buildable_flags),
        "turbine_parcel_prime_acres": f and round(f.prime_acres, 1),
        "turbine_parcel_prime_if_acres": f and round(f.prime_if_acres, 1),
        "turbine_parcel_statewide_acres": f and round(f.statewide_acres, 1),
        "turbine_parcel_cropland_acres": f and round(f.cropland_acres, 1),
        "turbine_parcel_wet_acres": f and round(f.wet_acres, 1),
        "turbine_parcel_pasture_aum": f and round(f.pasture_aum),
        "farmland_flags": "; ".join(r.farmland_flags),
        "turbine_parcel_open_acres": lc and round(lc.open_acres, 1),
        "turbine_parcel_open_cropland_acres": lc and round(lc.open_cropland_acres, 1),
        "turbine_parcel_forest_acres": lc and round(lc.forest_acres, 1),
        "turbine_parcel_developed_acres": lc and round(lc.developed_acres, 1),
        "turbine_parcel_canopy_pct": lc and _round(lc.canopy_pct, 0),
        "landcover_flags": "; ".join(r.landcover_flags),
        "home_site": pv and pv.home_site,
        "home_site_lat": pv and round(pv.lat, 6),
        "home_site_lon": pv and round(pv.lon, 6),
        "buildings_quarter_mile": pv and pv.buildings_quarter_mile,
        "buildings_half_mile": pv and pv.buildings_half_mile,
        "buildings_per_sqmi": pv and round(pv.buildings_per_sqmi, 1),
        "nearest_building_ft": pv and _ft(pv.nearest_building_m),
        "turbine_parcel_buildings": pv and pv.parcel_buildings,
        "home_public_road_ft": pv and _ft(pv.public_road_m),
        "road_woods_ft": pv and _ft(pv.road_woods_m),
        "privacy_flags": "; ".join(r.privacy_flags),
        "subdivision_score": sd and _round(sd.score, 1),
        "homestead_cut": sd and _yes_no(sd.homestead_cut),
        "road_frontage_ft": sd and _ft(sd.frontage_m),
        "stretch_on_parcel_pct": sd and round(100 * sd.stretch_share),
        "cut_driveway_ft": sd and _ft(sd.driveway_m),
        "cut_wire_ft": sd and _ft(sd.wire_m),
        "homestead_piece_acres": sd and round(sd.piece_acres, 1),
        "subdivision_flags": "; ".join(r.subdivision_flags),
        "explanation": r.explanation.get("summary", ""),
    }


def shortlist(rows: list[ExportRow]) -> list[ExportRow]:
    """Ranked corridors only, best first."""
    return sorted((r for r in rows if r.ranked), key=lambda r: r.rank)


def all_corridors(rows: list[ExportRow]) -> list[ExportRow]:
    """Ranked corridors in rank order, then the filtered ones grouped by status."""
    return sorted(
        rows,
        key=lambda r: (not r.ranked, r.rank or 0, r.status, -(r.firm_power_w or 0), r.nhdplusid),
    )


def to_csv(rows: list[ExportRow], columns: list[str]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, columns, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: "" if v is None else v for k, v in record(r).items()})
    return out.getvalue()


def to_geojson(rows: list[ExportRow], info: RunInfo) -> dict:
    return {
        "type": "FeatureCollection",
        "name": f"hhsf_{info.aoi_key}_score_run_{info.score_run_id}",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": [
            {
                "type": "Feature",
                "id": r.corridor_id,
                "geometry": r.geometry,
                "properties": {k: record(r)[k] for k in GEOJSON_COLUMNS},
            }
            for r in rows
        ],
    }


# --- Report -------------------------------------------------------------------


def _fmt(value: float | None, spec: str = ",.0f", unit: str = "") -> str:
    return "–" if value is None else f"{value:{spec}}{unit}"


def assumptions(info: RunInfo) -> list[tuple[str, str]]:
    """The assumptions behind the numbers, read from the two runs' params."""
    h = info.hydro_params.get("hydro", {})
    ss = h.get("streamstats", {})
    gauge = info.hydro_params.get("reference_gauge")
    p = info.score_params.get("scoring_profile", {})
    weights = p.get("weights", {})
    penstock_ft = h.get("corridor", {}).get("penstock_length_ft")
    items = [
        (
            "Corridor: longest penstock (ft, D-08)",
            _fmt(penstock_ft) if stretches(info) else "whole stream reach (no elevation model)",
        ),
        ("System efficiency (water to electricity, D-06)", h.get("system_efficiency")),
        ("Net head as a share of gross head (D-07)", h.get("net_head_factor")),
        ("Share of firm flow that can be diverted (D-05)", h.get("diversion_fraction")),
        ("Firm flow statistic (D-04)", ss.get("statistic_code")),
        ("StreamStats regression regions", ", ".join(ss.get("regression_regions", [])) or None),
        (
            "Smallest basin the low-flow equations apply to (sq mi, D-09)",
            ss.get("min_drainage_area_sqmi"),
        ),
        ("StreamStats used in this run", info.hydro_params.get("use_streamstats")),
    ]
    if gauge:
        items.append(
            (
                "Reference gauge for the drainage-area fallback",
                f"USGS {gauge.get('site_no')}: Q90 {gauge.get('q90_cfs')} cfs over "
                f"{gauge.get('drainage_area_sqmi')} sq mi "
                f"({' to '.join(gauge.get('period', []))})",
            )
        )
    else:
        items.append(("Reference gauge for the drainage-area fallback", "none"))
    if p.get("power_target_w") is not None:
        items.append(
            ("Power target, below which corridors drop (D-01)", f"{p['power_target_w']:,.0f} W")
        )
    if weights:
        items.append(
            ("Score weights (D-11)", f"power {weights.get('power')}, head {weights.get('head')}")
        )
    for name in ("power_subscore", "head_subscore"):
        if name in p:
            items.append(
                (
                    f"{name.replace('_', ' ').capitalize()} breakpoints [input, score]",
                    json.dumps(p[name].get("breakpoints")),
                )
            )
    if "head_only" in p:
        items.append(
            (
                "Smallest net head for a head-only corridor (ft)",
                p["head_only"].get("min_net_head_ft"),
            )
        )
    if "rank_tiers" in p:
        items.append(("Ranking tiers, in order", " > ".join(p["rank_tiers"])))
    if o := p.get("overall"):
        w = o.get("weights", {})
        items.append(
            (
                "Overall score weights (FR-7, D-15; parts with no data left out, rest rescaled)",
                ", ".join(f"{k} {v:g}" for k, v in w.items()),
            )
        )
        items.append(("Within a tier, ranked by", "overall score"))
    return [(label, _text(v)) for label, v in items]


def _text(v: object) -> str:
    if v is None:
        return "–"
    if isinstance(v, bool):
        return "yes" if v else "no"
    return str(v)


def stretches(info: RunInfo) -> bool:
    """True if the hydro run's corridors are steepest stretches (D-08), not whole reaches."""
    return info.hydro_params.get("corridor_method") == stretch.HEAD_METHOD


def _corridor_caveat(info: RunInfo) -> str:
    if not stretches(info):
        return (
            "**Head and power are for the whole stream reach.** This run didn't use the "
            "elevation model, so power assumes a penstock the length of the reach. Long "
            "reaches are overstated; check the drop over a practical pipe length on the visit."
        )
    ft = info.hydro_params.get("hydro", {}).get("corridor", {}).get("penstock_length_ft")
    return (
        f"**Head is over the steepest {_fmt(ft)} ft of each stream reach** (D-08), from the "
        "USGS 10 m elevation model, measured along the stream. Flow is the whole reach's, at "
        "its downstream end, so a stretch near the top of a long reach has a little less. "
        "The coordinates are the bottom of the stretch, where a turbine would go. Confirm "
        "the drop on the visit: 10 m elevations miss small falls and can blur narrow ravines."
    )


def caveats(info: RunInfo) -> list[str]:
    return [
        _corridor_caveat(info),
        "**Gauge-based flows are an upper bound.** Flows scaled from the reference gauge "
        "(`flow_method = gauge_drainage_area_ratio`) came out about 10× StreamStats' Q90 "
        "where both exist, and NHDPlus mean flows (`nhdplus_erom`) by over 100×. Treat "
        "low-tier power as optimistic and measure dry-season flow.",
        "**Small basins use a regional yield.** Basins too small for the low-flow equations "
        "(flag `below_regression_min_drainage_area`) get the median StreamStats Q90 per "
        "square mile of their HUC8 (`flow_method = regional_streamstats_yield`). It's an "
        "extrapolation, so it is low confidence; it misses springs, which a visit can find.",
        "**Low-flow corridors are below the target, but not ruled out.** Their power is under "
        f"the {_target(info)} target on a low-confidence flow, so they stay on the list, "
        "last (D-01). Most are small basins on the regional yield; a "
        "spring-fed one could still work.",
        "**Head-only corridors have no flow estimate.** Their basins are too small for the "
        "low-flow equations and had no regional yield to borrow, so they haven't been checked "
        f"against the {_target(info)} target at all. Their hydro part is head alone, and they "
        "need a flow check (springs, dry-season visit).",
        "StreamStats Q90 itself is a regression estimate (roughly ±60%), and nothing here "
        "covers permits.",
        _parcel_caveat(info),
        _flood_caveat(info),
        _roads_caveat(info),
        _drive_caveat(info),
        _buildable_caveat(info),
        _farmland_caveat(info),
        _landcover_caveat(info),
        _privacy_caveat(info),
        _subdivision_caveat(info),
        *_overall_caveat(info),
    ]


def _parcel_caveat(info: RunInfo) -> str:
    if info.parcels_version is None:
        return (
            "**No parcels are loaded**, so the parcel and owner columns are empty. Run "
            "`make ingest` (or `uv run hhsf ingest parcels`) and export again."
        )
    return (
        "**Parcels and owners come from the Tennessee Comptroller's public parcel layer** "
        f"({info.parcels_version}): for reference only, not legal boundaries or proof of "
        "ownership, and not for commercial use. The turbine parcel is the one under the "
        "bottom of the stretch; streams often are property lines, so the parcel across the "
        "creek may matter as much (see `stretch_parcel_owners`). Owner names are personal "
        "information: keep these files to yourselves. `parcel_under_min_acres` uses the "
        "D-03 placeholder and removes nothing."
    )


def _flood_caveat(info: RunInfo) -> str:
    if info.flood_version is None:
        return (
            "**No flood zones are loaded**, so the flood columns are empty. Run `make ingest` "
            "(or `uv run hhsf ingest flood`) and export again."
        )
    return (
        "**Flood zones come from FEMA's National Flood Hazard Layer** "
        f"({info.flood_version}), the effective flood maps. They are flagged only: nothing is "
        "filtered or re-ranked on them. `turbine_in_sfha` means the bottom of the stretch is "
        'in the 1% annual chance ("100-year") floodplain, zone A or AE, where building needs '
        "a floodplain permit; a floodway (`turbine_in_floodway`) is the strictest part. "
        "**Where the data runs out:** FEMA generally studies streams draining about a square "
        "mile or more, so zone X (minimal hazard) on a small creek usually means the creek "
        "was never studied, not that it can't flood. Zone A is drawn approximately, without "
        "flood heights. Grundy County's map has very few studied streams. "
        "`flood_zone_not_mapped` marks turbine ends with no FEMA zone at all: outside the "
        "loaded counties, a few meters inside a county line (where FEMA's county outline "
        "differs from the Census one), or a county without a digital flood map."
    )


def _roads_caveat(info: RunInfo) -> str:
    if info.roads_version is None:
        return (
            "**No roads are loaded**, so the road columns are empty. Run `make ingest` "
            "(or `uv run hhsf ingest roads`) and export again."
        )
    return (
        f"**Roads come from Census TIGER/Line** ({info.roads_version}). `turbine_road_ft` "
        "is the straight-line distance from the bottom of the stretch to the nearest road of "
        "any kind, interstates aside; `turbine_public_road_ft` to the nearest public one "
        "(`nearest_road_not_public` marks a 4WD trail or private road being nearer). TIGER "
        "misses most driveways, logging roads and farm tracks, and a public road in TIGER "
        "isn't proof of a right of way to the parcel: check access on the visit. Only the "
        "loaded counties' roads count, so near a county line the nearest road can be in the "
        "next county and is then overstated."
    )


def _drive_caveat(info: RunInfo) -> str:
    a = info.drive_area
    if a is None:
        return (
            "**No drive-time area is built**, so the drive columns are empty. Run `make "
            "routing`, `make drive-area` and `make drive-time`, then export again."
        )
    return (
        f"**Drive times are from {a.origin_name}** (D-02, a placeholder origin), by car with "
        f"no traffic, from {a.engine_version or 'Valhalla'} on OpenStreetMap roads "
        f"(© OpenStreetMap contributors), built {a.built}. They end at the road point "
        "nearest the turbine end, so the walk or track in from the road isn't counted; "
        "`drive_road_point_ft` is how far that road point is (OpenStreetMap often has "
        "tracks TIGER lacks). Drives end on the nearest public road; "
        "`drive_ends_on_track` marks the few that had no route to it (a gated or private "
        "section) and end on the nearest track or driveway instead. "
        f"`in_drive_area` is whether the drive is {a.minutes} minutes or less (the search "
        "region; without a drive time, whether the turbine end is inside the area's "
        "outline); `outside_drive_area` flags the ones that aren't. Flagged only: "
        "nothing is filtered or re-ranked on drive time or road distance. "
        "`no_drive_time` means drive times weren't computed for this run yet (`make "
        "drive-time`) or Valhalla found no route."
    )


def _buildable_caveat(info: RunInfo) -> str:
    b = info.buildable
    if b is None:
        return (
            "**Buildable ground isn't computed**, so its columns are empty. Run `make ingest` "
            "(for soils) and `make analyze` (or `uv run hhsf analyze buildable`), then export "
            "again."
        )
    soils = f", {info.soils_version}" if info.soils_version else ""
    rule = "Tennessee" if b.septic_rating == "septic_tn" else "national"
    return (
        "**Buildable ground is a screen, not a site evaluation** (FR-4, D-13; computed "
        f"{b.computed}). On the turbine parcel, `turbine_parcel_gentle_acres` is ground "
        f"sloped {b.max_slope_pct:g}% or less on the USGS 10 m elevation model; "
        "`turbine_parcel_buildable_acres` is the part of it also outside FEMA's 100-year "
        f"floodplain and on soils whose {rule} septic rating isn't "
        f"{' or '.join(b.septic_excluded)} (USDA SSURGO{soils}), in patches of "
        f"{b.min_patch_acres:g} acres or more; `nearest_buildable_ft` is the straight line "
        "from the turbine end to the nearest of it (0: on it). Flagged only: "
        "`no_buildable_ground` removes nothing; `soils_not_loaded` means the parcel reaches "
        "past the loaded soil surveys, so its buildable acres may be understated. "
        "**Where the data runs out:** soil maps are "
        "drawn at 1:24,000, so a map unit holds pockets of other soils, and its rating is "
        "the one most of it has; Tennessee permits septic systems only on a soil "
        "scientist's on-site evaluation. A 10 m elevation model smooths small benches and "
        "ledges. Floodplains on small creeks are mostly unmapped (see the flood caveat). "
        "Road access, the fourth FR-4 test, is in the road columns, not here."
    )


def _farmland_caveat(info: RunInfo) -> str:
    f = info.farmland
    if f is None:
        return (
            "**Farmland isn't computed**, so its columns are empty. Run `make ingest` (for "
            "soils) and `make analyze` (or `uv run hhsf analyze farmland`), then export again."
        )
    soils = f" ({info.soils_version})" if info.soils_version else ""
    return (
        "**Farmland is what the soils could do, not what the land is now** (FR-5, D-14; "
        f"computed {f.computed}), from USDA SSURGO{soils}, over the whole turbine parcel. "
        "`turbine_parcel_prime_acres` is NRCS prime farmland; `turbine_parcel_prime_if_acres` "
        "is prime only if drained or protected from flooding, which it usually isn't yet; "
        "`turbine_parcel_statewide_acres` is farmland of statewide importance. "
        "`turbine_parcel_cropland_acres` is land "
        f"capability class 1–{f.max_capability_class} (non-irrigated): ground that can be "
        "tilled year after year with at most moderate care. `turbine_parcel_wet_acres` is "
        "somewhat poorly drained or wetter. `turbine_parcel_pasture_aum` is the grazing the "
        "soils could grow as improved pasture, in animal-unit months a year (one AUM feeds "
        "one cow for a month), from each soil's main component. Steep or floodplain ground "
        "counts too: capability classes already reflect slope and flooding. Flagged only: "
        "`no_farmland` removes nothing; `soils_not_loaded` means part of the parcel has no "
        "soil survey loaded. **Where the data runs out:** a forested parcel is scored on "
        "its soils as if cleared; soil maps are drawn at 1:24,000 and each map unit gets "
        "its main soil's class, so pockets of better or worse ground hide in it."
    )


def _landcover_caveat(info: RunInfo) -> str:
    if info.landcover_year is None:
        return (
            "**Land cover isn't computed**, so its columns are empty. Run `make analyze` (or "
            "`uv run hhsf analyze landcover`), then export again."
        )
    return (
        f"**Land cover is from USGS NLCD {info.landcover_year}** (30 m cells classified from "
        "Landsat imagery, with the USFS tree canopy cover of the same year). "
        "`turbine_parcel_open_acres` is grassland, pasture/hay and crops on the turbine "
        "parcel: ground that could be farmed without clearing trees. "
        "`turbine_parcel_open_cropland_acres` is the part of it on class 1–3 cropland soils, "
        "the farmland that's usable now; the farmland columns count the soils under the "
        "forest too. `turbine_parcel_canopy_pct` is the mean tree canopy cover. Not a filter: "
        "`no_open_land` removes nothing. **Where the data runs out:** it's a few years old "
        "and 30 m cells blur fields smaller than about half an acre and the edges of "
        "woods; clear-cuts can show as grassland or shrub until they grow back."
    )


def _privacy_caveat(info: RunInfo) -> str:
    p = info.privacy
    if p is None:
        return (
            "**Privacy isn't computed**, so its columns are empty and the overall score leaves "
            "it out. Run `make ingest` and `make analyze` (or `uv run hhsf ingest buildings` and "
            "`uv run hhsf analyze privacy`), then score and export again."
        )
    return (
        "**Privacy (FR-6) is measured at a home site**: the turbine parcel's buildable ground "
        "nearest the turbine end (`home_site` buildable), or the turbine end where there's "
        "none (`turbine_end`); `home_site_lat`/`lon` say where. Buildings are Microsoft's "
        f"machine-traced footprints ({p.buildings_version or 'Global ML Building Footprints'}) "
        f"of {p.min_footprint_sqft:,.0f} sq ft or more, not on the turbine parcel: "
        "`buildings_quarter_mile`, `buildings_half_mile`, `nearest_building_ft`, and "
        "`buildings_per_sqmi` within 1 mile. `turbine_parcel_buildings` counts the ones on the "
        "parcel itself (a house or barn that may come with it). `road_woods_ft` is how many "
        "feet of the straight line from the home site toward the nearest public road "
        f"(`home_public_road_ft`) are woods ({p.woods_canopy_pct:g}%+ tree canopy, NLCD 2021), "
        "over its first 1,320 ft. Flags `neighbor_within_660_ft`, `open_to_road` (a public road "
        "within 500 ft with under 100 ft of woods between) and `buildings_on_parcel` remove "
        "nothing. **Where the data runs out:** footprints say nothing about use, so a barn "
        "counts as a neighbor and a farmstead as 2 or 3; the imagery is a few years old "
        "(new houses are missing, torn-down ones still there); hills that hide a house from "
        "the road aren't counted, nor winter, when leaf-off hardwoods screen far less; and "
        "only roads in the loaded counties count."
    )


def _subdivision_caveat(info: RunInfo) -> str:
    sd = info.subdivision
    if sd is None:
        return (
            "**Subdivision feasibility isn't computed**, so its columns are empty. Run `make "
            "analyze` (or `uv run hhsf analyze subdivision`), then export again."
        )
    return (
        "**Subdivision feasibility (FR-9) asks whether a homestead piece could be cut from "
        "the turbine parcel** (D-18): a piece holding the stretch, a house site (the parcel's "
        "buildable ground nearest the turbine end) and a driveway to a public road, all on "
        "that one parcel. `homestead_cut` is yes when the parcel has "
        f"`road_frontage_ft` of {sd.min_frontage_ft:,.0f} or more (TIGER public roads on it "
        f"or within 50 ft of its edge), {100 * sd.min_stretch_share:.0f}% or more of the "
        "stretch is on it (`stretch_on_parcel_pct`), it has buildable ground, and the turbine "
        "end, house site and road frontage are on one part of it. `cut_driveway_ft` and "
        "`cut_wire_ft` are straight lines (a winding drive is longer); "
        "`homestead_piece_acres` is a 100 ft strip along the stretch, wire and driveway plus a "
        "2-acre house lot, rounded up to 5 acres: Tennessee leaves tracts of 5 acres or more "
        "with no new road out of subdivision review (TCA 13-3-401). `subdivision_score` "
        "(0-100) gives a quarter each to frontage, the share of the stretch on the parcel, "
        "buildable acres (up to 2) and access (full up to a 1/4-mile driveway, none at a "
        "mile or with no reachable frontage); a part with no data is left out. It isn't in "
        "the overall score: FR-7 doesn't list it. Flags `no_road_frontage`, "
        "`stretch_crosses_parcels`, `no_house_site`, `access_crosses_parcels` (the turbine "
        "end, house site and frontage are on separate parts of the parcel, or the turbine end "
        "is off it) and `long_driveway` remove nothing. **Where the data runs out:** TIGER "
        "doesn't say who maintains a road, so frontage on a private road that TIGER calls "
        "local still counts; parcel lines are the assessor's, not a survey; county rules on "
        "frontage, lot shape and driveways vary, and a seller may not split at all. Ask the "
        "county planning office before counting on a cut."
    )


def _overall_caveat(info: RunInfo) -> list[str]:
    o = info.score_params.get("scoring_profile", {}).get("overall")
    if not o:
        return []
    return [
        "**The overall score ranks each tier** (FR-7, D-15): the hydro part plus "
        "buildability (buildable ground on the turbine parcel, how far it is, and the nearest "
        "public road), agriculture (prime farmland, cropland and pasture on the turbine "
        "parcel), "
        + (
            "privacy (neighbors, density and road screening at the home site, D-17), "
            if o.get("privacy")
            else ""
        )
        + "and logistics (drive time), weighted "
        + ", ".join(f"{k} {v:g}" for k, v in o.get("weights", {}).items())
        + (
            ". A part with no data for a corridor"
            if o.get("privacy")
            else ". Privacy (FR-6) isn't in this profile, so it's left out and the other "
            "weights are rescaled, as is any part with no data for a corridor"
        )
        + " (no turbine parcel, no drive time) is left out and the rest rescaled: "
        "missing data is never scored as zero. Tiers still come first, so a low-confidence "
        "or low-flow corridor never outranks a medium one, and overall scores only compare "
        "within a tier. The sub-score columns show each part; the site data is read when "
        "`make score` runs, so score again after `make analyze` or `make drive-time`."
    ]


def _target(info: RunInfo) -> str:
    w = info.score_params.get("scoring_profile", {}).get("power_target_w")
    return "power" if w is None else f"{w / 1000:g} kW"


def _table(header: list[str], lines: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + " --- |" * len(header)]
    out += ["| " + " | ".join(cells) + " |" for cells in lines]
    return out


def _name(r: ExportRow) -> str:
    return (r.stream_name or "(unnamed)").replace("|", "/")


def _place(r: ExportRow) -> str:
    if r.outlet_lat is None or r.outlet_lon is None:
        return "–"
    return f"{r.outlet_lat:.5f}, {r.outlet_lon:.5f}"


def _parcel(r: ExportRow) -> str:
    t = r.turbine_parcel
    if t is None:
        return "–"
    return f"{t.parcel_id} ({t.acres:,.0f})".replace("|", "/")


def _owner(r: ExportRow) -> str:
    t = r.turbine_parcel
    if t is None or not t.owner:
        return "–"
    return (t.owner + (f" & {t.owner2}" if t.owner2 else "")).replace("|", "/")


def _flood(r: ExportRow) -> str:
    if r.turbine_flood_zone is None:
        return "not mapped" if r.flood_flags else "–"
    return r.turbine_flood_zone.label


def _buildable(r: ExportRow) -> str:
    b = r.turbine_buildable
    return "–" if b is None else f"{b.buildable_acres:,.1f}"


def _farmland(r: ExportRow) -> str:
    f = r.turbine_farmland
    if f is None:
        return "–"
    return f"{f.prime_acres + f.statewide_acres:,.0f} / {f.cropland_acres:,.0f}"


def _public_road(r: ExportRow) -> str:
    return _fmt(None if r.turbine_public_road_m is None else units.m_to_ft(r.turbine_public_road_m))


def _drive(r: ExportRow) -> str:
    if r.drive_minutes is None:
        return "–"
    return f"{r.drive_minutes:.0f}" + (" (outside)" if r.in_drive_area is False else "")


def _neighbors(r: ExportRow) -> str:
    """Other buildings within 1/4 mile of the home site, and the nearest one."""
    p = r.privacy
    if p is None:
        return "–"
    if p.nearest_building_m is None:
        return "0"
    return f"{p.buildings_quarter_mile} (nearest {units.m_to_ft(p.nearest_building_m):,.0f} ft)"


def _cut(r: ExportRow) -> str:
    """Whether a homestead piece can be cut, and the subdivision score."""
    sd = r.subdivision
    if sd is None:
        return "–"
    answer = {True: "yes", False: "no", None: "?"}[sd.homestead_cut]
    return answer if sd.score is None else f"{answer} ({sd.score:.0f})"


def tier_table(rows: list[ExportRow], tier: str) -> list[str]:
    if tier == scoring.HEAD_ONLY:
        header = [
            "#",
            "Rank",
            "Stream",
            "Overall",
            "Head score",
            "Net head (ft)",
            "Drainage (sq mi)",
            "Outlet (lat, lon)",
            "Turbine parcel (acres)",
            "Owner",
            "Turbine flood zone",
            "Buildable (ac)",
            "Prime / cropland (ac)",
            "Buildings within 1/4 mi",
            "Homestead cut (score)",
            "Public road (ft)",
            "Drive (min)",
            "NHDPlus ID",
        ]
        lines = [
            [
                str(r.tier_rank),
                str(r.rank),
                _name(r),
                _fmt(r.overall_score, ".1f"),
                _fmt(r.head_subscore, ".1f"),
                _fmt(r.net_head_ft),
                _fmt(record(r)["drainage_area_sqmi"], ".2f"),
                _place(r),
                _parcel(r),
                _owner(r),
                _flood(r),
                _buildable(r),
                _farmland(r),
                _neighbors(r),
                _cut(r),
                _public_road(r),
                _drive(r),
                str(r.nhdplusid),
            ]
            for r in rows
        ]
    else:
        header = [
            "#",
            "Rank",
            "Stream",
            "Overall",
            "Hydro score",
            "Net head (ft)",
            "Firm power (W)",
            "Firm flow (cfs)",
            "Flow method",
            "Outlet (lat, lon)",
            "Turbine parcel (acres)",
            "Owner",
            "Turbine flood zone",
            "Buildable (ac)",
            "Prime / cropland (ac)",
            "Buildings within 1/4 mi",
            "Homestead cut (score)",
            "Public road (ft)",
            "Drive (min)",
            "NHDPlus ID",
        ]
        lines = [
            [
                str(r.tier_rank),
                str(r.rank),
                _name(r),
                _fmt(r.overall_score, ".1f"),
                _fmt(r.hydro_score, ".1f"),
                _fmt(r.net_head_ft),
                _fmt(r.firm_power_w),
                _fmt(r.firm_flow_cfs, ".3g"),
                r.flow_method or "–",
                _place(r),
                _parcel(r),
                _owner(r),
                _flood(r),
                _buildable(r),
                _farmland(r),
                _neighbors(r),
                _cut(r),
                _public_road(r),
                _drive(r),
                str(r.nhdplusid),
            ]
            for r in rows
        ]
    return _table(header, lines)


def _flood_counts(info: RunInfo, ranked: list[ExportRow]) -> list[str]:
    if info.flood_version is None:
        return []
    n = Counter(f for r in ranked for f in r.flood_flags)
    return [
        *_table(
            ["FEMA flood zones (ranked corridors, flagged only)", "Corridors"],
            [
                ["Turbine end in a floodway", str(n[TURBINE_IN_FLOODWAY])],
                ["Turbine end in the 100-year floodplain (SFHA)", str(n[TURBINE_IN_SFHA])],
                ["Some of the stretch in the 100-year floodplain", str(n[STRETCH_IN_SFHA])],
                ["Turbine end not on any FEMA map", str(n[FLOOD_NOT_MAPPED])],
            ],
        ),
        "",
    ]


def _access_counts(info: RunInfo, ranked: list[ExportRow]) -> list[str]:
    lines = []
    if info.drive_area is not None:
        a = info.drive_area
        lines += [
            [f"Turbine end inside the {a.minutes}-minute drive area", _count(ranked, True)],
            ["Turbine end outside it", _count(ranked, False)],
            ["No drive time (not computed, or no route)", _flag_count(ranked, NO_DRIVE_TIME)],
        ]
    if info.roads_version is not None:
        quarter_mi_m = 402.336
        near = sum(
            r.turbine_public_road_m is not None and r.turbine_public_road_m <= quarter_mi_m
            for r in ranked
        )
        lines += [
            ["Public road within 1/4 mile of the turbine end", str(near)],
            ["Nearest road a 4WD trail or private road", _flag_count(ranked, ROAD_NOT_PUBLIC)],
        ]
    if not lines:
        return []
    header = ["Roads and drive time (ranked corridors, flagged only)", "Corridors"]
    return [*_table(header, lines), ""]


def _buildable_counts(info: RunInfo, ranked: list[ExportRow]) -> list[str]:
    if info.buildable is None:
        return []
    quarter_mi_m = 402.336
    with_ground = [
        r for r in ranked if r.turbine_buildable and r.turbine_buildable.buildable_acres > 0
    ]
    near = sum(
        r.turbine_buildable.nearest_m is not None and r.turbine_buildable.nearest_m <= quarter_mi_m
        for r in with_ground
    )
    n = Counter(f for r in ranked for f in r.buildable_flags)
    return [
        *_table(
            [
                "Buildable ground on the turbine parcel (ranked corridors, flagged only)",
                "Corridors",
            ],
            [
                ["Some buildable ground", str(len(with_ground))],
                ["Within 1/4 mile of the turbine end", str(near)],
                ["None", str(n[NO_BUILDABLE_GROUND])],
                ["Not computed (turbine parcel not analyzed)", str(n[BUILDABLE_NOT_COMPUTED])],
            ],
        ),
        "",
    ]


def _farmland_counts(info: RunInfo, ranked: list[ExportRow]) -> list[str]:
    if info.farmland is None:
        return []
    farms = [r.turbine_farmland for r in ranked if r.turbine_farmland]
    n = Counter(f for r in ranked for f in r.farmland_flags)
    top = info.farmland.max_capability_class
    return [
        *_table(
            ["Farmland on the turbine parcel (ranked corridors, flagged only)", "Corridors"],
            [
                [
                    "Some prime farmland or farmland of statewide importance",
                    str(sum(f.prime_acres + f.statewide_acres > 0 for f in farms)),
                ],
                [
                    f"Some cropland (capability class 1–{top})",
                    str(sum(f.cropland_acres > 0 for f in farms)),
                ],
                ["Neither", str(n[NO_FARMLAND])],
                ["Not computed (turbine parcel not analyzed)", str(n[FARMLAND_NOT_COMPUTED])],
            ],
        ),
        "",
    ]


def _landcover_counts(info: RunInfo, ranked: list[ExportRow]) -> list[str]:
    if info.landcover_year is None:
        return []
    covers = [r.turbine_landcover for r in ranked if r.turbine_landcover]
    n = Counter(f for r in ranked for f in r.landcover_flags)
    return [
        *_table(
            [f"Land cover on the turbine parcel (NLCD {info.landcover_year}, ranked)", "Corridors"],
            [
                [
                    "Some open land (field, pasture, crops)",
                    str(sum(c.open_acres > 0 for c in covers)),
                ],
                [
                    "Some of it on class 1–3 cropland soils",
                    str(sum(c.open_cropland_acres > 0 for c in covers)),
                ],
                ["No open land (all forest, shrub, water or built up)", str(n[NO_OPEN_LAND])],
                ["Not computed (turbine parcel not analyzed)", str(n[LANDCOVER_NOT_COMPUTED])],
            ],
        ),
        "",
    ]


def _privacy_counts(info: RunInfo, ranked: list[ExportRow]) -> list[str]:
    if info.privacy is None:
        return []
    found = [r.privacy for r in ranked if r.privacy]
    n = Counter(f for r in ranked for f in r.privacy_flags)
    return [
        *_table(
            ["Privacy at the home site (ranked)", "Corridors"],
            [
                [
                    "No other building within 1/4 mile",
                    str(sum(p.buildings_quarter_mile == 0 for p in found)),
                ],
                [
                    "Another building within 660 ft (1/8 mile)",
                    str(n[NEIGHBOR_CLOSE]),
                ],
                ["Open to a public road within 500 ft", str(n[OPEN_TO_ROAD])],
                ["Buildings on the turbine parcel already", str(n[BUILDINGS_ON_PARCEL])],
                [
                    "Home site at the turbine end (no buildable ground)",
                    str(sum(p.home_site != "buildable" for p in found)),
                ],
                ["Not computed", str(n[PRIVACY_NOT_COMPUTED])],
            ],
        ),
        "",
    ]


def _subdivision_counts(info: RunInfo, ranked: list[ExportRow]) -> list[str]:
    if info.subdivision is None:
        return []
    found = [r.subdivision for r in ranked if r.subdivision]
    n = Counter(f for r in ranked for f in r.subdivision_flags)
    return [
        *_table(
            ["Homestead cut from the turbine parcel (ranked)", "Corridors"],
            [
                ["A homestead piece can be cut", str(sum(s.homestead_cut is True for s in found))],
                ["No public road frontage", str(n[subdivision.NO_ROAD_FRONTAGE])],
                ["Stretch crosses onto other parcels", str(n[subdivision.STRETCH_CROSSES_PARCELS])],
                ["No buildable ground for a house", str(n[subdivision.NO_HOUSE_SITE])],
                [
                    "Turbine, house site and road on separate parts",
                    str(n[subdivision.ACCESS_CROSSES_PARCELS]),
                ],
                ["Driveway over 1/4 mile", str(n[subdivision.LONG_DRIVEWAY])],
                ["Not computed", str(n[SUBDIVISION_NOT_COMPUTED])],
            ],
        ),
        "",
    ]


def _count(rows: list[ExportRow], in_area: bool) -> str:
    return str(sum(r.in_drive_area is in_area for r in rows))


def _flag_count(rows: list[ExportRow], flag: str) -> str:
    return str(sum(flag in r.access_flags for r in rows))


def render_report(info: RunInfo, rows: list[ExportRow], top_n: int = 20) -> str:
    """The summary report for planning visits, as Markdown."""
    status = Counter(r.status for r in rows)
    tiers = Counter(r.tier for r in rows if r.ranked)
    ranked = shortlist(rows)
    order = info.score_params.get("scoring_profile", {}).get("rank_tiers") or list(TIER_TITLES)
    area = f"{info.aoi_name} (`{info.aoi_key}`)" if info.aoi_name else f"`{info.aoi_key}`"

    out = [
        f"# Hydro shortlist: {info.aoi_name or info.aoi_key}",
        "",
        f"{len(ranked)} of {len(rows)} stream reaches made the list. Read it tier by tier: "
        "every medium-confidence site ranks above every low-confidence one, then head-only "
        "and low-flow sites follow. "
        f"How scores and tiers work: [docs/SCORING.md]({DOCS}/SCORING.md). "
        f"How head, flow and power are estimated: [docs/ASSUMPTIONS.md]({DOCS}/ASSUMPTIONS.md).",
        "",
        "## Run",
        "",
        *_table(
            ["", ""],
            [
                ["Area", area],
                ["Score run", f"{info.score_run_id} (finished {info.finished_at})"],
                ["Hydro run it scored", str(info.hydro_run_id)],
                ["Scoring profile", f"`{info.scoring_profile_version}`"],
                ["Code version (score run)", f"`{info.score_code_version}`"],
                ["Code version (hydro run)", f"`{info.hydro_code_version}`"],
                *([["Exported with", f"`{info.exported_with}`"]] if info.exported_with else []),
            ],
        ),
        "",
        "### Data sources",
        "",
        *_table(
            ["Source", "Version"],
            [[k, str(v)] for k, v in sorted(info.data_source_versions.items())]
            + ([["tn_parcels (at export)", info.parcels_version]] if info.parcels_version else [])
            + ([["fema_nfhl (at export)", info.flood_version]] if info.flood_version else [])
            + ([["tiger_roads (at export)", info.roads_version]] if info.roads_version else [])
            + ([["usda_ssurgo (at export)", info.soils_version]] if info.soils_version else [])
            + (
                [["osm_valhalla (at export)", f"{a.engine_version}, area built {a.built}"]]
                if (a := info.drive_area)
                else []
            ),
        ),
        "",
        "## Key assumptions",
        "",
        "Read from this run's saved settings (`analysis_runs.params`).",
        "",
        *_table(["Assumption", "Value"], [[a, v] for a, v in assumptions(info)]),
        "",
        "## Counts",
        "",
        *_table(
            ["Status", "Corridors"],
            [[STATUS_TITLES.get(s, s), str(status[s])] for s in STATUS_TITLES if status[s]]
            + [[s, str(n)] for s, n in sorted(status.items()) if s not in STATUS_TITLES]
            + [["**Total**", f"**{len(rows)}**"]],
        ),
        "",
        *_table(
            ["Ranked tier", "Corridors"],
            [[TIER_TITLES.get(t, t), str(tiers[t])] for t in order],
        ),
        "",
        *_flood_counts(info, ranked),
        *_access_counts(info, ranked),
        *_buildable_counts(info, ranked),
        *_farmland_counts(info, ranked),
        *_landcover_counts(info, ranked),
        *_privacy_counts(info, ranked),
        *_subdivision_counts(info, ranked),
        "## Caveats",
        "",
        *[f"- {c}" for c in caveats(info)],
        "",
    ]
    for tier in order:
        in_tier = [r for r in ranked if r.tier == tier]
        shown = in_tier[:top_n]
        title = TIER_TITLES.get(tier, tier)
        out += [f"## Top {len(shown)} of {len(in_tier)}: {title}" if shown else f"## {title}", ""]
        out += [*tier_table(shown, tier), ""] if shown else ["None in this run.", ""]
    out += [
        "## Files",
        "",
        f"- `{SHORTLIST_CSV}`: every ranked corridor, in rank order, with its parcels and owners.",
        f"- `{SHORTLIST_GEOJSON}`: the same, with each reach's line (EPSG:4326), for GIS "
        "or Google Earth. Parcel IDs and acreage, but no owner names.",
        f"- `{ALL_CORRIDORS_CSV}`: every corridor, filtered ones included, with the reason "
        'in `explanation`. Use it to answer "why isn\'t this stream on the list?".',
        "",
        "The outlet is the downstream end of the reach, where a powerhouse would sit. "
        "Unnamed streams can be found by NHDPlus ID or by the outlet point. The CSVs and this "
        "report name property owners: personal information, so don't post or share them. "
        "`turbine_parcel_tpad_url` opens the parcel's full record on the Comptroller's site. "
        "`turbine_flood_zone` is FEMA's zone at the bottom of the stretch and "
        "`stretch_sfha_ft` how much of the stretch is in the 100-year floodplain. "
        "`turbine_public_road_ft` is the straight-line distance to the nearest public road "
        "and `drive_minutes` the drive from downtown Chattanooga to the road nearest the "
        "turbine end. `turbine_parcel_buildable_acres` is the turbine parcel's gently "
        "sloped, flood-free, septic-suitable ground, and `turbine_parcel_prime_acres` and "
        '`turbine_parcel_cropland_acres` its farmland (the tables\' "Prime / cropland").',
        "",
    ]
    return "\n".join(out)


# --- Files --------------------------------------------------------------------


def write_exports(out_dir: Path, info: RunInfo, rows: list[ExportRow]) -> list[Path]:
    """Write the four export files; returns their paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    ranked = shortlist(rows)
    files = {
        SHORTLIST_CSV: to_csv(ranked, SHORTLIST_COLUMNS),
        SHORTLIST_GEOJSON: json.dumps(to_geojson(ranked, info)) + "\n",
        ALL_CORRIDORS_CSV: to_csv(all_corridors(rows), ALL_CORRIDORS_COLUMNS),
        REPORT_MD: render_report(info, rows),
    }
    paths = []
    for name, content in files.items():
        path = out_dir / name
        path.write_text(content, encoding="utf-8")
        paths.append(path)
    return paths
