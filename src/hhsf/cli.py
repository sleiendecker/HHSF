"""Command line entry point: `uv run hhsf --help`."""

import shapely
import typer
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from hhsf.analysis import (
    basins,
    buildable,
    drive_time,
    export_run,
    farmland,
    gauges,
    hydro,
    landcover,
    run,
    scoring,
    scoring_run,
    streamstats,
)
from hhsf.core import crs
from hhsf.core.config import REPO_ROOT, data_source_settings, hydrography_source, load_aoi
from hhsf.core.db import engine, record_source
from hhsf.ingest import (
    boundary,
    flood,
    hydrography,
    nhdplus_bulk,
    parcels,
    roads,
    soils,
    terrain,
)
from hhsf.ingest import landcover as nlcd

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
        boundary.load_county(conn, feature, area)
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


@ingest_app.command("terrain")
def ingest_terrain(aoi: str = AOI_OPTION) -> None:
    """Save the USGS 3DEP 10 m elevation model under the AOI's streams (needs hydrography)."""
    area = load_aoi(aoi)
    with engine().connect() as conn:
        reaches = run.load_reaches(conn, area)
    if not reaches:
        raise typer.BadParameter(
            f"No streams loaded for {area.name}. Run `uv run hhsf ingest hydrography` first."
        )
    bounds = shapely.GeometryCollection([r.geom for r in reaches]).bounds
    typer.echo(f"Fetching 3DEP elevations under {len(reaches)} streams in {area.name}...")
    _, source = terrain.fetch_dem(area.key, bounds, echo=typer.echo)
    with engine().begin() as conn:
        record_source(conn, source)
    typer.echo("Terrain ready.")


@ingest_app.command("parcels")
def ingest_parcels(
    aoi: str = AOI_OPTION,
    refresh: bool = typer.Option(
        False, help="Download again instead of using data/raw/ (the layer updates monthly)."
    ),
) -> None:
    """Load the AOI county's property parcels and owners from the Tennessee Comptroller."""
    area = load_aoi(aoi)
    typer.echo(f"Fetching parcels for {area.name} from the Tennessee Comptroller...")
    records = parcels.fetch_parcels(area, refresh, echo=typer.echo)
    with engine().begin() as conn:
        n = parcels.load_parcels(conn, area, records)
    typer.echo(f"Loaded {n:,} parcels (owner names kept apart, for local exports only).")


@ingest_app.command("flood")
def ingest_flood(
    aoi: str = AOI_OPTION,
    refresh: bool = typer.Option(
        False, help="Download again instead of using data/raw/ (after a flood map revision)."
    ),
) -> None:
    """Load the AOI county's FEMA flood zones (National Flood Hazard Layer)."""
    area = load_aoi(aoi)
    typer.echo(f"Fetching FEMA flood zones for {area.name} (the FEMA server can be slow)...")
    records = flood.fetch_flood_zones(area, refresh, echo=typer.echo)
    with engine().begin() as conn:
        n = flood.load_flood_zones(conn, area, records)
    if n == 0:
        typer.echo(f"FEMA has no flood map for {area.name}: every corridor will show 'not mapped'.")
    else:
        sfha = sum(r.sfha for r in records)
        typer.echo(f"Loaded {n:,} flood zones ({sfha:,} in the 100-year floodplain).")


@ingest_app.command("roads")
def ingest_roads(
    aoi: str = AOI_OPTION,
    refresh: bool = typer.Option(
        False, help="Download again instead of using data/raw/ (TIGER/Line updates yearly)."
    ),
) -> None:
    """Load the AOI county's drivable roads from Census TIGER/Line."""
    area = load_aoi(aoi)
    typer.echo(f"Fetching Census TIGER/Line roads for {area.name}...")
    records = roads.fetch_roads(area, refresh)
    with engine().begin() as conn:
        n = roads.load_roads(conn, area, records)
    public = sum(roads.ROAD_CLASSES[r.mtfcc].public for r in records)
    typer.echo(f"Loaded {n:,} road segments ({public:,} public roads).")


@ingest_app.command("soils")
def ingest_soils(
    aoi: str = AOI_OPTION,
    refresh: bool = typer.Option(
        False, help="Download again instead of using data/raw/ (SSURGO updates yearly)."
    ),
) -> None:
    """Load the AOI county's soils and their septic ratings from USDA SSURGO."""
    area = load_aoi(aoi)
    typer.echo(f"Fetching USDA SSURGO soils for {area.name} from Soil Data Access...")
    units, polygons, versions = soils.fetch_soils(area, refresh, echo=typer.echo)
    with engine().begin() as conn:
        n = soils.load_soils(conn, area, units, polygons, versions)
    rated = sum(u.ratings["septic_tn"][0] != soils.NOT_RATED for u in units)
    prime = sum(u.farmland_class == farmland.PRIME for u in units)
    typer.echo(
        f"Loaded {n:,} soil polygons ({len(units)} map units, {rated} with a septic rating, "
        f"{prime} prime farmland)."
    )


