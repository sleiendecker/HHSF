"""Gauge Q90 and basin coverage, against canned NWIS/WBD responses (no network)."""

import json

import httpx
import pytest
from shapely.geometry import LineString, box

from hhsf.analysis import gauges
from hhsf.analysis.gauges import GaugeError, GaugeTransfer
from hhsf.core import crs
from hhsf.core.config import ReferenceGauge
from tests.unit.test_hydro import ASSUMPTIONS, GAUGE

SITE_RDB = "#\n#comment\nagency_cd\tsite_no\tdrain_area_va\n5s\t15s\t8s\nUSGS\t03571000\t402\n"


def dv(values: list[tuple[str, list[str]]]) -> dict:
    return {
        "value": {
            "timeSeries": [
                {"values": [{"value": [{"value": v, "qualifiers": q} for v, q in values]}]}
            ]
        }
    }


def test_q90_is_the_flow_exceeded_90_percent_of_days():
    days = [float(v) for v in range(1, 101)]  # 1..100 cfs
    assert gauges.exceedance_flow(days) == pytest.approx(10.9)
    assert gauges.exceedance_flow(days, 50) == pytest.approx(50.5)


def test_q90_of_nothing_is_an_error():
    with pytest.raises(GaugeError):
        gauges.exceedance_flow([])


def test_only_approved_non_negative_values_are_used():
    body = dv([("10", ["A"]), ("12", ["A", "e"]), ("99", ["P"]), ("-999999", ["A"])])
    assert gauges.approved_values(body) == [10.0, 12.0]


@pytest.mark.parametrize("body", [{}, {"value": {"timeSeries": []}}])
def test_missing_series_is_an_error(body):
    with pytest.raises(GaugeError):
        gauges.approved_values(body)


def test_drainage_area_from_site_rdb():
    assert gauges.parse_drainage_area(SITE_RDB) == 402.0
    with pytest.raises(GaugeError):
        gauges.parse_drainage_area("# nothing\n")
    with pytest.raises(GaugeError):
        gauges.parse_drainage_area("site_no\tdrain_area_va\n5s\t8s\n03571000\t\n")


def fake_nwis(days: int):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/dv/"):
            return httpx.Response(200, json=dv([(str(i % 100 + 1), ["A"]) for i in range(days)]))
        return httpx.Response(200, text=SITE_RDB)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_reference_computes_and_caches(tmp_path):
    gauge = ReferenceGauge(site_no="03571000", huc8="06020004")
    days = 11 * 366
    ref, record = gauges.fetch_reference(
        gauge, ASSUMPTIONS.gauge_transfer, tmp_path, fake_nwis(days)
    )
    assert ref.drainage_area_sqmi == 402.0
    assert ref.q90_cfs == pytest.approx(10.0, abs=1)
    assert record["approved_days"] == days
    assert len(list(tmp_path.iterdir())) == 2  # dv + site cached

    def offline(request):
        raise httpx.ConnectError("offline", request=request)

    again, _ = gauges.fetch_reference(
        gauge,
        ASSUMPTIONS.gauge_transfer,
        tmp_path,
        httpx.Client(transport=httpx.MockTransport(offline)),
    )
    assert again == ref


def test_short_record_is_refused(tmp_path):
    gauge = ReferenceGauge(site_no="03571000", huc8="06020004")
    with pytest.raises(GaugeError, match="years"):
        gauges.fetch_reference(gauge, ASSUMPTIONS.gauge_transfer, tmp_path, fake_nwis(365))


def test_http_errors_and_outages_are_gauge_errors(tmp_path):
    gauge = ReferenceGauge(site_no="03571000", huc8="06020004")
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    with pytest.raises(GaugeError, match="503"):
        gauges.fetch_reference(gauge, ASSUMPTIONS.gauge_transfer, tmp_path, http)

    def offline(request):
        raise httpx.ConnectError("offline", request=request)

    http = httpx.Client(transport=httpx.MockTransport(offline))
    with pytest.raises(GaugeError, match="unreachable"):
        gauges.fetch_reference(gauge, ASSUMPTIONS.gauge_transfer, tmp_path / "x", http)


def test_fetch_basin_reads_cached_wbd(tmp_path):
    feature = {
        "type": "Feature",
        "geometry": json.loads(json.dumps(box(0, 0, 1, 1).__geo_interface__)),
    }
    (tmp_path / "wbd_hu8_06020004.geojson").write_text(json.dumps({"features": [feature]}))
    assert gauges.fetch_basin("06020004", tmp_path).equals(box(0, 0, 1, 1))
    (tmp_path / "wbd_hu8_00000000.geojson").write_text(json.dumps({"features": []}))
    with pytest.raises(GaugeError):
        gauges.fetch_basin("00000000", tmp_path)


def test_covers_uses_the_downstream_end():
    basin = box(1_000_500, 1_399_000, 1_002_000, 1_401_000)  # analysis CRS
    transfer = GaugeTransfer(GAUGE, basin, {})
    into = crs.to_storage(LineString([(1_000_000, 1_400_000), (1_001_000, 1_400_000)]))
    out_of = crs.to_storage(LineString([(1_001_000, 1_400_000), (1_000_000, 1_400_000)]))
    assert transfer.covers(into)
    assert not transfer.covers(out_of)
