"""Command line entry point: `uv run hhsf --help`."""

import typer
from alembic import command
from alembic.config import Config

from hhsf.analysis import (
    export_run,
    gauges,
    hydro,
    run,
    scoring,
    scoring_run,
    streamstats,
)
from hhsf.core.config import REPO_ROOT, hydrography_source, load_aoi
from hhsf.core.db import engine
from hhsf.ingest import boundary, hydrography, nhdplus_bulk

app = typer.Typer(help="Hydro Homestead Site Finder", no_args_is_help=True)
db_app = typer.Typer(help="Database schema", no_args_is_help=True)
ingest_app = typer.Typer(help="Load source data into PostGIS", no_args_is_help=True)
app.add_typer(db_app, name="db")
analyze_app = typer.Typer(help="Compute results from loaded data", no_args_is_help=True)
app.add_typer(ingest_app, name="ingest")
app.add_typer(analyze_app, name="analyze")
score_app = typer.Typer(help="Score and rank analysis results", no_args_is_help=True)
app.add_typer(score_app, name="score")
export_app = typer.Typer(help="Write results to files in data/outputs/", no_args_is_help=True)
app.add_typer(export_app, name="export")

AOI_OPTION = typer.Option("sequatchie_tn", help="AOI key from config/aoi/")
SOURCE_OPTION = typer.Option(
    None,
    help="bulk (download NHDPlus HR files) or service (USGS web service). "
    "Default: hydrography_source in config/data_sources.yaml.",
)


@db_app.command("upgrade")
def db_upgrade() -> None:
    """Apply all schema migrations."""
    command.upgrade(Config(str(REPO_ROOT / "alembic.ini")), "head")


@ingest_app.command("boundary")
def ingest_boundary(aoi: str = AOI_OPTION) -> None:
    """Load the AOI's county boundary from Census TIGERweb."""
    area = load_aoi(aoi)
    typer.echo(f"Fetching boundary for {area.name}...")
    feature = boundary.fetch_county(area)
    with engine().begin() as conn:
        boundary.load_county(conn, feature)
    typer.echo("Boundary loaded.")


@ingest_app.command("hydrography")
def ingest_hydrography(aoi: str = AOI_OPTION, source: str | None = SOURCE_OPTION) -> None:
    """Load perennial streams for the AOI from NHDPlus HR."""
    area = load_aoi(aoi)
    source = source or hydrography_source()
    with engine().connect() as conn:
        county = hydrography.county_area(conn, area)
    if source == "bulk":
        typer.echo(f"Loading perennial streams for {area.name} from NHDPlus HR files...")
        batches = nhdplus_bulk.fetch_perennial(area, county, echo=typer.echo)
        source_key = nhdplus_bulk.SOURCE_KEY
    elif source == "service":
        typer.echo(f"Fetching perennial streams for {area.name} (the USGS service can be slow)...")
        batches = hydrography.fetch_perennial(area, county)
        source_key = hydrography.SOURCE_KEY
    else:
        raise typer.BadParameter(f"Unknown source '{source}'. Use bulk or service.")
    # Save each batch as it arrives: a failed run keeps what it got, and the map fills in.
    total = 0
    for records in batches:
        with engine().begin() as conn:
            total += hydrography.load_streams(conn, records, source_key)
        if records:
            typer.echo(f"  saved {len(records)} reaches ({total} so far)")
    typer.echo(f"Loaded {total} perennial stream reaches.")


@ingest_app.command("all")
def ingest_all(aoi: str = AOI_OPTION, source: str | None = SOURCE_OPTION) -> None:
    """Boundary, then hydrography."""
    ingest_boundary(aoi)
    ingest_hydrography(aoi, source)