@ingest_app.command("all")
def ingest_all(aoi: str = AOI_OPTION, source: str | None = SOURCE_OPTION) -> None:
    """Boundary, then hydrography, terrain, parcels, flood zones, roads and soils."""
    ingest_boundary(aoi)
    ingest_hydrography(aoi, source)
    ingest_terrain(aoi)
    ingest_parcels(aoi)
    ingest_flood(aoi)
    ingest_roads(aoi)
    ingest_soils(aoi)


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
    use_regional_yield: bool = typer.Option(
        True,
        "--regional-yield/--no-regional-yield",
        help="Give basins too small for StreamStats the median StreamStats Q90 per sq mi "
        "of their HUC8 (low confidence, D-09), instead of the gauge transfer or no flow. "
        "Without it, small basins use the gauge where it covers them and are otherwise "
        "ranked on head only.",
    ),
    use_terrain: bool = typer.Option(
        True,
        "--terrain/--no-terrain",
        help="Use each reach's steepest stretch on the 3DEP elevation model (D-08). Without "
        "it, each whole reach is one corridor (overstates long reaches).",
    ),
    penstock_ft: float | None = typer.Option(
        None,
        help="Longest penstock (ft) a corridor may assume. Default: penstock_length_ft in "
        "config/hydro.yaml. For the D-08 sensitivity check (500 / 1000 / 2000).",
    ),
    limit: int | None = typer.Option(None, help="Only analyze the first N reaches (for testing)"),
) -> None:
    """Head, firm flow and firm power for every perennial reach; saved as a new run."""
    area = load_aoi(aoi)
    assumptions = hydro.load_assumptions()
    if penstock_ft is not None:
        assumptions.corridor.penstock_length_ft = penstock_ft
    dem = None
    if use_terrain:
        dem = terrain.dem_path(area.key)
        if not dem.exists():
            raise typer.BadParameter(
                f"No elevation model for {area.name}. Run `uv run hhsf ingest terrain --aoi "
                f"{area.key}` (or `make ingest`) first, or pass --no-terrain."
            )
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
    huc8s = None
    if use_regional_yield:
        with engine().connect() as conn:
            county = hydrography.county_area(conn, area)
        huc8s = basins.load_basins(area, county)
        typer.echo(f"Regional yield basins (HUC8): {', '.join(huc8s.polygons)}.")
    typer.echo(f"Analyzing perennial streams in {area.name}...")
    run_id, summary = run.run_hydro(
        engine(), area, source, service, limit, typer.echo, gauge, dem, assumptions, huc8s
    )
    typer.echo(f"Run {run_id} saved {summary['reaches']} hydro corridors.")
    if dem is not None:
        typer.echo(
            f"  {summary['stretch_shorter_than_reach']} are a stretch of a longer reach; the "
            f"rest are whole reaches up to {assumptions.corridor.penstock_length_ft:,.0f} ft."
        )
    if summary["head_flags"]:
        typer.echo(f"  no head for: {summary['head_flags']}")
    typer.echo(f"  flow method: {summary['flow_method']}")
    typer.echo(f"  flow confidence: {summary['flow_confidence']}")
    for basin, r in summary.get("regional_yield", {}).items():
        typer.echo(
            f"  regional yield {basin}: {r['yield_cfs_per_sqmi']:.4f} cfs/sq mi (median of "
            f"{r['streamstats_reaches']} StreamStats reaches), "
            f"filled in for {r['reaches_filled']} reaches"
        )
    typer.echo(
        f"  {summary['meets_power_target']} of {summary['with_power']} reaches with a power "
        f"estimate reach the 1 kW target ({summary['meets_power_target_medium_confidence']} "
        "of them at medium confidence)."
    )


_AOI_COUNTIES = text("SELECT geoid, aoi_key FROM counties WHERE aoi_key IS NOT NULL")


def _fail(message: str) -> int:
    typer.echo(message, err=True)
    return 1


