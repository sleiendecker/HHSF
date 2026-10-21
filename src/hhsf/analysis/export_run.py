"""`hhsf export hydro`: write a score run's shortlist, every corridor and a report.

Loads one finished score run (default: the AOI's latest) with its hydro numbers and
hands the rows to export.py, which does all the formatting. Files go to
data/outputs/<aoi>/run_<score run id>/, so each run's exports sit side by side.

Parcels, flood zones and roads are linked when the export runs (views corridor_parcels
and corridor_turbine_parcels, migration 0010; corridor_flood_zones and
corridor_turbine_flood_zones, 0011; corridor_turbine_roads, 0012), so they match the
latest ingest. Drive times are read from corridor_drive_times (`hhsf analyze drive-time`), and the
turbine parcels' buildable ground from parcel_buildable (`hhsf analyze buildable`, linked
through corridor_turbine_buildable, 0013) and their farmland from parcel_farmland (`hhsf
analyze farmland`, through corridor_turbine_farmland, 0014) and their land cover from
parcel_landcover (`hhsf analyze landcover`, through corridor_turbine_landcover, 0016).
Each corridor's privacy is read from corridor_privacy (`hhsf analyze privacy`, 0018).
"""

import json
from dataclasses import replace
from pathlib import Path

from sqlalchemy import Connection, Engine, text

from hhsf.analysis import export
from hhsf.analysis.run import code_version
from hhsf.core.config import AOI, DATA_DIR, min_homestead_acres
from hhsf.ingest.buildings import SOURCE_KEY as BUILDINGS_SOURCE
from hhsf.ingest.flood import SOURCE_KEY as FLOOD_SOURCE
from hhsf.ingest.parcels import SOURCE_KEY as PARCELS_SOURCE
from hhsf.ingest.roads import SOURCE_KEY as ROADS_SOURCE
from hhsf.ingest.soils import SOURCE_KEY as SOILS_SOURCE

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


def roads_version(conn: Connection) -> str | None:
    """TIGER/Line roads version and download date, or None if no roads are loaded."""
    return _loaded_version(conn, ROADS_SOURCE, "roads")


def soils_version(conn: Connection, county_geoid: str) -> str | None:
    """The county's soil survey versions and download date, or None if none are loaded.

    (One data_sources row covers every county, so the version is read off the soils.)
    """
    row = conn.execute(
        text(
            "SELECT string_agg(DISTINCT survey_version, '; ') AS versions,"
            " to_char(max(ingested_at), 'YYYY-MM-DD') AS retrieved"
            " FROM soils WHERE county_geoid = :g AND source_key = :k"
        ),
        {"g": county_geoid, "k": SOILS_SOURCE},
    ).one()
    return None if row.versions is None else f"{row.versions}, downloaded {row.retrieved}"


def buildable_info(conn: Connection) -> export.BuildableInfo | None:
    """The settings of the latest buildable-ground computation, or None if never run."""
    row = conn.execute(
        text(
            "SELECT max_slope_pct, septic_rating, septic_excluded, min_patch_acres,"
            " to_char(computed_at, 'YYYY-MM-DD') AS computed FROM parcel_buildable"
            " ORDER BY computed_at DESC LIMIT 1"
        )
    ).one_or_none()
    if row is None:
        return None
    return export.BuildableInfo(
        max_slope_pct=row.max_slope_pct,
        septic_rating=row.septic_rating,
        septic_excluded=tuple(row.septic_excluded),
        min_patch_acres=row.min_patch_acres,
        computed=row.computed,
    )


def farmland_info(conn: Connection) -> export.FarmlandInfo | None:
    """The settings of the latest farmland computation, or None if never run."""
    row = conn.execute(
        text(
            "SELECT max_capability_class, wet_drainage,"
            " to_char(computed_at, 'YYYY-MM-DD') AS computed FROM parcel_farmland"
            " ORDER BY computed_at DESC LIMIT 1"
        )
    ).one_or_none()
    if row is None:
        return None
    return export.FarmlandInfo(
        max_capability_class=row.max_capability_class,
        wet_drainage=tuple(row.wet_drainage),
        computed=row.computed,
    )


def landcover_year(conn: Connection) -> int | None:
    """The NLCD year of the latest land cover computation, or None if never run."""
    return conn.execute(
        text("SELECT nlcd_year FROM parcel_landcover ORDER BY computed_at DESC LIMIT 1")
    ).scalar_one_or_none()


def privacy_info(conn: Connection) -> export.PrivacyInfo | None:
    """The settings of the latest privacy computation and the buildings' version, or None
    if never run."""
    row = conn.execute(
        text(
            "SELECT min_footprint_sqft, woods_canopy_pct,"
            " to_char(computed_at, 'YYYY-MM-DD') AS computed,"
            " (SELECT version || ', downloaded ' || to_char(retrieved_at, 'YYYY-MM-DD')"
            "  FROM data_sources WHERE source_key = :k) AS buildings_version"
            " FROM corridor_privacy ORDER BY computed_at DESC LIMIT 1"
        ),
        {"k": BUILDINGS_SOURCE},
    ).one_or_none()
    if row is None:
        return None
    return export.PrivacyInfo(
        min_footprint_sqft=row.min_footprint_sqft,
        woods_canopy_pct=row.woods_canopy_pct,
        buildings_version=row.buildings_version,
        computed=row.computed,
    )


