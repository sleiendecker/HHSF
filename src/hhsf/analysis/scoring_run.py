"""`hhsf score hydro`: score and rank the corridors of a finished hydro run.

Each scoring gets its own analysis_runs row (analysis = 'hydro_score') holding the
scoring profile version, the whole profile and the hydro run it scored, then one
hydro_scores row per corridor. Earlier score runs are kept.

A profile with an `overall` section (hydro_v4 on) also reads each corridor's site
data as it stands when scoring runs: its turbine parcel's buildable ground and
farmland and land cover, the privacy around its home site (`make analyze`), the nearest
public road, and the drive time (`make drive-time`). Run those first; whatever is
missing is left out of the overall score.
"""

import json
from collections import Counter
from dataclasses import replace

from sqlalchemy import Connection, Engine, text

from hhsf.analysis import scoring
from hhsf.analysis.run import code_version
from hhsf.core import units
from hhsf.core.config import AOI


class NoHydroRun(LookupError):
    pass


def latest_hydro_run(conn: Connection, aoi_key: str) -> int:
    run_id = conn.execute(
        text(
            "SELECT max(run_id) FROM analysis_runs"
            " WHERE analysis = 'hydro' AND status = 'finished' AND aoi_key = :aoi"
        ),
        {"aoi": aoi_key},
    ).scalar_one()
    if run_id is None:
        raise NoHydroRun(f"No finished hydro run for '{aoi_key}'. Run `make analyze` first.")
    return run_id


def load_inputs(conn: Connection, hydro_run_id: int) -> list[scoring.CorridorInputs]:
    rows = conn.execute(
        text(
            """
            SELECT corridor_id, net_head_m, firm_power_w, power_confidence,
                   flow_confidence, flow_method, flow_flags
            FROM hydro_corridors WHERE run_id = :r ORDER BY corridor_id
            """
        ),
        {"r": hydro_run_id},
    ).all()
    return [scoring.CorridorInputs(*row[:6], tuple(row[6] or ())) for row in rows]


def load_site_inputs(conn: Connection, hydro_run_id: int) -> dict[int, scoring.SiteInputs]:
    """Each corridor's site data for the overall score (None where there's none)."""
    rows = conn.execute(
        text(
            """
            SELECT c.corridor_id, b.buildable_acres, b.nearest_buildable_m,
                   r.public_distance_m, f.prime_acres + f.statewide_acres AS farmland_acres,
                   f.capable_acres, f.pasture_aum, l.open_cropland_acres, d.minutes,
                   pr.buildings_quarter_mile, pr.nearest_building_m, pr.buildings_per_sqmi,
                   pr.road_woods_m, pr.public_road_m AS home_road_m
            FROM hydro_corridors c
            LEFT JOIN corridor_turbine_buildable b USING (corridor_id)
            LEFT JOIN corridor_turbine_roads r USING (corridor_id)
            LEFT JOIN corridor_turbine_farmland f USING (corridor_id)
            LEFT JOIN corridor_turbine_landcover l USING (corridor_id)
            LEFT JOIN corridor_drive_times d USING (corridor_id)
            LEFT JOIN corridor_privacy pr USING (corridor_id)
            WHERE c.run_id = :r
            """
        ),
        {"r": hydro_run_id},
    ).all()

    def ft(m: float | None) -> float | None:
        return None if m is None else units.m_to_ft(m)

    return {
        r.corridor_id: scoring.SiteInputs(
            buildable_acres=r.buildable_acres,
            nearest_buildable_ft=ft(r.nearest_buildable_m),
            public_road_ft=ft(r.public_distance_m),
            farmland_acres=r.farmland_acres,
            cropland_acres=r.capable_acres,
            pasture_aum=r.pasture_aum,
            open_cropland_acres=r.open_cropland_acres,
            buildings_quarter_mile=r.buildings_quarter_mile,
            nearest_building_ft=ft(r.nearest_building_m),
            buildings_per_sqmi=r.buildings_per_sqmi,
            road_woods_ft=ft(r.road_woods_m),
            home_road_ft=ft(r.home_road_m),
            drive_minutes=r.minutes,
        )
        for r in rows
    }


def summarize(scores: list[scoring.Score], p: scoring.ScoringProfile) -> dict:
    out = {
        "corridors": len(scores),
        "status": dict(Counter(s.status for s in scores)),
        "ranked_by_tier": dict(Counter(s.tier for s in scores if s.rank is not None)),
    }
    overall = [s for s in scores if s.overall is not None]
    if overall:
        # Profiles before hydro_v6 always leave privacy out: no need to count it.
        always = "privacy" if p.overall is not None and p.overall.privacy is None else None
        out["overall_parts_left_out"] = dict(
            Counter(part for s in overall for part in s.overall["left_out"] if part != always)
        )
    return out


