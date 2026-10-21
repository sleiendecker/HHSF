"""Soils, and the buildable ground on each corridor's turbine parcel (PART 17).

- `soils`: USDA SSURGO soil map unit polygons (`hhsf ingest soils`), stored in pieces of
  at most 256 corners that keep the polygon's key (as flood zones are). Each piece
  carries its map unit's two septic ratings (the Tennessee rule and the national one,
  each the dominant condition: see hhsf.ingest.soils) and a few map unit summaries.
  Served as map tiles.
- `parcel_buildable`: for each turbine parcel, how much of it is gently sloped, outside
  FEMA's 100-year floodplain and on soils fit for a septic field, and that ground's
  outline (`hhsf analyze buildable`, config/buildability.yaml). Saved, not computed when
  read: it's raster work on the 10 m elevation model. A parcel re-ingest deletes its row
  (the outline may have changed); `make analyze` computes it again. Served as map tiles.
- `corridor_turbine_buildable`: each corridor's turbine parcel's buildable ground, and
  how far the nearest of it is from the turbine end. Computed when read. No row: no
  turbine parcel, or its buildable ground hasn't been computed.

`latest_hydro_scores` gains the turbine parcel's buildable acres and the distance to the
nearest buildable ground, for the map popup (and the turbine parcel's key, to join on).

Revision ID: 0013
Revises: 0012
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

# The 0012 view, unchanged except that it's a subquery now (see upgrade()). It has the
# 0011 view inside it, which has the 0010 one, which has the 0009 one.
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

VIEW_0011 = f"""
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

VIEW_0012 = f"""
    SELECT v.*,
           tr.distance_m AS turbine_road_m,
           tr.road_class AS turbine_road_class,
           tr.name AS turbine_road_name,
           tr.public_distance_m AS turbine_public_road_m,
           dt.minutes AS drive_minutes,
           dt.distance_km AS drive_km,
           da.in_drive_area
    FROM ({VIEW_0011}) v
    LEFT JOIN corridor_turbine_roads tr ON tr.corridor_id = v.corridor_id
    LEFT JOIN corridor_drive_times dt ON dt.corridor_id = v.corridor_id
    -- NULL when no drive-time area has been built.
    LEFT JOIN LATERAL (
        SELECT bool_or(ST_Intersects(
                   a.geom, ST_EndPoint(ST_GeometryN(v.geom, ST_NumGeometries(v.geom)))
               )) AS in_drive_area
        FROM drive_time_areas a
    ) da ON true
"""

