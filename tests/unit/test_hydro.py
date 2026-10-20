import pytest
from shapely.geometry import LineString, MultiLineString, Point

from hhsf.analysis import hydro
from hhsf.core import crs, units

SETTINGS = hydro.StreamStatsSettings(
    region="TN",
    statistic_group=5,
    statistic_code="D90",
    regression_regions=["GC1419", "GC1418"],
    min_drainage_area_sqmi=1.3,
    max_drainage_area_mismatch=0.25,
    pour_point_offset_m=30,
    pour_point_retry_shares=[0.25, 0.5, 0.75],
    min_pour_point_area_share=0.5,
)
ASSUMPTIONS = hydro.HydroAssumptions(
    power_target_w=1000,
    system_efficiency=0.5,
    net_head_factor=0.85,
    diversion_fraction=0.5,
    water_density_kg_m3=1000,
    gravity_m_s2=9.81,
    corridor=hydro.CorridorSettings(penstock_length_ft=1000, sample_spacing_m=10),
    streamstats=SETTINGS,
    gauge_transfer=hydro.GaugeTransferSettings(
        start="2001-10-01", end="2025-09-30", min_years=10, ratio_range=(0.5, 1.5)
    ),
    regional_yield=hydro.RegionalYieldSettings(min_reaches=3),
)
GAUGE = hydro.GaugeReference("03571000", q90_cfs=80.0, drainage_area_sqmi=400.0)
BIG_SQKM = 10.0  # 3.9 sq mi: above the 1.3 sq mi regression minimum
SMALL_SQKM = 2.0  # 0.77 sq mi: below it


def estimate(**kwargs) -> hydro.LowFlowEstimate:
    defaults = {
        "value_cfs": 0.8,
        "regression_region": "GC1419",
        "drainage_area_sqmi": units.sqkm_to_sqmi(BIG_SQKM),
        "std_error_pct": 60.2,
    }
    return hydro.LowFlowEstimate(**{**defaults, **kwargs})


def test_config_matches_decisions():
    a = hydro.load_assumptions()
    assert a.system_efficiency == 0.5  # D-06
    assert a.net_head_factor == 0.85  # D-07
    assert a.diversion_fraction == 0.5  # D-05
    assert a.power_target_w == 1000  # D-01
    assert a.streamstats.statistic_code == "D90"  # D-04
    assert a.corridor.penstock_length_ft == 1000  # D-08
    assert a.corridor.penstock_length_m == pytest.approx(304.8)


# --- head ---


def test_gross_head_is_drop_between_smoothed_end_elevations():
    h = hydro.head(552.71, 446.70, 0.85)
    assert h.gross_m == pytest.approx(106.01)
    assert h.net_m == pytest.approx(106.01 * 0.85)
    assert h.flag is None


def test_flat_reach_has_zero_head_not_null():
    h = hydro.head(546.2, 546.2, 0.85)
    assert h.gross_m == 0
    assert h.net_m == 0


@pytest.mark.parametrize("top,bottom", [(None, 100.0), (100.0, None), (None, None)])
def test_missing_elevation_gives_null_head(top, bottom):
    h = hydro.head(top, bottom, 0.85)
    assert h.gross_m is None and h.net_m is None
    assert h.flag == "missing_elevation"


def test_rising_reach_gives_null_head():
    h = hydro.head(100.0, 120.0, 0.85)
    assert h.gross_m is None
    assert h.flag == "elevation_rises_downstream"


# --- flow ---


def test_streamstats_q90_is_medium_confidence():
    f = hydro.firm_flow(BIG_SQKM, 5.0, estimate(), SETTINGS)
    assert f.firm_flow_cfs == 0.8
    assert f.statistic == "q90"
    assert f.method == hydro.FLOW_METHOD_STREAMSTATS
    assert f.confidence == "medium"
    assert f.flags == []
    assert f.std_error_pct == 60.2
    assert f.regression_region == "GC1419"
    assert f.streamstats_drainage_area_sqkm == pytest.approx(BIG_SQKM)


