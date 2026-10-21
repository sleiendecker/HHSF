import json

import httpx
import pytest
from shapely.geometry import MultiPolygon, box, mapping

from hhsf.analysis import drive_time

ORIGIN = drive_time.Origin(key="test", name="Test origin", lat=35.0, lon=-85.3)
CFG = drive_time.DriveTimeConfig(
    origin=ORIGIN,
    minutes=60,
    end_on_roads={"min_road_class": "residential"},
    batch_size=2,
)


def cell(i, minutes, km=10.0, end=None):
    c = {"from_index": 0, "to_index": i, "time": None if minutes is None else minutes * 60}
    c["distance"] = None if minutes is None else km
    if end:
        c["end_lon"], c["end_lat"] = end
    return c


def county(geoid, name, geom, state="47"):
    return {"properties": {"GEOID": geoid, "NAME": name, "STATE": state}, "geometry": mapping(geom)}


def test_config_file_loads():
    cfg = drive_time.load_config()
    assert cfg.minutes == 60  # D-02
    assert cfg.origin.key == "downtown_chattanooga"
    assert cfg.end_on_roads["min_road_class"] == "residential"


def test_parse_isochrone_keeps_polygons_only():
    body = {
        "features": [
            {"geometry": mapping(box(0, 0, 1, 1)), "properties": {"contour": 60}},
            {"geometry": {"type": "Point", "coordinates": [0.5, 0.5]}, "properties": {}},
        ]
    }
    area = drive_time.parse_isochrone(body)
    assert isinstance(area, MultiPolygon) and area.area == 1


def test_parse_isochrone_without_polygons_is_an_error():
    with pytest.raises(RuntimeError):
        drive_time.parse_isochrone({"features": []})


def test_parse_matrix_orders_cells_and_measures_the_road_point():
    # 0.01 degrees of latitude is about 1.1 km.
    body = {"sources_to_targets": [[cell(1, None), cell(0, 30, 25.0, end=(-85.3, 35.01))]]}
    times = drive_time.parse_matrix(body, [(-85.3, 35.0), (-85.4, 35.1)])
    assert times[0].minutes == 30 and times[0].distance_km == 25.0
    assert times[0].road_point_m == pytest.approx(1109, rel=0.02)  # Albers stretches ~1% here
    assert times[1] == drive_time.DriveTime(None, None)


def test_parse_matrix_checks_every_point_was_answered():
    with pytest.raises(RuntimeError):
        drive_time.parse_matrix({"sources_to_targets": [[cell(0, 5)]]}, [(0, 0), (1, 1)])


def test_status_version_names_the_graph_date():
    s = drive_time.status_version({"version": "3.9.1", "tileset_last_modified": 1792500000})
    assert s == "Valhalla 3.9.1, road graph built 2026-10-20"
    assert drive_time.status_version({"version": "3.9.1"}) == "Valhalla 3.9.1"


def test_county_shares_biggest_share_first_and_drops_counties_outside():
    area = box(-85.5, 35.0, -85.0, 35.5)
    shares = drive_time.county_shares(
        area,
        [
            county("47001", "Half", box(-85.25, 35.0, -84.75, 35.5)),
            county("13001", "Whole", box(-85.4, 35.1, -85.3, 35.2), state="13"),
            county("01001", "Outside", box(-84.0, 35.0, -83.5, 35.5), state="01"),
        ],
    )
    assert [s.label for s in shares] == ["Whole, GA", "Half, TN"]
    assert shares[0].share == pytest.approx(1.0)
    assert shares[1].share == pytest.approx(0.5, abs=0.01)


class FakeValhalla:
    """Answers /sources_to_targets: 10 min per degree of longitude from the origin.

    Points listed in `fails` make the whole request fail (as Valhalla 3.9 sometimes
    does); points in `unreachable` have no route unless the search filter is off.
    """

    def __init__(self, fails=(), unreachable=()):
        self.fails, self.unreachable, self.requests = set(fails), set(unreachable), []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        targets = [(t["lon"], t["lat"]) for t in body["targets"]]
        filtered = bool(body["targets"][0]["search_filter"])
        self.requests.append((len(targets), filtered))
        if self.fails & set(targets):
            return httpx.Response(500, json={"error": "Could not find candidate edge"})
        cells = [
            cell(i, None if filtered and t in self.unreachable else abs(t[0] + 85.3) * 10)
            for i, t in enumerate(targets)
        ]
        return httpx.Response(200, json={"sources_to_targets": [cells]})


def client(fake):
    return drive_time.Valhalla(
        CFG, httpx.Client(base_url="http://valhalla", transport=httpx.MockTransport(fake))
    )


POINTS = [(-85.3 - i, 35.0) for i in range(5)]


def test_drive_times_come_back_in_order_in_batches():
    fake = FakeValhalla()
    times = client(fake).drive_times(POINTS)
    assert [t.minutes for t in times] == pytest.approx([0, 10, 20, 30, 40])
    assert [n for n, _ in fake.requests] == [2, 2, 1]


def test_a_failing_point_is_split_off_and_gets_no_time():
    fake = FakeValhalla(fails={POINTS[1]})
    times = client(fake).drive_times(POINTS)
    assert [t.minutes for t in times] == pytest.approx([0, None, 20, 30, 40])


def test_points_with_no_route_to_a_public_road_try_any_road():
    fake = FakeValhalla(unreachable={POINTS[3]})
    times = client(fake).drive_times(POINTS)
    assert times[3].minutes == pytest.approx(30) and times[3].any_road
    assert not any(t.any_road for i, t in enumerate(times) if i != 3)
    assert fake.requests[-1] == (1, False)  # the retry, without the filter


def test_routing_down_says_how_to_start_it():
    def refuse(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(drive_time.RoutingUnavailable, match="make routing"):
        client(refuse).drive_times(POINTS)
