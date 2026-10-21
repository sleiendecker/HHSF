"""Farmland soils, each turbine parcel's farmland and its corridor link in a throwaway
PostGIS."""

import csv
from dataclasses import replace

import pytest
import shapely
from shapely.geometry import MultiPolygon, box
from sqlalchemy import text

from hhsf.analysis import buildable, export_run, farmland, scoring, scoring_run
from hhsf.ingest import parcels, soils
from tests.integration.test_buildable_db import soil_unit
from tests.integration.test_flood_db import outlet
from tests.integration.test_hydro_db import AREA, engine  # noqa: F401  (fixture)
from tests.integration.test_scoring_db import PROFILE, hydro_run  # noqa: F401  (fixture)
from tests.unit.test_parcels import feature as parcel_feature

pytestmark = pytest.mark.integration

SETTINGS = farmland.Settings(max_capability_class=3, wet_drainage=["Poorly drained"])
HALF = 0.004  # the parcel reaches this many degrees each way from the turbine end


def dense(*bounds):
    return shapely.segmentize(box(*bounds), 0.0005)


@pytest.fixture
def site(engine, hydro_run):  # noqa: F811
    """A parcel around the first corridor's turbine end: prime class 2 pasture soil north
    of the turbine end, steep, wet class 7 soil with no pasture yield south of it."""
    corridor, x, y = outlet(engine)
    lot = box(x - HALF, y - HALF, x + HALF, y + HALF)
    prime = replace(
        soil_unit("1", "Slightly limited"),
        farmland_class=farmland.PRIME,
        capability_class=2,
        capability_pct=90.0,
        pasture_aum=7.0,
    )
    steep = replace(
        soil_unit("2", "Very limited"),
        drainage="Poorly drained",
        farmland_class="Not prime farmland",
        capability_class=7,
        capability_pct=100.0,
    )
    with engine.begin() as conn:
        parcels.load_parcels(
            conn, AREA, [parcels.parse_feature(parcel_feature("099001    00300", lot), "99999")]
        )
        soils.load_soils(
            conn,
            AREA,
            [prime, steep],
            [
                # Densified: a long straight edge in degrees bends when projected to meters.
                soils.SoilPolygon(1, "1", "TN999", MultiPolygon([dense(x - 1, y, x + 1, y + 1)])),
                soils.SoilPolygon(2, "2", "TN999", MultiPolygon([dense(x - 1, y - 1, x + 1, y)])),
            ],
            {"TN999": "TN999 version 1 (1/1/2026)"},
        )
        parcel_acres = conn.execute(
            text("SELECT ST_Area(geom::geography) / :m2 FROM parcels"), {"m2": farmland.M2_PER_ACRE}
        ).scalar_one()
    yield corridor, parcel_acres
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE parcels, soils CASCADE"))


def run(engine):  # noqa: F811
    with engine.begin() as conn:
        return farmland.run_farmland(
            conn, AREA, buildable.latest_hydro_run(conn, AREA.key), SETTINGS
        )


def test_soils_keep_their_farmland_attributes(engine, site):  # noqa: F811
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT DISTINCT mukey, farmland_class, capability_class, capability_pct,"
                " pasture_aum FROM soils ORDER BY 1"  # densified polygons come in pieces
            )
        ).all()
    assert [tuple(r) for r in rows] == [
        ("1", farmland.PRIME, 2, 90.0, 7.0),
        ("2", "Not prime farmland", 7, 100.0, None),
    ]


def test_farmland_adds_up_each_soil_on_the_parcel(engine, site):  # noqa: F811
    corridor, parcel_acres = site
    [(gislink, farm_acres, capable)] = run(engine)
    assert farm_acres == pytest.approx(parcel_acres / 2, rel=0.01)
    assert capable == pytest.approx(parcel_acres / 2, rel=0.01)
    with engine.connect() as conn:
        f = conn.execute(
            text("SELECT * FROM corridor_turbine_farmland WHERE corridor_id = :c"),
            {"c": corridor},
        ).one()
    assert f.gislink == gislink
    assert f.parcel_acres == pytest.approx(parcel_acres)
    assert f.soil_acres == pytest.approx(parcel_acres, rel=1e-6)
    assert f.prime_if_acres == 0 and f.statewide_acres == 0
    assert f.wet_acres == pytest.approx(parcel_acres / 2, rel=0.01)
    assert f.pasture_aum == pytest.approx(7 * parcel_acres / 2, rel=0.01)
    assert f.max_capability_class == 3


