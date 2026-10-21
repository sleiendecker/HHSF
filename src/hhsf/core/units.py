"""Explicit unit conversions. Source data mixes units, so convert at the edges."""

FT_PER_M = 3.28084
ACRES_PER_SQKM = 247.105
GPM_PER_CFS = 448.831
M3S_PER_CFS = 0.0283168
SQKM_PER_SQMI = 2.58999
KM_PER_MI = 1.609344


def m_to_ft(m: float) -> float:
    return m * FT_PER_M


def cm_to_m(cm: float) -> float:
    # NHDPlus HR stores smoothed elevations in centimeters.
    return cm / 100.0


def sqkm_to_acres(sqkm: float) -> float:
    return sqkm * ACRES_PER_SQKM


def cfs_to_gpm(cfs: float) -> float:
    return cfs * GPM_PER_CFS


def cfs_to_m3s(cfs: float) -> float:
    return cfs * M3S_PER_CFS


def sqkm_to_sqmi(sqkm: float) -> float:
    # StreamStats regression limits are in square miles.
    return sqkm / SQKM_PER_SQMI


def sqmi_to_sqkm(sqmi: float) -> float:
    return sqmi * SQKM_PER_SQMI


def km_to_mi(km: float) -> float:
    return km / KM_PER_MI