def drive_area(conn: Connection) -> export.DriveArea | None:
    """The drive-time area corridors are checked against, or None if none is built."""
    row = conn.execute(
        text(
            "SELECT origin_name, minutes, engine_version,"
            " to_char(built_at, 'YYYY-MM-DD') AS built FROM drive_time_areas"
            " ORDER BY area_id DESC LIMIT 1"
        )
    ).one_or_none()
    if row is None:
        return None
    return export.DriveArea(
        origin_name=row.origin_name,
        minutes=row.minutes,
        built=row.built,
        engine_version=row.engine_version,
    )


def _road(r: dict) -> export.Road | None:
    if r["tr_distance_m"] is None:
        return None
    return export.Road(
        road_class=r["tr_road_class"],
        name=r["tr_name"],
        public=r["tr_public"],
        distance_m=r["tr_distance_m"],
    )


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


def _buildable(r: dict) -> export.Buildable | None:
    if r["tb_buildable_acres"] is None:
        return None
    return export.Buildable(
        gentle_acres=r["tb_gentle_acres"],
        buildable_acres=r["tb_buildable_acres"],
        nearest_m=r["tb_nearest_m"],
        no_soil_acres=r["tb_no_soil_acres"],
    )


def _farmland(r: dict) -> export.Farmland | None:
    if r["tfm_parcel_acres"] is None:
        return None
    return export.Farmland(
        parcel_acres=r["tfm_parcel_acres"],
        soil_acres=r["tfm_soil_acres"],
        prime_acres=r["tfm_prime_acres"],
        prime_if_acres=r["tfm_prime_if_acres"],
        statewide_acres=r["tfm_statewide_acres"],
        cropland_acres=r["tfm_capable_acres"],
        wet_acres=r["tfm_wet_acres"],
        pasture_aum=r["tfm_pasture_aum"],
    )


def _landcover(r: dict) -> export.Landcover | None:
    if r["tlc_open_acres"] is None:
        return None
    return export.Landcover(
        open_acres=r["tlc_open_acres"],
        open_cropland_acres=r["tlc_open_cropland_acres"],
        forest_acres=r["tlc_forest_acres"],
        developed_acres=r["tlc_developed_acres"],
        canopy_pct=r["tlc_canopy_pct"],
    )


def _privacy(r: dict) -> export.Privacy | None:
    if r["pv_home_site"] is None:
        return None
    return export.Privacy(
        home_site=r["pv_home_site"],
        lat=r["pv_lat"],
        lon=r["pv_lon"],
        buildings_quarter_mile=r["pv_quarter_mile"],
        buildings_half_mile=r["pv_half_mile"],
        buildings_per_sqmi=r["pv_per_sqmi"],
        nearest_building_m=r["pv_nearest_m"],
        parcel_buildings=r["pv_parcel_buildings"],
        public_road_m=r["pv_road_m"],
        road_woods_m=r["pv_woods_m"],
    )


