"""`hhsf analyze hydro`: compute hydro corridor metrics for every perennial reach.

Each run gets an analysis_runs row holding every assumption it used, then one
hydro_corridors row per reach. Earlier runs are kept, so results stay reproducible.

With a DEM (the default from the CLI), each corridor is the reach's steepest stretch
within the D-08 penstock length (analysis/stretch.py); without one it's the whole
reach, with head from NHDPlus (the method before terrain was added).
"""

import dataclasses
import json
import subprocess
import threading
from collections import Counter, defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import rasterio
from shapely import wkb
from shapely.geometry.base import BaseGeometry
from sqlalchemy import Connection, Engine, text

from hhsf.analysis import hydro, stretch
from hhsf.analysis.basins import Basins
from hhsf.analysis.gauges import GaugeTransfer
from hhsf.analysis.streamstats import (
    LowFlowSource,
    PointFailed,
    ServiceConfig,
    ServiceUnavailable,
)
from hhsf.analysis.stretch import CorridorHead, Stretch
from hhsf.core import crs
from hhsf.core.config import AOI, REPO_ROOT, load_data_source
from hhsf.core.db import record_source
from hhsf.ingest import terrain
from hhsf.ingest.hydrography import county_area, keep_area


@dataclass
class Reach:
    stream_id: int
    nhdplusid: int
    length_m: float
    drainage_area_sqkm: float | None
    max_elev_m: float | None
    min_elev_m: float | None
    mean_flow_cfs: float | None
    geom: BaseGeometry
    # What the reaches flowing into this one drain (None: a headwater, nothing flows in).
    upstream_drainage_area_sqkm: float | None = None


@dataclass
class Corridor:
    reach: Reach
    head: hydro.Head
    flow: hydro.Flow
    power: hydro.Power
    head_method: str = hydro.HEAD_METHOD
    stretch: Stretch | None = None  # None: the corridor is the whole reach