def test_streamstats_parameters_out_of_range_lower_confidence():
    f = hydro.firm_flow(BIG_SQKM, 5.0, estimate(out_of_range=("RECESS",)), SETTINGS)
    assert f.firm_flow_cfs == 0.8
    assert f.confidence == "low"
    assert f.flags == ["out_of_range:RECESS"]


def test_streamstats_below_min_drainage_area_is_null_not_mean_flow():
    est = estimate(value_cfs=None, below_min_drainage_area=True, drainage_area_sqmi=1.2)
    f = hydro.firm_flow(units.sqmi_to_sqkm(1.2), 5.0, est, SETTINGS)
    assert f.firm_flow_cfs is None
    assert f.confidence == "below_applicability"
    assert f.flags == ["below_regression_min_drainage_area"]
    assert f.regression_region == "GC1419"


def test_streamstats_wrong_basin_falls_back_to_mean_flow():
    est = estimate(drainage_area_sqmi=units.sqkm_to_sqmi(BIG_SQKM * 10))
    f = hydro.firm_flow(BIG_SQKM, 5.0, est, SETTINGS)
    assert f.firm_flow_cfs == 5.0
    assert f.method == hydro.FLOW_METHOD_EROM
    assert f.confidence == "low"
    assert f.flags == ["streamstats_drainage_area_mismatch", "mean_flow_not_q90"]


def test_moved_pour_point_q90_is_scaled_to_the_outlet():
    # StreamStats answered 25% up the reach, where NHDPlus drains 8 km2.
    est = estimate(drainage_area_sqmi=units.sqkm_to_sqmi(8.2), point_drainage_area_sqkm=8.0)
    f = hydro.firm_flow(BIG_SQKM, 5.0, est, SETTINGS)
    assert f.firm_flow_cfs == pytest.approx(0.8 * BIG_SQKM / 8.0)
    assert f.method == hydro.FLOW_METHOD_STREAMSTATS
    assert f.confidence == "medium"
    assert f.flags == ["streamstats_pour_point_moved"]
    assert f.streamstats_drainage_area_sqkm == pytest.approx(8.2)


def test_moved_pour_point_is_checked_against_the_area_where_it_is():
    # 10 km2 matches the outlet, but the point was where NHDPlus drains only 6 km2.
    est = estimate(point_drainage_area_sqkm=6.0)
    f = hydro.firm_flow(BIG_SQKM, 5.0, est, SETTINGS)
    assert f.method == hydro.FLOW_METHOD_EROM
    assert f.flags[0] == "streamstats_drainage_area_mismatch"


def test_moved_pour_point_out_of_range_is_low_with_both_flags():
    est = estimate(point_drainage_area_sqkm=BIG_SQKM, out_of_range=("RECESS",))
    f = hydro.firm_flow(BIG_SQKM, 5.0, est, SETTINGS)
    assert f.confidence == "low"
    assert f.flags == ["out_of_range:RECESS", "streamstats_pour_point_moved"]


def test_streamstats_without_a_value_falls_back():
    f = hydro.firm_flow(BIG_SQKM, 5.0, estimate(value_cfs=None), SETTINGS)
    assert f.method == hydro.FLOW_METHOD_EROM
    assert f.flags[0] == "streamstats_no_estimate"


def test_unavailable_streamstats_falls_back_to_erom_mean_flow_low_confidence():
    f = hydro.firm_flow(BIG_SQKM, 5.0, None, SETTINGS, "streamstats_unavailable")
    assert f.firm_flow_cfs == 5.0
    assert f.statistic == "mean_annual"
    assert f.method == hydro.FLOW_METHOD_EROM
    assert f.confidence == "low"
    assert f.flags == ["streamstats_unavailable", "mean_flow_not_q90"]