@analyze_app.command("hydro")
def analyze_hydro(
    aoi: str = AOI_OPTION,
    use_streamstats: bool = typer.Option(
        True,
        "--streamstats/--no-streamstats",
        help="Ask USGS StreamStats for Q90 low flow. Without it, every reach uses the "
        "NHDPlus mean flow, flagged low confidence.",
    ),
    use_gauge: bool = typer.Option(
        True,
        "--gauge/--no-gauge",
        help="Where StreamStats can't help, scale the AOI's reference gauge's measured "
        "Q90 by drainage area (low confidence).",
    ),
    limit: int | None = typer.Option(None, help="Only analyze the first N reaches (for testing)"),
) -> None:
    """Head, firm flow and firm power for every perennial reach; saved as a new run."""
    area = load_aoi(aoi)
    assumptions = hydro.load_assumptions()
    source = service = gauge = None
    if use_streamstats:
        service = streamstats.load_service_config()
        source = streamstats.StreamStats(assumptions.streamstats, service)
        typer.echo("Asking StreamStats for low flows (responses are cached in data/cache/).")
    if use_gauge and area.reference_gauge is not None:
        try:
            gauge = gauges.load_transfer(area.reference_gauge, assumptions.gauge_transfer)
        except (gauges.GaugeError, RuntimeError) as e:
            typer.echo(f"  Skipping gauge {area.reference_gauge.site_no}: {e}")
        else:
            r = gauge.record
            typer.echo(
                f"Gauge {r['site_no']}: Q90 {r['q90_cfs']:.1f} cfs from {r['approved_days']} days "
                f"({r['period'][0]} to {r['period'][1]}), {r['drainage_area_sqmi']:g} sq mi."
            )
    typer.echo(f"Analyzing perennial streams in {area.name}...")
    run_id, summary = run.run_hydro(
        engine(), area, source, service, limit, echo=typer.echo, gauge=gauge
    )
    typer.echo(f"Run {run_id} saved {summary['reaches']} hydro corridors.")
    typer.echo(f"  flow method: {summary['flow_method']}")
    typer.echo(f"  flow confidence: {summary['flow_confidence']}")
    typer.echo(
        f"  {summary['meets_power_target']} of {summary['with_power']} reaches with a power "
        f"estimate reach the 1 kW target ({summary['meets_power_target_medium_confidence']} "
        "of them at medium confidence)."
    )


@score_app.command("hydro")
def score_hydro(
    aoi: str = AOI_OPTION,
    profile: str = typer.Option("hydro_v1", help="Scoring profile from config/scoring_profiles/"),
    run_id: int | None = typer.Option(
        None, help="Hydro run to score. Default: the AOI's latest finished one."
    ),
    show: int = typer.Option(20, help="How many of the top corridors to print"),
) -> None:
    """Score and rank a hydro run's corridors; saved as a new run (see docs/SCORING.md)."""
    area = load_aoi(aoi)
    p = scoring.load_profile(profile)
    eng = engine()
    try:
        score_run_id, hydro_run_id, summary = scoring_run.score_hydro(eng, area, p, run_id)
    except scoring_run.NoHydroRun as e:
        raise typer.BadParameter(str(e)) from e
    status, tiers = summary["status"], summary["ranked_by_tier"]
    typer.echo(
        f"Run {score_run_id} scored hydro run {hydro_run_id} with profile {p.version}: "
        f"{summary['corridors']} corridors."
    )
    typer.echo(
        f"  ranked: {tiers.get('medium', 0)} medium confidence, {tiers.get('low', 0)} low "
        f"confidence, {tiers.get('head_only', 0)} head only (no flow estimate)"
    )
    typer.echo(
        f"  filtered out: {status.get(scoring.BELOW_POWER_TARGET, 0)} below "
        f"{p.power_target_w / 1000:g} kW, {status.get(scoring.BELOW_MIN_HEAD, 0)} head-only "
        f"below {p.head_only.min_net_head_ft:g} ft, {status.get(scoring.NOT_SCORABLE, 0)} "
        "missing data"
    )
    if show <= 0:
        return
    with eng.connect() as conn:
        rows = scoring_run.top(conn, score_run_id, show)
    typer.echo(f"\n{'rank':>4}  {'tier':<9} {'score':>5}  {'head ft':>7}  {'power W':>8}  stream")
    for r in rows:
        score = f"{r.hydro_score:5.1f}" if r.hydro_score is not None else "  -  "
        watts = f"{r.firm_power_w:8,.0f}" if r.firm_power_w is not None else "       -"
        name = r.stream_name or f"(unnamed, NHDPlus {r.nhdplusid})"
        typer.echo(f"{r.rank:>4}  {r.tier:<9} {score}  {r.net_head_ft:7.0f}  {watts}  {name}")


@export_app.command("hydro")
def export_hydro(
    aoi: str = AOI_OPTION,
    run_id: int | None = typer.Option(
        None, help="Score run to export. Default: the AOI's latest finished one."
    ),
) -> None:
    """Shortlist (CSV + GeoJSON), every corridor (CSV) and a summary report for a score run."""
    area = load_aoi(aoi)
    try:
        info, out_dir, files = export_run.export_hydro(engine(), area, run_id)
    except export_run.NoScoreRun as e:
        raise typer.BadParameter(str(e)) from e
    typer.echo(
        f"Exported score run {info.score_run_id} (hydro run {info.hydro_run_id}, profile "
        f"{info.scoring_profile_version}) to {out_dir.relative_to(REPO_ROOT)}/:"
    )
    for path in files:
        typer.echo(f"  {path.name}")


if __name__ == "__main__":
    app()
