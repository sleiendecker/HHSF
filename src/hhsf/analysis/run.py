"""`hhsf analyze hydro`: compute hydro corridor metrics for every perennial reach.

Each run gets an analysis_runs row holding every assumption it used, then one
hydro_corridors row per reach. Earlier runs are kept, so results stay reproducible.
"""

import json
import subprocess
import threading
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from shapely import wkb
from shapely.geometry.base import BaseGeometry
from sqlalchemy import Connection, Engine, text

from hhsf.analysis import hydro
from hhsf.analysis.streamstats import (
    LowFlowSource,
    PointFailed,
    ServiceConfig,
    ServiceUnavailable,
)
from hhsf.core import crs
from hhsf.core.config import AOI, REPO_ROOT, load_data_source
from hhsf.core.db import record_source
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


@dataclass
class Corridor:
    reach: Reach
    head: hydro.Head
    flow: hydro.Flow
    power: hydro.Power


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
    """Perennial reaches touching the AOI's county plus its buffer."""
    keep = keep_area(aoi, county_area(conn, aoi))
    rows = conn.execute(
        text(
            """
            SELECT stream_id, nhdplusid, length_m, drainage_area_sqkm, max_elev_m,
                   min_elev_m, mean_flow_cfs, ST_AsBinary(geom)
            FROM streams WHERE perennial ORDER BY stream_id
            """
        )
    ).all()
    reaches = [Reach(*row[:7], wkb.loads(bytes(row[7]))) for row in rows]
    return [r for r in reaches if keep.intersects(crs.to_analysis(r.geom))]


def wants_streamstats(reach: Reach, s: hydro.StreamStatsSettings) -> bool:
    """Only ask StreamStats about basins its equations can cover."""
    return reach.drainage_area_sqkm is not None and not hydro.below_regression_minimum(
        reach.drainage_area_sqkm, s
    )


class _Breaker:
    """Stops calling a service after N reaches in a row found it down."""

    def __init__(self, limit: int):
        self.limit = limit
        self.consecutive = 0
        self.lock = threading.Lock()

    @property
    def open(self) -> bool:
        return self.consecutive >= self.limit

    def record(self, ok: bool) -> None:
        with self.lock:
            self.consecutive = 0 if ok else self.consecutive + 1


def analyze_reach(
    reach: Reach,
    a: hydro.HydroAssumptions,
    source: LowFlowSource | None,
    breaker: _Breaker | None = None,
) -> Corridor:
    h = hydro.head(reach.max_elev_m, reach.min_elev_m, a.net_head_factor)
    estimate, reason = None, "streamstats_disabled" if source is None else None
    if source is not None and wants_streamstats(reach, a.streamstats):
        if breaker is not None and breaker.open:
            reason = "streamstats_unavailable"
        else:
            point = hydro.pour_point(reach.geom, a.streamstats.pour_point_offset_m)
            try:
                estimate = source.low_flow(point.y, point.x)
                if breaker is not None:
                    breaker.record(True)
            except ServiceUnavailable:
                reason = "streamstats_unavailable"
                if breaker is not None:
                    breaker.record(False)
            except PointFailed:
                reason = "streamstats_point_failed"
    flow = hydro.firm_flow(
        reach.drainage_area_sqkm, reach.mean_flow_cfs, estimate, a.streamstats, reason
    )
    return Corridor(reach, h, flow, hydro.firm_power(flow, h, a))


def analyze_reaches(
    reaches: list[Reach],
    a: hydro.HydroAssumptions,
    source: LowFlowSource | None,
    service: ServiceConfig | None = None,
    echo: Callable[[str], None] = print,
) -> list[Corridor]:
    workers = service.max_concurrent_requests if service else 1
    breaker = _Breaker(service.max_consecutive_failures if service else 3)
    corridors: list[Corridor] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        jobs = pool.map(lambda r: analyze_reach(r, a, source, breaker), reaches)
        for i, corridor in enumerate(jobs, 1):
            corridors.append(corridor)
            if i % 50 == 0 or i == len(reaches):
                echo(f"  analyzed {i}/{len(reaches)} reaches")
    if source is not None and breaker.open:
        echo("  StreamStats looks unavailable; remaining reaches use the NHDPlus mean flow.")
    return corridors


def start_run(conn: Connection, aoi: AOI, a: hydro.HydroAssumptions, use_streamstats: bool) -> int:
    sources = dict(conn.execute(text("SELECT source_key, version FROM data_sources")).all())
    if use_streamstats:
        streamstats = load_data_source("streamstats")
        record_source(conn, streamstats)
        sources["streamstats"] = streamstats.version
    params = {"hydro": a.model_dump(), "use_streamstats": use_streamstats}
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


def save_corridors(conn: Connection, run_id: int, corridors: list[Corridor]) -> None:
    if not corridors:
        return
    conn.execute(
        text(
            """
            INSERT INTO hydro_corridors (
                run_id, stream_id, nhdplusid, geom, length_m,
                gross_head_m, net_head_m, head_method, head_flag,
                drainage_area_sqkm, drainage_area_method,
                mean_flow_cfs, firm_flow_cfs, flow_statistic, flow_method, flow_confidence,
                flow_flags, flow_std_error_pct, regression_region, streamstats_drainage_area_sqkm,
                usable_flow_cfs, firm_power_w, power_confidence
            )
            SELECT
                :run_id, s.stream_id, s.nhdplusid, s.geom, s.length_m,
                :gross_head_m, :net_head_m, :head_method, :head_flag,
                s.drainage_area_sqkm, :drainage_area_method,
                s.mean_flow_cfs, :firm_flow_cfs, :flow_statistic, :flow_method, :flow_confidence,
                :flow_flags, :flow_std_error_pct, :regression_region, :ss_drainage_area_sqkm,
                :usable_flow_cfs, :firm_power_w, :power_confidence
            FROM streams s WHERE s.stream_id = :stream_id
            """
        ),
        [
            {
                "run_id": run_id,
                "stream_id": c.reach.stream_id,
                "gross_head_m": c.head.gross_m,
                "net_head_m": c.head.net_m,
                "head_method": hydro.HEAD_METHOD,
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
                "usable_flow_cfs": c.power.usable_flow_cfs,
                "firm_power_w": c.power.firm_power_w,
                "power_confidence": c.power.confidence,
            }
            for c in corridors
        ],
    )


def summarize(corridors: list[Corridor], a: hydro.HydroAssumptions) -> dict:
    watts = [c.power.firm_power_w for c in corridors if c.power.firm_power_w is not None]
    return {
        "reaches": len(corridors),
        "flow_method": dict(Counter(c.flow.method or "none" for c in corridors)),
        "flow_confidence": dict(Counter(c.flow.confidence for c in corridors)),
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
) -> tuple[int, dict]:
    """Analyze every perennial reach in the AOI and save one run. Returns (run_id, summary)."""
    a = hydro.load_assumptions()
    with eng.begin() as conn:
        reaches = load_reaches(conn, aoi)[:limit]
        run_id = start_run(conn, aoi, a, source is not None)
    try:
        if source is not None:
            eligible = sum(wants_streamstats(r, a.streamstats) for r in reaches)
            echo(f"  {eligible} of {len(reaches)} reaches are large enough for StreamStats")
        corridors = analyze_reaches(reaches, a, source, service, echo)
        summary = summarize(corridors, a)
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
