"""The StreamStats adapter against a fake service (no network)."""

import json

import httpx
import pytest

from hhsf.analysis import streamstats
from hhsf.analysis.streamstats import PointFailed, ServiceUnavailable, StreamStats
from tests.unit.test_hydro import SETTINGS

SERVICE = streamstats.ServiceConfig(
    version="test",
    endpoints=streamstats.Endpoints(
        delineate="https://ss.test/delineate/{region}",
        regression_regions="https://ss.test/regions",
        scenarios="https://ss.test/scenarios",
        basin_characteristics="https://ss.test/bc",
        estimate="https://ss.test/estimate",
    ),
    retries=1,
)
LIMITS = {
    "DRNAREA": {"min": 1.3, "max": 14441.0},
    "RECESS": {"min": 32.0, "max": 175.0},
}
DELINEATION = {
    "stateAbbreviation": "tn",
    "bcrequest": {
        "bcLabels": "*",
        "wsresp": {
            "workspace_id": "",
            "featurecollection": [
                [
                    {"name": "globalwatershedpoint", "feature": {"features": []}},
                    {
                        "name": "globalwatershed",
                        "feature": {
                            "features": [
                                {
                                    "geometry": {"type": "Polygon", "coordinates": []},
                                    "properties": {"GlobalWshd": 1},
                                }
                            ]
                        },
                    },
                ]
            ],
        },
    },
}


class FakeService:
    """Answers the four StreamStats calls; records what was asked."""

    def __init__(self, basin=None, regions=("GC1419",), fail_path=None, fail_status=502):
        self.basin = basin or {"DRNAREA": 4.0, "RECESS": 80.0}
        self.regions = regions
        self.fail_path = fail_path
        self.fail_status = fail_status
        self.calls: list[str] = []
        self.bodies: dict[str, object] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(path)
        if request.content:
            self.bodies[path] = json.loads(request.content)
        if path == self.fail_path:
            return httpx.Response(self.fail_status, text="Bad Gateway")
        if path.startswith("/delineate/"):
            return httpx.Response(200, json=DELINEATION)
        if path == "/regions":
            return httpx.Response(200, json=[{"code": c} for c in self.regions])
        if path == "/scenarios":
            params = [{"code": c, "limits": lim, "value": -999.99} for c, lim in LIMITS.items()]
            return httpx.Response(
                200, json=[{"regressionRegions": [{"code": "GC1419", "parameters": params}]}]
            )
        if path == "/bc":
            return httpx.Response(
                200, json=[{"code": c.lower(), "value": v} for c, v in self.basin.items()]
            )
        if path == "/estimate":
            results = [
                {"code": "D95", "value": 0.5, "errors": []},
                {"code": "D90", "value": 0.72, "errors": [{"code": "ASEp", "value": 60.2}]},
            ]
            return httpx.Response(200, json=[{"regressionRegions": [{"results": results}]}])
        return httpx.Response(404)


def client(service: FakeService, tmp_path) -> StreamStats:
    http = httpx.Client(transport=httpx.MockTransport(service))
    return StreamStats(SETTINGS, SERVICE, cache_dir=tmp_path, client=http, backoff_s=0)


def test_q90_from_the_four_step_workflow(tmp_path):
    fake = FakeService()
    est = client(fake, tmp_path).low_flow(35.5, -85.5)
    assert est.value_cfs == 0.72
    assert est.std_error_pct == 60.2
    assert est.regression_region == "GC1419"
    assert est.drainage_area_sqmi == 4.0
    assert est.out_of_range == ()
    assert not est.below_min_drainage_area
    assert fake.bodies["/bc"]["bcrequest"]["bcLabels"] == "DRNAREA;RECESS"
    sent = fake.bodies["/estimate"][0]["regressionRegions"][0]["parameters"]
    assert {p["code"]: p["value"] for p in sent} == {"DRNAREA": 4.0, "RECESS": 80.0}


