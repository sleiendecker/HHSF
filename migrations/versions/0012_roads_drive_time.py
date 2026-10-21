"""Roads, the drive-time area from Chattanooga, and each corridor's road and drive time.

- `roads`: drivable roads from Census TIGER/Line (`hhsf ingest roads`), with their
  Census class (MTFCC). Served as map tiles.
- `drive_time_areas`: the area within the D-02 drive of the origin (downtown
  Chattanooga), from Valhalla (`hhsf analyze drive-area`). One at a time. Served as
  map tiles.
- `drive_time_area_counties`: the counties that area touches, and how much of each.
- `corridor_drive_times`: minutes and km from the origin to each corridor's turbine
  end (`hhsf analyze drive-time`), and how far the road point the drive ends at is from
  the turbine end. Saved, not computed when read: each one is a call to Valhalla. NULL
  minutes: Valhalla found no route.
- `corridor_turbine_roads`: the nearest road to each turbine end, and the nearest
  public one. Computed when read, like the parcel and flood links (0010, 0011).
  Interstates and ramps don't count: no driveway can join them.

`latest_hydro_scores` gains the turbine end's nearest road, its drive time, and
whether it's inside the drive-time area, for the map popup.

Revision ID: 0012
Revises: 0011
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

# The 0011 view, unchanged except that it's a subquery now (see upgrade()). It has the
# 0010 view inside it, which has the 0009 one.
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


def _nearest_road(where: str) -> str:
    """The nearest road to the turbine point `o.pt` among roads matching `where`.

    The index finds the 8 nearest in degrees, which stretch east-west at this latitude;
    the nearest of those in meters is the answer.
    """
    return f"""
        SELECT k.road_id, k.name, k.road_class, k.public,
               ST_Distance(k.geom::geography, o.pt::geography) AS distance_m
        FROM (
            SELECT r.road_id, r.name, r.road_class, r.public, r.geom
            FROM roads r WHERE {where}
            ORDER BY r.geom <-> o.pt
            LIMIT 8
        ) k
        ORDER BY distance_m
        LIMIT 1
    """


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE roads (
            road_id        BIGSERIAL PRIMARY KEY,
            linearid       TEXT NOT NULL,          -- Census road ID; repeats across counties
            county_geoid   VARCHAR(5) NOT NULL,    -- the county file it came from
            name           TEXT,                   -- FULLNAME: 'Old York Hwy N'
            route_type     TEXT,                   -- RTTYP: I, U, S, C, M, O
            mtfcc          TEXT NOT NULL,          -- Census class: S1400 local road, ...
            road_class     TEXT NOT NULL,          -- hhsf.ingest.roads.ROAD_CLASSES key
            public         BOOLEAN NOT NULL,       -- not a 4WD trail, private or parking lot road
            limited_access BOOLEAN NOT NULL,       -- interstates and ramps
            geom           GEOMETRY(MULTILINESTRING, 4326) NOT NULL,
            source_key     TEXT NOT NULL REFERENCES data_sources(source_key),
            ingested_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX roads_geom_idx ON roads USING GIST (geom)")
    op.execute("CREATE INDEX roads_county_idx ON roads (county_geoid)")

    op.execute(
        """
        CREATE TABLE drive_time_areas (
            area_id        SERIAL PRIMARY KEY,
            origin_key     TEXT NOT NULL,          -- config/drive_time.yaml origin.key
            origin_name    TEXT NOT NULL,
            origin         GEOMETRY(POINT, 4326) NOT NULL,
            minutes        INTEGER NOT NULL,
            costing        TEXT NOT NULL,          -- Valhalla profile: auto
            geom           GEOMETRY(MULTIPOLYGON, 4326) NOT NULL,
            engine_version TEXT,                   -- 'Valhalla 3.9.1, road graph built ...'
            source_key     TEXT NOT NULL REFERENCES data_sources(source_key),
            built_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX drive_time_areas_geom_idx ON drive_time_areas USING GIST (geom)")
    # The tile server warns about any geometry column without one, even the origin point.
    op.execute("CREATE INDEX drive_time_areas_origin_idx ON drive_time_areas USING GIST (origin)")
    op.execute(
        """
        CREATE TABLE drive_time_area_counties (
            area_id      INTEGER NOT NULL REFERENCES drive_time_areas ON DELETE CASCADE,
            geoid        VARCHAR(5) NOT NULL,
            state_code   VARCHAR(2) NOT NULL,
            county_name  TEXT NOT NULL,
            county_sqkm  DOUBLE PRECISION NOT NULL,
            inside_sqkm  DOUBLE PRECISION NOT NULL,  -- how much of the county is in the area
            PRIMARY KEY (area_id, geoid)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE corridor_drive_times (
            corridor_id  BIGINT PRIMARY KEY REFERENCES hydro_corridors ON DELETE CASCADE,
            origin_key   TEXT NOT NULL,
            minutes      DOUBLE PRECISION,         -- NULL: no route found
            distance_km  DOUBLE PRECISION,
            road_point_m DOUBLE PRECISION,         -- turbine end to where the drive ends
            any_road     BOOLEAN NOT NULL DEFAULT false,  -- ends on a track: no public road route
            computed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    # NHDPlus draws flowlines upstream to downstream: the last point is the bottom of
    # the stretch (as in corridor_turbine_parcels). No row: no roads are loaded.
    op.execute(
        f"""
        CREATE VIEW corridor_turbine_roads AS
        SELECT c.corridor_id,
               n.road_id, n.name, n.road_class, n.public, n.distance_m,
               p.road_id AS public_road_id, p.name AS public_name,
               p.road_class AS public_road_class, p.distance_m AS public_distance_m
        FROM hydro_corridors c
        CROSS JOIN LATERAL (
            SELECT ST_EndPoint(ST_GeometryN(c.geom, ST_NumGeometries(c.geom))) AS pt
        ) o
        CROSS JOIN LATERAL ({_nearest_road("NOT r.limited_access")}) n
        LEFT JOIN LATERAL ({_nearest_road("r.public AND NOT r.limited_access")}) p ON true
        """
    )

    # As in 0010 and 0011: the old view becomes a subquery, so a map tile's bounding box
    # filters it before the road lookups run.
    op.execute("DROP VIEW latest_hydro_scores")
    op.execute(
        f"""
        CREATE VIEW latest_hydro_scores AS
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
    )


def downgrade() -> None:
    op.execute("DROP VIEW latest_hydro_scores")
    op.execute(f"CREATE VIEW latest_hydro_scores AS {VIEW_0011}")
    op.execute("DROP VIEW corridor_turbine_roads")
    op.execute("DROP TABLE corridor_drive_times")
    op.execute("DROP TABLE drive_time_area_counties")
    op.execute("DROP TABLE drive_time_areas")
    op.execute("DROP TABLE roads")
