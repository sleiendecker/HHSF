"""FEMA flood hazard zones from the National Flood Hazard Layer (NFHL).

One query per county, by its countywide flood map (DFIRM). Each zone says whether it
is a Special Flood Hazard Area (the "100-year floodplain": zones A, AE, AH, AO, ...),
the 0.2% annual chance ("500-year") area or minimal hazard (both zone X). See
docs/ASSUMPTIONS.md, "Flood zones".

A county's zones are replaced as a whole on every ingest: map revisions redraw them.
"""

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from shapely.geometry import MultiPolygon, shape
from sqlalchemy import Connection, text

from hhsf.core.config import AOI, DATA_DIR, data_source_settings, load_data_source
from hhsf.core.db import record_source
from hhsf.ingest.arcgis import query_geojson_paged
from hhsf.ingest.parcels import as_multipolygon

SOURCE_KEY = "fema_nfhl"

FIELDS = ["OBJECTID", "DFIRM_ID", "FLD_AR_ID", "FLD_ZONE", "ZONE_SUBTY", "SFHA_TF"]

FLOODWAY = "FLOODWAY"  # ZONE_SUBTY of the channel kept clear for the 100-year flood


@dataclass(frozen=True)
class FloodZoneRecord:
    fld_ar_id: str  # FEMA's ID for the zone polygon, unique within its flood map
    dfirm_id: str  # the flood map it's from: county FIPS + "C" for a countywide map
    county_geoid: str
    zone: str  # FLD_ZONE: A, AE, AH, AO, X, D, OPEN WATER, ...
    subtype: str | None  # ZONE_SUBTY: FLOODWAY, 0.2 PCT ANNUAL CHANCE FLOOD HAZARD, ...
    sfha: bool  # in a Special Flood Hazard Area (the 1% annual chance floodplain)
    floodway: bool
    geom: MultiPolygon


def dfirm_id(county_geoid: str) -> str:
    """The county's countywide flood map ID, e.g. 47153C for Sequatchie.

    All five analyzed counties have countywide maps (checked 2026-10-20). A county
    mapped town by town instead would need a spatial query.
    """
    if len(county_geoid) != 5 or not county_geoid.isdigit():
        raise ValueError(f"{county_geoid} isn't a county FIPS code.")
    return f"{county_geoid}C"


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    s = " ".join(str(value).split())
    return s or None


def parse_feature(feature: dict[str, Any], county_geoid: str) -> FloodZoneRecord:
    props = feature["properties"]
    zone = _clean(props.get("FLD_ZONE"))
    fld_ar_id = _clean(props.get("FLD_AR_ID"))
    if zone is None or fld_ar_id is None:
        raise ValueError(f"Flood zone without a zone or ID (OBJECTID {props.get('OBJECTID')})")
    subtype = _clean(props.get("ZONE_SUBTY"))
    return FloodZoneRecord(
        fld_ar_id=fld_ar_id,
        dfirm_id=_clean(props.get("DFIRM_ID")) or dfirm_id(county_geoid),
        county_geoid=county_geoid,
        zone=zone,
        subtype=subtype,
        sfha=props.get("SFHA_TF") == "T",
        floodway=subtype is not None and FLOODWAY in subtype,
        geom=as_multipolygon(shape(feature["geometry"])),
    )


def cache_dir(aoi: AOI):
    return DATA_DIR / "raw" / SOURCE_KEY / aoi.key


def fetch_flood_zones(
    aoi: AOI, refresh: bool = False, echo: Callable[[str], None] = print
) -> list[FloodZoneRecord]:
    """Every flood zone on the AOI county's flood map. Pages are cached in data/raw/."""
    settings = data_source_settings(SOURCE_KEY)
    folder = cache_dir(aoi)
    if refresh and folder.exists():
        shutil.rmtree(folder)
    pages = query_geojson_paged(
        settings["url"],
        {
            "where": f"DFIRM_ID='{dfirm_id(aoi.county_geoid)}'",
            "outFields": ",".join(FIELDS),
            "geometryPrecision": 6,  # about 10 cm; the maps are drawn far coarser
        },
        folder,
        settings.get("page_size", 100),
    )
    records: list[FloodZoneRecord] = []
    for features in pages:
        records += [
            parse_feature(f, aoi.county_geoid) for f in features if f.get("geometry") is not None
        ]
        if features:
            echo(f"  {len(records):,} flood zones so far")
    return records


def load_flood_zones(conn: Connection, aoi: AOI, records: list[FloodZoneRecord]) -> int:
    """Replace the county's flood zones. Returns the number of zones loaded.

    Each zone is stored cut into pieces of at most 256 corners (ST_Subdivide): the
    minimal-hazard zone X can be one polygon the size of the county, which would make
    every lookup scan all of it. The pieces don't overlap, and keep the zone's ID.
    """
    record_source(conn, load_data_source(SOURCE_KEY))
    conn.execute(text("DELETE FROM flood_zones WHERE county_geoid = :g"), {"g": aoi.county_geoid})
    if not records:
        return 0
    conn.execute(
        text(
            """
            INSERT INTO flood_zones (fld_ar_id, dfirm_id, county_geoid, zone, subtype, sfha,
                                     floodway, geom, source_key)
            SELECT :fld_ar_id, :dfirm_id, :county_geoid, :zone, :subtype, :sfha, :floodway,
                   ST_Multi(piece), :source_key
            FROM ST_Subdivide(ST_GeomFromWKB(:wkb, 4326), 256) AS piece
            """
        ),
        [
            {
                **{k: v for k, v in r.__dict__.items() if k != "geom"},
                "wkb": r.geom.wkb,
                "source_key": SOURCE_KEY,
            }
            for r in records
        ],
    )
    return len(records)
