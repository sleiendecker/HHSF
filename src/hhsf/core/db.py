"""Database engine, the data_sources provenance record, and the saved map layer."""

from sqlalchemy import Connection, Engine, create_engine, text

from hhsf.core.config import DataSource, settings


def engine(url: str | None = None) -> Engine:
    return create_engine(url or settings().database_url)


def record_source(conn: Connection, source: DataSource) -> None:
    """Upsert a data_sources row so every loaded feature can point at its origin."""
    conn.execute(
        text(
            """
            INSERT INTO data_sources (source_key, description, license, version, url, retrieved_at)
            VALUES (:key, :description, :license, :version, :url, now())
            ON CONFLICT (source_key) DO UPDATE SET
                description = EXCLUDED.description,
                license = EXCLUDED.license,
                version = EXCLUDED.version,
                url = EXCLUDED.url,
                retrieved_at = EXCLUDED.retrieved_at
            """
        ),
        source.model_dump(),
    )


def refresh_map(conn: Connection) -> None:
    """Rebuild the map's saved scores layer (latest_hydro_scores, migration 0017) from
    the live view. CONCURRENTLY: the map keeps serving the old rows while it runs."""
    conn.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY latest_hydro_scores"))
