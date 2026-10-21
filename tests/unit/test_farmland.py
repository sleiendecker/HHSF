import pytest
from pydantic import ValidationError

from hhsf.analysis import farmland


def test_settings_come_from_the_repo_config():
    s = farmland.load_settings()
    assert s.max_capability_class == 3
    assert "Poorly drained" in s.wet_drainage and "Well drained" not in s.wet_drainage


@pytest.mark.parametrize("top", [0, 9])
def test_settings_reject_a_capability_class_outside_1_to_8(top):
    with pytest.raises(ValidationError):
        farmland.Settings(max_capability_class=top, wet_drainage=[])


def test_acres_are_international_acres():
    assert farmland.M2_PER_ACRE == pytest.approx(4046.86, abs=0.01)
