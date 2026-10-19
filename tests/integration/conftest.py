import pytest
from alembic import command
from alembic.config import Config

from hhsf.core.config import REPO_ROOT


@pytest.fixture(scope="session")
def db_url():
    """One throwaway PostGIS for all integration tests, migrated to head."""
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("imresamu/postgis:17-3.5", driver="psycopg") as pg:
        url = pg.get_connection_url()
        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        cfg.set_main_option("sqlalchemy.url", url)
        command.upgrade(cfg, "head")
        yield url