@analyze_app.command("drive-area")
def analyze_drive_area() -> None:
    """Build the area within the D-02 drive of the origin, and list the counties it touches.

    Needs the Valhalla routing service (`make routing`). Settings: config/drive_time.yaml.
    """
    cfg = drive_time.load_config()
    routing = drive_time.Valhalla(cfg)
    try:
        _, shares = drive_time.build_area(engine(), cfg, routing, typer.echo)
    except drive_time.RoutingUnavailable as e:
        raise typer.Exit(_fail(str(e))) from e
    with engine().connect() as conn:
        analyzed = {r.geoid: r.aoi_key for r in conn.execute(_AOI_COUNTIES)}
    sqkm = sum(s.inside_sqkm for s in shares)
    typer.echo(
        f"Saved the {cfg.minutes}-minute drive area ({sqkm:,.0f} sq km). "
        f"It touches {len(shares)} counties:"
    )
    typer.echo(f"  {'county':<26} {'inside':>7}  {'sq km':>7}  AOI")
    for s in shares:
        aoi = analyzed.get(s.geoid) or "-"
        typer.echo(f"  {s.label:<26} {s.share:7.0%}  {s.inside_sqkm:7,.0f}  {aoi}")
    typer.echo("Counties without an AOI are the ones to add next (D-02).")


@analyze_app.command("buildable")
def analyze_buildable(aoi: str = AOI_OPTION) -> None:
    """Buildable ground on each corridor's turbine parcel: gentle slope, out of the
    100-year floodplain, septic-suitable soils (needs make ingest and analyze hydro)."""
    area = load_aoi(aoi)
    settings = buildable.load_settings()
    with engine().connect() as conn:
        hydro_run = buildable.latest_hydro_run(conn, area.key)
        if hydro_run is None:
            raise typer.BadParameter(f"No hydro run for {area.name}. Run `make analyze` first.")
        targets = buildable.turbine_parcels(conn, hydro_run)
        reaches = run.load_reaches(conn, area)
    if not targets:
        typer.echo(f"No turbine parcels for hydro run {hydro_run}: are parcels loaded?")
        return
    # The elevation model was saved for the streams; big parcels can reach past it.
    needed = buildable.bounds_of([*(r.geom for r in reaches), *(g for _, g in targets)])
    dem, source = terrain.fetch_dem(area.key, needed, echo=typer.echo)
    typer.echo(
        f"Buildable ground on {len(targets):,} turbine parcels of hydro run {hydro_run} "
        f"(slope up to {settings.max_slope_pct:g}%, outside the 100-year floodplain, "
        f"{settings.septic_rating} not {' or '.join(settings.septic_excluded)}, "
        f"patches of {settings.min_patch_acres:g} acres or more)..."
    )
    with engine().begin() as conn:
        record_source(conn, source)
        results = buildable.run_buildable(conn, area, targets, dem, settings, typer.echo)
    none = sum(r.buildable_acres == 0 for r in results)
    typer.echo(
        f"Saved {len(results):,} parcels: {sum(r.buildable_acres for r in results):,.0f} "
        f"buildable acres in all; {none:,} parcels have none."
    )


@analyze_app.command("farmland")
def analyze_farmland(aoi: str = AOI_OPTION) -> None:
    """Farmland on each corridor's turbine parcel: prime farmland, capability class,
    wet ground and pasture, from SSURGO soils (needs make ingest and analyze hydro)."""
    area = load_aoi(aoi)
    settings = farmland.load_settings()
    with engine().begin() as conn:
        hydro_run = buildable.latest_hydro_run(conn, area.key)
        if hydro_run is None:
            raise typer.BadParameter(f"No hydro run for {area.name}. Run `make analyze` first.")
        results = farmland.run_farmland(conn, area, hydro_run, settings)
    if not results:
        typer.echo(f"No turbine parcels for hydro run {hydro_run}: are parcels loaded?")
        return
    none = sum(f == 0 and c == 0 for _, f, c in results)
    typer.echo(
        f"Saved {len(results):,} turbine parcels of hydro run {hydro_run}: "
        f"{sum(f for _, f, _ in results):,.0f} acres of prime or statewide farmland, "
        f"{sum(c for _, _, c in results):,.0f} of capability class 1-"
        f"{settings.max_capability_class}; {none:,} parcels have neither."
    )


