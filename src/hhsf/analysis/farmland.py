"""Farmland on each corridor's turbine parcel: FR-5 (agriculture).

For each turbine parcel this overlays the SSURGO soil map units (hhsf.ingest.soils) and
adds up, in acres on the ground (geodesic):

- prime farmland: map units NRCS lists as "All areas are prime farmland";
- prime farmland only if something is done first ("Prime farmland if drained", "... if
  protected from flooding ..."), kept apart: the condition usually isn't met;
- farmland of statewide importance (none is designated in the five counties);
- land capability class 1 to `max_capability_class` (non-irrigated, dominant condition);
- wet ground: drainage classes in `wet_drainage`;
- pasture: each map unit's pasture yield (AUM per acre a year) times its acres.

Every acre of the parcel counts, steep or in the floodplain: capability classes already
reflect slope and flooding, and bottomland is often the best farmland there is. Saved
in parcel_farmland (a view would overlay the soils on every map tile; see migration
0014). Settings are in config/farmland.yaml; see docs/ASSUMPTIONS.md, "Farmland".

Flagged only: nothing is filtered or re-ranked on it (D-02, D-12, D-14).
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from sqlalchemy import Connection, text

from hhsf.core import units
from hhsf.core.config import AOI, CONFIG_DIR

# SSURGO farmland classes (mapunit.farmlndcl). Conditional prime classes all start
# "Prime farmland if"; statewide ones "Farmland of statewide importance" (some with
# their own "if ..." conditions, counted as statewide).
PRIME = "All areas are prime farmland"
PRIME_IF = "Prime farmland if%"
STATEWIDE = "Farmland of statewide importance%"
M2_PER_ACRE = 1e6 / units.sqkm_to_acres(1.0)


class Settings(BaseModel):
    max_capability_class: int = Field(ge=1, le=8)
    wet_drainage: list[str]


def load_settings(path: Path = CONFIG_DIR / "farmland.yaml") -> Settings:
    return Settings.model_validate(yaml.safe_load(path.read_text()))


# One parcel's soils: each soil piece's area inside it, in square meters on the ground.
# Parcels with no soils loaded still get a row (all zero but parcel_acres).
_SQL = """
    INSERT INTO parcel_farmland (gislink, aoi_key, parcel_acres, soil_acres, prime_acres,
        prime_if_acres, statewide_acres, capable_acres, wet_acres, pasture_aum,
        max_capability_class, wet_drainage)
    SELECT p.gislink, :aoi_key,
           ST_Area(p.geom::geography) / :m2,
           coalesce(sum(s.m2), 0) / :m2,
           coalesce(sum(s.m2) FILTER (WHERE s.farmland_class = :prime), 0) / :m2,
           coalesce(sum(s.m2) FILTER (WHERE s.farmland_class LIKE :prime_if), 0) / :m2,
           coalesce(sum(s.m2) FILTER (WHERE s.farmland_class LIKE :statewide), 0) / :m2,
           coalesce(sum(s.m2) FILTER (WHERE s.capability_class <= :max_class), 0) / :m2,
           coalesce(sum(s.m2) FILTER (WHERE s.drainage = ANY(:wet)), 0) / :m2,
           coalesce(sum(s.m2 * s.pasture_aum), 0) / :m2,
           :max_class, :wet
    FROM parcels p
    LEFT JOIN LATERAL (
        SELECT x.farmland_class, x.capability_class, x.drainage, x.pasture_aum,
               ST_Area(ST_Intersection(x.geom, p.geom)::geography) AS m2
        FROM soils x
        WHERE x.geom && p.geom AND ST_Intersects(x.geom, p.geom)
    ) s ON true
    WHERE p.gislink IN (
        SELECT tp.gislink FROM hydro_corridors c
        JOIN corridor_turbine_parcels tp ON tp.corridor_id = c.corridor_id
        WHERE c.run_id = :run_id
    )
    GROUP BY p.gislink, p.geom
    ON CONFLICT (gislink) DO UPDATE SET
        aoi_key = EXCLUDED.aoi_key, parcel_acres = EXCLUDED.parcel_acres,
        soil_acres = EXCLUDED.soil_acres, prime_acres = EXCLUDED.prime_acres,
        prime_if_acres = EXCLUDED.prime_if_acres, statewide_acres = EXCLUDED.statewide_acres,
        capable_acres = EXCLUDED.capable_acres, wet_acres = EXCLUDED.wet_acres,
        pasture_aum = EXCLUDED.pasture_aum,
        max_capability_class = EXCLUDED.max_capability_class,
        wet_drainage = EXCLUDED.wet_drainage, computed_at = now()
    RETURNING gislink, prime_acres + statewide_acres AS farmland_acres, capable_acres
"""


def run_farmland(
    conn: Connection, aoi: AOI, hydro_run_id: int, settings: Settings
) -> list[tuple[str, float, float]]:
    """Compute and save farmland for a hydro run's turbine parcels. Returns each parcel's
    (gislink, prime + statewide acres, capability class acres)."""
    rows = conn.execute(
        text(_SQL),
        {
            "aoi_key": aoi.key,
            "run_id": hydro_run_id,
            "m2": M2_PER_ACRE,
            "prime": PRIME,
            "prime_if": PRIME_IF,
            "statewide": STATEWIDE,
            "max_class": settings.max_capability_class,
            "wet": settings.wet_drainage,
        },
    ).all()
    return [(r.gislink, r.farmland_acres, r.capable_acres) for r in rows]
