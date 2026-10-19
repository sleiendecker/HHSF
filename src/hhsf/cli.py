"""Command line entry point: `uv run hhsf --help`."""

import typer
from alembic import command
from alembic.config import Config

from hhsf.core.config import REPO_ROOT, load_aoi
from hhsf.core.db import engine
from hhsf.ingest import boundary, hydrography

app = typer.Typer(help="Hydro Homestead Site Finder", no_args_is_help=True)
db_app = typer.Typer(help="Database schema", no_args_is_help=True)
ingest_app = typer.Typer(help="Load source data into PostGIS", no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(ingest_app, name="ingest")

AOI_OPTION = typer.Option("sequatchie_tn", help="AOI key from config/aoi/")


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
def ingest_hydrography(aoi: str = AOI_OPTION) -> None:
    """Load perennial streams for the AOI from NHDPlus HR."""
    area = load_aoi(aoi)
    with engine().connect() as conn:
        county = hydrography.county_area(conn, area)
    typer.echo(f"Fetching perennial streams for {area.name} (the USGS service can be slow)...")
    records = hydrography.fetch_perennial(area, county)
    with engine().begin() as conn:
        count = hydrography.load_streams(conn, records)
    typer.echo(f"Loaded {count} perennial stream reaches.")


@ingest_app.command("all")
def ingest_all(aoi: str = AOI_OPTION) -> None:
    """Boundary, then hydrography."""
    ingest_boundary(aoi)
    ingest_hydrography(aoi)


if __name__ == "__main__":
    app()
