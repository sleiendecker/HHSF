"""Property parcels and their owners from the Tennessee Comptroller's statewide layer.

One query per county returns both the parcel outlines and the owner names. They
are split on the way in: outlines go to `parcels` (served as map tiles), owner
names and the property address to `parcel_owners`, which the tile server never
publishes and only the local exports read. See docs/ASSUMPTIONS.md, "Parcels and
owners".

A county's parcels are replaced as a whole on every ingest: parcels split and merge
from month to month, so updating row by row would leave retired ones behind.
"""

import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

import shapely
from shapely.geometry import MultiPolygon, shape
from shapely.geometry.base import BaseGeometry
from sqlalchemy import Connection, text

from hhsf.core import crs, units
from hhsf.core.config import AOI, DATA_DIR, data_source_settings, load_data_source
from hhsf.core.db import record_source
from hhsf.ingest.arcgis import query_geojson_paged

SOURCE_KEY = "tn_parcels"
TN_STATE_FIPS = "47"

FIELDS = [
    "OBJECTID",
    "COUNTY_ID",
    "COUNTY_NAME",
    "PARCEL_TYPE",
    "GISLINK",
    "CMAP",
    "GP",
    "PARCEL",
    "DEEDAC",
    "LINK_TPAD",
    # Personal information: only ever saved to parcel_owners.
    "OWNER",
    "OWNER2",
    "ADDRESS",
]


@dataclass(frozen=True)
class ParcelRecord:
    gislink: str  # the Comptroller's key for the parcel, as in their links
    parcel_id: str  # control map, group and parcel, as the assessor writes it
    county_geoid: str
    county_name: str | None
    acres: float  # measured from the outline
    deed_acres: float | None  # as recorded on the deed; missing for many parcels
    parcel_type: int | None
    tpad_url: str | None  # the parcel's page on the Comptroller's assessment site
    geom: MultiPolygon


@dataclass(frozen=True)
class OwnerRecord:
    gislink: str
    owner: str | None
    owner2: str | None
    property_address: str | None


def county_number(county_geoid: str) -> int:
    """The layer's COUNTY_ID: Tennessee's counties numbered alphabetically, 1-95.

    Tennessee's county FIPS codes are the odd numbers 1-189 in the same order, so the
    number is (FIPS + 1) / 2: Bledsoe (47007) is 4, Sequatchie (47153) is 77.
    """
    if len(county_geoid) != 5 or not county_geoid.startswith(TN_STATE_FIPS):
        raise ValueError(f"County {county_geoid} isn't in Tennessee; the parcel layer is TN only.")
    fips = int(county_geoid[2:])
    if fips % 2 == 0 or not 1 <= fips <= 189:
        raise ValueError(f"{county_geoid} isn't a Tennessee county FIPS code.")
    return (fips + 1) // 2


def _clean(value: Any) -> str | None:
    """Collapse runs of spaces; blank becomes None."""
    if value is None:
        return None
    s = " ".join(str(value).split())
    return s or None


def parcel_label(cmap: Any, group: Any, parcel: Any) -> str:
    """'048M D 001.10': control map, group (often blank) and parcel number."""
    return " ".join(p for p in (_clean(cmap), _clean(group), _clean(parcel)) if p)


def as_multipolygon(geom: BaseGeometry) -> MultiPolygon:
    """Repair self-intersections (common in parcel linework) and keep the polygons."""
    if not geom.is_valid:
        geom = shapely.make_valid(geom)
    polygons = [
        g for g in getattr(geom, "geoms", [geom]) if g.geom_type in ("Polygon", "MultiPolygon")
    ]
    parts = [p for g in polygons for p in getattr(g, "geoms", [g])]
    if not parts:
        raise ValueError(f"No polygon in parcel geometry ({geom.geom_type})")
    return MultiPolygon(parts)


