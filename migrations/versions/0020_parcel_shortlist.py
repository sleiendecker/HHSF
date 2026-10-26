"""The parcel shortlist and its research status (PART 23).

- `parcel_shortlist`: one row per turbine parcel across every analyzed county, from each
  county's latest scoring run, ranked by the hydro score (hhsf.analysis.shortlist), with
  what the map app filters on (buildable acres, drive time, ...). Keyed
  on the parcel's own county and parcel ID: parcel IDs repeat across counties, and a
  stretch analyzed for one county can end on a parcel in the next. Rebuilt with the map's
  saved layer (`hhsf db refresh-map`, which every step that changes it runs).
- `parcel_research`: what the owner has made of each parcel (new, interested, ruled out,
  visited) and a note. Keyed the same way and not tied to `parcels`, so it outlives
  re-ingests and re-runs.

The map app reads them through the `api` schema, served by PostgREST (docker-compose.yml,
service `api`). PostgREST logs in as `hhsf_api` and works as `web_anon`, which can read the
`api` views and call `api.set_research`, and nothing else: no owner names, addresses or
assessor links. Both roles are cluster-wide, so they're created only if missing.

Revision ID: 0020
Revises: 0019
"""

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

STATUSES = ("new", "interested", "ruled_out", "visited")

# The list view: everything the list panel filters and sorts on, and the research status.
LIST_COLUMNS = """
    s.county_geoid, s.parcel_id, s.county_name, s.rank, s.overall_score,
    s.hydro_subscore, s.buildability_subscore, s.agriculture_subscore,
    s.privacy_subscore, s.logistics_subscore, s.power_group, s.tier,
    s.corridor_id, s.stream_name, s.firm_power_w, s.max_power_w, s.net_head_ft,
    s.stretches, s.acres, s.buildable_acres, s.drive_minutes, s.open_acres,
    s.open_cropland_acres, s.prime_acres, s.buildings_quarter_mile, s.road_frontage_ft,
    s.public_road_ft, s.turbine_in_sfha, s.subdivision_score, s.flags, s.bbox,
    coalesce(r.status, 'new') AS status, r.note, r.updated_at AS status_updated_at
"""


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE parcel_shortlist (
            county_geoid    VARCHAR(5) NOT NULL,    -- the parcel's county (parcels.county_geoid)
            parcel_id       TEXT NOT NULL,          -- the county's parcel ID
            gislink         TEXT NOT NULL,          -- parcels.gislink when it was built
            county_name     TEXT,
            rank            INTEGER NOT NULL,       -- 1 = best hydro score, every county
            overall_score   DOUBLE PRECISION,       -- 0-100, FR-7, of its best stretch
            -- The five parts of it. The hydro part is what the list ranks on: the hydro
            -- score, or for head-only and low-flow stretches the overall score's stand-in.
            hydro_subscore  DOUBLE PRECISION,
            buildability_subscore DOUBLE PRECISION,
            agriculture_subscore  DOUBLE PRECISION,
            privacy_subscore      DOUBLE PRECISION,
            logistics_subscore    DOUBLE PRECISION,
            power_group     TEXT NOT NULL CHECK (power_group IN ('kw', 'head_only', 'low_flow')),
            tier            TEXT NOT NULL,          -- hydro_scores.tier of its best stretch
            corridor_id     BIGINT NOT NULL,        -- its best stretch
            aoi_key         TEXT NOT NULL,          -- the AOI run that scored it
            score_run_id    BIGINT NOT NULL,
            stream_name     TEXT,
            firm_power_w    DOUBLE PRECISION,       -- of its best stretch
            max_power_w     DOUBLE PRECISION,       -- of any stretch on the parcel
            net_head_ft     DOUBLE PRECISION,
            stretches       INTEGER NOT NULL,       -- ranked stretches ending on it
            acres           DOUBLE PRECISION,       -- measured from the outline
            deed_acres      DOUBLE PRECISION,
            buildable_acres DOUBLE PRECISION,
            drive_minutes   DOUBLE PRECISION,       -- from downtown Chattanooga
            open_acres      DOUBLE PRECISION,       -- field, pasture, crops (NLCD 2021)
            open_cropland_acres DOUBLE PRECISION,   -- of it, on class 1-3 soils
            prime_acres     DOUBLE PRECISION,       -- prime + statewide-importance farmland
            buildings_quarter_mile INTEGER,         -- other buildings near the home site
            road_frontage_ft DOUBLE PRECISION,      -- public road on or along the parcel
            public_road_ft  DOUBLE PRECISION,       -- turbine end to the nearest public road
            turbine_in_sfha BOOLEAN,                -- in the 100-year floodplain; NULL: unmapped
            subdivision_score DOUBLE PRECISION,     -- 0-100, FR-9 (not in the overall score)
            flags           TEXT[] NOT NULL,        -- hhsf.analysis.shortlist
            checks          JSONB NOT NULL,         -- the "check on the visit" list
            parts           JSONB NOT NULL,         -- the overall score's parts, explained
            bbox            DOUBLE PRECISION[] NOT NULL,  -- parcel and stretch, lon/lat
            PRIMARY KEY (county_geoid, parcel_id)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE parcel_research (
            county_geoid VARCHAR(5) NOT NULL,
            parcel_id    TEXT NOT NULL,
            status       TEXT NOT NULL DEFAULT 'new' CHECK (status IN {STATUSES}),
            note         TEXT,
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (county_geoid, parcel_id)
        )
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'web_anon') THEN
                CREATE ROLE web_anon NOLOGIN;
            END IF;
            -- Fixed password, like the database's own: every port is on 127.0.0.1 only.
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'hhsf_api') THEN
                CREATE ROLE hhsf_api LOGIN NOINHERIT PASSWORD 'hhsf_api';
            END IF;
        END $$
        """
    )
    op.execute("GRANT web_anon TO hhsf_api")
    op.execute("CREATE SCHEMA api")
    op.execute(
        f"""
        CREATE VIEW api.parcels AS
        SELECT {LIST_COLUMNS}
        FROM parcel_shortlist s
        LEFT JOIN parcel_research r USING (county_geoid, parcel_id)
        ORDER BY s.rank
        """
    )
    # One parcel's card: the list columns, its explanation and visit checks, and the parcel
    # and stretch outlines to highlight (simplified to about 5 m).
    op.execute(
        f"""
        CREATE VIEW api.parcel_detail AS
        SELECT {LIST_COLUMNS}, s.deed_acres, s.checks, s.parts,
               ST_AsGeoJSON(ST_SimplifyPreserveTopology(p.geom, 0.00005), 6)::json AS parcel_geom,
               ST_AsGeoJSON(c.geom, 6)::json AS stretch_geom
        FROM parcel_shortlist s
        LEFT JOIN parcel_research r USING (county_geoid, parcel_id)
        LEFT JOIN parcels p ON p.gislink = s.gislink
        LEFT JOIN hydro_corridors c ON c.corridor_id = s.corridor_id
        """
    )
    # The score runs behind the list, with the overall score's weights, for the explainer.
    op.execute(
        """
        CREATE VIEW api.score_runs AS
        SELECT a.aoi_key, a.run_id, a.scoring_profile_version AS profile, a.finished_at,
               a.params -> 'scoring_profile' -> 'overall' -> 'weights' AS weights
        FROM analysis_runs a
        WHERE a.run_id IN (SELECT DISTINCT score_run_id FROM parcel_shortlist)
        ORDER BY a.aoi_key
        """
    )
    op.execute(
        """
        CREATE FUNCTION api.set_research(
            county_geoid TEXT, parcel_id TEXT, status TEXT, note TEXT DEFAULT NULL
        ) RETURNS parcel_research
        LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
            INSERT INTO parcel_research AS r (county_geoid, parcel_id, status, note)
            VALUES ($1, $2, $3, nullif(btrim($4), ''))
            ON CONFLICT (county_geoid, parcel_id) DO UPDATE
                SET status = EXCLUDED.status, note = EXCLUDED.note, updated_at = now()
            RETURNING r.*
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION api.set_research(TEXT, TEXT, TEXT, TEXT) FROM PUBLIC")
    op.execute("GRANT USAGE ON SCHEMA api TO web_anon")
    op.execute("GRANT SELECT ON api.parcels, api.parcel_detail, api.score_runs TO web_anon")
    op.execute("GRANT EXECUTE ON FUNCTION api.set_research(TEXT, TEXT, TEXT, TEXT) TO web_anon")
    # A running PostgREST reads the schema when it starts: tell it to read it again.
    op.execute("NOTIFY pgrst, 'reload schema'")


def downgrade() -> None:
    op.execute("DROP SCHEMA api CASCADE")
    op.execute("DROP TABLE parcel_research")
    op.execute("DROP TABLE parcel_shortlist")
    # The roles stay: they're cluster-wide, and harmless with nothing granted.
