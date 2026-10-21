"""Flood zones and the corridor links in a throwaway PostGIS."""

import csv

import pytest
from shapely.geometry import Point, box
from sqlalchemy import text

from hhsf.analysis import export_run, scoring_run
from hhsf.ingest import flood
from tests.integration.test_hydro_db import AREA, engine  # noqa: F401  (fixture)
from tests.integration.test_scoring_db import PROFILE, hydro_run  # noqa: F401  (fixture)
from tests.unit.test_flood import feature

pytestmark = pytest.mark.integration

# The 100-year floodplain over the west half of the fixture streams, minimal hazard east.
WEST = feature("99999C_1", "AE", geom=box(-85.5, 35.3, -85.4, 35.5))
EAST = feature(
    "99999C_2", "X", "AREA OF MINIMAL FLOOD HAZARD", "F", geom=box(-85.4, 35.3, -85.3, 35.5)
)


def load(engine, *features):  # noqa: F811
    with engine.begin() as conn:
        flood.load_flood_zones(
            conn, AREA, [flood.parse_feature(f, AREA.county_geoid) for f in features]
        )


@pytest.fixture
def loaded(engine):  # noqa: F811
    load(engine, WEST, EAST)
    yield
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE flood_zones"))


def outlet(engine):  # noqa: F811
    """The first corridor's turbine end."""
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT corridor_id, ST_X(pt) x, ST_Y(pt) y FROM ("
                " SELECT corridor_id, ST_EndPoint(ST_GeometryN(geom, ST_NumGeometries(geom))) pt"
                " FROM hydro_corridors ORDER BY corridor_id LIMIT 1) o"
            )
        ).one()


def test_big_zones_are_stored_in_pieces_that_keep_their_id(engine, loaded):  # noqa: F811
    # A circle with ~1,000 corners: more than one 256-corner piece.
    ring = feature("99999C_3", "A", geom=Point(-85.0, 35.0).buffer(0.05, quad_segs=256))
    load(engine, WEST, ring)
    with engine.connect() as conn:
        pieces = dict(
            conn.execute(
                text("SELECT fld_ar_id, count(*) FROM flood_zones GROUP BY fld_ar_id")
            ).all()
        )
    # Reloading replaced the county's zones: EAST is gone.
    assert pieces.keys() == {"99999C_1", "99999C_3"}
    assert pieces["99999C_3"] > 1


def test_every_corridor_gets_its_flood_zone(engine, hydro_run, loaded):  # noqa: F811
    scoring_run.score_hydro(engine, AREA, PROFILE)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT turbine_flood_zone, turbine_in_sfha, stretch_sfha_m,"
                " corridor_length_m FROM latest_hydro_scores_live"
            )
        ).all()
    assert len(rows) == 2
    for zone, sfha, sfha_m, length_m in rows:
        assert zone in ("AE", "X")
        assert sfha == (zone == "AE")
        assert 0 <= sfha_m <= length_m + 1
        if sfha:
            assert sfha_m > 0


def test_floodway_wins_where_zones_meet(engine, hydro_run, loaded):  # noqa: F811
    corridor, x, y = outlet(engine)
    way = feature(
        "99999C_4", "AE", "FLOODWAY", geom=box(x - 0.001, y - 0.001, x + 0.001, y + 0.001)
    )
    load(engine, WEST, EAST, way)
    with engine.connect() as conn:
        zone = conn.execute(
            text("SELECT zone, floodway FROM corridor_turbine_flood_zones WHERE corridor_id = :c"),
            {"c": corridor},
        ).one()
    assert tuple(zone) == ("AE", True)


def test_export_flags_flood_zones_and_unmapped_ends(engine, hydro_run, loaded, tmp_path):  # noqa: F811
    # Only a zone far away: every turbine end is off the map.
    load(engine, feature("99999C_5", "A", geom=box(-80.0, 30.0, -79.9, 30.1)))
    scoring_run.score_hydro(engine, AREA, PROFILE)
    info, out_dir, _ = export_run.export_hydro(engine, AREA, out_root=tmp_path)

    assert info.flood_version and "downloaded" in info.flood_version
    with (out_dir / "all_corridors.csv").open() as f:
        rows = list(csv.DictReader(f))
    assert {r["flood_flags"] for r in rows} == {"flood_zone_not_mapped"}
    assert all(r["turbine_flood_zone"] == "" for r in rows)
    # The report counts ranked corridors: one of the two here.
    assert "| Turbine end not on any FEMA map | 1 |" in (out_dir / "report.md").read_text()