def test_rerun_replaces_and_a_parcel_reload_deletes(engine, site):  # noqa: F811
    run(engine)
    run(engine)
    with engine.begin() as conn:
        assert conn.execute(text("SELECT count(*) FROM parcel_farmland")).scalar() == 1
        conn.execute(text("DELETE FROM parcels"))
        assert conn.execute(text("SELECT count(*) FROM parcel_farmland")).scalar() == 0


def test_map_view_and_export_have_farmland(engine, site, tmp_path):  # noqa: F811
    corridor, parcel_acres = site
    scoring_run.score_hydro(engine, AREA, PROFILE)
    info, _, _ = export_run.export_hydro(engine, AREA, out_root=tmp_path)
    assert info.farmland is None  # not computed yet
    run(engine)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT corridor_id, turbine_prime_acres, turbine_capable_acres,"
                " turbine_max_capability_class, turbine_pasture_aum FROM latest_hydro_scores"
            )
        ).all()
    on_site = {r.corridor_id: r for r in rows}[corridor]
    assert on_site.turbine_prime_acres == pytest.approx(parcel_acres / 2, rel=0.01)
    assert on_site.turbine_max_capability_class == 3 and on_site.turbine_pasture_aum > 0

    info, out_dir, _ = export_run.export_hydro(engine, AREA, out_root=tmp_path)
    assert info.farmland.max_capability_class == 3
    with (out_dir / "all_corridors.csv").open() as f:
        exported = [r for r in csv.DictReader(f) if r["turbine_parcel_prime_acres"]]
    assert len(exported) == 1 and exported[0]["farmland_flags"] == ""
    assert float(exported[0]["turbine_parcel_cropland_acres"]) > 0
    report = (out_dir / "report.md").read_text()
    assert "Farmland on the turbine parcel (ranked" in report
    assert "not what the land is now" in report


def test_overall_score_reads_farmland_and_drive_time(engine, site, tmp_path):  # noqa: F811
    corridor, _ = site
    run(engine)
    v4 = scoring.load_profile("hydro_v4")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO corridor_drive_times (corridor_id, origin_key, minutes)"
                " SELECT corridor_id, 'test', 60 FROM hydro_corridors"
            )
        )
    run_id, _, summary = scoring_run.score_hydro(engine, AREA, v4)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT corridor_id, rank, overall_score, agriculture_subscore,"
                " logistics_subscore, buildability_subscore, explanation"
                " FROM hydro_scores WHERE run_id = :r AND rank IS NOT NULL"
            ),
            {"r": run_id},
        ).all()
        view = conn.execute(
            text(
                "SELECT corridor_id, overall_score FROM latest_hydro_scores WHERE rank IS NOT NULL"
            )
        ).all()
    assert rows, "the sample has a ranked corridor"
    for r in rows:
        assert r.logistics_subscore == pytest.approx(70)  # 60 minutes
        assert r.overall_score is not None and "overall" in r.explanation
    on_site = [r for r in rows if r.corridor_id == corridor]
    assert on_site
    for r in on_site:
        assert r.agriculture_subscore > 0
        # No buildable ground computed, no roads loaded: buildability is left out.
        assert r.buildability_subscore is None
        assert "buildability" in r.explanation["overall"]["left_out"]
    assert {(v.corridor_id, v.overall_score) for v in view} == {
        (r.corridor_id, r.overall_score) for r in rows
    }
    assert "overall_parts_left_out" in summary
    _, out_dir, _ = export_run.export_hydro(engine, AREA, out_root=tmp_path)
    with (out_dir / "shortlist.csv").open() as f:
        assert all(r["overall_score"] for r in csv.DictReader(f))


