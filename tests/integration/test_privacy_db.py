"""Buildings, each corridor's privacy and its part in the overall score, in a throwaway
PostGIS."""

import csv

import pytest
from shapely.geometry import MultiPolygon, box
from sqlalchemy import text

from hhsf.analysis import buildable, export_run, privacy, scoring, scoring_run
from hhsf.core import crs
from hhsf.core.config import load_data_source
from hhsf.core.db import record_source
from hhsf.ingest import buildings, roads
from tests.integration.test_farmland_db import site, write_landcover  # noqa: F401  (fixture)
from tests.integration.test_flood_db import outlet
from tests.integration.test_hydro_db import AREA, engine  # noqa: F401  (fixture)
from tests.integration.test_roads_db import road
from tests.integration.test_scoring_db import hydro_run  # noqa: F401  (fixture)

pytestmark = pytest.mark.integration

SETTINGS = privacy.load_settings()


def building(x, y, side_deg=0.0002) -> buildings.Building:
    """A square footprint (about 20 m a side by default) with its south-west corner at x, y."""
    geom = MultiPolygon([box(x, y, x + side_deg, y + side_deg)])
    return buildings.Building(geom, crs.area_m2(geom), None)


@pytest.fixture
def neighbors(engine, site):  # noqa: F811
    """On the turbine parcel (which reaches ~360 m each way): one building. East of it, ~550 m
    from the turbine end: a neighbor, and a shed too small to count. A public road ~330 m
    north, across the pasture write_landcover puts there."""
    corridor, x, y = outlet(engine)
    with engine.begin() as conn:
        record_source(conn, load_data_source(buildings.SOURCE_KEY))
        n = buildings.load_tile(
            conn,
            "032002112",
            [
                building(x + 0.001, y + 0.001),
                building(x + 0.006, y),
                building(x + 0.0055, y, side_deg=0.00003),  # ~3 m a side
            ],
        )
        roads.load_roads(conn, AREA, [road("1", "S1400", x, y, 0.003, "Valley Rd")])
    assert n == 3
    yield corridor, x, y
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE buildings, roads, corridor_privacy"))


def run(engine, tmp_path, x, y):  # noqa: F811
    canopy = write_landcover(tmp_path, x, y)["tree_canopy"]
    with engine.begin() as conn:
        sites = privacy.load_sites(conn, buildable.latest_hydro_run(conn, AREA.key))
        return privacy.run_privacy(conn, AREA, sites, canopy, SETTINGS, lambda _: None)


def test_privacy_counts_neighbors_not_the_parcels_own_buildings(engine, neighbors, tmp_path):  # noqa: F811
    corridor, x, y = neighbors
    by_id = {r.corridor_id: r for r in run(engine, tmp_path, x, y)}
    r = by_id[corridor]
    # No buildable ground computed: the home site is the turbine end.
    assert r.home_site_basis == privacy.TURBINE_END and r.gislink is not None
    assert r.parcel_buildings == 1
    assert (r.buildings_quarter_mile, r.buildings_half_mile, r.buildings_mile) == (0, 1, 1)
    assert r.nearest_building_m == pytest.approx(545, rel=0.05)
    assert r.public_road_m == pytest.approx(333, rel=0.05)
    assert r.road_woods_m == 0  # open pasture north of the turbine end
    with engine.connect() as conn:
        saved = conn.execute(
            text("SELECT * FROM corridor_privacy WHERE corridor_id = :c"), {"c": corridor}
        ).one()
    assert saved.home_site_basis == privacy.TURBINE_END
    assert saved.min_footprint_sqft == 400 and saved.buildings_half_mile == 1


def test_privacy_scores_shows_on_the_map_and_exports(engine, neighbors, tmp_path):  # noqa: F811
    corridor, x, y = neighbors
    run(engine, tmp_path, x, y)
    run_id, _, summary = scoring_run.score_hydro(engine, AREA, scoring.load_profile("hydro_v6"))
    with engine.connect() as conn:
        score = conn.execute(
            text(
                "SELECT rank, privacy_subscore, explanation FROM hydro_scores"
                " WHERE run_id = :r AND corridor_id = :c"
            ),
            {"r": run_id, "c": corridor},
        ).one()
        view = conn.execute(
            text(
                "SELECT privacy_subscore, privacy_home_site, buildings_half_mile, road_woods_m"
                " FROM latest_hydro_scores_live WHERE corridor_id = :c"
            ),
            {"c": corridor},
        ).one()
    if score.rank is not None:
        assert score.privacy_subscore is not None
        assert "privacy" not in score.explanation["overall"]["left_out"]
        assert view.privacy_subscore == pytest.approx(score.privacy_subscore)
    assert view.privacy_home_site == privacy.TURBINE_END and view.buildings_half_mile == 1
    assert "privacy" not in summary.get("overall_parts_left_out", {})

    info, out_dir, _ = export_run.export_hydro(engine, AREA, out_root=tmp_path)
    assert info.privacy.min_footprint_sqft == 400
    assert info.privacy.buildings_version.startswith("Global ML Building Footprints")
    with (out_dir / "all_corridors.csv").open() as f:
        exported = {r["nhdplusid"]: r for r in csv.DictReader(f)}
    on_site = [r for r in exported.values() if r["turbine_parcel_buildings"] == "1"]
    assert len(on_site) == 1
    # The road is ~1,100 ft away, so it isn't flagged open_to_road.
    assert on_site[0]["privacy_flags"] == "buildings_on_parcel"
    assert "Privacy (FR-6) is measured at a home site" in (out_dir / "report.md").read_text()


def test_parcel_reload_deletes_its_privacy(engine, neighbors, tmp_path):  # noqa: F811
    corridor, x, y = neighbors
    run(engine, tmp_path, x, y)
    with engine.begin() as conn:
        before = conn.execute(text("SELECT count(*) FROM corridor_privacy")).scalar()
        conn.execute(text("DELETE FROM parcels"))
        after = conn.execute(text("SELECT count(*) FROM corridor_privacy")).scalar()
        gone = conn.execute(
            text("SELECT count(*) FROM corridor_privacy WHERE corridor_id = :c"), {"c": corridor}
        ).scalar()
    assert gone == 0 and after == before - 1  # corridors with no parcel keep theirs
