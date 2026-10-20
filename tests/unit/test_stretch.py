"""The steepest-stretch search (D-08) on synthetic profiles and a synthetic DEM."""

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import LineString, MultiLineString, Point

from hhsf.analysis import hydro, stretch
from hhsf.core import crs

SETTINGS = hydro.CorridorSettings(penstock_length_ft=1000, sample_spacing_m=10)
WINDOW = SETTINGS.penstock_length_m  # 304.8 m


def profile(length_m: float, fn, spacing_m: float = 10) -> tuple[np.ndarray, np.ndarray]:
    d = stretch.sample_distances(length_m, spacing_m)
    return d, np.array([fn(x) for x in d], dtype=float)


# --- sampling distances ---


def test_samples_cover_both_ends_no_further_apart_than_spacing():
    d = stretch.sample_distances(1005.0, 10)
    assert d[0] == 0 and d[-1] == 1005.0
    assert np.diff(d).max() <= 10


def test_short_reach_still_has_two_samples():
    assert list(stretch.sample_distances(3.0, 10)) == [0.0, 3.0]


# --- cleaning ---


def test_cleaned_profile_never_rises_downstream():
    # A road fill crossing the stream shows as a 4 m bump in the DEM.
    z = np.array([100, 99, 98, 102, 97, 96], dtype=float)
    assert list(stretch.clean_profile(z)) == [100, 99, 98, 98, 97, 96]


def test_cleaned_profile_stays_inside_nhdplus_end_elevations():
    # The first sample sits on a bank 5 m above the channel NHDPlus recorded.
    z = np.array([105, 100, 95, 88], dtype=float)
    assert list(stretch.clean_profile(z, top_m=100, bottom_m=90)) == [100, 100, 95, 90]


def test_cleaning_with_one_bound():
    z = np.array([105, 100, 95], dtype=float)
    assert list(stretch.clean_profile(z, top_m=101)) == [101, 100, 95]


# --- the search ---


def test_uniform_slope_drop_is_slope_times_penstock():
    d, z = profile(2000, lambda x: 500 - 0.1 * x)
    s = stretch.best_stretch(d, z, WINDOW)
    assert s.drop_m == pytest.approx(0.1 * WINDOW)
    assert s.length_m == pytest.approx(WINDOW)
    # Every window drops the same: the most downstream one wins (more water there).
    assert s.end_m == pytest.approx(2000)


def test_finds_the_waterfall():
    # Gentle 1% slope with a 30 m fall between 1,200 and 1,220 m.
    d, z = profile(3000, lambda x: 400 - 0.01 * x - 30 * min(1, max(0, x - 1200) / 20))
    s = stretch.best_stretch(d, z, WINDOW)
    assert s.start_m <= 1200 and s.end_m >= 1220
    assert s.drop_m == pytest.approx(30 + 0.01 * WINDOW, abs=0.01)


def test_steep_headwater_puts_the_stretch_at_the_top():
    # Concave profile, like a stream leaving the plateau edge: steepest upstream.
    d, z = profile(2500, lambda x: 300 * np.exp(-x / 500))
    s = stretch.best_stretch(d, z, WINDOW)
    assert s.start_m == 0
    assert s.drop_m == pytest.approx(300 * (1 - np.exp(-WINDOW / 500)), rel=1e-3)


def test_window_end_is_interpolated_between_samples():
    # 304.8 m isn't a multiple of the 10 m spacing.
    d, z = profile(1000, lambda x: 1000 - x)
    s = stretch.best_stretch(d, z, WINDOW)
    assert s.drop_m == pytest.approx(WINDOW)


def test_reach_shorter_than_penstock_is_one_stretch():
    d, z = profile(200, lambda x: 100 - 0.2 * x)
    s = stretch.best_stretch(d, z, WINDOW)
    assert (s.start_m, s.end_m) == (0, 200)
    assert s.drop_m == pytest.approx(40)
    assert s.whole_reach
    assert (s.from_measure, s.to_measure) == (0, 100)


def test_bank_spike_at_the_top_is_capped_by_nhdplus():
    d, z = profile(1000, lambda x: 200 - 0.05 * x)
    z[0] += 20  # first sample on the bank
    raw = stretch.best_stretch(d, z, WINDOW)
    capped = stretch.best_stretch(d, z, WINDOW, top_m=200, bottom_m=150)
    assert raw.drop_m > 20
    assert capped.drop_m == pytest.approx(0.05 * WINDOW)


def test_stretch_never_drops_more_than_nhdplus_says_the_reach_does():
    d, z = profile(1000, lambda x: 300 - 0.2 * x)  # DEM: 200 m over the reach
    s = stretch.best_stretch(d, z, WINDOW, top_m=150, bottom_m=140)  # NHDPlus: 10 m
    assert s.drop_m <= 10


def test_flat_reach_has_zero_drop():
    d, z = profile(800, lambda x: 250.0)
    assert stretch.best_stretch(d, z, WINDOW).drop_m == 0


def test_measures_count_from_the_downstream_end():
    s = stretch.Stretch(start_m=200, end_m=500, drop_m=10, reach_length_m=1000)
    assert s.from_measure == pytest.approx(50)  # bottom of the stretch
    assert s.to_measure == pytest.approx(80)  # top
    assert not s.whole_reach


