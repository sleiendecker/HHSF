"""`hhsf export hydro`: write a score run's shortlist, every corridor and a report.

Loads one finished score run (default: the AOI's latest) with its hydro numbers and
hands the rows to export.py, which does all the formatting. Files go to
data/outputs/<aoi>/run_<score run id>/, so each run's exports sit side by side.

Parcels and flood zones are linked when the export runs (views corridor_parcels and
corridor_turbine_parcels, migration 0010; corridor_flood_zones and
corridor_turbine_flood_zones, 0011), so they match the latest ingest.
"""

import json
from dataclasses import replace
from pathlib import Path

from sqlalchemy import Connection, Engine, text

from hhsf.analysis import export
from hhsf.analysis.run import code_version
from hhsf.core.config import AOI, DATA_DIR, min_homestead_acres
from hhsf.ingest.flood import SOURCE_KEY as FLOOD_SOURCE
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


def _loaded_version(conn: Connection, source_key: str, table: str) -> str | None:
    """A source's version and download date, or None if its table is empty."""
    row = conn.execute(
        text(
            "SELECT version, to_char(retrieved_at, 'YYYY-MM-DD') AS retrieved"
            f" FROM data_sources WHERE source_key = :k AND EXISTS (SELECT 1 FROM {table})"
        ),
        {"k": source_key},
    ).one_or_none()
    return None if row is None else f"{row.version}, downloaded {row.retrieved}"


def parcels_version(conn: Connection) -> str | None:
    """The parcel layer's version and download date, or None if no parcels are loaded."""
    return _loaded_version(conn, PARCELS_SOURCE, "parcels")


def flood_version(conn: Connection) -> str | None:
    """FEMA's flood layer version and download date, or None if no zones are loaded."""
    return _loaded_version(conn, FLOOD_SOURCE, "flood_zones")


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


def _zone_labels(zones: list[str] | None) -> tuple[str, ...]:
    """'AE|FLOODWAY' etc. (longest share first) as distinct labels, in that order."""
    labels = []
    for z in zones or ():
        zone, _, subtype = z.partition("|")
        labels.append(export.flood_zone_label(zone, subtype or None))
    return tuple(dict.fromkeys(labels))


def _flood_zone(r: dict) -> export.FloodZone | None:
    if r["tf_zone"] is None:
        return None
    return export.FloodZone(
        zone=r["tf_zone"], subtype=r["tf_subtype"], sfha=r["tf_sfha"], floodway=r["tf_floodway"]
    )


def load_rows(
    conn: Connection, score_run_id: int, flag_parcels: bool = True, flag_flood: bool = True
) -> list[export.ExportRow]:
    """The run's corridors with their parcels and flood zones.

    `flag_parcels` and `flag_flood`: false when that layer isn't loaded, so a missing
    layer isn't reported as "nothing found".
    """
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
                   sp.ids AS stretch_parcel_ids, sp.owners AS stretch_parcel_owners,
                   tf.zone AS tf_zone, tf.subtype AS tf_subtype, tf.sfha AS tf_sfha,
                   tf.floodway AS tf_floodway, sf.zones AS stretch_flood_zones,
                   sf.sfha_m AS stretch_sfha_m
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
            LEFT JOIN corridor_turbine_flood_zones tf USING (corridor_id)
            CROSS JOIN LATERAL (
                SELECT array_agg(f.zone || coalesce('|' || f.subtype, '')
                                 ORDER BY f.length_m DESC) AS zones,
                       CASE WHEN count(*) > 0
                            THEN coalesce(sum(f.length_m) FILTER (WHERE f.sfha), 0) END AS sfha_m
                FROM corridor_flood_zones f
                WHERE f.corridor_id = s.corridor_id
            ) sf
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
        flood_zone = _flood_zone(r)
        out.append(
            export.ExportRow(
                **{
                    k: v
                    for k, v in r.items()
                    if k not in ("flow_flags", "geometry", "stretch_sfha_m")
                    and not k.startswith(("tp_", "tf_", "stretch_"))
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
                turbine_flood_zone=flood_zone,
                stretch_flood_zones=_zone_labels(r["stretch_flood_zones"]),
                stretch_sfha_m=r["stretch_sfha_m"],
                flood_flags=(
                    export.flood_flags(flood_zone, r["stretch_sfha_m"]) if flag_flood else ()
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
        flood = flood_version(conn)
        rows = load_rows(
            conn, score_run_id, flag_parcels=parcels is not None, flag_flood=flood is not None
        )
    info = replace(info, exported_with=code_version(), parcels_version=parcels, flood_version=flood)
    out_dir = out_root / aoi.key / f"run_{score_run_id}"
    return info, out_dir, export.write_exports(out_dir, info, rows)
