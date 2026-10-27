"""Hydro corridor metrics: head, firm flow and firm power for one stream reach.

Pure functions, no database or network, so every number can be unit-tested.
Assumptions come from config/hydro.yaml (docs/DECISIONS.md D-01, D-04 to D-07, D-09)
and are explained in docs/ASSUMPTIONS.md.

Rule: when a value can't be estimated reliably it is None plus a flag, never a guess.
"""

import statistics
from dataclasses import dataclass, field, replace

import yaml
from pydantic import BaseModel
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from hhsf.core import crs, units
from hhsf.core.config import CONFIG_DIR

HEAD_METHOD = "nhdplus_vaa_smoothed_reach"  # whole reach; the stretch method is in stretch.py
DRAINAGE_AREA_METHOD = "nhdplus_vaa_totdasqkm"
FLOW_METHOD_STREAMSTATS = "streamstats_q90"
FLOW_METHOD_EROM = "nhdplus_erom"
FLOW_METHOD_GAUGE = "gauge_drainage_area_ratio"
FLOW_METHOD_REGIONAL = "regional_streamstats_yield"
# A regional yield pooled with another county's StreamStats reaches (D-19: Georgia).
REGIONAL_YIELD_BORROWED = "regional_yield_borrowed"


class StreamStatsSettings(BaseModel):
    region: str
    statistic_group: int
    statistic_code: str
    regression_regions: list[str]
    min_drainage_area_sqmi: float
    max_drainage_area_mismatch: float
    pour_point_offset_m: float
    # When the basin at the outlet is the wrong one, try again at these shares of
    # the reach's length up from its downstream end, in order (first match wins).
    pour_point_retry_shares: list[float] = []
    # Skip a retry point whose drainage area is below this share of the outlet's,
    # so scaling the answer back to the outlet stays a small step.
    min_pour_point_area_share: float = 0.5


class GaugeTransferSettings(BaseModel):
    start: str  # first day of the gauge record used (YYYY-MM-DD)
    end: str  # last day
    min_years: float  # refuse a gauge with less record than this
    # Drainage-area ratios (reach / gauge) where the method is usually trusted.
    # Outside it the value is still used, but flagged.
    ratio_range: tuple[float, float]


class RegionalYieldSettings(BaseModel):
    """D-09 fallback: a basin's typical StreamStats Q90 per square mile (regional_flow)."""

    # A basin's median is only used once it has this many StreamStats Q90s.
    min_reaches: int


class CorridorSettings(BaseModel):
    """D-08: how a reach's steepest stretch is found (analysis/stretch.py)."""

    penstock_length_ft: float  # longest penstock one corridor may assume
    sample_spacing_m: float  # distance between elevation samples along the reach

    @property
    def penstock_length_m(self) -> float:
        return self.penstock_length_ft / units.FT_PER_M


class HydroAssumptions(BaseModel):
    power_target_w: float
    system_efficiency: float
    net_head_factor: float
    diversion_fraction: float
    water_density_kg_m3: float
    gravity_m_s2: float
    corridor: CorridorSettings
    streamstats: StreamStatsSettings
    gauge_transfer: GaugeTransferSettings
    regional_yield: RegionalYieldSettings


def load_assumptions() -> HydroAssumptions:
    return HydroAssumptions.model_validate(yaml.safe_load((CONFIG_DIR / "hydro.yaml").read_text()))


# --- Head ---------------------------------------------------------------------


@dataclass(frozen=True)
class Head:
    gross_m: float | None
    net_m: float | None
    flag: str | None = None


def head(max_elev_m: float | None, min_elev_m: float | None, net_head_factor: float) -> Head:
    """Gross and net head over the whole reach (site-selection.md B9).

    Gross head is the drop between the reach's upstream and downstream ends, from
    NHDPlus HR smoothed elevations (maxelevsmo - minelevsmo). Smoothing forces
    elevations to never rise downstream, so the drop is never negative. Net head
    is gross head times the D-07 factor for penstock losses: a planning proxy, not
    a site design.
    """
    if max_elev_m is None or min_elev_m is None:
        return Head(None, None, "missing_elevation")
    gross = max_elev_m - min_elev_m
    if gross < 0:
        return Head(None, None, "elevation_rises_downstream")
    return Head(gross, gross * net_head_factor)