def test_fallback_still_flags_small_basins_with_null():
    f = hydro.firm_flow(SMALL_SQKM, 1.0, None, SETTINGS, "streamstats_unavailable")
    assert f.firm_flow_cfs is None
    assert f.confidence == "below_applicability"


def test_fallback_without_mean_flow_is_unavailable():
    f = hydro.firm_flow(BIG_SQKM, None, None, SETTINGS)
    assert f.firm_flow_cfs is None
    assert f.confidence == "unavailable"
    assert f.flags == ["streamstats_not_used", "missing_mean_flow"]


def test_missing_drainage_area_is_unavailable():
    f = hydro.firm_flow(None, 5.0, estimate(), SETTINGS)
    assert f.firm_flow_cfs is None
    assert f.confidence == "unavailable"


def test_regression_minimum_boundary():
    assert hydro.below_regression_minimum(units.sqmi_to_sqkm(1.29), SETTINGS)
    assert not hydro.below_regression_minimum(units.sqmi_to_sqkm(1.3), SETTINGS)


# --- power ---


def test_power_formula():
    # 1000 kg/m3 * 9.81 m/s2 * 0.1 m3/s * 50 m * 0.5 = 24,525 W
    assert hydro.power_w(0.1, 50, ASSUMPTIONS) == pytest.approx(24525)


def test_power_matches_decisions_rule_of_thumb():
    # D-01: watts ~= head (ft) x flow (gpm) / 10 at 50% efficiency: 100 ft x 100 gpm ~ 1 kW
    q = units.cfs_to_m3s(100 / units.GPM_PER_CFS)
    watts = hydro.power_w(q, 100 / units.FT_PER_M, ASSUMPTIONS)
    assert watts == pytest.approx(1000, rel=0.1)


def test_firm_power_uses_diverted_flow_and_net_head():
    flow = hydro.Flow(1.0, "q90", hydro.FLOW_METHOD_STREAMSTATS, "medium")
    h = hydro.head(200.0, 100.0, 0.85)
    p = hydro.firm_power(flow, h, ASSUMPTIONS)
    assert p.usable_flow_cfs == 0.5
    # 1000 * 9.81 * (0.5 cfs = 0.0141584 m3/s) * 85 m * 0.5
    assert p.firm_power_w == pytest.approx(5903.0, rel=1e-4)
    assert p.confidence == "medium"


def test_low_confidence_flow_gives_low_confidence_power():
    flow = hydro.Flow(1.0, "mean_annual", hydro.FLOW_METHOD_EROM, "low")
    p = hydro.firm_power(flow, hydro.head(200.0, 100.0, 0.85), ASSUMPTIONS)
    assert p.firm_power_w is not None
    assert p.confidence == "low"


def test_power_is_null_without_flow_or_head():
    no_flow = hydro.Flow(None, None, None, "below_applicability")
    with_flow = hydro.Flow(1.0, "q90", hydro.FLOW_METHOD_STREAMSTATS, "medium")
    assert hydro.firm_power(no_flow, hydro.head(200.0, 100.0, 0.85), ASSUMPTIONS) == (
        hydro.Power(None, None, "unavailable")
    )
    no_head = hydro.head(None, 1.0, 0.85)
    assert hydro.firm_power(with_flow, no_head, ASSUMPTIONS).firm_power_w is None


# --- pour point ---


def test_pour_point_is_just_above_the_downstream_end():
    # A 1 km line in the analysis CRS, drawn upstream -> downstream.
    line = crs.to_storage(LineString([(1_000_000, 1_400_000), (1_001_000, 1_400_000)]))
    p = crs.to_analysis(hydro.pour_point(line, 30))
    assert p.distance(Point(1_000_970, 1_400_000)) < 0.01


def test_pour_point_on_short_reach_stays_within_ten_percent():
    line = crs.to_storage(LineString([(1_000_000, 1_400_000), (1_000_100, 1_400_000)]))
    p = crs.to_analysis(hydro.pour_point(MultiLineString([line]), 30))
    assert p.distance(Point(1_000_090, 1_400_000)) < 0.01