def write_landcover(folder, x, y):
    """Pasture with no trees north of the turbine end, forest at 80% canopy south of it
    (NLCD's 30 m grid in EPSG:5070)."""
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin
    from shapely.geometry import Point

    from hhsf.core import crs

    pt = crs.to_analysis(Point(x, y))
    x0 = (pt.x // 30) * 30 - 3000 + 15
    y1 = (pt.y // 30) * 30 + 3000 + 15
    n = 200
    rows_y = y1 - (np.arange(n) + 0.5) * 30
    north = np.repeat((rows_y > pt.y)[:, None], n, axis=1)
    layers = {
        "land_cover": np.where(north, 81, 41).astype("uint8"),
        "tree_canopy": np.where(north, 0, 80).astype("uint8"),
    }
    paths = {}
    for layer, data in layers.items():
        paths[layer] = folder / f"{layer}.tif"
        with rasterio.open(
            paths[layer], "w", driver="GTiff", width=n, height=n, count=1, dtype="uint8",
            crs="EPSG:5070", transform=from_origin(x0, y1, 30, 30),
        ) as f:  # fmt: skip
            f.write(data, 1)
    return paths


def test_land_cover_counts_open_good_soil_and_feeds_the_score(engine, site, tmp_path):  # noqa: F811
    from hhsf.analysis import landcover
    from tests.integration.test_flood_db import outlet as turbine_end

    corridor, parcel_acres = site
    _, x, y = turbine_end(engine)
    run(engine)  # farmland
    rasters = write_landcover(tmp_path, x, y)
    with engine.begin() as conn:
        targets = buildable.turbine_parcels(conn, buildable.latest_hydro_run(conn, AREA.key))
        [r] = landcover.run_landcover(conn, AREA, targets, rasters, SETTINGS, 2021, lambda _: None)
    assert r.parcel_acres == pytest.approx(parcel_acres, rel=0.05)
    assert r.open_acres == pytest.approx(parcel_acres / 2, rel=0.1)
    assert r.forest_acres == pytest.approx(parcel_acres / 2, rel=0.1)
    # The pasture is on the prime class 2 soil (north): open prime = open cropland = open.
    assert r.open_prime_acres == pytest.approx(r.open_acres, rel=0.05)
    assert r.open_cropland_acres == pytest.approx(r.open_acres, rel=0.05)
    assert r.canopy_pct == pytest.approx(40, abs=5)

    with engine.connect() as conn:
        link = conn.execute(
            text("SELECT open_acres FROM corridor_turbine_landcover WHERE corridor_id = :c"),
            {"c": corridor},
        ).scalar_one()
    assert link == pytest.approx(r.open_acres)

    run_id, _, _ = scoring_run.score_hydro(engine, AREA, scoring.load_profile("hydro_v5"))
    with engine.connect() as conn:
        e = conn.execute(
            text("SELECT explanation FROM hydro_scores WHERE run_id = :r AND corridor_id = :c"),
            {"r": run_id, "c": corridor},
        ).scalar_one()
        view = conn.execute(
            text(
                "SELECT turbine_open_acres, turbine_canopy_pct FROM latest_hydro_scores"
                " WHERE corridor_id = :c"
            ),
            {"c": corridor},
        ).one()
    agri = next(p for p in e["overall"]["parts"] if p["part"] == "agriculture")
    assert "open_cropland_acres" in [f["factor"] for f in agri["factors"]]
    assert view.turbine_open_acres == pytest.approx(r.open_acres)
    _, out_dir, _ = export_run.export_hydro(engine, AREA, out_root=tmp_path)
    with (out_dir / "all_corridors.csv").open() as f:
        exported = [x for x in csv.DictReader(f) if x["turbine_parcel_open_acres"]]
    assert len(exported) == 1 and exported[0]["landcover_flags"] == ""
    assert "Land cover is from USGS NLCD 2021" in (out_dir / "report.md").read_text()