# --- Flow ---------------------------------------------------------------------


@dataclass(frozen=True)
class LowFlowEstimate:
    """What StreamStats returned for one pour point (see analysis/streamstats.py)."""

    value_cfs: float | None
    regression_region: str
    drainage_area_sqmi: float
    std_error_pct: float | None = None
    below_min_drainage_area: bool = False
    out_of_range: tuple[str, ...] = ()  # other basin characteristics outside the limits
    # NHDPlus drainage area where the pour point was, when it was moved up the reach
    # because the outlet snapped to the wrong basin (None: at the reach's outlet).
    point_drainage_area_sqkm: float | None = None


@dataclass(frozen=True)
class Flow:
    firm_flow_cfs: float | None
    statistic: str | None  # "q90" or "mean_annual"
    method: str | None
    confidence: str  # medium | low | below_applicability | unavailable
    flags: list[str] = field(default_factory=list)
    std_error_pct: float | None = None
    regression_region: str | None = None
    streamstats_drainage_area_sqkm: float | None = None
    reference_gauge: str | None = None  # USGS site number, for gauge transfers
    drainage_area_ratio: float | None = None  # reach / gauge


@dataclass(frozen=True)
class GaugeReference:
    """Q90 measured at a stream gauge, for scaling to ungauged reaches."""

    site_no: str
    q90_cfs: float
    drainage_area_sqmi: float


def gauge_transfer(
    drainage_area_sqkm: float, gauge: GaugeReference, ratio_range: tuple[float, float]
) -> tuple[float, float, list[str]]:
    """Drainage-area ratio method: Q90_reach = Q90_gauge x (A_reach / A_gauge).

    Returns (flow, ratio, flags). It assumes low flow per square mile is the same
    across the basin, which is least true far from the gauge's size, so ratios
    outside `ratio_range` are flagged.
    """
    ratio = units.sqkm_to_sqmi(drainage_area_sqkm) / gauge.drainage_area_sqmi
    low, high = ratio_range
    flags = [] if low <= ratio <= high else [f"area_ratio_outside_{low:g}_{high:g}"]
    return gauge.q90_cfs * ratio, ratio, flags


def below_regression_minimum(drainage_area_sqkm: float, s: StreamStatsSettings) -> bool:
    return units.sqkm_to_sqmi(drainage_area_sqkm) < s.min_drainage_area_sqmi


def drainage_area_mismatch(
    nhd_sqkm: float, streamstats_sqmi: float, s: StreamStatsSettings
) -> bool:
    """True if StreamStats delineated a basin that isn't this reach's (wrong snap)."""
    ss_sqkm = units.sqmi_to_sqkm(streamstats_sqmi)
    return abs(ss_sqkm - nhd_sqkm) > s.max_drainage_area_mismatch * nhd_sqkm