def test_point_up_reach_measures_from_the_downstream_end():
    line = crs.to_storage(LineString([(1_000_000, 1_400_000), (1_001_000, 1_400_000)]))
    p = crs.to_analysis(hydro.point_up_reach(MultiLineString([line]), 0.25))
    assert p.distance(Point(1_000_750, 1_400_000)) < 0.01


def test_drainage_area_up_reach_interpolates_from_upstream_inflow():
    assert hydro.drainage_area_up_reach(10.0, 6.0, 0.25) == pytest.approx(9.0)
    assert hydro.drainage_area_up_reach(10.0, 6.0, 1.0) == pytest.approx(6.0)
    assert hydro.drainage_area_up_reach(10.0, None, 0.5) == pytest.approx(5.0)  # headwater


# --- gauge transfer ---


def test_gauge_transfer_scales_q90_by_drainage_area():
    flow, ratio, flags = hydro.gauge_transfer(units.sqmi_to_sqkm(4.0), GAUGE, (0.5, 1.5))
    assert ratio == pytest.approx(0.01)
    assert flow == pytest.approx(0.8)  # 80 cfs x 4/400
    assert flags == ["area_ratio_outside_0.5_1.5"]


def test_gauge_transfer_inside_ratio_range_has_no_ratio_flag():
    _, ratio, flags = hydro.gauge_transfer(units.sqmi_to_sqkm(300.0), GAUGE, (0.5, 1.5))
    assert ratio == pytest.approx(0.75)
    assert flags == []


def test_gauge_replaces_mean_flow_when_streamstats_is_down():
    f = hydro.firm_flow(BIG_SQKM, 5.0, None, SETTINGS, "streamstats_unavailable", GAUGE, (0.5, 1.5))
    assert f.firm_flow_cfs == pytest.approx(80.0 * units.sqkm_to_sqmi(BIG_SQKM) / 400)
    assert f.statistic == "q90"
    assert f.method == hydro.FLOW_METHOD_GAUGE
    assert f.confidence == "low"
    assert f.flags == ["streamstats_unavailable", "area_ratio_outside_0.5_1.5"]
    assert f.reference_gauge == "03571000"
    assert f.drainage_area_ratio == pytest.approx(units.sqkm_to_sqmi(BIG_SQKM) / 400)


def test_gauge_covers_basins_too_small_for_the_regression_with_flag_kept():
    f = hydro.firm_flow(SMALL_SQKM, 1.0, None, SETTINGS, None, GAUGE, (0.5, 1.5))
    assert f.firm_flow_cfs is not None
    assert f.method == hydro.FLOW_METHOD_GAUGE
    assert f.confidence == "low"
    assert f.flags[0] == "below_regression_min_drainage_area"


def test_streamstats_below_minimum_uses_gauge_when_available():
    est = estimate(value_cfs=None, below_min_drainage_area=True, drainage_area_sqmi=1.2)
    f = hydro.firm_flow(units.sqmi_to_sqkm(1.2), 5.0, est, SETTINGS, None, GAUGE, (0.5, 1.5))
    assert f.method == hydro.FLOW_METHOD_GAUGE
    assert f.flags[0] == "below_regression_min_drainage_area"
    assert f.regression_region == "GC1419"


def test_streamstats_q90_beats_the_gauge():
    f = hydro.firm_flow(BIG_SQKM, 5.0, estimate(), SETTINGS, None, GAUGE, (0.5, 1.5))
    assert f.method == hydro.FLOW_METHOD_STREAMSTATS
    assert f.reference_gauge is None


def test_zero_drainage_area_is_unavailable_even_with_a_gauge():
    f = hydro.firm_flow(0.0, 1.0, None, SETTINGS, None, GAUGE, (0.5, 1.5))
    assert f.firm_flow_cfs is None
    assert f.confidence == "unavailable"


