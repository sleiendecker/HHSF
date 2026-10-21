"""`hhsf export hydro`: write a score run's shortlist, every corridor and a report.

Loads one finished score run (default: the AOI's latest) with its hydro numbers and
hands the rows to export.py, which does all the formatting. Files go to
data/outputs/<aoi>/run_<score run id>/, so each run's exports sit side by side.

Parcels are linked when the export runs (views corridor_parcels and
corridor_turbine_parcels, migration 0010), so they match the latest parcel ingest.
"""

import json
from dataclasses import replace
from pathlib import Path

from sqlalchemy import Connection, Engine, text

from hhsf.analysis import export
from hhsf.analysis.run import code_version
from hhsf.core.config import AOI, DATA_DIR, min_homestead_acres
from hhsf.ingest.parcels import SOURCE_KEY as PARCELS_SOURCE

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


def parcels_version(conn: Connection) -> str | None:
    """The parcel layer's version and download date, or None if no parcels are loaded."""
    row = conn.execute(
        text(
            "SELECT version, to_char(retrieved_at, 'YYYY-MM-DD') AS retrieved"
            " FROM data_sources WHERE source_key = :k AND EXISTS (SELECT 1 FROM parcels)"
        ),
        {"k": PARCELS_SOURCE},
    ).one_or_none()
    return None if row is None else f"{row.version}, downloaded {row.retrieved}"


def _parcel(r: dict) -> export.Parcel | None:
    if r["tp_parcel_id"] is None:
        return None
    return export.Parcel(
        parcel_id=r["tp_parcel_id"],
        county_name=r["tp_county"],
        acres=r["tp_acres"],
        deed_acres=r["tp_deed_acres"],
        tpad_url=r["tp_tpad_url"],
        owner=r["tp_owner"],
        owner2=r["tp_owner2"],
        property_address=r["tp_address"],
    )


def load_rows(
    conn: Connection, score_run_id: int, flag_parcels: bool = True
) -> list[export.ExportRow]:
    """The run's corridors with their parcels. `flag_parcels`: false when none are loaded."""
    min_acres = min_homestead_acres()
    rows = conn.execute(
        text(
            """
            SELECT s.corridor_id, c.nhdplusid, st.stream_name, s.status, s.tier, s.rank,
                   s.tier_rank, s.hydro_score, s.head_subscore, s.power_subscore,
                   s.net_head_ft, c.firm_power_w, c.firm_flow_cfs, c.flow_method,
                   c.flow_confidence, c.flow_flags, c.drainage_area_sqkm, c.length_m,
                   ST_X(o.pt) AS outlet_lon, ST_Y(o.pt) AS outlet_lat, s.explanation,
                   ST_AsGeoJSON(c.geom, 6) AS geometry, st.length_m AS reach_length_m,
                   tp.parcel_id AS tp_parcel_id, p.county_name AS tp_county,
                   tp.acres AS tp_acres, tp.deed_acres AS tp_deed_acres,
                   p.tpad_url AS tp_tpad_url, tp.distance_m AS tp_distance_m,
                   po.owner AS tp_owner, po.owner2 AS tp_owner2,
                   po.property_address AS tp_address,
                   sp.ids AS stretch_parcel_ids, sp.owners AS stretch_parcel_owners
            FROM hydro_scores s
            JOIN hydro_corridors c USING (corridor_id)
            JOIN streams st USING (stream_id)
            -- NHDPlus draws flowlines upstream to downstream: the last point is the outlet
            -- (for a stretch, its bottom: where the turbine would go).
            CROSS JOIN LATERAL (
                SELECT ST_EndPoint(ST_GeometryN(c.geom, ST_NumGeometries(c.geom))) AS pt
            ) o
            LEFT JOIN corridor_turbine_parcels tp USING (corridor_id)
            LEFT JOIN parcels p ON p.gislink = tp.gislink
            LEFT JOIN parcel_owners po ON po.gislink = tp.gislink
            CROSS JOIN LATERAL (
                SELECT array_agg(cp.parcel_id ORDER BY cp.length_m DESC, cp.parcel_id) AS ids,
                       array_remove(array_agg(DISTINCT o2.owner), NULL) AS owners
                FROM corridor_parcels cp
                LEFT JOIN parcel_owners o2 ON o2.gislink = cp.gislink
                WHERE cp.corridor_id = s.corridor_id
            ) sp
            WHERE s.run_id = :r
            ORDER BY s.corridor_id
            """
        ),
        {"r": score_run_id},
    ).mappings()
    out = []
    for r in rows:
        turbine = _parcel(r)
        distance = r["tp_distance_m"]
        out.append(
            export.ExportRow(
                **{
                    k: v
                    for k, v in r.items()
                    if k not in ("flow_flags", "geometry") and not k.startswith(("tp_", "stretch_"))
                },
                flow_flags=tuple(r["flow_flags"] or ()),
                geometry=json.loads(r["geometry"]),
                turbine_parcel=turbine,
                turbine_parcel_distance_m=distance,
                stretch_parcel_ids=tuple(r["stretch_parcel_ids"] or ()),
                stretch_parcel_owners=tuple(r["stretch_parcel_owners"] or ()),
                parcel_flags=(
                    export.parcel_flags(turbine, distance, min_acres) if flag_parcels else ()
                ),
            )
        )
    return out


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
        parcels = parcels_version(conn)
        rows = load_rows(conn, score_run_id, flag_parcels=parcels is not None)
    info = replace(info, exported_with=code_version(), parcels_version=parcels)
    out_dir = out_root / aoi.key / f"run_{score_run_id}"
    return info, out_dir, export.write_exports(out_dir, info, rows)
