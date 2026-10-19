"""Alembic environment. Migrations are hand-written SQL; there is no ORM metadata."""

from alembic import context
from sqlalchemy import create_engine

from hhsf.core.config import settings

config = context.config
url = config.get_main_option("sqlalchemy.url") or settings().database_url


def run_migrations_offline() -> None:
    context.configure(url=url, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    with create_engine(url).connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
