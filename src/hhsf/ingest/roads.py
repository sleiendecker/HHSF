"""Roads from the Census Bureau's TIGER/Line roads file, one file per county.

Each road keeps its Census class (MTFCC), which says what kind of road it is: an
interstate, a US or state highway, a local road, a 4WD trail, a private road. Paths,
stairs and bike or bridle paths aren't roads you can drive to a site on, so they're
left out. See docs/ASSUMPTIONS.md, "Roads and drive time".

A county's roads are replaced as a whole on every ingest, like parcels and flood zones.
"""

from dataclasses import dataclass
from pathlib import Path

import httpx
import pyogrio
import shapely
from shapely.geometry import MultiLineString
from shapely.geometry.base import BaseGeometry
from sqlalchemy import Connection, text

from hhsf.core import crs
from hhsf.core.config import AOI, DATA_DIR, data_source_settings, load_data_source
from hhsf.core.db import record_source

SOURCE_KEY = "tiger_roads"


@dataclass(frozen=True)
class RoadClass:
    key: str  # short name, for the map and the exports
    label: str  # as people read it in the popup and the report
    public: bool  # a road the public can use to reach a property
    limited_access: bool = False  # interstates and ramps: no driveway can join them


# Census MAF/TIGER Feature Class Codes for roads (TIGER/Line technical documentation,
# appendix). Codes missing here aren't loaded: S1710 walkway, S1720 stairway, S1820 bike
# path, S1830 bridle path, and S1750, "internal Census Bureau use" (unnamed lines with no
# stated meaning; about a sixth of Sequatchie County's road length in 2025).
ROAD_CLASSES = {
    "S1100": RoadClass("primary", "Interstate or divided highway", True, limited_access=True),
    "S1200": RoadClass("secondary", "US or state highway", True),
    "S1400": RoadClass("local", "Local road", True),
    "S1500": RoadClass("4wd_trail", "4WD trail", False),
    "S1630": RoadClass("ramp", "Highway ramp", True, limited_access=True),
    "S1640": RoadClass("service", "Frontage road", True),
    "S1730": RoadClass("alley", "Alley", True),
    "S1740": RoadClass("private", "Private road (logging, farm, utility)", False),
    "S1780": RoadClass("parking_lot", "Parking lot road", False),
}

ROAD_LABELS = {c.key: c.label for c in ROAD_CLASSES.values()}


@dataclass(frozen=True)
class RoadRecord:
    linearid: str  # the Census ID of the road segment
    county_geoid: str
    name: str | None  # FULLNAME, e.g. "Old York Hwy N"; often missing on trails
    route_type: str | None  # RTTYP: I interstate, U US, S state, C county, M common name
    mtfcc: str
    geom: MultiLineString


def road_class(mtfcc: str) -> RoadClass | None:
    """The road's class, or None for lines that aren't drivable roads."""
    return ROAD_CLASSES.get(mtfcc)


def file_url(county_geoid: str) -> str:
    """The county's roads file, e.g. .../TIGER2025/ROADS/tl_2025_47153_roads.zip."""
    if len(county_geoid) != 5 or not county_geoid.isdigit():
        raise ValueError(f"{county_geoid} isn't a county FIPS code.")
    s = data_source_settings(SOURCE_KEY)
    return s["file_url"].format(year=s["year"], geoid=county_geoid)


def cache_path(county_geoid: str) -> Path:
    return DATA_DIR / "raw" / SOURCE_KEY / file_url(county_geoid).rsplit("/", 1)[-1]


def download(county_geoid: str, refresh: bool = False) -> Path:
    """The county's zipped roads shapefile, downloaded once to data/raw/."""
    path = cache_path(county_geoid)
    if path.exists() and not refresh:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".part")
    with httpx.stream("GET", file_url(county_geoid), timeout=60, follow_redirects=True) as r:
        r.raise_for_status()
        with partial.open("wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
    # Rename only when complete, so an interrupted download is never mistaken for a good file.
    partial.rename(path)
    return path


def _as_multiline(geom: BaseGeometry) -> MultiLineString:
    if geom.geom_type == "LineString":
        return MultiLineString([geom])
    if geom.geom_type == "MultiLineString":
        return geom
    raise ValueError(f"Expected a line, got {geom.geom_type}")


def read_roads(path: Path, county_geoid: str) -> list[RoadRecord]:
    """The drivable roads in a TIGER/Line roads file, in storage CRS."""
    meta, _, geometry, columns = pyogrio.raw.read(path)
    fields = {name: values for name, values in zip(meta["fields"], columns, strict=True)}
    records = []
    for i, wkb in enumerate(geometry):
        mtfcc = fields["MTFCC"][i]
        if wkb is None or road_class(mtfcc) is None:
            continue
        records.append(
            RoadRecord(
                linearid=fields["LINEARID"][i],
                county_geoid=county_geoid,
                name=fields["FULLNAME"][i] or None,
                route_type=fields["RTTYP"][i] or None,
                mtfcc=mtfcc,
                # The files are NAD83 (EPSG:4269); within a meter of WGS84 here.
                geom=_as_multiline(crs.file_to_storage(shapely.from_wkb(wkb), meta["crs"])),
            )
        )
    return records


def fetch_roads(aoi: AOI, refresh: bool = False) -> list[RoadRecord]:
    return read_roads(download(aoi.county_geoid, refresh), aoi.county_geoid)


def load_roads(conn: Connection, aoi: AOI, records: list[RoadRecord]) -> int:
    """Replace the county's roads. Returns the number of road segments loaded."""
    record_source(conn, load_data_source(SOURCE_KEY))
    conn.execute(text("DELETE FROM roads WHERE county_geoid = :g"), {"g": aoi.county_geoid})
    if not records:
        return 0
    conn.execute(
        text(
            """
            INSERT INTO roads (linearid, county_geoid, name, route_type, mtfcc, road_class,
                               public, limited_access, geom, source_key)
            VALUES (:linearid, :county_geoid, :name, :route_type, :mtfcc, :road_class,
                    :public, :limited_access, ST_GeomFromWKB(:wkb, 4326), :source_key)
            """
        ),
        [
            {
                **{k: v for k, v in r.__dict__.items() if k != "geom"},
                "road_class": (c := ROAD_CLASSES[r.mtfcc]).key,
                "public": c.public,
                "limited_access": c.limited_access,
                "wkb": r.geom.wkb,
                "source_key": SOURCE_KEY,
            }
            for r in records
        ],
    )
    return len(records)
