"""Provenance, county boundaries, and stream flowlines.

Geometry is stored in EPSG:4326 (see hhsf.core.crs). Every geometry column
gets a GiST index in the same migration that creates it.

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE data_sources (
            source_key   TEXT PRIMARY KEY,
            description  TEXT,
            license      TEXT,
            version      TEXT,
            url          TEXT,
            retrieved_at TIMESTAMPTZ
        )
        """
    )

    op.execute(
        """
        CREATE TABLE counties (
            county_id   SERIAL PRIMARY KEY,
            geoid       VARCHAR(5) NOT NULL UNIQUE,
            state_code  VARCHAR(2) NOT NULL,
            county_name VARCHAR(100) NOT NULL,
            geom        GEOMETRY(MULTIPOLYGON, 4326) NOT NULL,
            source_key  TEXT NOT NULL REFERENCES data_sources(source_key),
            ingested_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX counties_geom_idx ON counties USING GIST (geom)")

    # Attribute names follow NHDPlus HR, with units made explicit.
    op.execute(
        """
        CREATE TABLE streams (
            stream_id            BIGSERIAL PRIMARY KEY,
            nhdplusid            BIGINT NOT NULL UNIQUE,
            permanent_identifier TEXT,
            stream_name          TEXT,
            fcode                INTEGER NOT NULL,
            perennial            BOOLEAN NOT NULL,
            stream_order         SMALLINT,
            length_m             DOUBLE PRECISION NOT NULL,  -- computed in EPSG:5070
            drainage_area_sqkm   DOUBLE PRECISION,           -- NHDPlus totdasqkm
            max_elev_m           DOUBLE PRECISION,           -- NHDPlus maxelevsmo (cm -> m)
            min_elev_m           DOUBLE PRECISION,           -- NHDPlus minelevsmo (cm -> m)
            slope                DOUBLE PRECISION,           -- NHDPlus slope (m/m)
            mean_flow_cfs        DOUBLE PRECISION,           -- NHDPlus qema, mean annual
            geom                 GEOMETRY(MULTILINESTRING, 4326) NOT NULL,
            source_key           TEXT NOT NULL REFERENCES data_sources(source_key),
            ingested_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX streams_geom_idx ON streams USING GIST (geom)")


def downgrade() -> None:
    op.execute("DROP TABLE streams")
    op.execute("DROP TABLE counties")
    op.execute("DROP TABLE data_sources")