# --- regional yield (D-09 fallback) ---


def test_only_medium_streamstats_flows_give_a_yield():
    medium = hydro.firm_flow(BIG_SQKM, 5.0, estimate(), SETTINGS)
    assert hydro.streamstats_yield(medium, BIG_SQKM) == pytest.approx(
        0.8 / units.sqkm_to_sqmi(BIG_SQKM)
    )
    out_of_range = hydro.firm_flow(BIG_SQKM, 5.0, estimate(out_of_range=("RECESS",)), SETTINGS)
    assert hydro.streamstats_yield(out_of_range, BIG_SQKM) is None
    gauge = hydro.firm_flow(BIG_SQKM, 5.0, None, SETTINGS, None, GAUGE)
    assert hydro.streamstats_yield(gauge, BIG_SQKM) is None
    assert hydro.streamstats_yield(medium, None) is None


def test_regional_yield_is_the_median_of_the_reaches_own_huc8():
    yields = {"06020001": [0.016, 0.017, 0.030], "05130107": [0.5, 0.5, 0.5]}
    assert hydro.regional_yield(yields, "06020001", 3) == (0.017, "06020001", 3)


def test_regional_yield_pools_the_huc6_then_huc4_when_the_huc8_has_too_few():
    yields = {"05130107": [0.018, 0.019], "05130108": [0.015], "05130205": [0.9, 0.9]}
    assert hydro.regional_yield(yields, "05130108", 3) == (0.018, "051301", 3)
    assert hydro.regional_yield(yields, "05130108", 5) == (0.019, "0513", 5)
    assert hydro.regional_yield(yields, "05130108", 6) is None
    assert hydro.regional_yield({}, "05130108", 1) is None


def test_regional_flow_fills_a_small_basin_low_confidence_with_flags_kept():
    small = hydro.firm_flow(SMALL_SQKM, 1.0, None, SETTINGS)
    f = hydro.regional_flow(small, SMALL_SQKM, 0.018, "06020001")
    assert f.firm_flow_cfs == pytest.approx(units.sqkm_to_sqmi(SMALL_SQKM) * 0.018)
    assert f.statistic == "q90"
    assert f.method == hydro.FLOW_METHOD_REGIONAL
    assert f.confidence == "low"
    assert f.flags == ["below_regression_min_drainage_area", "regional_yield_basin:06020001"]
    assert hydro.firm_power(f, hydro.head(200, 100, 0.85), ASSUMPTIONS).confidence == "low"


def test_regional_flow_replaces_the_gauge_on_small_basins_only():
    small_gauge = hydro.firm_flow(SMALL_SQKM, 1.0, None, SETTINGS, None, GAUGE)
    f = hydro.regional_flow(small_gauge, SMALL_SQKM, 0.018, "06020004")
    assert f.method == hydro.FLOW_METHOD_REGIONAL
    assert f.firm_flow_cfs == pytest.approx(units.sqkm_to_sqmi(SMALL_SQKM) * 0.018)
    assert f.flags == ["below_regression_min_drainage_area", "regional_yield_basin:06020004"]
    assert f.reference_gauge is None and f.drainage_area_ratio is None
    # A larger basin on the gauge (StreamStats down) keeps it.
    big_gauge = hydro.firm_flow(BIG_SQKM, 1.0, None, SETTINGS, "streamstats_unavailable", GAUGE)
    assert hydro.regional_flow(big_gauge, BIG_SQKM, 0.018, "06020004") is big_gauge


def test_regional_flow_never_replaces_streamstats_or_missing_data():
    medium = hydro.firm_flow(BIG_SQKM, 5.0, estimate(), SETTINGS)
    assert hydro.regional_flow(medium, BIG_SQKM, 0.018, "06020004") is medium
    unavailable = hydro.firm_flow(None, 1.0, None, SETTINGS)
    assert hydro.regional_flow(unavailable, SMALL_SQKM, 0.018, "06020004") is unavailable