def firm_flow(
    drainage_area_sqkm: float | None,
    mean_flow_cfs: float | None,
    estimate: LowFlowEstimate | None,
    s: StreamStatsSettings,
    unavailable_reason: str | None = None,
    gauge: GaugeReference | None = None,
    ratio_range: tuple[float, float] = (0.5, 1.5),
) -> Flow:
    """Pick the firm flow for a reach, best first.

    1. StreamStats Q90 inside the regression's applicability: medium confidence.
       (The Tennessee equations' standard error is ~60%, so never "high".) If the
       pour point was moved up the reach (`estimate.point_drainage_area_sqkm`), its
       basin is checked against the drainage area there and the Q90 is scaled to
       the outlet by drainage area; still medium, flagged.
    2. A gauge in the same basin (`gauge`, when the reach drains to it): its Q90
       scaled by drainage area, low confidence. Also covers basins too small for
       the regression (D-09), with that flag kept, though run.py then replaces
       those with the regional yield where it can (regional_flow).
    3. Basin below the regression's minimum drainage area: None, below_applicability.
       (run.py then fills it from the regional yield where it can; see regional_flow.)
    4. Otherwise NHDPlus EROM mean annual flow, low confidence. Mean flow
       overstates what a turbine can rely on; the flags say so.
    """
    if drainage_area_sqkm is None or drainage_area_sqkm <= 0:
        return Flow(None, None, None, "unavailable", ["missing_drainage_area"])

    common: dict = {}
    small = below_regression_minimum(drainage_area_sqkm, s)
    reason = [unavailable_reason or "streamstats_not_used"]
    if estimate is not None:
        ss_sqkm = units.sqmi_to_sqkm(estimate.drainage_area_sqmi)
        common = {
            "regression_region": estimate.regression_region,
            "streamstats_drainage_area_sqkm": ss_sqkm,
        }
        point_sqkm = estimate.point_drainage_area_sqkm or drainage_area_sqkm
        if drainage_area_mismatch(point_sqkm, estimate.drainage_area_sqmi, s):
            reason = ["streamstats_drainage_area_mismatch"]
        elif estimate.below_min_drainage_area:
            small, reason = True, []
        elif estimate.value_cfs is None:
            reason = ["streamstats_no_estimate"]
        else:
            flags = [f"out_of_range:{code}" for code in estimate.out_of_range]
            value = estimate.value_cfs
            if estimate.point_drainage_area_sqkm is not None:
                # Moved up the reach: scale to the outlet by drainage area (as for a gauge).
                value *= drainage_area_sqkm / point_sqkm
                flags.append("streamstats_pour_point_moved")
            return Flow(
                value,
                "q90",
                FLOW_METHOD_STREAMSTATS,
                "low" if estimate.out_of_range else "medium",
                flags,
                std_error_pct=estimate.std_error_pct,
                **common,
            )

    if small:
        reason = ["below_regression_min_drainage_area"]
    if gauge is not None:
        value, ratio, ratio_flags = gauge_transfer(drainage_area_sqkm, gauge, ratio_range)
        return Flow(
            value,
            "q90",
            FLOW_METHOD_GAUGE,
            "low",
            [*reason, *ratio_flags],
            reference_gauge=gauge.site_no,
            drainage_area_ratio=ratio,
            **common,
        )
    if small:
        return Flow(None, None, None, "below_applicability", reason, **common)
    if mean_flow_cfs is None:
        return Flow(None, None, None, "unavailable", [*reason, "missing_mean_flow"], **common)
    return Flow(
        mean_flow_cfs,
        "mean_annual",
        FLOW_METHOD_EROM,
        "low",
        [*reason, "mean_flow_not_q90"],
        **common,
    )


def streamstats_yield(flow: Flow, drainage_area_sqkm: float | None) -> float | None:
    """Q90 per square mile of a medium-confidence StreamStats flow; None for any other flow.

    Only these feed the regional yield: an out-of-range or fallback flow would
    carry its own error into every small basin that borrows from it.
    """
    if flow.method != FLOW_METHOD_STREAMSTATS or flow.confidence != "medium":
        return None
    if flow.firm_flow_cfs is None or not drainage_area_sqkm:
        return None
    return flow.firm_flow_cfs / units.sqkm_to_sqmi(drainage_area_sqkm)


def regional_yield(
    yields: dict[str, list[float]], huc8: str, min_reaches: int
) -> tuple[float, str, int] | None:
    """The median Q90 per square mile for a reach in `huc8`: (yield, basin, reaches used).

    `yields` holds the StreamStats yields of in-range reaches, keyed by HUC8. The
    reach's own HUC8 is used if it has at least `min_reaches` of them; otherwise
    the reaches of every HUC8 sharing its first 6, then 4 digits are pooled (the
    returned code is then 6 or 4 digits long). None if even that is too few.
    """
    for digits in (8, 6, 4):
        code = huc8[:digits]
        pooled = [y for basin, ys in yields.items() if basin.startswith(code) for y in ys]
        if len(pooled) >= min_reaches:
            return statistics.median(pooled), code, len(pooled)
    return None


def regional_yield_applies(flow: Flow) -> bool:
    """True for a basin too small for the regression with no flow, or only the gauge's,
    and for any basin left on the NHDPlus mean flow.

    D-09 (decided 2026-10-19): the regional yield replaces the gauge transfer for
    small basins too, since the gauge runs about 10x high on them. A gauge
    transfer on a larger basin (StreamStats down or snapped wrong) is kept.
    PART 13: it also replaces the mean-flow fallback (StreamStats snapped wrong,
    or the reach is across the state line), which runs about 100x a Q90.
    """
    if flow.confidence == "below_applicability" or flow.method == FLOW_METHOD_EROM:
        return True
    return flow.method == FLOW_METHOD_GAUGE and "below_regression_min_drainage_area" in flow.flags


