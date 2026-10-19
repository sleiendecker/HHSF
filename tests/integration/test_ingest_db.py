"""Runs migrations and loads fixture streams into a throwaway PostGIS container."""

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from hhsf.core.config import REPO_ROOT
from hhsf.ingest import hydrography

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def db_url():
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("imresamu/postgis:17-3.5", driver="psycopg") as pg:
        url = pg.get_connection_url()
        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        cfg.set_main_option("sqlalchemy.url", url)
        command.upgrade(cfg, "head")
        yield url


@pytest.fixture
def engine(db_url):
    eng = create_engine(db_url)
    with eng.begin() as conn:
        conn.execute(text("TRUNCATE streams"))
    return eng


def test_migrations_downgrade_and_upgrade_cleanly(db_url):
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


def test_loads_only_perennial_in_storage_crs(engine, sample_features):
    records = [hydrography.parse_feature(f) for f in sample_features]
    perennial = [r for r in records if hydrography.is_perennial(r.fcode)]
    with engine.begin() as conn:
        hydrography.load_streams(conn, perennial)
        count, srids, all_perennial = conn.execute(
            text(
                "SELECT count(*), array_agg(DISTINCT ST_SRID(geom)), bool_and(perennial)"
                " FROM streams"
            )
        ).one()
    assert count == 2
    assert srids == [4326]
    assert all_perennial
    with engine.connect() as conn:
        assert conn.execute(text("SELECT source_key FROM data_sources")).scalars().all() == [
            "nhdplus_hr"
        ]


def test_reload_does_not_duplicate(engine, sample_features):
    records = [hydrography.parse_feature(f) for f in sample_features[:2]]
    for _ in range(2):
        with engine.begin() as conn:
            hydrography.load_streams(conn, records)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM streams")).scalar() == 2
