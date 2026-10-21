"""County boundary ingest from Census TIGERweb."""

from shapely.geometry import MultiPolygon, shape
from sqlalchemy import Connection, text

from hhsf.core.config import AOI, DATA_DIR, load_data_source
from hhsf.core.db import record_source
from hhsf.ingest.arcgis import query_geojson

SOURCE_KEY = "tiger_counties"


def fetch_county(aoi: AOI) -> dict:
    source = load_data_source(SOURCE_KEY)
    features = query_geojson(
        source.url,
        {"where": f"GEOID='{aoi.county_geoid}'", "outFields": "GEOID,NAME,STATE"},
        cache_dir=DATA_DIR / "raw" / SOURCE_KEY / aoi.key,
    )
    if len(features) != 1:
        raise RuntimeError(f"Expected 1 county for GEOID {aoi.county_geoid}, got {len(features)}")
    return features[0]


def load_county(conn: Connection, feature: dict, aoi: AOI) -> None:
    """Save the county and which AOI it is (one AOI per county), for the map's county names."""
    geom = shape(feature["geometry"])
    if geom.geom_type == "Polygon":
        geom = MultiPolygon([geom])
    props = feature["properties"]
    record_source(conn, load_data_source(SOURCE_KEY))
    conn.execute(
        text(
            """
            INSERT INTO counties
                (geoid, state_code, county_name, geom, source_key, aoi_key, aoi_name)
            VALUES (:geoid, :state, :name, ST_GeomFromWKB(:wkb, 4326), :source_key, :aoi_key,
                    :aoi_name)
            ON CONFLICT (geoid) DO UPDATE SET
                state_code = EXCLUDED.state_code,
                county_name = EXCLUDED.county_name,
                geom = EXCLUDED.geom,
                aoi_key = EXCLUDED.aoi_key,
                aoi_name = EXCLUDED.aoi_name,
                ingested_at = now()
            """
        ),
        {
            "geoid": props["GEOID"],
            "state": props["STATE"],
            "name": props["NAME"],
            "wkb": geom.wkb,
            "source_key": SOURCE_KEY,
            "aoi_key": aoi.key,
            "aoi_name": aoi.name,
        },
    )