# --- geometry ---

UPSTREAM, DOWNSTREAM = (1_000_000, 1_400_000), (1_001_000, 1_400_000)
REACH = MultiLineString([crs.to_storage(LineString([UPSTREAM, DOWNSTREAM]))])


def test_stretch_geometry_is_that_part_of_the_reach():
    s = stretch.Stretch(start_m=200, end_m=500, drop_m=10, reach_length_m=1000)
    g = stretch.stretch_geometry(REACH, s)
    assert g.geom_type == "MultiLineString"
    line = crs.to_analysis(g).geoms[0]
    assert line.length == pytest.approx(300, abs=0.5)
    assert Point(line.coords[0]).distance(Point(1_000_200, 1_400_000)) < 0.5  # top first
    assert Point(line.coords[-1]).distance(Point(1_000_500, 1_400_000)) < 0.5


def test_whole_reach_stretch_keeps_the_reach_geometry():
    s = stretch.Stretch(0, 1000, 10, 1000)
    assert stretch.stretch_geometry(REACH, s) is REACH


def test_touching_pieces_are_merged_into_one_line():
    a = crs.to_storage(LineString([UPSTREAM, (1_000_500, 1_400_000)]))
    b = crs.to_storage(LineString([(1_000_500, 1_400_000), DOWNSTREAM]))
    line = stretch.reach_line(MultiLineString([a, b]))
    assert line is not None and line.length == pytest.approx(1000, abs=0.5)


def test_separate_pieces_are_not_one_line():
    a = crs.to_storage(LineString([UPSTREAM, (1_000_400, 1_400_000)]))
    b = crs.to_storage(LineString([(1_000_600, 1_400_000), DOWNSTREAM]))
    assert stretch.reach_line(MultiLineString([a, b])) is None


# --- reading a DEM ---


@pytest.fixture
def dem_file(tmp_path):
    """A 10 m DEM in the analysis CRS around REACH: flat at 300 m, a 40 m fall at
    x = 1,000,400-1,000,450, then a 2% slope. One no-data cell well off the reach."""
    x0, y0, cell = 999_900.0, 1_400_050.0, 10.0
    xs = x0 + cell * (np.arange(130) + 0.5)
    fall = np.clip((xs - 1_000_400) / 50, 0, 1) * 40
    slope = np.clip(xs - 1_000_450, 0, None) * 0.02
    z = np.tile(300 - fall - slope, (10, 1)).astype("float32")
    z[0, 0] = -9999
    path = tmp_path / "dem.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=130,
        height=10,
        count=1,
        dtype="float32",
        crs=f"EPSG:{crs.ANALYSIS_EPSG}",
        transform=from_origin(x0, y0, cell, cell),
        nodata=-9999,
    ) as f:
        f.write(z, 1)
    return path


def test_profile_reads_cells_along_the_line(dem_file):
    with rasterio.open(dem_file) as dem:
        d, z = stretch.sample_profile(dem, LineString([UPSTREAM, DOWNSTREAM]), 10)
    assert d[-1] == pytest.approx(1000)
    assert z[0] == pytest.approx(300)
    assert z[-1] == pytest.approx(300 - 40 - 0.02 * 550, abs=0.3)


def test_profile_off_the_dem_is_none(dem_file):
    with rasterio.open(dem_file) as dem:
        off = LineString([(1_000_900, 1_400_000), (1_002_000, 1_400_000)])
        assert stretch.sample_profile(dem, off, 10) is None


def test_profile_through_no_data_is_none(dem_file):
    with rasterio.open(dem_file) as dem:
        corner = LineString([(999_905, 1_400_045), (999_995, 1_400_045)])
        assert stretch.sample_profile(dem, corner, 10) is None


def test_corridor_head_finds_the_fall_on_the_dem(dem_file):
    with rasterio.open(dem_file) as dem:
        c = stretch.corridor_head(dem, REACH, 300.0, 240.0, SETTINGS, 0.85)
    assert c.method == stretch.HEAD_METHOD
    assert c.stretch is not None
    assert c.stretch.start_m <= 400 and c.stretch.end_m >= 450
    assert c.head.gross_m == pytest.approx(40 + 0.02 * (WINDOW - 50), abs=0.5)
    assert c.head.net_m == pytest.approx(c.head.gross_m * 0.85)


def test_corridor_head_without_terrain_is_null_with_flag(dem_file):
    far = MultiLineString([crs.to_storage(LineString([(2_000_000, 1_400_000), (2_001_000, 0)]))])
    with rasterio.open(dem_file) as dem:
        c = stretch.corridor_head(dem, far, 300.0, 240.0, SETTINGS, 0.85)
    assert c.head == hydro.Head(None, None, stretch.NO_TERRAIN)
    assert c.stretch is None


def test_nhdplus_bounds_that_rise_downstream_are_ignored(dem_file):
    with rasterio.open(dem_file) as dem:
        c = stretch.corridor_head(dem, REACH, 240.0, 300.0, SETTINGS, 0.85)
    assert c.head.gross_m > 40
