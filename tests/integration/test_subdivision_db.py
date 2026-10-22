"""Road frontage and the homestead cut from each turbine parcel (FR-9), in a throwaway
PostGIS."""

import csv

import pytest
from shapely.geometry import MultiPolygon, box
from sqlalchemy import text

from hhsf.analysis import buildable, export_run, privacy, scoring, scoring_run, subdivision
from hhsf.ingest import roads
from tests.integration.test_farmland_db import site  # noqa: F401  (fixture)
from tests.integration.test_flood_db import outlet
from tests.integration.test_hydro_db import AREA, engine  # noqa: F401  (fixture)
from tests.integration.test_roads_db import road
from tests.integration.test_scoring_db import hydro_run  # noqa: F401  (fixture)

pytestmark = pytest.mark.integration

SETTINGS = subdivision.load_settings()


@pytest.fixture
def fronted(engine, site):  # noqa: F811
    """The turbine parcel (reaching ~360 m each way from the turbine end) with a public road
    across it ~330 m north, and a 4WD trail across it ~110 m south, which isn't frontage."""
    corridor, x, y = outlet(engine)
    with engine.begin() as conn:
        roads.load_roads(
            conn,
            AREA,
            [road("1", "S1400", x, y, 0.003, "Valley Rd"), road("2", "S1500", x, y, -0.001)],
        )
    yield corridor, x, y
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE roads, corridor_subdivision, parcel_buildable"))


def add_buildable(engine, x, y):  # noqa: F811
    """3 acres of buildable ground on the turbine parcel, just north of the turbine end."""
    patch = MultiPolygon([box(x - 0.0005, y + 0.0002, x + 0.0005, y + 0.0009)])
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO parcel_buildable (gislink, aoi_key, parcel_acres, analyzed_acres,
                    gentle_acres, gentle_dry_acres, septic_ok_acres, no_soil_acres,
                    buildable_acres, patches, geom, max_slope_pct, septic_rating,
                    septic_excluded, min_patch_acres)
                SELECT gislink, :a, 160, 160, 3, 3, 3, 0, 3, 1, ST_GeomFromText(:g, 4326),
                       15, 'septic_tn', ARRAY['Very limited'], 0.5
                FROM parcels
                """
            ),
            {"a": AREA.key, "g": patch.wkt},
        )


def run(engine):  # noqa: F811
    with engine.begin() as conn:
        sites = subdivision.load_sites(conn, buildable.latest_hydro_run(conn, AREA.key))
        return subdivision.run_subdivision(conn, AREA, sites, SETTINGS, lambda _: None)


def test_frontage_is_public_road_on_the_parcel(engine, fronted):  # noqa: F811
    corridor, x, y = fronted
    by_id = {r.corridor_id: r for r in run(engine)}
    assert len(by_id) == 1  # only the corridor with a turbine parcel
    r = by_id[corridor]
    # Valley Rd crosses the ~725 m wide parcel; the trail doesn't count.
    assert r.frontage_m == pytest.approx(725 + 2 * SETTINGS.tolerance_m, rel=0.03)
    assert r.home_site_basis == privacy.TURBINE_END and r.wire_m == 0
    assert r.driveway_m == pytest.approx(333, rel=0.05) and r.access_on_parcel is True
    assert r.homestead_cut is None  # buildable ground not computed: can't tell
    assert subdivision.NO_HOUSE_SITE not in r.flags
    with engine.connect() as conn:
        saved = conn.execute(
            text("SELECT * FROM corridor_subdivision WHERE corridor_id = :c"), {"c": corridor}
        ).one()
    assert saved.home_site_basis == privacy.TURBINE_END and saved.min_frontage_ft == 50
    assert saved.piece_acres >= SETTINGS.min_piece_acres and saved.flags == list(r.flags)


def test_homestead_cut_shows_on_the_map_and_exports(engine, fronted, tmp_path):  # noqa: F811
    corridor, x, y = fronted
    add_buildable(engine, x, y)
    r = {r.corridor_id: r for r in run(engine)}[corridor]
    assert r.home_site_basis == privacy.BUILDABLE and r.wire_m == pytest.approx(22, rel=0.1)
    # The test stretch runs on past the parcel's north edge: most of it is a neighbor's.
    assert r.stretch_share == pytest.approx(0.41, abs=0.02)
    assert r.homestead_cut is False and r.flags == (subdivision.STRETCH_CROSSES_PARCELS,)
    scoring_run.score_hydro(engine, AREA, scoring.load_profile("hydro_v6"))
    with engine.connect() as conn:
        view = conn.execute(
            text(
                "SELECT subdivision_score, homestead_cut, road_frontage_m, cut_driveway_m,"
                " subdivision_flags FROM latest_hydro_scores_live WHERE corridor_id = :c"
            ),
            {"c": corridor},
        ).one()
    assert view.subdivision_score == pytest.approx(r.subdivision_score)
    assert view.homestead_cut is r.homestead_cut
    assert view.road_frontage_m == pytest.approx(r.frontage_m)
    assert view.subdivision_flags == ",".join(r.flags)

    info, out_dir, _ = export_run.export_hydro(engine, AREA, out_root=tmp_path)
    assert info.subdivision.min_frontage_ft == 50
    with (out_dir / "all_corridors.csv").open() as f:
        exported = list(csv.DictReader(f))
    cut = [row for row in exported if row["road_frontage_ft"]]
    assert len(cut) == 1
    assert cut[0]["homestead_cut"] == ("yes" if r.homestead_cut else "no")
    assert cut[0]["cut_driveway_ft"] == str(round(r.driveway_m * 3.28084))
    # No turbine parcel: parcel_flags say so, and there's nothing to cut.
    assert {row["subdivision_flags"] for row in exported if row is not cut[0]} == {""}
    assert "Subdivision feasibility (FR-9) asks" in (out_dir / "report.md").read_text()


def test_parcel_reload_deletes_its_homestead_cut(engine, fronted):  # noqa: F811
    run(engine)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM parcels"))
        left = conn.execute(text("SELECT count(*) FROM corridor_subdivision")).scalar()
    assert left == 0
