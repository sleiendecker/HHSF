"""Runs migrations and loads fixture streams into a throwaway PostGIS container."""

import pytest
from alembic import command
from alembic.config import Config
from shapely.geometry import box, mapping
from sqlalchemy import create_engine, text

from hhsf.core.config import AOI, REPO_ROOT
from hhsf.ingest import boundary, hydrography

pytestmark = pytest.mark.integration


@pytest.fixture
def engine(db_url):
    eng = create_engine(db_url)
    with eng.begin() as conn:
        conn.execute(text("TRUNCATE streams CASCADE"))
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
        sources = conn.execute(text("SELECT source_key FROM streams")).scalars().all()
        assert set(sources) == {"nhdplus_hr"}


def test_reload_does_not_duplicate(engine, sample_features):
    records = [hydrography.parse_feature(f) for f in sample_features[:2]]
    for _ in range(2):
        with engine.begin() as conn:
            hydrography.load_streams(conn, records)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM streams")).scalar() == 2


def test_reload_from_another_source_updates_provenance(engine, sample_features):
    records = [hydrography.parse_feature(f) for f in sample_features[:2]]
    with engine.begin() as conn:
        hydrography.load_streams(conn, records)
        hydrography.load_streams(conn, records, source_key="nhdplus_hr_bulk")
        keys = conn.execute(text("SELECT DISTINCT source_key FROM streams")).scalars().all()
    assert keys == ["nhdplus_hr_bulk"]


def test_county_remembers_which_aoi_loaded_it(db_url):
    eng = create_engine(db_url)
    feature = {
        "type": "Feature",
        "geometry": mapping(box(-85.7, 35.3, -85.5, 35.5)),
        "properties": {"GEOID": "99997", "STATE": "99", "NAME": "Sample"},
    }
    for name in ("Sample County", "Sample County, TN"):  # a reload updates the AOI
        with eng.begin() as conn:
            boundary.load_county(conn, feature, AOI(key="sample", name=name, county_geoid="99997"))
    with eng.connect() as conn:
        row = conn.execute(
            text(
                "SELECT aoi_key, aoi_name, ST_GeometryType(geom) FROM counties"
                " WHERE geoid = '99997'"
            )
        ).one()
    assert tuple(row) == ("sample", "Sample County, TN", "ST_MultiPolygon")
