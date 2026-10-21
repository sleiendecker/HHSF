import numpy as np
import pytest
from pydantic import ValidationError
from rasterio.transform import from_origin

from hhsf.analysis import buildable

SETTINGS = buildable.Settings(
    max_slope_pct=15,
    septic_rating="septic_tn",
    septic_excluded=["Very limited"],
    min_patch_acres=0.5,
)
CELL_M2 = buildable.CELL_M**2


def test_settings_come_from_the_repo_config():
    s = buildable.load_settings()
    assert s.max_slope_pct == 15 and s.septic_rating == "septic_tn"
    assert "Very limited" in s.septic_excluded and s.min_patch_acres == 0.5


def test_settings_reject_an_unknown_septic_rating():
    with pytest.raises(ValidationError):
        buildable.Settings(**{**SETTINGS.model_dump(), "septic_rating": "septic_moon"})


def test_horn_slope_of_a_plane():
    # Rising 1 m per 10 m cell eastward: 10%. Edge cells have no slope.
    elev = np.tile(np.arange(6, dtype=float), (5, 1))
    slope = buildable.horn_slope_pct(elev)
    assert np.allclose(slope[1:-1, 1:-1], 10)
    assert np.isnan(slope[0]).all() and np.isnan(slope[:, -1]).all()


def test_horn_slope_of_a_diagonal_plane():
    y, x = np.mgrid[0:5, 0:5].astype(float)
    slope = buildable.horn_slope_pct(3 * x + 4 * y)  # 3 m and 4 m per cell: 50%
    assert np.allclose(slope[1:-1, 1:-1], 50)


def test_missing_elevations_spread_to_their_neighbors():
    elev = np.zeros((5, 5))
    elev[2, 2] = np.nan
    slope = buildable.horn_slope_pct(elev)
    ring = np.ones((3, 3), dtype=bool)
    ring[1, 1] = False  # Horn's method doesn't use the cell's own elevation
    assert np.isnan(slope[1:4, 1:4][ring]).all() and slope[2, 2] == 0


def test_grid_snaps_to_whole_cells_and_pads():
    transform, width, height = buildable.grid((103.0, 207.0, 155.0, 248.0))
    assert (transform.c, transform.f) == (80.0, 270.0)  # 100 - 2 cells, 250 + 2 cells
    assert (width, height) == (6 + 4, 5 + 4)


def test_patches_keep_only_big_enough_ones():
    mask = np.zeros((10, 10), dtype=bool)
    mask[0:3, 0:3] = True  # 9 cells
    mask[6:8, 6:8] = True  # 4 cells
    mask[5, 0] = mask[6, 1] = True  # two cells meeting at a corner: two patches of one
    transform = from_origin(0, 100, 10, 10)
    kept = buildable.patches(mask, transform, 5 * CELL_M2)
    assert [p.area for p in kept] == [9 * CELL_M2]
    assert len(buildable.patches(mask, transform, CELL_M2)) == 4
    assert buildable.patches(np.zeros((3, 3), dtype=bool), transform, 1) == []


def test_classify_applies_slope_floodplain_soils_and_patch_size():
    # A 30 x 30 cell parcel (9 ha, ~22 acres) in Albers meters near Sequatchie County.
    shape = (32, 32)
    transform = from_origin(1_000_000, 1_500_000, 10, 10)
    parcel = np.zeros(shape, dtype=bool)
    parcel[1:31, 1:31] = True
    slope = np.full(shape, 5.0)
    slope[:, 16:] = 30.0  # east half steep
    slope[0, :] = np.nan  # no elevation on the top row (outside the parcel anyway)
    sfha = np.zeros(shape, dtype=bool)
    sfha[1:31, 1:6] = True  # a floodplain strip on the west edge
    soil_ok = np.ones(shape, dtype=bool)
    soil_ok[20:, :] = False  # very limited soils in the south
    soil_ok[2, 10] = False  # a hole: patches go around it

    r = buildable.classify("g1", slope, parcel, sfha, soil_ok, transform, SETTINGS)
    acres = buildable.CELL_ACRES
    assert r.gislink == "g1"
    assert r.parcel_acres == pytest.approx(900 * acres)
    assert r.analyzed_acres == r.parcel_acres
    assert r.gentle_acres == pytest.approx(30 * 15 * acres)
    assert r.gentle_dry_acres == pytest.approx(30 * 10 * acres)
    assert r.septic_ok_acres == pytest.approx((19 * 10 - 1) * acres)
    assert r.buildable_acres == pytest.approx(r.septic_ok_acres)
    assert r.patches == 1
    assert r.geom.geom_type == "MultiPolygon"
    lon, lat = r.geom.centroid.coords[0]
    assert -90 < lon < -80 and 30 < lat < 40  # back in storage CRS


def test_classify_with_nothing_buildable():
    shape = (5, 5)
    steep = np.full(shape, 40.0)
    everywhere = np.ones(shape, dtype=bool)
    r = buildable.classify(
        "g2", steep, everywhere, ~everywhere, everywhere, from_origin(0, 50, 10, 10), SETTINGS
    )
    assert (r.gentle_acres, r.buildable_acres, r.patches, r.geom) == (0, 0, 0, None)


def test_small_patches_count_as_septic_ok_but_not_buildable():
    shape = (10, 10)
    gentle = np.full(shape, 2.0)
    parcel = np.ones(shape, dtype=bool)
    soil_ok = np.zeros(shape, dtype=bool)
    soil_ok[0:4, 0:4] = True  # 16 cells, 0.4 acres: under the 0.5 acre minimum
    r = buildable.classify(
        "g3", gentle, parcel, ~parcel, soil_ok, from_origin(1e6, 1.5e6, 10, 10), SETTINGS
    )
    assert r.septic_ok_acres == pytest.approx(16 * buildable.CELL_ACRES)
    assert (r.buildable_acres, r.patches) == (0, 0)


def test_ground_without_soil_data_is_not_buildable_but_is_counted():
    shape = (10, 10)
    gentle = np.full(shape, 2.0)
    parcel = np.ones(shape, dtype=bool)
    mapped = np.zeros(shape, dtype=bool)
    mapped[:, :6] = True  # the east 4 columns reach into a county with no soils loaded
    r = buildable.classify(
        "g4", gentle, parcel, ~parcel, mapped, from_origin(1e6, 1.5e6, 10, 10), SETTINGS, mapped
    )
    assert r.no_soil_acres == pytest.approx(40 * buildable.CELL_ACRES)
    assert r.buildable_acres == pytest.approx(60 * buildable.CELL_ACRES)
