import pytest
import shapely
from shapely.geometry import LineString, Point, box

from hhsf.analysis import privacy, subdivision

SETTINGS = subdivision.load_settings()
ACRE = subdivision.M2_PER_ACRE


def test_settings_load_from_config():
    assert SETTINGS.min_frontage_ft == 50 and SETTINGS.min_stretch_share == 0.9
    assert SETTINGS.tolerance_m == pytest.approx(15.24, abs=0.01)
    assert sum(SETTINGS.weights.model_dump().values()) == 100


def test_ramp():
    assert subdivision.ramp(100, 1320, 5280) == 1
    assert subdivision.ramp(3300, 1320, 5280) == pytest.approx(0.5)
    assert subdivision.ramp(9000, 1320, 5280) == 0


def test_score_leaves_out_parts_with_no_data():
    w = SETTINGS.weights
    parts = {"frontage": 1.0, "hydro_retention": 1.0, "buildable": 0.0, "access": 0.5}
    assert subdivision.score(parts, w) == pytest.approx(62.5)
    # No roads loaded: frontage and access are unknown, not zero.
    assert subdivision.score({**parts, "frontage": None, "access": None}, w) == 50
    assert subdivision.score(dict.fromkeys(parts), w) is None


def test_same_part_needs_every_point_on_one_part():
    edge = shapely.MultiPolygon([box(0, 0, 10, 10), box(100, 0, 110, 10)])
    assert subdivision.same_part(edge, Point(1, 1), Point(9, 9)).equals(box(0, 0, 10, 10))
    assert subdivision.same_part(edge, Point(1, 1), Point(105, 5)) is None


def site(parcel=None, buildable=None, buildable_acres=None, stretch=None):
    """A 400 m square parcel with a stretch running south down its middle to (200, 50)."""
    return subdivision.Site(
        corridor_id=1,
        stretch=stretch or LineString([(200, 350), (200, 50)]),
        turbine_end=Point(200, 50),
        gislink="G1",
        parcel=parcel or box(0, 0, 400, 400),
        parcel_acres=(parcel or box(0, 0, 400, 400)).area / ACRE,
        buildable=buildable,
        buildable_acres=buildable_acres,
    )


ROAD_SOUTH = shapely.STRtree([LineString([(-100, -10), (500, -10)])])  # 10 m off the edge
ROAD_FAR = shapely.STRtree([LineString([(-100, -500), (500, -500)])])


def test_cut_with_frontage_buildable_ground_and_the_stretch_on_the_parcel():
    ground = box(250, 20, 330, 100)  # 0.6 ha, just east of the turbine end
    r = subdivision.measure(site(buildable=ground, buildable_acres=1.6), ROAD_SOUTH, SETTINGS)
    assert r.homestead_cut is True and r.flags == ()
    assert r.frontage_m == pytest.approx(400 + 2 * 15.24 * 0.9, abs=10)  # along the south edge
    assert r.stretch_share == 1 and r.home_site_basis == privacy.BUILDABLE
    assert r.home_site.equals(Point(250, 50)) and r.wire_m == pytest.approx(50)
    assert r.driveway_m == pytest.approx(60)  # straight south to the road
    assert r.access_on_parcel is True
    # Full points but buildable: 1.6 of the 2 acres wanted.
    assert r.subdivision_score == pytest.approx(100 - 25 * (1 - 1.6 / 2))
    assert r.piece_acres == SETTINGS.min_piece_acres  # strips and lot under 5 acres


def test_no_frontage_and_a_stretch_mostly_on_a_neighbor():
    stretch = LineString([(200, 50), (200, 1000)])  # runs 600 m off the north edge
    r = subdivision.measure(site(stretch=stretch, buildable_acres=0.0), ROAD_FAR, SETTINGS)
    assert r.homestead_cut is False and r.frontage_m == 0 and r.driveway_m is None
    assert r.flags == (
        subdivision.NO_ROAD_FRONTAGE,
        subdivision.STRETCH_CROSSES_PARCELS,
        subdivision.NO_HOUSE_SITE,
    )
    assert r.stretch_share == pytest.approx((350 + 15.24) / 950, abs=0.01)
    assert r.subdivision_score == pytest.approx(25 * r.stretch_share)  # the rest score 0


def test_turbine_and_road_on_separate_parts_of_the_parcel():
    # Two lots split by a neighbor's 100 m strip: the road only touches the southern one.
    parcel = shapely.MultiPolygon([box(0, -300, 400, -150), box(0, 0, 400, 400)])
    road = shapely.STRtree([LineString([(-100, -310), (500, -310)])])
    r = subdivision.measure(site(parcel=parcel, buildable_acres=0.0), road, SETTINGS)
    assert r.frontage_m > 0 and r.access_on_parcel is False
    assert subdivision.ACCESS_CROSSES_PARCELS in r.flags and r.homestead_cut is False


def test_unknowns_are_left_out():
    r = subdivision.measure(site(), None, SETTINGS)  # no roads, buildable not computed
    assert r.homestead_cut is None and r.frontage_m is None and r.access_on_parcel is None
    assert r.flags == (subdivision.NO_ROADS_LOADED,)
    assert r.subdivision_score == 100  # only hydro retention known, and it's all there


def test_long_driveway_is_flagged_and_scored_down():
    parcel = box(0, -1600, 400, 400)
    road = shapely.STRtree([LineString([(-100, -1610), (500, -1610)])])
    ground = box(150, 0, 250, 40)
    r = subdivision.measure(
        site(parcel=parcel, buildable=ground, buildable_acres=2.0), road, SETTINGS
    )
    assert r.homestead_cut is True and r.flags == (subdivision.LONG_DRIVEWAY,)
    assert r.driveway_m == pytest.approx(1650)  # from (200, 40): 5,413 ft, no access points
    assert r.subdivision_score == pytest.approx(75)
    assert r.piece_acres > SETTINGS.min_piece_acres  # a 100 ft strip a mile long
