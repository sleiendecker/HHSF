"""Terrain ingest: which 3DEP tiles, how much is read, and saving the clipped DEM."""

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from hhsf.ingest import terrain

SEQUATCHIE_STREAMS = (-85.607, 35.142, -85.219, 35.579)


def test_tiles_are_named_by_their_north_west_corner():
    assert terrain.tile_names(SEQUATCHIE_STREAMS) == ["n36w086"]


def test_bounds_crossing_tile_edges_need_every_tile():
    assert terrain.tile_names((-86.2, 35.9, -85.8, 36.1)) == [
        "n36w087",
        "n36w086",
        "n37w087",
        "n37w086",
    ]


def test_eastern_or_southern_hemisphere_is_refused():
    with pytest.raises(ValueError):
        terrain.tile_names((10.0, 45.0, 11.0, 46.0))


def test_tile_url_follows_the_staged_products_layout():
    source = terrain.load_data_source(terrain.SOURCE_KEY)
    assert terrain.tile_url(source, "n36w086").endswith("/current/n36w086/USGS_13_n36w086.tif")


def test_snap_grows_bounds_to_cell_edges():
    t = from_origin(-86.0, 36.0, 0.01, 0.01)
    snapped = terrain.snap((-85.605, 35.195, -85.395, 35.405), t)
    assert snapped == pytest.approx((-85.61, 35.19, -85.39, 35.41))


def test_share_of_tile_estimates_how_much_is_read():
    assert terrain.share_of_tile((-85.6, 35.1, -85.2, 35.6), "n36w086") == pytest.approx(0.2)
    assert terrain.share_of_tile((-85.6, 35.1, -85.2, 35.6), "n37w086") == 0


def write_tile(path, west=-86.0, north=36.0, cells=100):
    """A tiny 1x1 degree 'tile': elevation falls 1 m per cell going east."""
    z = np.tile(np.arange(500, 500 - cells, -1, dtype="float32"), (cells, 1))
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=cells,
        height=cells,
        count=1,
        dtype="float32",
        crs="EPSG:4269",
        transform=from_origin(west, north, 1 / cells, 1 / cells),
        nodata=-999999,
    ) as f:
        f.write(z, 1)


@pytest.fixture
def local_tile(tmp_path, monkeypatch):
    tile = tmp_path / "USGS_13_n36w086.tif"
    write_tile(tile)
    monkeypatch.setattr(terrain, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(terrain, "tile_url", lambda source, name: str(tile))
    calls = []

    def info(url):
        calls.append(url)
        return 4_000_000, "2026-04-29"

    monkeypatch.setattr(terrain, "tile_info", info)
    return calls


def test_fetch_saves_only_the_part_under_the_streams(local_tile):
    messages = []
    path, source = terrain.fetch_dem("test_aoi", (-85.6, 35.2, -85.4, 35.4), messages.append)
    assert path == terrain.dem_path("test_aoi") and path.exists()
    with rasterio.open(path) as dem:
        b = dem.bounds
        assert dem.crs.to_epsg() == 4269
        assert dem.tags()["source_version"] == source.version
        # Cells are copied from the tile, not resampled: 1 m less per 0.01 degree east.
        z = dem.read(1)
        assert z[0, 0] == 500 - 39 and z[0, -1] == 500 - 60
    # Padded by PAD_DEG (0.005), then out to whole cells (0.01).
    assert (b.left, b.bottom, b.right, b.top) == pytest.approx((-85.61, 35.19, -85.39, 35.41))
    assert "n36w086 2026-04-29" in source.version
    assert "Reading about" in messages[0]
    assert not list(path.parent.glob("*.part.tif"))


def test_fetch_reuses_a_saved_dem_that_covers_the_streams(local_tile):
    terrain.fetch_dem("test_aoi", (-85.6, 35.2, -85.4, 35.4), lambda _: None)
    messages = []
    _, source = terrain.fetch_dem("test_aoi", (-85.55, 35.25, -85.45, 35.35), messages.append)
    assert len(local_tile) == 1  # the tile wasn't looked up again
    assert messages[0].startswith("Using the saved DEM")
    assert "n36w086" in source.version


def test_fetch_reads_again_when_streams_reach_past_the_saved_dem(local_tile):
    terrain.fetch_dem("test_aoi", (-85.6, 35.2, -85.4, 35.4), lambda _: None)
    terrain.fetch_dem("test_aoi", (-85.6, 35.2, -85.2, 35.4), lambda _: None)
    assert len(local_tile) == 2
