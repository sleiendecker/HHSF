"""Save the map's scores layer instead of computing it per tile.

`latest_hydro_scores` was a view: every map tile re-ran its parcel, flood, road,
buildable, farmland, land cover and score lookups for every stretch in the tile (a
zoom 7 tile took over 10 seconds). Now:

- `latest_hydro_scores_live` is that view, unchanged (just renamed);
- `latest_hydro_scores` is a materialized view of it: the same rows saved as a table,
  with an index on its geometry, so a tile is one indexed read. The tile server and
  everything else keep using this name.

It's rebuilt by `hhsf db refresh-map`, which every command that changes what it shows
runs at its end (ingest, analyze, drive time, score; see hhsf.core.db.refresh_map).
The rebuild takes about 10 seconds for five counties; the map keeps serving the old
rows meanwhile.

Later migrations that change the layer: drop the materialized view, replace the live
view, then run CREATE_SAVED below again.

Revision ID: 0017
Revises: 0016
"""

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

CREATE_SAVED = [
    "CREATE MATERIALIZED VIEW latest_hydro_scores AS SELECT * FROM latest_hydro_scores_live",
    # Unique, so it can be rebuilt without blocking the map (REFRESH ... CONCURRENTLY).
    "CREATE UNIQUE INDEX latest_hydro_scores_corridor_idx ON latest_hydro_scores (corridor_id)",
    "CREATE INDEX latest_hydro_scores_geom_idx ON latest_hydro_scores USING GIST (geom)",
]


def upgrade() -> None:
    op.execute("ALTER VIEW latest_hydro_scores RENAME TO latest_hydro_scores_live")
    for sql in CREATE_SAVED:
        op.execute(sql)


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW latest_hydro_scores")
    op.execute("ALTER VIEW latest_hydro_scores_live RENAME TO latest_hydro_scores")