def save_scores(
    conn: Connection, run_id: int, scores: list[scoring.Score], p: scoring.ScoringProfile
) -> None:
    if not scores:
        return
    conn.execute(
        text(
            """
            INSERT INTO hydro_scores (
                run_id, corridor_id, status, tier, net_head_ft, head_subscore,
                power_subscore, hydro_score, rank, tier_rank, explanation, overall_score,
                buildability_subscore, agriculture_subscore, logistics_subscore,
                privacy_subscore
            ) VALUES (
                :run_id, :corridor_id, :status, :tier, :net_head_ft, :head_subscore,
                :power_subscore, :hydro_score, :rank, :tier_rank, CAST(:explanation AS jsonb),
                :overall_score, :buildability_subscore, :agriculture_subscore,
                :logistics_subscore, :privacy_subscore
            )
            """
        ),
        [
            {
                "run_id": run_id,
                "corridor_id": s.corridor_id,
                "status": s.status,
                "tier": s.tier,
                "net_head_ft": s.net_head_ft,
                "head_subscore": s.head_subscore,
                "power_subscore": s.power_subscore,
                "hydro_score": s.hydro_score,
                "rank": s.rank,
                "tier_rank": s.tier_rank,
                "explanation": json.dumps(s.explanation(p)),
                "overall_score": s.overall_score,
                "buildability_subscore": s.buildability_subscore,
                "agriculture_subscore": s.agriculture_subscore,
                "logistics_subscore": s.logistics_subscore,
                "privacy_subscore": s.privacy_subscore,
            }
            for s in scores
        ],
    )


def score_hydro(
    eng: Engine,
    aoi: AOI,
    profile: scoring.ScoringProfile,
    hydro_run_id: int | None = None,
) -> tuple[int, int, dict]:
    """Score a hydro run (default: the AOI's latest finished one) as a new run.

    Returns (score_run_id, hydro_run_id, summary). One transaction: a failure
    leaves nothing behind.
    """
    with eng.begin() as conn:
        if hydro_run_id is None:
            hydro_run_id = latest_hydro_run(conn, aoi.key)
        inputs = load_inputs(conn, hydro_run_id)
        if profile.overall is not None:
            site = load_site_inputs(conn, hydro_run_id)
            inputs = [replace(c, site=site.get(c.corridor_id)) for c in inputs]
        scores = scoring.score_all(inputs, profile)
        summary = summarize(scores, profile)
        run_id = conn.execute(
            text(
                """
                INSERT INTO analysis_runs (
                    analysis, aoi_key, code_version, scoring_profile_version,
                    data_source_versions, params, summary, source_run_id,
                    finished_at, status
                )
                SELECT 'hydro_score', :aoi, :code, :version, data_source_versions,
                       CAST(:params AS jsonb), CAST(:summary AS jsonb), run_id,
                       now(), 'finished'
                FROM analysis_runs
                WHERE run_id = :hydro_run_id AND analysis = 'hydro' AND status = 'finished'
                  AND aoi_key = :aoi
                RETURNING run_id
                """
            ),
            {
                "aoi": aoi.key,
                "code": code_version(),
                "version": profile.version,
                "params": json.dumps({"scoring_profile": profile.model_dump()}),
                "summary": json.dumps(summary),
                "hydro_run_id": hydro_run_id,
            },
        ).scalar_one_or_none()
        if run_id is None:
            raise NoHydroRun(f"Run {hydro_run_id} is not a finished hydro run for '{aoi.key}'.")
        save_scores(conn, run_id, scores, profile)
    return run_id, hydro_run_id, summary


def top(conn: Connection, run_id: int, n: int) -> list:
    """The best `n` ranked corridors of a score run, for printing."""
    return conn.execute(
        text(
            """
            SELECT s.rank, s.tier, s.overall_score, s.hydro_score, s.head_subscore, s.net_head_ft,
                   c.firm_power_w, c.flow_method, st.stream_name, c.nhdplusid
            FROM hydro_scores s
            JOIN hydro_corridors c USING (corridor_id)
            JOIN streams st USING (stream_id)
            WHERE s.run_id = :r AND s.rank IS NOT NULL
            ORDER BY s.rank LIMIT :n
            """
        ),
        {"r": run_id, "n": n},
    ).all()
