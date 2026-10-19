"""`hhsf score hydro`: score and rank the corridors of a finished hydro run.

Each scoring gets its own analysis_runs row (analysis = 'hydro_score') holding the
scoring profile version, the whole profile and the hydro run it scored, then one
hydro_scores row per corridor. Earlier score runs are kept.
"""

import json
from collections import Counter

from sqlalchemy import Connection, Engine, text

from hhsf.analysis import scoring
from hhsf.analysis.run import code_version
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


def summarize(scores: list[scoring.Score]) -> dict:
    return {
        "corridors": len(scores),
        "status": dict(Counter(s.status for s in scores)),
        "ranked_by_tier": dict(Counter(s.tier for s in scores if s.rank is not None)),
    }


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
                power_subscore, hydro_score, rank, tier_rank, explanation
            ) VALUES (
                :run_id, :corridor_id, :status, :tier, :net_head_ft, :head_subscore,
                :power_subscore, :hydro_score, :rank, :tier_rank, CAST(:explanation AS jsonb)
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
        scores = scoring.score_all(load_inputs(conn, hydro_run_id), profile)
        summary = summarize(scores)
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
            SELECT s.rank, s.tier, s.hydro_score, s.head_subscore, s.net_head_ft,
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
