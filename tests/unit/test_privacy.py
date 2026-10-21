import numpy as np
import pytest
from rasterio.transform import from_origin
from shapely.geometry import LineString, Point, box

from hhsf.analysis import privacy

SETTINGS = privacy.load_settings()


def square(x: float, y: float, side: float = 10) -> object:
    return box(x, y, x + side, y + side)


def test_settings_load_from_config():
    assert SETTINGS.min_footprint_sqft == 400 and SETTINGS.woods_canopy_pct == 50
    assert SETTINGS.sightline_max_m == pytest.approx(402.3, abs=0.1)
    assert SETTINGS.search_m == pytest.approx(2 * 1609.344)


def test_home_site_is_the_buildable_ground_nearest_the_turbine_end():
    turbine = Point(0, 0)
    home, basis = privacy.home_site(turbine, box(100, -50, 200, 50))
    assert basis == privacy.BUILDABLE and home.equals(Point(100, 0))
    home, basis = privacy.home_site(turbine, None)
    assert basis == privacy.TURBINE_END and home.equals(turbine)


def test_neighbors_leave_out_buildings_on_the_turbine_parcel():
    parcel = box(-100, -100, 100, 100)
    b = privacy.Buildings([square(10, 10), square(300, 0), square(1000, 0)])
    d = b.neighbors(Point(0, 0), privacy.QUARTER_MILE_M, parcel)
    assert list(np.round(d)) == [300]  # the one on the parcel is yours; 1,000 m is too far
    assert len(b.neighbors(Point(0, 0), privacy.QUARTER_MILE_M, None)) == 2
    assert b.on(parcel) == 1


def test_woods_on_line_counts_canopy_cells_over_the_threshold():
    # Three 30 m cells west to east: open, woods, woods; no data in the row below.
    canopy = np.array([[0, 80, 60], [255, 255, 255]], dtype="uint8")
    t = from_origin(0, 60, 30, 30)
    line = LineString([(0, 45), (90, 45)])
    assert privacy.woods_on_line(line, canopy, t, 50) == pytest.approx(60)
    assert privacy.woods_on_line(line, canopy, t, 70) == pytest.approx(30)
    assert privacy.woods_on_line(LineString([(0, 15), (90, 15)]), canopy, t, 50) is None
    assert privacy.woods_on_line(LineString([(5, 45), (5, 45)]), canopy, t, 50) == 0


def test_measure_counts_rings_density_and_screening():
    turbine = Point(0, 0)
    site = privacy.Site(1, turbine, "p1", box(-50, -50, 400, 50), box(100, -20, 200, 20))
    buildings = privacy.Buildings(
        [
            square(150, 0),  # on the turbine parcel: not a neighbor
            square(300, 200),  # ~ 210 m from the home site (100, 0)
            square(700, 0),  # ~ 600 m
            square(1500, 0),  # ~ 1,400 m
        ]
    )
    roads = privacy.shapely.STRtree([LineString([(100, -300), (100, -900)])])
    canopy = (np.full((100, 100), 90, dtype="uint8"), from_origin(-1500, 1500, 30, 30))
    r = privacy.measure(site, buildings, roads, canopy, SETTINGS)
    assert r.home_site_basis == privacy.BUILDABLE and r.home_site.equals(Point(100, 0))
    assert (r.buildings_quarter_mile, r.buildings_half_mile, r.buildings_mile) == (1, 2, 3)
    assert r.buildings_per_sqmi == pytest.approx(3 / np.pi)
    assert r.nearest_building_m == pytest.approx(np.hypot(200, 200))
    assert r.parcel_buildings == 1
    assert r.public_road_m == pytest.approx(300) and r.sightline_m == pytest.approx(300)
    assert r.road_woods_m == pytest.approx(300)  # all woods
    # From the turbine end, the building at (300, 200) is ~360 m away: within 1/4 mile.
    assert r.turbine_buildings_quarter_mile == 1


def test_measure_with_no_neighbors_roads_or_canopy():
    site = privacy.Site(2, Point(0, 0), None, None, None)
    r = privacy.measure(site, privacy.Buildings([square(5000, 0)]), None, None, SETTINGS)
    assert r.home_site_basis == privacy.TURBINE_END
    assert r.buildings_mile == 0 and r.nearest_building_m is None and r.parcel_buildings is None
    assert r.public_road_m is None and r.road_woods_m is None


def test_sightline_stops_at_its_maximum():
    site = privacy.Site(3, Point(0, 0), None, None, None)
    roads = privacy.shapely.STRtree([LineString([(2000, -100), (2000, 100)])])
    # Woods only past 1/4 mile: they don't count.
    grid = np.zeros((10, 100), dtype="uint8")
    grid[:, 20:] = 90  # x >= 600 m
    canopy = (grid, from_origin(0, 150, 30, 30))
    r = privacy.measure(site, privacy.Buildings([square(9000, 0)]), roads, canopy, SETTINGS)
    assert r.public_road_m == pytest.approx(2000)
    assert r.sightline_m == pytest.approx(SETTINGS.sightline_max_m)
    assert r.road_woods_m == 0
