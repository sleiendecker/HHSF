"""Reading the bulk NHDPlus HR geodatabase, using a tiny synthetic one built per test."""

import zipfile

import numpy as np
import pytest
import shapely
from pyogrio.raw import write
from shapely.geometry import MultiLineString, box

from hhsf.core.config import AOI
from hhsf.ingest import nhdplus_bulk

# Synthetic reaches near Dunlap, TN. Not real NHDPlus IDs.
PERENNIAL = 25000300000001  # perennial, in the network, inside the AOI
NOT_IN_NETWORK = 25000300000002  # perennial but no VAA row (not routed)
INTERMITTENT = 25000300000003
FAR_AWAY = 25000300000004  # perennial, in the network, well outside the AOI
NO_FLOW = 25000300000005  # perennial, in the network, no EROM row


def _line(x: float, y: float) -> bytes:
    """A ~1.4 km MultiLineString Z in NAD83 degrees, like the real files."""
    return shapely.to_wkb(
        MultiLineString([[(x, y, 500.0), (x + 0.01, y - 0.01, 480.0)]]), output_dimension=3
    )


def _write_table(path, layer, fields, rows, geometry=None, **kwargs):
    columns = [np.array([row[i] for row in rows]) for i in range(len(fields))]
    # Real files store FCode and stream order as 32-bit integers.
    columns = [c.astype(np.int32) if c.dtype == np.int64 else c for c in columns]
    write(
        path,
        geometry=np.array(geometry, dtype=object) if geometry else None,
        field_data=columns,
        fields=fields,
        layer=layer,
        driver="OpenFileGDB",
        append=layer != "NHDFlowline",
        **kwargs,
    )


@pytest.fixture
def gdb_zip(tmp_path):
    gdb = tmp_path / "NHDPLUS_H_9999_HU4_GDB.gdb"
    flowlines = [
        (float(PERENNIAL), "test-1", "Test Creek", 46006, -85.40, 35.40),
        (float(NOT_IN_NETWORK), "test-2", "", 46006, -85.39, 35.39),
        (float(INTERMITTENT), "test-3", "", 46003, -85.38, 35.38),
        (float(FAR_AWAY), "test-4", "", 46006, -85.0, 35.40),
        (float(NO_FLOW), "test-5", "", 46006, -85.41, 35.41),
    ]
    _write_table(
        str(gdb),
        "NHDFlowline",
        ["NHDPlusID", "Permanent_Identifier", "GNIS_Name", "FCode"],
        [row[:4] for row in flowlines],
        geometry=[_line(x, y) for *_, x, y in flowlines],
        geometry_type="MultiLineString Z",
        crs="EPSG:4269",
    )
    # Lowercase names on purpose: newer releases use them.
    _write_table(
        str(gdb),
        "NHDPlusFlowlineVAA",
        ["nhdplusid", "streamorde", "totdasqkm", "maxelevsmo", "minelevsmo", "slope"],
        [
            (float(PERENNIAL), 2, 8.98, 55271.0, 44670.0, 0.0649),
            (float(INTERMITTENT), 1, 0.5, 56000.0, 55271.0, 0.01),
            (float(FAR_AWAY), 1, 1.0, 50000.0, 49000.0, 0.01),
            (float(NO_FLOW), 1, 2.88, 54236.0, 54085.0, -9998.0),
        ],
    )
    _write_table(
        str(gdb),
        "NHDPlusEROMMA",
        ["NHDPlusID", "QEMA"],
        [(float(PERENNIAL), 9.13), (float(INTERMITTENT), 0.4), (float(FAR_AWAY), 1.0)],
    )
    zip_path = tmp_path / "NHDPLUS_H_9999_HU4_GDB.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for f in gdb.iterdir():
            zf.write(f, f"{gdb.name}/{f.name}")
    return zip_path


def _read_all(zip_path):
    records = nhdplus_bulk.read_flowlines(f"/vsizip/{zip_path}", (-86, 35, -84.9, 36))
    return {r.nhdplusid: r for r in records}


def test_reads_perennial_network_flowlines_only(gdb_zip):
    assert set(_read_all(gdb_zip)) == {PERENNIAL, FAR_AWAY, NO_FLOW}


def test_joins_attributes_and_converts_units(gdb_zip):
    record = _read_all(gdb_zip)[PERENNIAL]
    assert record.stream_name == "Test Creek"
    assert record.permanent_identifier == "test-1"
    assert record.stream_order == 2
    assert record.drainage_area_sqkm == pytest.approx(8.98)
    assert record.max_elev_m == pytest.approx(552.71)  # cm -> m
    assert record.min_elev_m == pytest.approx(446.70)
    assert record.slope == pytest.approx(0.0649)
    assert record.mean_flow_cfs == pytest.approx(9.13)


def test_geometry_is_2d_storage_crs_measured_in_meters(gdb_zip):
    record = _read_all(gdb_zip)[PERENNIAL]
    assert record.geom.geom_type == "MultiLineString"
    assert not record.geom.has_z
    (x, y), _ = record.geom.geoms[0].coords
    assert (x, y) == pytest.approx((-85.40, 35.40), abs=1e-4)
    assert 1000 < record.length_m < 2000


def test_missing_values_become_none(gdb_zip):
    record = _read_all(gdb_zip)[NO_FLOW]
    assert record.slope is None  # -9998 sentinel
    assert record.mean_flow_cfs is None  # no EROM row
    assert record.stream_name is None  # empty string


def test_fetch_clips_to_aoi_buffer(gdb_zip, monkeypatch):
    monkeypatch.setattr(nhdplus_bulk, "download", lambda huc4, echo: gdb_zip)
    aoi = AOI(key="test", name="Test", county_geoid="00000", buffer_m=500, huc4=["9999"])
    county = box(-85.45, 35.35, -85.35, 35.45)
    batches = list(nhdplus_bulk.fetch_perennial(aoi, county, echo=lambda _: None))
    assert {r.nhdplusid for r in batches[0]} == {PERENNIAL, NO_FLOW}


def test_gdb_url_picks_the_file_for_the_region(monkeypatch):
    listing = """<?xml version="1.0" encoding="UTF-8"?>
    <ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
      <Contents><Key>StagedProducts/Hydrography/NHDPlusHR/VPU/Current/GDB/NHDPLUS_H_0602_HU4_20220418_GDB.jpg</Key></Contents>
      <Contents><Key>StagedProducts/Hydrography/NHDPlusHR/VPU/Current/GDB/NHDPLUS_H_0602_HU4_20220418_GDB.zip</Key></Contents>
    </ListBucketResult>"""

    class Response:
        text = listing

        def raise_for_status(self):
            pass

    monkeypatch.setattr(nhdplus_bulk.httpx, "get", lambda *args, **kwargs: Response())
    assert nhdplus_bulk.gdb_url("0602") == (
        "https://prd-tnm.s3.amazonaws.com/StagedProducts/Hydrography/NHDPlusHR/VPU/Current/GDB/"
        "NHDPLUS_H_0602_HU4_20220418_GDB.zip"
    )