def test_second_call_is_served_from_cache(tmp_path):
    fake = FakeService()
    first = client(fake, tmp_path).low_flow(35.5, -85.5)
    calls = len(fake.calls)
    again = client(fake, tmp_path).low_flow(35.5, -85.5)  # new client, same cache dir
    assert again == first
    assert len(fake.calls) == calls


def test_below_min_drainage_area_skips_the_estimate(tmp_path):
    fake = FakeService(basin={"DRNAREA": 0.9, "RECESS": 80.0})
    est = client(fake, tmp_path).low_flow(35.5, -85.5)
    assert est.below_min_drainage_area
    assert est.value_cfs is None
    assert "/estimate" not in fake.calls


def test_other_parameters_out_of_range_are_reported(tmp_path):
    fake = FakeService(basin={"DRNAREA": 4.0, "RECESS": 200.0})
    est = client(fake, tmp_path).low_flow(35.5, -85.5)
    assert est.value_cfs == 0.72
    assert est.out_of_range == ("RECESS",)


def test_service_outage_raises_unavailable_after_retries(tmp_path):
    fake = FakeService(fail_path="/bc")
    with pytest.raises(ServiceUnavailable):
        client(fake, tmp_path).low_flow(35.5, -85.5)
    assert fake.calls.count("/bc") == 2  # first try + 1 retry


def test_rate_limit_counts_as_unavailable(tmp_path):
    with pytest.raises(ServiceUnavailable):
        client(FakeService(fail_path="/regions", fail_status=429), tmp_path).low_flow(35.5, -85.5)


def test_outage_after_delineation_keeps_the_delineation_cached(tmp_path):
    with pytest.raises(ServiceUnavailable):
        client(FakeService(fail_path="/bc"), tmp_path).low_flow(35.5, -85.5)
    fake = FakeService()
    client(fake, tmp_path).low_flow(35.5, -85.5)
    assert not any(c.startswith("/delineate/") for c in fake.calls)
    assert "/regions" not in fake.calls


def test_client_error_is_a_point_failure(tmp_path):
    with pytest.raises(PointFailed):
        client(FakeService(fail_path="/estimate", fail_status=400), tmp_path).low_flow(35.5, -85.5)


def test_timeout_is_unavailable(tmp_path):
    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    http = httpx.Client(transport=httpx.MockTransport(timeout))
    ss = StreamStats(SETTINGS, SERVICE, cache_dir=tmp_path, client=http, backoff_s=0)
    with pytest.raises(ServiceUnavailable):
        ss.low_flow(35.5, -85.5)


def test_no_low_flow_region_is_a_point_failure(tmp_path):
    with pytest.raises(PointFailed, match="no low-flow regression region"):
        client(FakeService(regions=("GC2015",)), tmp_path).low_flow(35.5, -85.5)


def test_missing_basin_characteristic_is_a_point_failure(tmp_path):
    with pytest.raises(PointFailed, match="RECESS"):
        client(FakeService(basin={"DRNAREA": 4.0}), tmp_path).low_flow(35.5, -85.5)


def test_delineation_without_a_watershed_is_a_point_failure():
    with pytest.raises(PointFailed):
        streamstats._watershed_geometry({"bcrequest": {}})
    empty = json.loads(json.dumps(DELINEATION))
    empty["bcrequest"]["wsresp"]["featurecollection"][0][1]["feature"]["features"] = []
    with pytest.raises(PointFailed, match="no watershed"):
        streamstats._watershed_geometry(empty)


def test_parameter_values_accepts_a_wrapped_list():
    body = {"parameters": [{"code": "drnarea", "value": 2}, {"code": "X", "value": None}]}
    assert streamstats._parameter_values(body) == {"DRNAREA": 2.0}


def test_service_config_loads_from_data_sources():
    cfg = streamstats.load_service_config()
    assert cfg.max_concurrent_requests == 4
    assert "{region}" in cfg.endpoints.delineate