def regional_flow(
    flow: Flow, drainage_area_sqkm: float, yield_cfs_per_sqmi: float, basin: str
) -> Flow:
    """D-09 fallback for a basin too small for the regression: area x the basin's yield.

    `yield_cfs_per_sqmi` is the median StreamStats Q90 per square mile of the
    in-range reaches draining to the same basin (`basin`: a HUC8, or the HUC6/HUC4
    pooled when the HUC8 has too few; see regional_yield). Applying it below
    the regression's 1.3 sq mi minimum is an extrapolation, so it is always low
    confidence and keeps `below_regression_min_drainage_area`; the basin it came
    from is added as a flag. Replaces only a flow regional_yield_applies() to;
    a replaced gauge transfer loses its gauge, ratio and ratio flag, and a
    replaced mean flow keeps why StreamStats wasn't used.
    """
    if not regional_yield_applies(flow):
        return flow
    flags = [
        f
        for f in flow.flags
        if not f.startswith("area_ratio_outside_") and f != "mean_flow_not_q90"
    ]
    return replace(
        flow,
        firm_flow_cfs=units.sqkm_to_sqmi(drainage_area_sqkm) * yield_cfs_per_sqmi,
        statistic="q90",
        method=FLOW_METHOD_REGIONAL,
        confidence="low",
        flags=[*flags, f"regional_yield_basin:{basin}"],
        reference_gauge=None,
        drainage_area_ratio=None,
    )


# --- Power --------------------------------------------------------------------


@dataclass(frozen=True)
class Power:
    usable_flow_cfs: float | None
    firm_power_w: float | None
    confidence: str  # medium | low | unavailable


def power_w(flow_m3s: float, head_m: float, a: HydroAssumptions) -> float:
    """P = rho * g * Q * H * eta, in watts."""
    return a.water_density_kg_m3 * a.gravity_m_s2 * flow_m3s * head_m * a.system_efficiency


def firm_power(flow: Flow, h: Head, a: HydroAssumptions) -> Power:
    """Continuous power from the divertible share of firm flow (D-05) over net head (D-07)."""
    if flow.firm_flow_cfs is None or h.net_m is None:
        return Power(None, None, "unavailable")
    usable_cfs = flow.firm_flow_cfs * a.diversion_fraction
    watts = power_w(units.cfs_to_m3s(usable_cfs), h.net_m, a)
    return Power(usable_cfs, watts, "medium" if flow.confidence == "medium" else "low")


# --- Pour point ---------------------------------------------------------------


def pour_point(geom: BaseGeometry, offset_m: float) -> Point:
    """A point on the reach just above its downstream end (storage CRS).

    NHDPlus flowlines are drawn upstream to downstream, so the last vertex is the
    outlet. Stepping back a little keeps StreamStats from snapping to the
    confluence with the next reach. The step is at most 10% of the reach.
    """
    line = _last_line(geom)
    distance = line.length - min(offset_m, 0.1 * line.length)
    return crs.to_storage(line.interpolate(distance))


def point_up_reach(geom: BaseGeometry, share: float) -> Point:
    """The point `share` of the reach's length up from its downstream end (storage CRS)."""
    line = _last_line(geom)
    return crs.to_storage(line.interpolate(line.length * (1 - share)))


def drainage_area_up_reach(outlet_sqkm: float, upstream_sqkm: float | None, share: float) -> float:
    """Drainage area `share` of the way up a reach, by straight-line interpolation.

    NHDPlus gives the area at the reach's outlet; at its top it is what the
    reaches flowing into it drain (`upstream_sqkm`; none for a headwater, which
    starts from nothing). The land in between is assumed to join evenly.
    """
    top = upstream_sqkm or 0.0
    return outlet_sqkm - share * (outlet_sqkm - top)


def outlet(geom: BaseGeometry) -> Point:
    """The reach's downstream end (analysis CRS)."""
    line = _last_line(geom)
    return line.interpolate(line.length)


def _last_line(geom: BaseGeometry) -> BaseGeometry:
    line = crs.to_analysis(geom)
    if line.geom_type == "MultiLineString":
        line = line.geoms[-1]
    return line