def code_version() -> str:
    try:
        out = subprocess.run(
            ["git", "describe", "--always", "--dirty"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def load_reaches(conn: Connection, aoi: AOI) -> list[Reach]:
    """Perennial reaches touching the AOI's county plus its buffer.

    A reach's upstream drainage area is the sum over the streams (any kind) whose
    downstream end is its upstream end; flowlines are drawn upstream to downstream.
    """
    keep = keep_area(aoi, county_area(conn, aoi))
    rows = conn.execute(
        text(
            """
            SELECT s.stream_id, s.nhdplusid, s.length_m, s.drainage_area_sqkm, s.max_elev_m,
                   s.min_elev_m, s.mean_flow_cfs, ST_AsBinary(s.geom), up.drainage_area_sqkm
            FROM streams s
            LEFT JOIN LATERAL (
                SELECT sum(u.drainage_area_sqkm) AS drainage_area_sqkm FROM streams u
                WHERE u.geom && ST_StartPoint(ST_GeometryN(s.geom, 1))
                  AND ST_Equals(
                      ST_EndPoint(ST_GeometryN(u.geom, ST_NumGeometries(u.geom))),
                      ST_StartPoint(ST_GeometryN(s.geom, 1))
                  )
            ) up ON true
            WHERE s.perennial ORDER BY s.stream_id
            """
        )
    ).all()
    reaches = [Reach(*row[:7], wkb.loads(bytes(row[7])), row[8]) for row in rows]
    return [r for r in reaches if keep.intersects(crs.to_analysis(r.geom))]


def wants_streamstats(reach: Reach, s: hydro.StreamStatsSettings) -> bool:
    """Only ask StreamStats about basins its equations can cover."""
    return reach.drainage_area_sqkm is not None and not hydro.below_regression_minimum(
        reach.drainage_area_sqkm, s
    )


class _Breaker:
    """Stops calling a service after N reaches in a row found it down.

    Once open it stays open for the rest of the run: an answer that was already
    on its way when it opened doesn't switch the service back on.
    """

    def __init__(self, limit: int):
        self.limit = limit
        self.consecutive = 0
        self.lock = threading.Lock()

    @property
    def open(self) -> bool:
        return self.consecutive >= self.limit

    def record(self, ok: bool) -> None:
        with self.lock:
            if not self.open:
                self.consecutive = 0 if ok else self.consecutive + 1


def streamstats_low_flow(
    reach: Reach, s: hydro.StreamStatsSettings, source: LowFlowSource
) -> hydro.LowFlowEstimate:
    """StreamStats at the reach's outlet, moving up the reach if it found the wrong basin.

    StreamStats snaps the pour point to its own stream grid, which doesn't always
    line up with the NHDPlus flowline: near a confluence it can land on the bigger
    stream, elsewhere on a hillside cell draining almost nothing. If the outlet's
    basin doesn't match NHDPlus, try the `pour_point_retry_shares` points up the
    reach and keep the first whose basin matches the drainage area there. If none
    does, the outlet's answer is returned and firm_flow() flags the mismatch.

    ServiceUnavailable propagates (the caller's breaker counts it); a retry point
    the service can't answer for is skipped.
    """
    point = hydro.pour_point(reach.geom, s.pour_point_offset_m)
    estimate = source.low_flow(point.y, point.x)
    outlet = reach.drainage_area_sqkm
    if outlet is None or not hydro.drainage_area_mismatch(outlet, estimate.drainage_area_sqmi, s):
        return estimate
    for share in s.pour_point_retry_shares:
        expected = hydro.drainage_area_up_reach(outlet, reach.upstream_drainage_area_sqkm, share)
        if expected < s.min_pour_point_area_share * outlet:
            continue
        point = hydro.point_up_reach(reach.geom, share)
        try:
            moved = source.low_flow(point.y, point.x)
        except PointFailed:
            continue
        if not hydro.drainage_area_mismatch(expected, moved.drainage_area_sqmi, s):
            return dataclasses.replace(moved, point_drainage_area_sqkm=expected)
    return estimate


def whole_reach_head(reach: Reach, a: hydro.HydroAssumptions) -> CorridorHead:
    h = hydro.head(reach.max_elev_m, reach.min_elev_m, a.net_head_factor)
    return CorridorHead(h, hydro.HEAD_METHOD)


def corridor_heads(
    reaches: list[Reach], a: hydro.HydroAssumptions, dem_path: Path | None
) -> list[CorridorHead]:
    """Each reach's head: over its steepest stretch with a DEM, else over the whole reach.

    Runs before the (threaded) flow lookups: one DEM file handle isn't safe to share.
    """
    if dem_path is None:
        return [whole_reach_head(r, a) for r in reaches]
    with rasterio.open(dem_path) as dem:
        return [
            stretch.corridor_head(
                dem, r.geom, r.max_elev_m, r.min_elev_m, a.corridor, a.net_head_factor
            )
            for r in reaches
        ]


def analyze_reach(
    reach: Reach,
    a: hydro.HydroAssumptions,
    source: LowFlowSource | None,
    breaker: _Breaker | None = None,
    gauge: GaugeTransfer | None = None,
    head: CorridorHead | None = None,
) -> Corridor:
    """Head (given, or the whole reach's), then flow at the reach's outlet, then power.

    Flow stays the reach's even when the corridor is a stretch of it (D-08 doesn't
    change that): the drainage area, and so the flow, is measured at the reach's
    downstream end.
    """
    head = head or whole_reach_head(reach, a)
    h = head.head
    estimate, reason = None, "streamstats_disabled" if source is None else None
    if source is not None and wants_streamstats(reach, a.streamstats):
        if breaker is not None and breaker.open:
            reason = "streamstats_unavailable"
        else:
            try:
                estimate = streamstats_low_flow(reach, a.streamstats, source)
                if breaker is not None:
                    breaker.record(True)
            except ServiceUnavailable:
                reason = "streamstats_unavailable"
                if breaker is not None:
                    breaker.record(False)
            except PointFailed:
                reason = "streamstats_point_failed"
    flow = hydro.firm_flow(
        reach.drainage_area_sqkm,
        reach.mean_flow_cfs,
        estimate,
        a.streamstats,
        reason,
        gauge.reference if gauge is not None and gauge.covers(reach.geom) else None,
        a.gauge_transfer.ratio_range,
    )
    return Corridor(reach, h, flow, hydro.firm_power(flow, h, a), head.method, head.stretch)


def analyze_reaches(
    reaches: list[Reach],
    a: hydro.HydroAssumptions,
    source: LowFlowSource | None,
    service: ServiceConfig | None = None,
    echo: Callable[[str], None] = print,
    gauge: GaugeTransfer | None = None,
    heads: list[CorridorHead] | None = None,
) -> list[Corridor]:
    workers = service.max_concurrent_requests if service else 1
    breaker = _Breaker(service.max_consecutive_failures if service else 3)
    corridors: list[Corridor] = []
    heads = heads or [whole_reach_head(r, a) for r in reaches]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        jobs = pool.map(
            lambda rh: analyze_reach(rh[0], a, source, breaker, gauge, rh[1]),
            zip(reaches, heads, strict=True),
        )
        for i, corridor in enumerate(jobs, 1):
            corridors.append(corridor)
            if i % 50 == 0 or i == len(reaches):
                echo(f"  analyzed {i}/{len(reaches)} reaches")
    down = sum("streamstats_unavailable" in c.flow.flags for c in corridors)
    if down:
        echo(
            f"  StreamStats looks unavailable: {down} reaches got a fallback flow instead. "
            "Run again later; answers already received are cached."
        )
    return corridors


def fill_regional_yield(
    corridors: list[Corridor], basins: Basins, a: hydro.HydroAssumptions
) -> dict[str, dict]:
    """D-09: give basins too small for StreamStats their basin's typical Q90.

    Runs after every reach has its own flow. Each medium-confidence StreamStats
    Q90 is turned into a yield (cfs per sq mi) and grouped by the HUC8 its reach
    drains to. A small basin with no flow, or only a gauge transfer, and any
    reach left on the NHDPlus mean flow (hydro.regional_yield_applies) then gets its
    drainage area times the median yield of its HUC8 (or of the HUC6/HUC4 if the
    HUC8 has too few), and its power is recomputed. Where no yield is found it
    keeps what it had. Returns the yields used, per basin, for the run summary.
    """
    huc8s = [basins.huc8_of(c.reach.geom) for c in corridors]
    yields: dict[str, list[float]] = defaultdict(list)
    for c, huc8 in zip(corridors, huc8s, strict=True):
        y = hydro.streamstats_yield(c.flow, c.reach.drainage_area_sqkm)
        if y is not None and huc8 is not None:
            yields[huc8].append(y)
    used: dict[str, dict] = {}
    for c, huc8 in zip(corridors, huc8s, strict=True):
        if not hydro.regional_yield_applies(c.flow) or huc8 is None:
            continue
        found = hydro.regional_yield(yields, huc8, a.regional_yield.min_reaches)
        if found is None:
            continue
        value, basin, n = found
        c.flow = hydro.regional_flow(c.flow, c.reach.drainage_area_sqkm, value, basin)
        c.power = hydro.firm_power(c.flow, c.head, a)
        entry = used.setdefault(
            basin, {"yield_cfs_per_sqmi": value, "streamstats_reaches": n, "reaches_filled": 0}
        )
        entry["reaches_filled"] += 1
    return used


def start_run(
    conn: Connection,
    aoi: AOI,
    a: hydro.HydroAssumptions,
    use_streamstats: bool,
    gauge: GaugeTransfer | None = None,
    use_terrain: bool = False,
    use_regional_yield: bool = False,
) -> int:
    sources = dict(conn.execute(text("SELECT source_key, version FROM data_sources")).all())
    used = ["streamstats"] if use_streamstats else []
    used += ["nwis"] if gauge else []
    used += ["wbd_hu8"] if gauge or use_regional_yield else []
    for key in used:
        source = load_data_source(key)
        record_source(conn, source)
        sources[key] = source.version
    if not use_terrain:
        sources.pop(terrain.SOURCE_KEY, None)  # loaded, but this run doesn't use it
    params = {
        "hydro": a.model_dump(),
        "corridor_method": stretch.HEAD_METHOD if use_terrain else hydro.HEAD_METHOD,
        "use_streamstats": use_streamstats,
        "reference_gauge": gauge.record if gauge else None,
        "use_regional_yield": use_regional_yield,
    }
    return conn.execute(
        text(
            """
            INSERT INTO analysis_runs
                (analysis, aoi_key, code_version, data_source_versions, params)
            VALUES ('hydro', :aoi, :code, CAST(:sources AS jsonb), CAST(:params AS jsonb))
            RETURNING run_id
            """
        ),
        {
            "aoi": aoi.key,
            "code": code_version(),
            "sources": json.dumps(sources),
            "params": json.dumps(params),
        },
    ).scalar_one()


def _where(c: Corridor) -> dict:
    """Measures, geometry (WKB) and length of the corridor: its stretch, or the whole reach."""
    if c.stretch is None:
        return {
            "from_measure": 0.0,
            "to_measure": 100.0,
            "geom": c.reach.geom.wkb,
            "length_m": c.reach.length_m,
        }
    return {
        "from_measure": c.stretch.from_measure,
        "to_measure": c.stretch.to_measure,
        "geom": stretch.stretch_geometry(c.reach.geom, c.stretch).wkb,
        "length_m": c.stretch.length_m,
    }


def save_corridors(conn: Connection, run_id: int, corridors: list[Corridor]) -> None:
    if not corridors:
        return
    conn.execute(
        text(
            """
            INSERT INTO hydro_corridors (
                run_id, stream_id, nhdplusid, from_measure, to_measure, geom, length_m,
                gross_head_m, net_head_m, head_method, head_flag,
                drainage_area_sqkm, drainage_area_method,
                mean_flow_cfs, firm_flow_cfs, flow_statistic, flow_method, flow_confidence,
                flow_flags, flow_std_error_pct, regression_region, streamstats_drainage_area_sqkm,
                flow_reference_gauge, flow_drainage_area_ratio,
                usable_flow_cfs, firm_power_w, power_confidence
            )
            SELECT
                :run_id, s.stream_id, s.nhdplusid, :from_measure, :to_measure,
                ST_Multi(ST_GeomFromWKB(:geom, 4326)), :length_m,
                :gross_head_m, :net_head_m, :head_method, :head_flag,
                s.drainage_area_sqkm, :drainage_area_method,
                s.mean_flow_cfs, :firm_flow_cfs, :flow_statistic, :flow_method, :flow_confidence,
                :flow_flags, :flow_std_error_pct, :regression_region, :ss_drainage_area_sqkm,
                :reference_gauge, :drainage_area_ratio,
                :usable_flow_cfs, :firm_power_w, :power_confidence
            FROM streams s WHERE s.stream_id = :stream_id
            """
        ),
        [
            {
                "run_id": run_id,
                "stream_id": c.reach.stream_id,
                **_where(c),
                "gross_head_m": c.head.gross_m,
                "net_head_m": c.head.net_m,
                "head_method": c.head_method,
                "head_flag": c.head.flag,
                "drainage_area_method": hydro.DRAINAGE_AREA_METHOD,
                "firm_flow_cfs": c.flow.firm_flow_cfs,
                "flow_statistic": c.flow.statistic,
                "flow_method": c.flow.method,
                "flow_confidence": c.flow.confidence,
                "flow_flags": c.flow.flags,
                "flow_std_error_pct": c.flow.std_error_pct,
                "regression_region": c.flow.regression_region,
                "ss_drainage_area_sqkm": c.flow.streamstats_drainage_area_sqkm,
                "reference_gauge": c.flow.reference_gauge,
                "drainage_area_ratio": c.flow.drainage_area_ratio,
                "usable_flow_cfs": c.power.usable_flow_cfs,
                "firm_power_w": c.power.firm_power_w,
                "power_confidence": c.power.confidence,
            }
            for c in corridors
        ],
    )


def summarize(corridors: list[Corridor], a: hydro.HydroAssumptions) -> dict:
    watts = [c.power.firm_power_w for c in corridors if c.power.firm_power_w is not None]
    stretches = [c.stretch for c in corridors if c.stretch is not None]
    return {
        "reaches": len(corridors),
        "head_method": dict(Counter(c.head_method for c in corridors)),
        "head_flags": dict(Counter(c.head.flag for c in corridors if c.head.flag)),
        "stretch_shorter_than_reach": sum(not s.whole_reach for s in stretches),
        "flow_method": dict(Counter(c.flow.method or "none" for c in corridors)),
        "flow_confidence": dict(Counter(c.flow.confidence for c in corridors)),
        "streamstats_pour_point_moved": sum(
            "streamstats_pour_point_moved" in c.flow.flags for c in corridors
        ),
        "below_regression_min_drainage_area": sum(
            "below_regression_min_drainage_area" in c.flow.flags for c in corridors
        ),
        "with_power": len(watts),
        "meets_power_target": sum(w >= a.power_target_w for w in watts),
        "meets_power_target_medium_confidence": sum(
            c.power.firm_power_w is not None
            and c.power.firm_power_w >= a.power_target_w
            and c.power.confidence == "medium"
            for c in corridors
        ),
    }


def run_hydro(
    eng: Engine,
    aoi: AOI,
    source: LowFlowSource | None,
    service: ServiceConfig | None = None,
    limit: int | None = None,
    echo: Callable[[str], None] = print,
    gauge: GaugeTransfer | None = None,
    dem: Path | None = None,
    assumptions: hydro.HydroAssumptions | None = None,
    basins: Basins | None = None,
) -> tuple[int, dict]:
    """Analyze every perennial reach in the AOI and save one run. Returns (run_id, summary).

    `dem`: the AOI's DEM (data/raw/usgs_3dep_13/<aoi>.tif) to find each reach's
    steepest stretch; None treats each whole reach as one corridor.
    `basins`: the HUC8s the AOI drains to, for the regional-yield fallback on
    basins too small for StreamStats (D-09); None leaves those without a flow.
    """
    a = assumptions or hydro.load_assumptions()
    with eng.begin() as conn:
        reaches = load_reaches(conn, aoi)[:limit]
        run_id = start_run(
            conn, aoi, a, source is not None, gauge, dem is not None, basins is not None
        )
    try:
        if dem is not None:
            echo(
                f"  finding each reach's steepest {a.corridor.penstock_length_ft:,.0f} ft "
                f"on the DEM ({dem.name})"
            )
        heads = corridor_heads(reaches, a, dem)
        if source is not None:
            eligible = sum(wants_streamstats(r, a.streamstats) for r in reaches)
            echo(f"  {eligible} of {len(reaches)} reaches are large enough for StreamStats")
        if gauge is not None:
            covered = sum(gauge.covers(r.geom) for r in reaches)
            echo(f"  {covered} of {len(reaches)} reaches drain to gauge {gauge.reference.site_no}")
        corridors = analyze_reaches(reaches, a, source, service, echo, gauge, heads)
        regional = fill_regional_yield(corridors, basins, a) if basins is not None else None
        summary = summarize(corridors, a)
        if regional is not None:
            summary["regional_yield"] = regional
        with eng.begin() as conn:
            save_corridors(conn, run_id, corridors)
            conn.execute(
                text(
                    "UPDATE analysis_runs SET status = 'finished', finished_at = now(),"
                    " summary = CAST(:summary AS jsonb) WHERE run_id = :run_id"
                ),
                {"run_id": run_id, "summary": json.dumps(summary)},
            )
    except BaseException:
        with eng.begin() as conn:
            conn.execute(
                text(
                    "UPDATE analysis_runs SET status = 'failed', finished_at = now()"
                    " WHERE run_id = :run_id"
                ),
                {"run_id": run_id},
            )
        raise
    return run_id, summary
