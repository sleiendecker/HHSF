"""Roads, the drive-time area and drive times in a throwaway PostGIS (no Valhalla)."""

import csv

import pytest
from shapely.geometry import LineString, MultiLineString, MultiPolygon, box
from sqlalchemy import text

from hhsf.analysis import drive_time, export_run, scoring_run
from hhsf.ingest import roads
from tests.integration.test_flood_db import outlet
from tests.integration.test_hydro_db import AREA, engine  # noqa: F401  (fixture)
from tests.integration.test_scoring_db import PROFILE, hydro_run  # noqa: F401  (fixture)
from tests.unit.test_drive_time import CFG

pytestmark = pytest.mark.integration

# About 111 m per 0.001 degree of latitude.
DEG_M = 111_000


def road(linearid, mtfcc, x, y, dy, name=None):
    """An east-west road `dy` degrees north of (x, y)."""
    line = LineString([(x - 0.01, y + dy), (x + 0.01, y + dy)])
    return roads.RoadRecord(linearid, AREA.county_geoid, name, None, mtfcc, MultiLineString([line]))


@pytest.fixture
def turbine(engine, hydro_run):  # noqa: F811
    """The first corridor's turbine end, with an interstate, a 4WD trail and a local road
    north of it, nearest first."""
    corridor, x, y = outlet(engine)
    with engine.begin() as conn:
        roads.load_roads(
            conn,
            AREA,
            [
                road("1", "S1100", x, y, 0.0005, "I- 24"),
                road("2", "S1500", x, y, 0.001),
                road("3", "S1400", x, y, 0.003, "Smith Mountain Rd"),
            ],
        )
    yield corridor, x, y
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE roads, drive_time_areas CASCADE"))


def test_nearest_road_skips_interstates_and_finds_the_public_one(engine, turbine):  # noqa: F811
    corridor, _, _ = turbine
    with engine.connect() as conn:
        r = conn.execute(
            text("SELECT * FROM corridor_turbine_roads WHERE corridor_id = :c"), {"c": corridor}
        ).one()
    assert (r.road_class, r.public) == ("4wd_trail", False)
    assert r.distance_m == pytest.approx(0.001 * DEG_M, rel=0.01)
    assert (r.public_road_class, r.public_name) == ("local", "Smith Mountain Rd")
    assert r.public_distance_m == pytest.approx(0.003 * DEG_M, rel=0.01)


def test_reloading_a_county_replaces_its_roads(engine, turbine):  # noqa: F811
    corridor, x, y = turbine
    with engine.begin() as conn:
        roads.load_roads(conn, AREA, [road("4", "S1200", x, y, 0.002, "US-127")])
        names = conn.execute(text("SELECT name FROM roads")).scalars().all()
    assert names == ["US-127"]


def save_area(engine, geom, times=None):  # noqa: F811
    """Save a drive-time area (and drive times for every corridor, if given)."""
    area = geom if isinstance(geom, MultiPolygon) else MultiPolygon([geom])
    share = drive_time.CountyShare("99999", "47", "Fixture", 100.0, 50.0)
    with engine.begin() as conn:
        drive_time.save_area(conn, CFG, area, [share], "Valhalla test")
        if times is not None:
            ids = conn.execute(text("SELECT corridor_id FROM hydro_corridors")).scalars().all()
            drive_time.save_drive_times(conn, CFG.origin.key, ids, [times] * len(ids))


def test_map_view_has_road_drive_time_and_area(engine, turbine):  # noqa: F811
    corridor, x, y = turbine
    scoring_run.score_hydro(engine, AREA, PROFILE)
    with engine.connect() as conn:
        before = conn.execute(text("SELECT in_drive_area FROM latest_hydro_scores_live")).scalars()
        assert set(before) == {None}  # no area built yet
    # The area covers the first corridor's turbine end only.
    near = box(x - 0.0001, y - 0.0001, x + 0.0001, y + 0.0001)
    save_area(engine, near, drive_time.DriveTime(42.0, 50.0, 10.0))
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT corridor_id, turbine_road_class, turbine_public_road_m, drive_minutes,"
                " drive_km, in_drive_area FROM latest_hydro_scores_live"
            )
        ).all()
    assert len(rows) == 2
    for r in rows:
        assert r.turbine_road_class is not None and r.turbine_public_road_m > 0
        assert (r.drive_minutes, r.drive_km) == (42.0, 50.0)
        assert r.in_drive_area == (r.corridor_id == corridor)


def test_a_new_area_replaces_the_old_one_and_its_counties(engine, turbine):  # noqa: F811
    save_area(engine, box(0, 0, 1, 1))
    save_area(engine, box(1, 1, 2, 2))
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM drive_time_areas")).scalar() == 1
        assert conn.execute(text("SELECT count(*) FROM drive_time_area_counties")).scalar() == 1
        version = conn.execute(
            text("SELECT version FROM data_sources WHERE source_key = 'osm_valhalla'")
        ).scalar()
    assert version == "Valhalla test"


def test_export_flags_access(engine, turbine, tmp_path):  # noqa: F811
    # The area is far away and only the first corridor has a drive time.
    save_area(engine, box(-80.0, 30.0, -79.9, 30.1))
    corridor, _, _ = turbine
    with engine.begin() as conn:
        drive_time.save_drive_times(
            conn, CFG.origin.key, [corridor], [drive_time.DriveTime(75.0, 90.0, 20.0)]
        )
    scoring_run.score_hydro(engine, AREA, PROFILE)
    info, out_dir, _ = export_run.export_hydro(engine, AREA, out_root=tmp_path)

    assert info.roads_version and "downloaded" in info.roads_version
    assert info.drive_area.minutes == 60 and info.drive_area.origin_name == "Test origin"
    with (out_dir / "all_corridors.csv").open() as f:
        rows = list(csv.DictReader(f))
    assert {r["access_flags"] for r in rows} == {
        "outside_drive_area; no_drive_time; nearest_road_not_public",
        "outside_drive_area; nearest_road_not_public",
    }
    timed = [r for r in rows if r["drive_minutes"]]
    assert [(r["drive_minutes"], r["drive_miles"], r["in_drive_area"]) for r in timed] == [
        ("75.0", "55.9", "False")
    ]
    assert "Roads and drive time (ranked corridors" in (out_dir / "report.md").read_text()
