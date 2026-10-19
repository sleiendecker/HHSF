import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_features() -> list[dict]:
    return json.loads((FIXTURES / "flowlines_sample.geojson").read_text())["features"]