# The same with the turbine parcel's key added, so its buildable ground is joined on it
# instead of looking the parcel up a second time (that cost ~40% more on a zoom 9 tile).
VIEW_0012_GISLINK = VIEW_0012.replace(
    "tp.distance_m AS turbine_parcel_distance_m,",
    "tp.distance_m AS turbine_parcel_distance_m, tp.gislink AS turbine_parcel_gislink,",
)
assert VIEW_0012_GISLINK != VIEW_0012


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE soils (
            soil_id             BIGSERIAL PRIMARY KEY,  -- one piece of a soil polygon
            mupolygonkey        BIGINT NOT NULL,        -- SSURGO polygon key; shared by its pieces
            mukey               TEXT NOT NULL,          -- SSURGO map unit key
            areasymbol          TEXT NOT NULL,          -- soil survey area: TN153
            county_geoid        VARCHAR(5) NOT NULL,    -- the AOI county it was loaded for
            survey_version      TEXT NOT NULL,          -- 'TN153 version 20 (8/28/2026)'
            musym               TEXT,                   -- map label: 'LnC'
            muname              TEXT,                   -- 'Lily loam, 5 to 12 percent slopes'
            septic_tn           TEXT NOT NULL,          -- Tennessee septic rule, dominant condition
            septic_tn_pct       DOUBLE PRECISION,       -- share of the map unit with that rating
            septic_national     TEXT NOT NULL,          -- national septic rule, dominant condition
            septic_national_pct DOUBLE PRECISION,
            hydric_pct          DOUBLE PRECISION,       -- share of the map unit that's hydric
            bedrock_min_cm      DOUBLE PRECISION,       -- shallowest depth to bedrock
            water_table_min_cm  DOUBLE PRECISION,       -- shallowest annual water table
            flooding            TEXT,                   -- flooding frequency: None, Rare, ...
            drainage            TEXT,                   -- drainage class: Well drained, ...
            geom                GEOMETRY(MULTIPOLYGON, 4326) NOT NULL,
            source_key          TEXT NOT NULL REFERENCES data_sources(source_key),
            ingested_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX soils_geom_idx ON soils USING GIST (geom)")
    op.execute("CREATE INDEX soils_county_idx ON soils (county_geoid)")

    op.execute(
        """
        CREATE TABLE parcel_buildable (
            gislink          TEXT PRIMARY KEY REFERENCES parcels(gislink) ON DELETE CASCADE,
            aoi_key          TEXT NOT NULL,             -- the AOI run that computed it
            parcel_acres     DOUBLE PRECISION NOT NULL, -- 10 m cells whose center is in the parcel
            analyzed_acres   DOUBLE PRECISION NOT NULL, -- of those, with a slope (elevation model)
            gentle_acres     DOUBLE PRECISION NOT NULL, -- slope at most max_slope_pct
            gentle_dry_acres DOUBLE PRECISION NOT NULL, -- and outside the 100-year floodplain
            septic_ok_acres  DOUBLE PRECISION NOT NULL, -- and on soils not excluded for septic
            no_soil_acres    DOUBLE PRECISION NOT NULL, -- gentle and dry, but no soils loaded there
            buildable_acres  DOUBLE PRECISION NOT NULL, -- of that, patches of min_patch_acres+
            patches          INTEGER NOT NULL,          -- how many such patches
            geom             GEOMETRY(MULTIPOLYGON, 4326),  -- the patches; NULL: none
            max_slope_pct    DOUBLE PRECISION NOT NULL, -- settings used: config/buildability.yaml
            septic_rating    TEXT NOT NULL,
            septic_excluded  TEXT[] NOT NULL,
            min_patch_acres  DOUBLE PRECISION NOT NULL,
            computed_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX parcel_buildable_geom_idx ON parcel_buildable USING GIST (geom)")

    # NHDPlus draws flowlines upstream to downstream: the last point is the bottom of
    # the stretch (as in corridor_turbine_parcels). 0 m: the turbine end is on it.
    op.execute(
        """
        CREATE VIEW corridor_turbine_buildable AS
        SELECT tp.corridor_id, b.gislink, b.parcel_acres, b.analyzed_acres, b.gentle_acres,
               b.gentle_dry_acres, b.septic_ok_acres, b.no_soil_acres, b.buildable_acres,
               b.patches,
               ST_Distance(b.geom::geography, o.pt::geography) AS nearest_buildable_m,
               b.computed_at
        FROM corridor_turbine_parcels tp
        JOIN hydro_corridors c ON c.corridor_id = tp.corridor_id
        JOIN parcel_buildable b ON b.gislink = tp.gislink
        CROSS JOIN LATERAL (
            SELECT ST_EndPoint(ST_GeometryN(c.geom, ST_NumGeometries(c.geom))) AS pt
        ) o
        """
    )

    # As in 0010-0012: the old view becomes a subquery, so a map tile's bounding box
    # filters it before the distances are measured. Same numbers as
    # corridor_turbine_buildable.
    op.execute("DROP VIEW latest_hydro_scores")
    op.execute(
        f"""
        CREATE VIEW latest_hydro_scores AS
        SELECT v.*,
               b.buildable_acres AS turbine_buildable_acres,
               b.gentle_acres AS turbine_gentle_acres,
               ST_Distance(
                   b.geom::geography,
                   ST_EndPoint(ST_GeometryN(v.geom, ST_NumGeometries(v.geom)))::geography
               ) AS nearest_buildable_m
        FROM ({VIEW_0012_GISLINK}) v
        LEFT JOIN parcel_buildable b ON b.gislink = v.turbine_parcel_gislink
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW latest_hydro_scores")
    op.execute(f"CREATE VIEW latest_hydro_scores AS {VIEW_0012}")
    op.execute("DROP VIEW corridor_turbine_buildable")
    op.execute("DROP TABLE parcel_buildable")
    op.execute("DROP TABLE soils")
