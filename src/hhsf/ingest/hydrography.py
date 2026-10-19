"""Perennial stream ingest from NHDPlus HR.

NHDPlus HR is the final, frozen release (USGS moved to 3DHP). It ships the
value-added attributes later stages need (drainage area, smoothed elevations,
flow estimates), so we pull them now. Keep NHDPlus specifics in this module so a
3DHP source can replace it later.
"""

from dataclasses import dataclass
from typing import Any

from shapely import wkb
from shapely.geometry import MultiLineString, shape
from shapely.geometry.base import BaseGeometry
from sqlalchemy import Connection, text

from hhsf.core import crs, units
from hhsf.core.config import AOI, DATA_DIR, load_data_source
from hhsf.core.db import record_source
from hhsf.ingest.arcgis import query_geojson_tiled

SOURCE_KEY = "nhdplus_hr"

# NHD feature codes: perennial is an attribute of the source data, not something we derive.
FCODE_PERENNIAL = 46006  # Stream/River, hydrographic category = perennial
FCODE_INTERMITTENT = 46003
FCODE_EPHEMERAL = 46007

FIELDS = [
    "nhdplusid",
    "permanent_identifier",
    "gnis_name",
    "fcode",
    "streamorde",
    "totdasqkm",
    "maxelevsmo",
    "minelevsmo",
    "slope",
    "qema",
]

# NHDPlus uses negative sentinel values (e.g. -9998) for "not computed".
_MISSING_BELOW = -9000


def is_perennial(fcode: int | None) -> bool:
    return fcode == FCODE_PERENNIAL


@dataclass
class StreamRecord:
    nhdplusid: int
    permanent_identifier: str | None
    stream_name: str | None
    fcode: int
    stream_order: int | None
    length_m: float
    drainage_area_sqkm: float | None
    max_elev_m: float | None
    min_elev_m: float | None
    slope: float | None
    mean_flow_cfs: float | None
    geom: MultiLineString


def _value(props: dict[str, Any], key: str) -> float | None:
    v = props.get(key)
    if v is None or v < _MISSING_BELOW:
        return None
    return float(v)


def _as_multiline(geom: BaseGeometry) -> MultiLineString:
    if geom.geom_type == "LineString":
        return MultiLineString([geom])
    if geom.geom_type == "MultiLineString":
        return geom
    raise ValueError(f"Unexpected flowline geometry: {geom.geom_type}")


def parse_feature(feature: dict[str, Any]) -> StreamRecord:
    props = feature["properties"]
    geom = _as_multiline(shape(feature["geometry"]))
    max_cm = _value(props, "maxelevsmo")
    min_cm = _value(props, "minelevsmo")
    return StreamRecord(
        nhdplusid=int(props["nhdplusid"]),
        permanent_identifier=props.get("permanent_identifier"),
        stream_name=props.get("gnis_name"),
        fcode=int(props["fcode"]),
        stream_order=props.get("streamorde"),
        length_m=crs.length_m(geom),
        drainage_area_sqkm=_value(props, "totdasqkm"),
        max_elev_m=units.cm_to_m(max_cm) if max_cm is not None else None,
        min_elev_m=units.cm_to_m(min_cm) if min_cm is not None else None,
        slope=_value(props, "slope"),
        mean_flow_cfs=_value(props, "qema"),
        geom=geom,
    )


def county_area(conn: Connection, aoi: AOI) -> BaseGeometry:
    """The AOI's county boundary (storage CRS), loaded by the boundary ingest."""
    row = conn.execute(
        text("SELECT ST_AsBinary(geom) FROM counties WHERE geoid = :geoid"),
        {"geoid": aoi.county_geoid},
    ).first()
    if row is None:
        raise RuntimeError(f"No boundary for {aoi.name}. Run `hhsf ingest boundary` first.")
    return wkb.loads(bytes(row[0]))


def fetch_perennial(aoi: AOI, area: BaseGeometry) -> list[StreamRecord]:
    """Perennial flowlines touching the AOI (plus its buffer)."""
    source = load_data_source(SOURCE_KEY)
    keep_area = crs.to_analysis(area).buffer(aoi.buffer_m)
    features = query_geojson_tiled(
        source.url,
        {"where": f"fcode={FCODE_PERENNIAL}", "outFields": ",".join(FIELDS)},
        bbox=crs.to_storage(keep_area).bounds,
        cache_dir=DATA_DIR / "raw" / SOURCE_KEY / aoi.key,
    )
    records: dict[int, StreamRecord] = {}
    for feature in features:
        if feature.get("geometry") is None:
            continue
        record = parse_feature(feature)
        # Query boxes are rectangles; keep only reaches that touch the real area.
        if (
            record.nhdplusid not in records
            and is_perennial(record.fcode)
            and keep_area.intersects(crs.to_analysis(record.geom))
        ):
            records[record.nhdplusid] = record
    return list(records.values())


def load_streams(conn: Connection, records: list[StreamRecord]) -> int:
    """Upsert stream records keyed on nhdplusid. Re-running never duplicates rows."""
    record_source(conn, load_data_source(SOURCE_KEY))
    if not records:
        return 0
    conn.execute(
        text(
            """
            INSERT INTO streams (
                nhdplusid, permanent_identifier, stream_name, fcode, perennial, stream_order,
                length_m, drainage_area_sqkm, max_elev_m, min_elev_m, slope, mean_flow_cfs,
                geom, source_key
            ) VALUES (
                :nhdplusid, :permanent_identifier, :stream_name, :fcode, :perennial, :stream_order,
                :length_m, :drainage_area_sqkm, :max_elev_m, :min_elev_m, :slope, :mean_flow_cfs,
                ST_GeomFromWKB(:wkb, 4326), :source_key
            )
            ON CONFLICT (nhdplusid) DO UPDATE SET
                permanent_identifier = EXCLUDED.permanent_identifier,
                stream_name = EXCLUDED.stream_name,
                fcode = EXCLUDED.fcode,
                perennial = EXCLUDED.perennial,
                stream_order = EXCLUDED.stream_order,
                length_m = EXCLUDED.length_m,
                drainage_area_sqkm = EXCLUDED.drainage_area_sqkm,
                max_elev_m = EXCLUDED.max_elev_m,
                min_elev_m = EXCLUDED.min_elev_m,
                slope = EXCLUDED.slope,
                mean_flow_cfs = EXCLUDED.mean_flow_cfs,
                geom = EXCLUDED.geom,
                ingested_at = now()
            """
        ),
        [
            {
                "nhdplusid": r.nhdplusid,
                "permanent_identifier": r.permanent_identifier,
                "stream_name": r.stream_name,
                "fcode": r.fcode,
                "perennial": is_perennial(r.fcode),
                "stream_order": r.stream_order,
                "length_m": r.length_m,
                "drainage_area_sqkm": r.drainage_area_sqkm,
                "max_elev_m": r.max_elev_m,
                "min_elev_m": r.min_elev_m,
                "slope": r.slope,
                "mean_flow_cfs": r.mean_flow_cfs,
                "wkb": r.geom.wkb,
                "source_key": SOURCE_KEY,
            }
            for r in records
        ],
    )
    return len(records)
