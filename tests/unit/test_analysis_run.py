"""Per-reach orchestration without a database: StreamStats use, fallback, breaker."""

import pytest
from shapely.geometry import LineString, Point, box

from hhsf.analysis import hydro, run
from hhsf.analysis.streamstats import PointFailed, ServiceConfig, ServiceUnavailable
from hhsf.core import crs, units
from tests.unit.test_hydro import ASSUMPTIONS, BIG_SQKM, SMALL_SQKM, estimate
from tests.unit.test_streamstats import SERVICE

LINE = crs.to_storage(LineString([(1_000_000, 1_400_000), (1_001_000, 1_400_000)]))


def reach(
    da_sqkm=BIG_SQKM, top=200.0, bottom=100.0, mean_flow=5.0, i=1, upstream=None
) -> run.Reach:
    return run.Reach(i, 10_000 + i, 1000.0, da_sqkm, top, bottom, mean_flow, LINE, upstream)


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


class Answers:
    """A source answering from a list, one item per call (an exception is raised)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.points = []

    def low_flow(self, lat, lon):
        self.points.append(crs.to_analysis(Point(lon, lat)))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


WRONG_BASIN = estimate(drainage_area_sqmi=units.sqkm_to_sqmi(40.0))


def area(sqkm):
    return estimate(drainage_area_sqmi=units.sqkm_to_sqmi(sqkm))


def test_wrong_basin_at_the_outlet_retries_up_the_reach():
    # 10 km2 at the outlet, 6 km2 flowing in: 9 km2 a quarter of the way up.
    source = Answers(WRONG_BASIN, area(9.1))
    c = run.analyze_reach(reach(upstream=6.0), ASSUMPTIONS, source)
    assert c.flow.method == hydro.FLOW_METHOD_STREAMSTATS
    assert c.flow.confidence == "medium"
    assert c.flow.flags == ["streamstats_pour_point_moved"]
    assert c.flow.firm_flow_cfs == pytest.approx(0.8 * BIG_SQKM / 9.0)
    assert c.flow.streamstats_drainage_area_sqkm == pytest.approx(9.1)
    outlet, moved = source.points
    assert outlet.distance(Point(1_000_970, 1_400_000)) < 0.01
    assert moved.distance(Point(1_000_750, 1_400_000)) < 0.01


def test_retry_skips_failed_and_wrong_points_until_one_matches():
    source = Answers(WRONG_BASIN, PointFailed("excluded"), area(0.3), area(7.1))
    c = run.analyze_reach(reach(upstream=6.0), ASSUMPTIONS, source)
    assert len(source.points) == 4
    assert c.flow.method == hydro.FLOW_METHOD_STREAMSTATS
    assert c.flow.firm_flow_cfs == pytest.approx(0.8 * BIG_SQKM / 7.0)


def test_no_matching_point_keeps_the_mismatch_fallback():
    source = Answers(WRONG_BASIN, area(0.3), area(0.3), area(0.3))
    c = run.analyze_reach(reach(upstream=6.0), ASSUMPTIONS, source)
    assert c.flow.method == hydro.FLOW_METHOD_EROM
    assert c.flow.flags == ["streamstats_drainage_area_mismatch", "mean_flow_not_q90"]
    assert c.flow.streamstats_drainage_area_sqkm == pytest.approx(40.0)  # the outlet's answer


def test_retry_skips_points_draining_too_little_of_the_outlet():
    # Headwater: 7.5 km2 at 25%, 5 km2 at 50%, 2.5 km2 at 75% (below half the outlet).
    source = Answers(WRONG_BASIN, area(0.3), area(0.3))
    c = run.analyze_reach(reach(upstream=None), ASSUMPTIONS, source)
    assert len(source.points) == 3
    assert c.flow.method == hydro.FLOW_METHOD_EROM


def test_matching_outlet_is_never_retried():
    source = Answers(area(BIG_SQKM * 1.2))
    run.analyze_reach(reach(upstream=6.0), ASSUMPTIONS, source)
    assert len(source.points) == 1


def test_outage_during_retry_counts_as_unavailable():
    breaker = run._Breaker(5)
    source = Answers(WRONG_BASIN, ServiceUnavailable("502"))
    c = run.analyze_reach(reach(upstream=6.0), ASSUMPTIONS, source, breaker)
    assert c.flow.flags[0] == "streamstats_unavailable"
    assert breaker.consecutive == 1


def test_summary_counts_moved_pour_points():
    c = run.analyze_reach(reach(upstream=6.0), ASSUMPTIONS, Answers(WRONG_BASIN, area(9.0)))
    assert run.summarize([c], ASSUMPTIONS)["streamstats_pour_point_moved"] == 1


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


def test_open_breaker_stays_open_when_a_late_answer_arrives():
    # An answer already in flight when the breaker opened must not switch the service back on.
    breaker = run._Breaker(2)
    breaker.record(False)
    breaker.record(False)
    breaker.record(True)
    assert breaker.open


def test_run_warns_whenever_a_reach_found_streamstats_down():
    messages: list[str] = []
    source = Source(error=ServiceUnavailable("502"))
    run.analyze_reaches([reach()], ASSUMPTIONS, source, SERVICE, echo=messages.append)
    assert messages[-1].startswith("  StreamStats looks unavailable: 1 reaches got a fallback")


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


def basins(*codes_and_boxes):
    from hhsf.analysis.basins import Basins

    return Basins(dict(codes_and_boxes))


# LINE runs east from x = 1,000,000 to 1,001,000 (analysis CRS); its outlet is the east end.
EAST = box(1_000_500, 1_399_000, 1_002_000, 1_401_000)
WEST = box(998_000, 1_399_000, 1_000_500, 1_401_000)
WESTWARD = crs.to_storage(LineString([(1_001_000, 1_400_000), (1_000_000, 1_400_000)]))


def test_small_basins_get_the_median_yield_of_their_huc8():
    big = [
        run.analyze_reach(reach(i=i), ASSUMPTIONS, Source(estimate(value_cfs=q)))
        for i, q in enumerate([0.6, 0.8, 1.0], 1)
    ]
    small = run.analyze_reach(reach(da_sqkm=SMALL_SQKM, i=4), ASSUMPTIONS, Source())
    used = run.fill_regional_yield([*big, small], basins(("06020001", EAST)), ASSUMPTIONS)

    median = 0.8 / units.sqkm_to_sqmi(BIG_SQKM)
    assert small.flow.method == hydro.FLOW_METHOD_REGIONAL
    assert small.flow.firm_flow_cfs == pytest.approx(median * units.sqkm_to_sqmi(SMALL_SQKM))
    assert small.flow.flags == [
        "below_regression_min_drainage_area",
        "regional_yield_basin:06020001",
    ]
    assert small.power.firm_power_w > 0 and small.power.confidence == "low"
    assert [c.flow.method for c in big] == [hydro.FLOW_METHOD_STREAMSTATS] * 3
    assert used == {
        "06020001": {"yield_cfs_per_sqmi": median, "streamstats_reaches": 3, "reaches_filled": 1}
    }
    assert run.summarize([*big, small], ASSUMPTIONS)["below_regression_min_drainage_area"] == 1


def test_small_basins_without_enough_streamstats_neighbours_stay_head_only():
    big = [run.analyze_reach(reach(i=i), ASSUMPTIONS, Source()) for i in (1, 2, 3)]
    # Drains west, into a basin with no StreamStats reaches of its own (and no HUC6 kin).
    small = run.analyze_reach(
        run.Reach(4, 10_004, 1000.0, SMALL_SQKM, 200.0, 100.0, 5.0, WESTWARD), ASSUMPTIONS, None
    )
    stray = run.analyze_reach(  # outlet in none of the basins
        run.Reach(5, 10_005, 1000.0, SMALL_SQKM, 200.0, 100.0, 5.0, LINE), ASSUMPTIONS, None
    )
    used = run.fill_regional_yield([*big, small, stray], basins(("06020001", WEST)), ASSUMPTIONS)
    assert used == {}
    assert small.flow.confidence == "below_applicability"
    assert stray.flow.confidence == "below_applicability"
    assert small.power.firm_power_w is None


def test_small_basins_on_the_gauge_switch_to_the_regional_yield():
    from hhsf.analysis.gauges import GaugeTransfer

    gauge = GaugeTransfer(hydro.GaugeReference("03571000", 80.0, 400.0), EAST, {})
    big = [run.analyze_reach(reach(i=i), ASSUMPTIONS, Source(), gauge=gauge) for i in (1, 2, 3)]
    small = run.analyze_reach(reach(da_sqkm=SMALL_SQKM, i=4), ASSUMPTIONS, Source(), gauge=gauge)
    assert small.flow.method == hydro.FLOW_METHOD_GAUGE
    run.fill_regional_yield([*big, small], basins(("06020004", EAST)), ASSUMPTIONS)
    assert small.flow.method == hydro.FLOW_METHOD_REGIONAL
    assert small.flow.reference_gauge is None
    assert "regional_yield_basin:06020004" in small.flow.flags


def test_small_basin_keeps_the_gauge_when_no_regional_yield_is_found():
    from hhsf.analysis.gauges import GaugeTransfer

    gauge = GaugeTransfer(hydro.GaugeReference("03571000", 80.0, 400.0), EAST, {})
    small = run.analyze_reach(reach(da_sqkm=SMALL_SQKM), ASSUMPTIONS, None, gauge=gauge)
    assert run.fill_regional_yield([small], basins(("06020004", EAST)), ASSUMPTIONS) == {}
    assert small.flow.method == hydro.FLOW_METHOD_GAUGE


def test_mean_flow_fallback_switches_to_the_regional_yield():
    big = [
        run.analyze_reach(reach(i=i), ASSUMPTIONS, Source(estimate(value_cfs=q)))
        for i, q in enumerate([0.6, 0.8, 1.0], 1)
    ]
    # No StreamStats answer for this one (here: switched off), so it fell back to the mean flow.
    fallback = run.analyze_reach(reach(i=4), ASSUMPTIONS, None)
    assert fallback.flow.method == hydro.FLOW_METHOD_EROM
    used = run.fill_regional_yield([*big, fallback], basins(("06030001", EAST)), ASSUMPTIONS)
    assert fallback.flow.method == hydro.FLOW_METHOD_REGIONAL
    assert fallback.flow.firm_flow_cfs == pytest.approx(0.8)  # same area as the median reach
    assert fallback.flow.flags == ["streamstats_disabled", "regional_yield_basin:06030001"]
    assert used["06030001"]["reaches_filled"] == 1
