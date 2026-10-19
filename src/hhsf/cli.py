"""Command line entry point: `uv run hhsf --help`."""

import typer
from alembic import command
from alembic.config import Config

from hhsf.core.config import REPO_ROOT, hydrography_source, load_aoi
from hhsf.core.db import engine
from hhsf.ingest import boundary, hydrography, nhdplus_bulk

app = typer.Typer(help="Hydro Homestead Site Finder", no_args_is_help=True)
db_app = typer.Typer(help="Database schema", no_args_is_help=True)
ingest_app = typer.Typer(help="Load source data into PostGIS", no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(ingest_app, name="ingest")

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


if __name__ == "__main__":
    app()
