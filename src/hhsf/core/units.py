"""Explicit unit conversions. Source data mixes units, so convert at the edges."""

FT_PER_M = 3.28084
ACRES_PER_SQKM = 247.105
GPM_PER_CFS = 448.831


def m_to_ft(m: float) -> float:
    return m * FT_PER_M


def cm_to_m(cm: float) -> float:
    # NHDPlus HR stores smoothed elevations in centimeters.
    return cm / 100.0


def sqkm_to_acres(sqkm: float) -> float:
    return sqkm * ACRES_PER_SQKM


def cfs_to_gpm(cfs: float) -> float:
    return cfs * GPM_PER_CFS