def load_rows(
    conn: Connection,
    score_run_id: int,
    flag_parcels: bool = True,
    flag_flood: bool = True,
    flag_drive: bool = True,
    flag_buildable: bool = True,
    min_patch_acres: float = 0.5,
    flag_farmland: bool = True,
    flag_landcover: bool = True,
    flag_privacy: bool = True,
) -> list[export.ExportRow]:
    """The run's corridors with their parcels, flood zones, roads, drive times,
    buildable ground and farmland.

    `flag_parcels`, `flag_flood`, `flag_drive`, `flag_buildable` and `flag_farmland`:
    false when that layer isn't loaded (or the drive-time area, buildable ground or
    farmland isn't computed), so a missing layer isn't reported as "nothing found".
    `min_patch_acres`: the buildable ground's smallest patch, for the `soils_not_loaded`
    flag.
    """
    min_acres = min_homestead_acres()
    rows = conn.execute(
        text(
            """
            SELECT s.corridor_id, c.nhdplusid, st.stream_name, s.status, s.tier, s.rank,
                   s.tier_rank, s.hydro_score, s.head_subscore, s.power_subscore,
                   s.net_head_ft, c.firm_power_w, c.firm_flow_cfs, c.flow_method,
                   s.overall_score, s.buildability_subscore, s.agriculture_subscore,
                   s.logistics_subscore, s.privacy_subscore,
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
                   sf.sfha_m AS stretch_sfha_m,
                   tr.distance_m AS tr_distance_m, tr.road_class AS tr_road_class,
                   tr.name AS tr_name, tr.public AS tr_public,
                   tr.public_distance_m AS turbine_public_road_m,
                   dt.minutes AS drive_minutes, dt.distance_km AS drive_km,
                   dt.road_point_m AS drive_road_point_m, dt.any_road AS dt_any_road,
                   da.in_drive_area,
                   tb.gentle_acres AS tb_gentle_acres, tb.buildable_acres AS tb_buildable_acres,
                   tb.nearest_buildable_m AS tb_nearest_m, tb.no_soil_acres AS tb_no_soil_acres,
                   tfm.parcel_acres AS tfm_parcel_acres, tfm.soil_acres AS tfm_soil_acres,
                   tfm.prime_acres AS tfm_prime_acres, tfm.prime_if_acres AS tfm_prime_if_acres,
                   tfm.statewide_acres AS tfm_statewide_acres,
                   tfm.capable_acres AS tfm_capable_acres, tfm.wet_acres AS tfm_wet_acres,
                   tfm.pasture_aum AS tfm_pasture_aum,
                   tlc.open_acres AS tlc_open_acres,
                   tlc.open_cropland_acres AS tlc_open_cropland_acres,
                   tlc.forest_acres AS tlc_forest_acres,
                   tlc.developed_acres AS tlc_developed_acres,
                   tlc.canopy_pct AS tlc_canopy_pct,
                   pv.home_site_basis AS pv_home_site, ST_Y(pv.home_site) AS pv_lat,
                   ST_X(pv.home_site) AS pv_lon,
                   pv.buildings_quarter_mile AS pv_quarter_mile,
                   pv.buildings_half_mile AS pv_half_mile,
                   pv.buildings_per_sqmi AS pv_per_sqmi,
                   pv.nearest_building_m AS pv_nearest_m,
                   pv.parcel_buildings AS pv_parcel_buildings,
                   pv.public_road_m AS pv_road_m, pv.road_woods_m AS pv_woods_m
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
            LEFT JOIN corridor_turbine_roads tr USING (corridor_id)
            LEFT JOIN corridor_drive_times dt USING (corridor_id)
            LEFT JOIN corridor_turbine_buildable tb USING (corridor_id)
            LEFT JOIN corridor_turbine_farmland tfm USING (corridor_id)
            LEFT JOIN corridor_turbine_landcover tlc USING (corridor_id)
            LEFT JOIN corridor_privacy pv USING (corridor_id)
            CROSS JOIN LATERAL (
                SELECT bool_or(ST_Intersects(a.geom, o.pt)) AS in_drive_area
                FROM drive_time_areas a
            ) da
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
        road = _road(r)
        build = _buildable(r)
        farm = _farmland(r)
        cover = _landcover(r)
        privacy = _privacy(r)
        out.append(
            export.ExportRow(
                **{
                    k: v
                    for k, v in r.items()
                    if k not in ("flow_flags", "geometry", "stretch_sfha_m")
                    and not k.startswith(
                        ("tp_", "tf_", "tr_", "dt_", "tb_", "tfm_", "tlc_", "pv_", "stretch_")
                    )
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
                turbine_road=road,
                access_flags=export.access_flags(
                    road, r["drive_minutes"], r["in_drive_area"], flag_drive, bool(r["dt_any_road"])
                ),
                turbine_buildable=build,
                buildable_flags=(
                    export.buildable_flags(turbine, build, min_patch_acres)
                    if flag_buildable
                    else ()
                ),
                turbine_farmland=farm,
                farmland_flags=export.farmland_flags(turbine, farm) if flag_farmland else (),
                turbine_landcover=cover,
                landcover_flags=export.landcover_flags(turbine, cover) if flag_landcover else (),
                privacy=privacy,
                privacy_flags=export.privacy_flags(privacy) if flag_privacy else (),
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
        road_info = roads_version(conn)
        area = drive_area(conn)
        soils = soils_version(conn, aoi.county_geoid)
        build = buildable_info(conn)
        farm = farmland_info(conn)
        cover_year = landcover_year(conn)
        privacy = privacy_info(conn)
        rows = load_rows(
            conn,
            score_run_id,
            flag_parcels=parcels is not None,
            flag_flood=flood is not None,
            flag_drive=area is not None,
            flag_buildable=build is not None,
            min_patch_acres=build.min_patch_acres if build else 0.5,
            flag_farmland=farm is not None,
            flag_landcover=cover_year is not None,
            flag_privacy=privacy is not None,
        )
    info = replace(
        info,
        exported_with=code_version(),
        parcels_version=parcels,
        flood_version=flood,
        roads_version=road_info,
        drive_area=area,
        soils_version=soils,
        buildable=build,
        farmland=farm,
        landcover_year=cover_year,
        privacy=privacy,
    )
    out_dir = out_root / aoi.key / f"run_{score_run_id}"
    return info, out_dir, export.write_exports(out_dir, info, rows)
