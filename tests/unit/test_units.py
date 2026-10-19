import pytest

from hhsf.core import units


def test_conversions():
    assert units.m_to_ft(100) == pytest.approx(328.084)
    assert units.cm_to_m(52344) == pytest.approx(523.44)
    assert units.sqkm_to_acres(1) == pytest.approx(247.105)
    assert units.cfs_to_gpm(1) == pytest.approx(448.831)