def parse_feature(feature: dict[str, Any], county_geoid: str) -> tuple[ParcelRecord, OwnerRecord]:
    """One GeoJSON feature as the parcel (public) and its owners (private)."""
    props = feature["properties"]
    gislink = _clean(props.get("GISLINK"))
    if gislink is None:
        raise ValueError(f"Parcel without a GISLINK (OBJECTID {props.get('OBJECTID')})")
    geom = as_multipolygon(shape(feature["geometry"]))
    deed = props.get("DEEDAC")
    parcel_type = props.get("PARCEL_TYPE")
    parcel = ParcelRecord(
        gislink=gislink,
        parcel_id=parcel_label(props.get("CMAP"), props.get("GP"), props.get("PARCEL")),
        county_geoid=county_geoid,
        county_name=_clean(props.get("COUNTY_NAME")),
        acres=units.sqkm_to_acres(crs.area_m2(geom) / 1e6),
        # 0 means "not recorded", not an empty lot.
        deed_acres=float(deed) if deed else None,
        parcel_type=int(parcel_type) if parcel_type is not None else None,
        tpad_url=_clean(props.get("LINK_TPAD")),
        geom=geom,
    )
    owner = OwnerRecord(
        gislink=gislink,
        owner=_clean(props.get("OWNER")),
        owner2=_clean(props.get("OWNER2")),
        property_address=_clean(props.get("ADDRESS")),
    )
    return parcel, owner


def merge_duplicates(
    pairs: Iterable[tuple[ParcelRecord, OwnerRecord]],
) -> list[tuple[ParcelRecord, OwnerRecord]]:
    """One record per GISLINK. A parcel drawn as several features keeps all its pieces."""
    merged: dict[str, tuple[ParcelRecord, OwnerRecord]] = {}
    for parcel, owner in pairs:
        if parcel.gislink not in merged:
            merged[parcel.gislink] = (parcel, owner)
            continue
        first, first_owner = merged[parcel.gislink]
        geom = as_multipolygon(shapely.union(first.geom, parcel.geom))
        acres = units.sqkm_to_acres(crs.area_m2(geom) / 1e6)
        merged[parcel.gislink] = (
            ParcelRecord(**{**first.__dict__, "geom": geom, "acres": acres}),
            first_owner,
        )
    return list(merged.values())


def cache_dir(aoi: AOI):
    return DATA_DIR / "raw" / SOURCE_KEY / aoi.key


def fetch_parcels(
    aoi: AOI, refresh: bool = False, echo: Callable[[str], None] = print
) -> list[tuple[ParcelRecord, OwnerRecord]]:
    """Every parcel in the AOI's county, with its owners. Pages are cached in data/raw/.

    The cache holds owner names too; data/ is never committed. `refresh` drops the
    cache first, to pick up the monthly update.
    """
    settings = data_source_settings(SOURCE_KEY)
    folder = cache_dir(aoi)
    if refresh and folder.exists():
        shutil.rmtree(folder)
    pages = query_geojson_paged(
        settings["url"],
        {
            "where": f"COUNTY_ID={county_number(aoi.county_geoid)}",
            "outFields": ",".join(FIELDS),
        },
        folder,
        settings.get("page_size", 2000),
    )
    pairs = []
    for features in pages:
        pairs += [
            parse_feature(f, aoi.county_geoid) for f in features if f.get("geometry") is not None
        ]
        if features:
            echo(f"  {len(pairs):,} parcels so far")
    return merge_duplicates(pairs)


def load_parcels(
    conn: Connection, aoi: AOI, records: list[tuple[ParcelRecord, OwnerRecord]]
) -> int:
    """Replace the county's parcels and owners. Returns the number of parcels."""
    if not records:
        raise RuntimeError(f"The parcel layer returned no parcels for {aoi.name}.")
    record_source(conn, load_data_source(SOURCE_KEY))
    conn.execute(text("DELETE FROM parcels WHERE county_geoid = :g"), {"g": aoi.county_geoid})
    conn.execute(
        text(
            """
            INSERT INTO parcels (gislink, parcel_id, county_geoid, county_name, acres,
                                 deed_acres, parcel_type, tpad_url, geom, source_key)
            VALUES (:gislink, :parcel_id, :county_geoid, :county_name, :acres, :deed_acres,
                    :parcel_type, :tpad_url, ST_GeomFromWKB(:wkb, 4326), :source_key)
            """
        ),
        [
            {
                **{k: v for k, v in p.__dict__.items() if k != "geom"},
                "wkb": p.geom.wkb,
                "source_key": SOURCE_KEY,
            }
            for p, _ in records
        ],
    )
    conn.execute(
        text(
            """
            INSERT INTO parcel_owners (gislink, owner, owner2, property_address)
            VALUES (:gislink, :owner, :owner2, :property_address)
            """
        ),
        [o.__dict__ for _, o in records],
    )
    return len(records)
