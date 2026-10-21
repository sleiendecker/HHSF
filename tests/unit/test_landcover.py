import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

from hhsf.analysis import landcover
from hhsf.ingest import landcover as nlcd


def test_cells_are_30_m_in_acres():
    assert landcover.CELL_ACRES == pytest.approx(0.2224, abs=1e-4)


def test_summarize_counts_open_land_on_good_soil_and_mean_canopy():
    lc = np.array([[81, 82, 41], [71, 21, 81]], dtype="uint8")
    canopy = np.array([[0, 0, 90], [10, 255, 0]], dtype="uint8")
    parcel = np.array([[1, 1, 1], [1, 1, 0]], dtype=bool)  # the last cell is outside
    prime = np.array([[1, 0, 1], [0, 0, 1]], dtype=bool)
    crop = np.array([[1, 1, 1], [1, 0, 1]], dtype=bool)
    r = landcover.summarize("x", lc, canopy, parcel, prime, crop)
    a = landcover.CELL_ACRES
    assert r.parcel_acres == pytest.approx(5 * a)
    assert r.open_acres == pytest.approx(3 * a)  # 81, 82, 71
    assert (r.pasture_hay_acres, r.crop_acres) == (pytest.approx(a), pytest.approx(a))
    assert r.forest_acres == pytest.approx(a) and r.developed_acres == pytest.approx(a)
    assert r.open_prime_acres == pytest.approx(a)  # the pasture cell
    assert r.open_cropland_acres == pytest.approx(3 * a)
    assert r.canopy_pct == pytest.approx((0 + 0 + 90 + 10) / 4)  # 255 is no data


def test_summarize_with_no_canopy_data():
    one = np.ones((1, 1), dtype=bool)
    r = landcover.summarize("x", np.array([[41]]), np.array([[255]]), one, one, one)
    assert r.canopy_pct is None and r.open_acres == 0


def test_snap_widens_to_whole_nlcd_cells():
    xmin, ymin, xmax, ymax = nlcd.snap((907900, 1398050, 949170, 1439730), pad_m=0)
    assert (xmin, ymin, xmax, ymax) == (907875, 1398045, 949185, 1439745)
    assert all((v - 15) % 30 == 0 for v in (xmin, ymin, xmax, ymax))


def write(path, data, x0=900_015.0, y1=1_400_025.0):
    with rasterio.open(
        path, "w", driver="GTiff", width=data.shape[1], height=data.shape[0], count=1,
        dtype="uint8", crs="EPSG:5070", transform=from_origin(x0, y1, 30, 30),
    ) as f:  # fmt: skip
        f.write(data, 1)


def test_window_covers_the_parcel_with_a_cell_of_margin(tmp_path):
    write(tmp_path / "lc.tif", np.zeros((100, 100), dtype="uint8"))
    with rasterio.open(tmp_path / "lc.tif") as ds:
        win = landcover.window(ds, box(900_100, 1_398_000, 900_200, 1_399_000).bounds)
        left, bottom, right, top = ds.window_bounds(win)
    assert left <= 900_100 - 30 and right >= 900_200 + 30
    assert bottom <= 1_398_000 - 30 and top >= 1_399_000 + 30


def test_fetch_downloads_both_layers_once_and_reuses_a_covering_pair(tmp_path, monkeypatch):
    monkeypatch.setattr(nlcd, "DATA_DIR", tmp_path)
    calls = []

    def download(coverage, bounds, path):
        calls.append(coverage)
        path.parent.mkdir(parents=True, exist_ok=True)
        cols, rows = round((bounds[2] - bounds[0]) / 30), round((bounds[3] - bounds[1]) / 30)
        write(path, np.zeros((rows, cols), dtype="uint8"), bounds[0], bounds[3])

    monkeypatch.setattr(nlcd, "_download", download)
    monkeypatch.setattr(nlcd, "load_data_source", lambda key: key)
    paths, _ = nlcd.fetch_landcover("aoi", (901_000, 1_390_000, 905_000, 1_395_000), echo=print)
    assert set(paths) == {"land_cover", "tree_canopy"} and len(calls) == 2
    # A box inside the first one comes from the cache; a bigger one downloads again.
    nlcd.fetch_landcover("aoi", (902_000, 1_391_000, 903_000, 1_392_000), echo=print)
    assert len(calls) == 2
    nlcd.fetch_landcover("aoi", (890_000, 1_390_000, 905_000, 1_395_000), echo=print)
    assert len(calls) == 4
