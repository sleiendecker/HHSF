"""FEMA flood zones, and which ones each corridor's turbine end and stretch sit in.

- `flood_zones`: zones from FEMA's National Flood Hazard Layer (`hhsf ingest flood`),
  each stored as pieces of at most 256 corners (see hhsf.ingest.flood) that keep the
  zone's ID. Served as map tiles.
- `corridor_flood_zones`: the zones each corridor's stretch runs through, with how
  much of the stretch is in each.
- `corridor_turbine_flood_zones`: the zone at the bottom of each stretch, where the
  turbine would go. Where zones meet, the more hazardous one: floodway, then Special
  Flood Hazard Area, then the rest. No row means no zone is mapped at that point.

The two link views are computed when read, like the parcel ones (0010).

`latest_hydro_scores` gains the turbine end's zone and how much of the stretch lies in a
Special Flood Hazard Area, for the map popup.

Revision ID: 0011
Revises: 0010
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

# The 0010 view, unchanged except that it's a subquery now (see upgrade()).
VIEW_0009 = """
    SELECT DISTINCT ON (c.nhdplusid)
        c.corridor_id, c.geom, r.aoi_key, s.run_id AS score_run_id,
        c.run_id AS hydro_run_id, c.nhdplusid, st.stream_name, s.status, s.tier,
        s.rank, s.tier_rank, s.hydro_score, s.head_subscore, s.power_subscore,
        s.net_head_ft, c.firm_power_w, c.firm_flow_cfs, c.flow_method,
        c.flow_confidence, s.explanation->>'summary' AS summary,
        array_to_string(c.flow_flags, ',') AS flow_flags,
        c.flow_reference_gauge,
        (s.explanation->'factors')::text AS factors,
        c.head_method,
        c.length_m AS corridor_length_m,
        st.length_m AS reach_length_m,
        st.max_elev_m - st.min_elev_m AS reach_drop_m,
        co.aoi_name
    FROM hydro_scores s
    JOIN analysis_runs r ON r.run_id = s.run_id
    JOIN hydro_corridors c ON c.corridor_id = s.corridor_id
    JOIN streams st ON st.stream_id = c.stream_id
    LEFT JOIN counties co ON co.aoi_key = r.aoi_key
    WHERE s.run_id IN (
        SELECT max(run_id) FROM analysis_runs
        WHERE analysis = 'hydro_score' AND status = 'finished'
        GROUP BY aoi_key
    )
    ORDER BY c.nhdplusid,
             ST_Intersects(co.geom, ST_PointOnSurface(c.geom)) DESC NULLS LAST,
             s.run_id DESC
"""

VIEW_0010 = f"""
    SELECT v.*,
           tp.parcel_id AS turbine_parcel_id,
           tp.acres AS turbine_parcel_acres,
           tp.deed_acres AS turbine_parcel_deed_acres,
           tp.distance_m AS turbine_parcel_distance_m,
           cp.parcels_crossed
    FROM ({VIEW_0009}) v
    LEFT JOIN corridor_turbine_parcels tp ON tp.corridor_id = v.corridor_id
    LEFT JOIN LATERAL (
        SELECT count(*)::int AS parcels_crossed
        FROM corridor_parcels x WHERE x.corridor_id = v.corridor_id
    ) cp ON true
"""


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE flood_zones (
            flood_zone_id BIGSERIAL PRIMARY KEY,  -- one piece of a zone
            fld_ar_id     TEXT NOT NULL,          -- FEMA's zone ID; shared by its pieces
            dfirm_id      TEXT NOT NULL,          -- the flood map: '47153C' (countywide)
            county_geoid  VARCHAR(5) NOT NULL,
            zone          TEXT NOT NULL,          -- FLD_ZONE: A, AE, AH, AO, X, ...
            subtype       TEXT,                   -- ZONE_SUBTY: FLOODWAY, 0.2 PCT ..., ...
            sfha          BOOLEAN NOT NULL,       -- Special Flood Hazard Area (1% annual)
            floodway      BOOLEAN NOT NULL,
            geom          GEOMETRY(MULTIPOLYGON, 4326) NOT NULL,
            source_key    TEXT NOT NULL REFERENCES data_sources(source_key),
            ingested_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX flood_zones_geom_idx ON flood_zones USING GIST (geom)")
    op.execute("CREATE INDEX flood_zones_county_idx ON flood_zones (county_geoid)")

    op.execute(
        """
        CREATE VIEW corridor_flood_zones AS
        SELECT c.corridor_id, f.fld_ar_id, f.zone, f.subtype, f.sfha, f.floodway,
               sum(ST_Length(ST_Intersection(f.geom, c.geom)::geography)) AS length_m
        FROM hydro_corridors c
        JOIN flood_zones f ON ST_Intersects(f.geom, c.geom)
        GROUP BY c.corridor_id, f.fld_ar_id, f.zone, f.subtype, f.sfha, f.floodway
        """
    )
    # NHDPlus draws flowlines upstream to downstream: the last point is the bottom of
    # the stretch (as in corridor_turbine_parcels).
    op.execute(
        """
        CREATE VIEW corridor_turbine_flood_zones AS
        SELECT c.corridor_id, t.fld_ar_id, t.zone, t.subtype, t.sfha, t.floodway
        FROM hydro_corridors c
        CROSS JOIN LATERAL (
            SELECT ST_EndPoint(ST_GeometryN(c.geom, ST_NumGeometries(c.geom))) AS pt
        ) o
        CROSS JOIN LATERAL (
            SELECT f.fld_ar_id, f.zone, f.subtype, f.sfha, f.floodway
            FROM flood_zones f
            WHERE ST_Intersects(f.geom, o.pt)
            ORDER BY f.floodway DESC, f.sfha DESC, f.zone
            LIMIT 1
        ) t
        """
    )

    # As in 0010: the old view becomes a subquery, so a map tile's bounding box filters
    # it before the flood lookups run.
    op.execute("DROP VIEW latest_hydro_scores")
    op.execute(
        f"""
        CREATE VIEW latest_hydro_scores AS
        SELECT v.*,
               tf.zone AS turbine_flood_zone,
               tf.subtype AS turbine_flood_subtype,
               tf.sfha AS turbine_in_sfha,
               cf.stretch_sfha_m
        FROM ({VIEW_0010}) v
        LEFT JOIN corridor_turbine_flood_zones tf ON tf.corridor_id = v.corridor_id
        -- 0 where the stretch is mapped but outside the SFHA; NULL where nothing is mapped.
        LEFT JOIN LATERAL (
            SELECT CASE WHEN count(*) > 0
                        THEN coalesce(sum(x.length_m) FILTER (WHERE x.sfha), 0) END
                   AS stretch_sfha_m
            FROM corridor_flood_zones x WHERE x.corridor_id = v.corridor_id
        ) cf ON true
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW latest_hydro_scores")
    op.execute(f"CREATE VIEW latest_hydro_scores AS {VIEW_0010}")
    op.execute("DROP VIEW corridor_turbine_flood_zones")
    op.execute("DROP VIEW corridor_flood_zones")
    op.execute("DROP TABLE flood_zones")
