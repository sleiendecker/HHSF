"""The parcel shortlist, its research status and what the map app's API role can reach
(PART 23, migration 0020), in a throwaway PostGIS."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from hhsf.analysis import scoring, scoring_run, shortlist
from hhsf.core.db import refresh_map
from tests.integration.test_farmland_db import site  # noqa: F401  (fixture)
from tests.integration.test_hydro_db import AREA, engine  # noqa: F401  (fixture)
from tests.integration.test_scoring_db import hydro_run  # noqa: F401  (fixture)
from tests.unit.test_shortlist import stretch

pytestmark = pytest.mark.integration

# With the overall score, so the parts are explained.
PROFILE = scoring.load_profile("hydro_v6")


@pytest.fixture
def api(engine):  # noqa: F811
    """A connection working as the API's role, as PostgREST does; rolled back after."""
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE parcel_shortlist, parcel_research"))
    with engine.connect() as conn:
        yield conn
        conn.rollback()
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE parcel_shortlist, parcel_research"))


def as_api(conn) -> None:
    conn.execute(text("SET LOCAL ROLE web_anon"))


def save(conn, stretches) -> None:
    conn.execute(text("RESET ROLE"))
    shortlist.save(conn, shortlist.build(stretches))
    as_api(conn)


def set_research(conn, county, parcel, status, note=None):
    return conn.execute(
        text("SELECT * FROM api.set_research(:c, :p, :s, :n)"),
        {"c": county, "p": parcel, "s": status, "n": note},
    ).one()


def listed(conn):
    return conn.execute(
        text("SELECT rank, county_geoid, parcel_id, status, note FROM api.parcels")
    ).all()


def test_list_is_ranked_and_every_parcel_starts_new(api):
    save(
        api,
        [
            stretch("47153", "001", overall=50),
            stretch("47115", "001", overall=70),
            stretch("47115", "001", overall=20),
        ],
    )
    assert listed(api) == [(1, "47115", "001", "new", None), (2, "47153", "001", "new", None)]


def test_status_and_note_survive_a_rebuild(api):
    save(api, [stretch("47153", "001", overall=50), stretch("47115", "001", overall=70)])
    saved = set_research(api, "47153", "001", "interested", "  call the owner  ")
    assert (saved.status, saved.note) == ("interested", "call the owner")
    set_research(api, "47153", "001", "visited", "walked it")
    # A re-run: new scores, new ranks, one parcel gone. The status stays with the parcel.
    save(api, [stretch("47153", "001", overall=90)])
    assert listed(api) == [(1, "47153", "001", "visited", "walked it")]
    # And comes back with a parcel that drops out and returns.
    save(api, [stretch("47153", "001", overall=90), stretch("47115", "001", overall=70)])
    set_research(api, "47115", "001", "ruled_out", "")
    assert listed(api)[1] == (2, "47115", "001", "ruled_out", None)


def test_unknown_status_is_refused(api):
    as_api(api)
    with pytest.raises(DBAPIError, match="parcel_research_status_check"):
        set_research(api, "47153", "001", "maybe")


def test_api_role_reaches_nothing_but_the_api(api):
    as_api(api)
    for sql in (
        "SELECT * FROM parcel_owners",
        "SELECT * FROM parcels",
        "SELECT * FROM parcel_shortlist",
        "INSERT INTO parcel_research (county_geoid, parcel_id) VALUES ('1', '1')",
    ):
        api.execute(text("SAVEPOINT s"))
        with pytest.raises(DBAPIError, match="permission denied"):
            api.execute(text(sql))
        api.execute(text("ROLLBACK TO SAVEPOINT s"))


def test_api_views_carry_no_owner_fields(api):
    columns = api.execute(
        text(
            "SELECT table_name, column_name FROM information_schema.columns"
            " WHERE table_schema = 'api'"
        )
    ).all()
    assert {t for t, _ in columns} == {"parcels", "parcel_detail", "score_runs"}
    for table, column in columns:
        assert not any(word in column for word in ("owner", "address", "tpad")), (table, column)


def test_map_refresh_builds_the_shortlist(engine, site):  # noqa: F811
    scoring_run.score_hydro(engine, AREA, PROFILE)
    with engine.begin() as conn:
        refresh_map(conn)
        parcels = conn.execute(
            text(
                "SELECT count(DISTINCT turbine_parcel_gislink) FROM latest_hydro_scores"
                " WHERE rank IS NOT NULL"
            )
        ).scalar_one()
        rows = conn.execute(
            text("SELECT rank, parcel_id, stretches, checks, parts FROM parcel_shortlist")
        ).all()
        detail = conn.execute(text("SELECT parcel_geom, stretch_geom FROM api.parcel_detail")).all()
        runs = conn.execute(text("SELECT profile, weights FROM api.score_runs")).all()
    assert parcels >= 1 and len(rows) == parcels
    assert rows[0].rank == 1 and rows[0].stretches == 1
    assert rows[0].checks[0]["topic"] == "Dry-season flow"
    assert [p["part"] for p in rows[0].parts["parts"]][0] == "hydro"
    assert detail[0].parcel_geom["type"] in ("Polygon", "MultiPolygon")
    assert detail[0].stretch_geom["type"] == "MultiLineString"
    assert runs == [("hydro_v6", PROFILE.overall.weights)]
