"""Typed loaders for versioned config (config/) and environment settings."""

from pathlib import Path

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = REPO_ROOT / "config"
DATA_DIR = REPO_ROOT / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    database_url: str = "postgresql+psycopg://hhsf:hhsf@localhost:5433/hhsf"


class ReferenceGauge(BaseModel):
    site_no: str  # USGS site number
    huc8: str  # the gauge's 8-digit basin; only reaches inside it use the gauge


class AOI(BaseModel):
    key: str
    name: str
    county_geoid: str
    buffer_m: float = 0
    # NHDPlus HR regions covering the county. Looked up from WBD when not listed.
    huc4: list[str] = []
    reference_gauge: ReferenceGauge | None = None


class DataSource(BaseModel):
    key: str
    description: str
    url: str
    license: str
    version: str


def settings() -> Settings:
    return Settings()


def load_aoi(key: str) -> AOI:
    path = CONFIG_DIR / "aoi" / f"{key}.yaml"
    if not path.exists():
        available = sorted(p.stem for p in (CONFIG_DIR / "aoi").glob("*.yaml"))
        raise FileNotFoundError(f"No AOI '{key}'. Available: {', '.join(available)}")
    return AOI.model_validate(yaml.safe_load(path.read_text()))


def _data_sources() -> dict:
    return yaml.safe_load((CONFIG_DIR / "data_sources.yaml").read_text())


def data_source_settings(key: str) -> dict:
    """Everything config/data_sources.yaml says about one source, as a plain dict."""
    return _data_sources()[key]


def load_data_source(key: str) -> DataSource:
    return DataSource.model_validate({"key": key, **_data_sources()[key]})


def hydrography_source() -> str:
    """Which NHDPlus HR source to ingest from by default: "bulk" or "service"."""
    return _data_sources().get("hydrography_source", "bulk")
