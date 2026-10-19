"""Per-reach orchestration without a database: StreamStats use, fallback, breaker."""

from shapely.geometry import LineString

from hhsf.analysis import hydro, run
from hhsf.analysis.streamstats import PointFailed, ServiceConfig, ServiceUnavailable
from hhsf.core import crs, units
from tests.unit.test_hydro import ASSUMPTIONS, BIG_SQKM, SMALL_SQKM, estimate
from tests.unit.test_streamstats import SERVICE

LINE = crs.to_storage(LineString([(1_000_000, 1_400_000), (1_001_000, 1_400_000)]))


def reach(da_sqkm=BIG_SQKM, top=200.0, bottom=100.0, mean_flow=5.0, i=1) -> run.Reach:
    return run.Reach(i, 10_000 + i, 1000.0, da_sqkm, top, bottom, mean_flow, LINE)


class Source:
    def __init__(self, result=None, error=None):
        self.result = result or estimate()
        self.error = error
        self.points = []

    def low_flow(self, lat, lon):
        self.points.append((lat, lon))
        if self.error:
            raise self.error
        return self.result


def test_streamstats_q90_drives_power():
    c = run.analyze_reach(reach(), ASSUMPTIONS, Source())
    assert c.flow.method == hydro.FLOW_METHOD_STREAMSTATS
    assert c.head.gross_m == 100
    assert c.power.confidence == "medium"
    assert c.power.firm_power_w > 0


def test_small_basins_never_call_streamstats():
    source = Source()
    c = run.analyze_reach(reach(da_sqkm=SMALL_SQKM), ASSUMPTIONS, source)
    assert source.points == []
    assert c.flow.confidence == "below_applicability"
    assert c.power.firm_power_w is None
    assert c.head.gross_m == 100  # still ranked on head (D-09)


def test_no_source_means_erom_fallback():
    c = run.analyze_reach(reach(), ASSUMPTIONS, None)
    assert c.flow.method == hydro.FLOW_METHOD_EROM
    assert c.flow.flags[0] == "streamstats_disabled"
    assert c.power.confidence == "low"


def test_point_failure_falls_back_without_tripping_the_breaker():
    breaker = run._Breaker(1)
    c = run.analyze_reach(reach(), ASSUMPTIONS, Source(error=PointFailed("x")), breaker)
    assert c.flow.flags[0] == "streamstats_point_failed"
    assert not breaker.open


def test_breaker_stops_calls_after_consecutive_outages():
    source = Source(error=ServiceUnavailable("502"))
    service = ServiceConfig(**{**SERVICE.model_dump(), "max_consecutive_failures": 2})
    reaches = [reach(i=i) for i in range(5)]
    corridors = run.analyze_reaches(reaches, ASSUMPTIONS, source, service, echo=lambda _: None)
    assert len(corridors) == 5
    assert len(source.points) <= 2 + service.max_concurrent_requests
    assert all(c.flow.flags[0] == "streamstats_unavailable" for c in corridors)


def test_success_resets_the_breaker():
    breaker = run._Breaker(2)
    breaker.record(False)
    breaker.record(True)
    breaker.record(False)
    assert not breaker.open


def test_summary_counts_power_target():
    corridors = [
        run.analyze_reach(reach(), ASSUMPTIONS, Source()),
        run.analyze_reach(reach(da_sqkm=SMALL_SQKM), ASSUMPTIONS, Source()),
        run.analyze_reach(reach(top=100.5), ASSUMPTIONS, None),
    ]
    s = run.summarize(corridors, ASSUMPTIONS)
    assert s["reaches"] == 3
    assert s["with_power"] == 2
    assert s["meets_power_target"] == 1
    assert s["meets_power_target_medium_confidence"] == 1
    assert s["flow_confidence"] == {"medium": 1, "below_applicability": 1, "low": 1}


def test_wants_streamstats_uses_regression_minimum():
    s = ASSUMPTIONS.streamstats
    assert run.wants_streamstats(reach(da_sqkm=units.sqmi_to_sqkm(1.3)), s)
    assert not run.wants_streamstats(reach(da_sqkm=None), s)


def test_gauge_is_used_only_for_reaches_in_its_basin():
    from shapely.geometry import box

    from hhsf.analysis.gauges import GaugeTransfer
    from tests.unit.test_hydro import GAUGE

    inside = GaugeTransfer(GAUGE, box(1_000_500, 1_399_000, 1_002_000, 1_401_000), {})
    outside = GaugeTransfer(GAUGE, box(0, 0, 1, 1), {})
    c = run.analyze_reach(reach(), ASSUMPTIONS, None, gauge=inside)
    assert c.flow.method == hydro.FLOW_METHOD_GAUGE
    assert c.flow.flags[0] == "streamstats_disabled"
    c = run.analyze_reach(reach(), ASSUMPTIONS, None, gauge=outside)
    assert c.flow.method == hydro.FLOW_METHOD_EROM
