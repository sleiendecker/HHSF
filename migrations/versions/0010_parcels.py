"""Property parcels, their owners (kept apart), and which parcels each corridor is on.

- `parcels`: outlines, assessor parcel ID and acreage from the Tennessee Comptroller
  (`hhsf ingest parcels`). Served as map tiles.
- `parcel_owners`: owner names and property address for each parcel. Personal
  information: martin/martin.yaml must never list this table, and nothing reads it
  but the local exports.
- `corridor_parcels`: the parcels each corridor's stretch crosses (or runs along:
  streams are often property lines, so both sides count).
- `corridor_turbine_parcels`: the parcel at the bottom of each stretch, where the
  turbine would go. If that point falls in a gap between parcels (a road, a river
  bed), the nearest parcel within 100 m, with how far it is.

The two link views are computed when read, so they always match the latest parcel
ingest without a separate step.

`latest_hydro_scores` gains the turbine parcel's ID and acreage and the number of
parcels the stretch crosses, for the map popup. Never owner fields.

Revision ID: 0010
Revises: 0009
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

# How far from the turbine point to look for a parcel when it isn't inside one.
TURBINE_SEARCH_M = 100
TURBINE_SEARCH_DEG = 0.002  # more than 100 m in any direction at Tennessee's latitude

# The 0009 view, unchanged except that it's a subquery now (see upgrade()).
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


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE parcels (
            gislink      TEXT PRIMARY KEY,          -- the Comptroller's parcel key
            parcel_id    TEXT NOT NULL,             -- control map, group, parcel: '048M D 001.10'
            county_geoid VARCHAR(5) NOT NULL,
            county_name  TEXT,
            acres        DOUBLE PRECISION NOT NULL, -- measured from the outline (EPSG:5070)
            deed_acres   DOUBLE PRECISION,          -- as recorded; NULL where not recorded
            parcel_type  INTEGER,
            tpad_url     TEXT,                      -- the parcel's assessment record
            geom         GEOMETRY(MULTIPOLYGON, 4326) NOT NULL,
            source_key   TEXT NOT NULL REFERENCES data_sources(source_key),
            ingested_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX parcels_geom_idx ON parcels USING GIST (geom)")
    op.execute("CREATE INDEX parcels_county_idx ON parcels (county_geoid)")
    op.execute(
        """
        CREATE TABLE parcel_owners (
            gislink          TEXT PRIMARY KEY REFERENCES parcels(gislink) ON DELETE CASCADE,
            owner            TEXT,
            owner2           TEXT,
            property_address TEXT
        )
        """
    )
    op.execute(
        "COMMENT ON TABLE parcel_owners IS 'Personal information. Local exports only: never "
        "publish in martin/martin.yaml or commit.'"
    )

    op.execute(
        """
        CREATE VIEW corridor_parcels AS
        SELECT c.corridor_id, p.gislink, p.parcel_id, p.acres, p.deed_acres,
               ST_Length(ST_Intersection(p.geom, c.geom)::geography) AS length_m
        FROM hydro_corridors c
        JOIN parcels p ON ST_Intersects(p.geom, c.geom)
        """
    )
    # NHDPlus draws flowlines upstream to downstream: the last point is the bottom of
    # the stretch. Inside a parcel the distance is 0; on a shared line, the bigger
    # parcel wins.
    op.execute(
        f"""
        CREATE VIEW corridor_turbine_parcels AS
        SELECT c.corridor_id, t.gislink, t.parcel_id, t.acres, t.deed_acres, t.distance_m
        FROM hydro_corridors c
        CROSS JOIN LATERAL (
            SELECT ST_EndPoint(ST_GeometryN(c.geom, ST_NumGeometries(c.geom))) AS pt
        ) o
        CROSS JOIN LATERAL (
            SELECT p.gislink, p.parcel_id, p.acres, p.deed_acres,
                   ST_Distance(p.geom::geography, o.pt::geography) AS distance_m
            FROM parcels p
            -- The first test (in degrees, generous) lets the spatial index find candidates;
            -- the second is the real distance in meters.
            WHERE ST_DWithin(p.geom, o.pt, {TURBINE_SEARCH_DEG})
              AND ST_DWithin(p.geom::geography, o.pt::geography, {TURBINE_SEARCH_M})
            ORDER BY distance_m, p.acres DESC
            LIMIT 1
        ) t
        """
    )

    # A view can't change in place except by adding columns at the end, and the parcel
    # lookups should only run for the rows a map tile asks for: the old view becomes a
    # subquery, so the tile's bounding box filters it before the lookups run.
    op.execute("DROP VIEW latest_hydro_scores")
    op.execute(
        f"""
        CREATE VIEW latest_hydro_scores AS
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
    )


def downgrade() -> None:
    op.execute("DROP VIEW latest_hydro_scores")
    op.execute(f"CREATE VIEW latest_hydro_scores AS {VIEW_0009}")
    op.execute("DROP VIEW corridor_turbine_parcels")
    op.execute("DROP VIEW corridor_parcels")
    op.execute("DROP TABLE parcel_owners")
    op.execute("DROP TABLE parcels")
