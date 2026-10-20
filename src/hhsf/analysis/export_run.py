"""`hhsf export hydro`: write a score run's shortlist, every corridor and a report.

Loads one finished score run (default: the AOI's latest) with its hydro numbers and
hands the rows to export.py, which does all the formatting. Files go to
data/outputs/<aoi>/run_<score run id>/, so each run's exports sit side by side.
"""

import json
from dataclasses import replace
from pathlib import Path

from sqlalchemy import Connection, Engine, text

from hhsf.analysis import export
from hhsf.analysis.run import code_version
from hhsf.core.config import AOI, DATA_DIR

OUTPUTS_DIR = DATA_DIR / "outputs"


class NoScoreRun(LookupError):
    pass


def latest_score_run(conn: Connection, aoi_key: str) -> int:
    run_id = conn.execute(
        text(
            "SELECT max(run_id) FROM analysis_runs"
            " WHERE analysis = 'hydro_score' AND status = 'finished' AND aoi_key = :aoi"
        ),
        {"aoi": aoi_key},
    ).scalar_one()
    if run_id is None:
        raise NoScoreRun(f"No finished score run for '{aoi_key}'. Run `make score` first.")
    return run_id


def load_run_info(conn: Connection, aoi: AOI, score_run_id: int) -> export.RunInfo:
    row = conn.execute(
        text(
            """
            SELECT s.code_version, s.scoring_profile_version, s.data_source_versions,
                   s.params, to_char(s.finished_at, 'YYYY-MM-DD HH24:MI TZ') AS finished,
                   h.run_id AS hydro_run_id, h.code_version AS hydro_code, h.params AS hydro_params
            FROM analysis_runs s JOIN analysis_runs h ON h.run_id = s.source_run_id
            WHERE s.run_id = :r AND s.analysis = 'hydro_score' AND s.status = 'finished'
              AND s.aoi_key = :aoi
            """
        ),
        {"r": score_run_id, "aoi": aoi.key},
    ).one_or_none()
    if row is None:
        raise NoScoreRun(f"Run {score_run_id} is not a finished score run for '{aoi.key}'.")
    return export.RunInfo(
        aoi_key=aoi.key,
        aoi_name=aoi.name,
        score_run_id=score_run_id,
        hydro_run_id=row.hydro_run_id,
        score_code_version=row.code_version,
        hydro_code_version=row.hydro_code,
        scoring_profile_version=row.scoring_profile_version,
        finished_at=row.finished,
        data_source_versions=row.data_source_versions,
        hydro_params=row.hydro_params,
        score_params=row.params,
    )


def load_rows(conn: Connection, score_run_id: int) -> list[export.ExportRow]:
    rows = conn.execute(
        text(
            """
            SELECT s.corridor_id, c.nhdplusid, st.stream_name, s.status, s.tier, s.rank,
                   s.tier_rank, s.hydro_score, s.head_subscore, s.power_subscore,
                   s.net_head_ft, c.firm_power_w, c.firm_flow_cfs, c.flow_method,
                   c.flow_confidence, c.flow_flags, c.drainage_area_sqkm, c.length_m,
                   ST_X(o.pt) AS outlet_lon, ST_Y(o.pt) AS outlet_lat, s.explanation,
                   ST_AsGeoJSON(c.geom, 6) AS geometry, st.length_m AS reach_length_m
            FROM hydro_scores s
            JOIN hydro_corridors c USING (corridor_id)
            JOIN streams st USING (stream_id)
            -- NHDPlus draws flowlines upstream to downstream: the last point is the outlet
            -- (for a stretch, its bottom: where the turbine would go).
            CROSS JOIN LATERAL (
                SELECT ST_EndPoint(ST_GeometryN(c.geom, ST_NumGeometries(c.geom))) AS pt
            ) o
            WHERE s.run_id = :r
            ORDER BY s.corridor_id
            """
        ),
        {"r": score_run_id},
    ).mappings()
    return [
        export.ExportRow(
            **{k: v for k, v in r.items() if k not in ("flow_flags", "geometry")},
            flow_flags=tuple(r["flow_flags"] or ()),
            geometry=json.loads(r["geometry"]),
        )
        for r in rows
    ]


def export_hydro(
    eng: Engine, aoi: AOI, score_run_id: int | None = None, out_root: Path = OUTPUTS_DIR
) -> tuple[export.RunInfo, Path, list[Path]]:
    """Export a score run (default: the AOI's latest finished one).

    Returns (run info, output folder, files written).
    """
    with eng.connect() as conn:
        if score_run_id is None:
            score_run_id = latest_score_run(conn, aoi.key)
        info = load_run_info(conn, aoi, score_run_id)
        rows = load_rows(conn, score_run_id)
    info = replace(info, exported_with=code_version())
    out_dir = out_root / aoi.key / f"run_{score_run_id}"
    return info, out_dir, export.write_exports(out_dir, info, rows)