@analyze_app.command("landcover")
def analyze_landcover(
    aoi: str = AOI_OPTION,
    refresh: bool = typer.Option(False, help="Download the land cover again, ignoring the cache"),
) -> None:
    """Land cover on each corridor's turbine parcel: open field, forest, built up, tree
    canopy, and open land on good soils, from USGS NLCD 2021 (needs make ingest and
    analyze hydro)."""
    area = load_aoi(aoi)
    settings = farmland.load_settings()
    with engine().connect() as conn:
        hydro_run = buildable.latest_hydro_run(conn, area.key)
        if hydro_run is None:
            raise typer.BadParameter(f"No hydro run for {area.name}. Run `make analyze` first.")
        targets = buildable.turbine_parcels(conn, hydro_run)
    if not targets:
        typer.echo(f"No turbine parcels for hydro run {hydro_run}: are parcels loaded?")
        return
    needed = shapely.GeometryCollection([crs.to_analysis(g) for _, g in targets]).bounds
    rasters, source = nlcd.fetch_landcover(area.key, needed, refresh, echo=typer.echo)
    year = int(data_source_settings(nlcd.SOURCE_KEY)["year"])
    typer.echo(f"Land cover on {len(targets):,} turbine parcels of hydro run {hydro_run}...")
    with engine().begin() as conn:
        record_source(conn, source)
        results = landcover.run_landcover(conn, area, targets, rasters, settings, year, typer.echo)
    total = sum(r.parcel_acres for r in results)
    opened = sum(r.open_acres for r in results)
    typer.echo(
        f"Saved {len(results):,} parcels: {opened:,.0f} of {total:,.0f} acres open "
        f"({opened / total:.0%}), {sum(r.open_cropland_acres for r in results):,.0f} of them on "
        f"class 1-{settings.max_capability_class} soils; "
        f"{sum(r.open_acres == 0 for r in results):,} parcels have no open land."
    )


@analyze_app.command("drive-time")
def analyze_drive_time(
    aoi: str = AOI_OPTION,
    run_id: int | None = typer.Option(
        None, help="Hydro run whose corridors to time. Default: the AOI's latest finished one."
    ),
) -> None:
    """Drive time from the origin to every corridor's turbine end (needs `make routing`)."""
    area = load_aoi(aoi)
    cfg = drive_time.load_config()
    try:
        hydro_run_id, times = drive_time.compute_drive_times(
            engine(), area, cfg, drive_time.Valhalla(cfg), run_id
        )
    except drive_time.RoutingUnavailable as e:
        raise typer.Exit(_fail(str(e))) from e
    except scoring_run.NoHydroRun as e:
        raise typer.BadParameter(str(e)) from e
    found = [t.minutes for t in times if t.minutes is not None]
    inside = sum(m <= cfg.minutes for m in found)
    typer.echo(
        f"Drive times from {cfg.origin.name} to {len(times):,} corridors of hydro run "
        f"{hydro_run_id}: {inside:,} within {cfg.minutes} min, {len(found) - inside:,} beyond, "
        f"{len(times) - len(found):,} with no route."
    )
    if found:
        typer.echo(f"  closest {min(found):.0f} min, farthest {max(found):.0f} min.")


@score_app.command("hydro")
def score_hydro(
    aoi: str = AOI_OPTION,
    profile: str = typer.Option("hydro_v5", help="Scoring profile from config/scoring_profiles/"),
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
        f"confidence, {tiers.get('head_only', 0)} head only (no flow estimate), "
        f"{tiers.get(scoring.LOW_FLOW, 0)} low flow (under "
        f"{p.power_target_w / 1000:g} kW on a low-confidence flow)"
    )
    typer.echo(
        f"  filtered out: {status.get(scoring.BELOW_POWER_TARGET, 0)} below "
        f"{p.power_target_w / 1000:g} kW, {status.get(scoring.BELOW_MIN_HEAD, 0)} head-only or "
        f"low-flow below {p.head_only.min_net_head_ft:g} ft, {status.get(scoring.NOT_SCORABLE, 0)} "
        "missing data"
    )
    if (left_out := summary.get("overall_parts_left_out")) is not None:
        missing = ", ".join(f"{n:,} {part}" for part, n in sorted(left_out.items())) or "none"
        typer.echo(f"  overall score: parts left out for lack of data: {missing} (and privacy)")
    if show <= 0:
        return
    with eng.connect() as conn:
        rows = scoring_run.top(conn, score_run_id, show)
    typer.echo(
        f"\n{'rank':>4}  {'tier':<9} {'overall':>7} {'hydro':>5}  {'head ft':>7}  "
        f"{'power W':>8}  stream"
    )
    for r in rows:
        overall = f"{r.overall_score:7.1f}" if r.overall_score is not None else "      -"
        score = f"{r.hydro_score:5.1f}" if r.hydro_score is not None else "  -  "
        watts = f"{r.firm_power_w:8,.0f}" if r.firm_power_w is not None else "       -"
        name = r.stream_name or f"(unnamed, NHDPlus {r.nhdplusid})"
        typer.echo(
            f"{r.rank:>4}  {r.tier:<9} {overall} {score}  {r.net_head_ft:7.0f}  {watts}  {name}"
        )


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
