"""Owner names must never reach the public map tiles (docs/ASSUMPTIONS.md)."""

import yaml

from hhsf.core.config import REPO_ROOT

TABLES = yaml.safe_load((REPO_ROOT / "martin" / "martin.yaml").read_text())["postgres"]["tables"]
PERSONAL = ("owner", "address", "tpad")


def test_tile_server_publishes_only_listed_tables():
    config = yaml.safe_load((REPO_ROOT / "martin" / "martin.yaml").read_text())
    assert config["postgres"]["auto_publish"] is False


def test_owner_table_is_never_published():
    assert all(t["table"] != "parcel_owners" for t in TABLES.values())


def test_no_layer_carries_owner_fields():
    for name, table in TABLES.items():
        for prop in table.get("properties", {}):
            assert not any(word in prop for word in PERSONAL), f"{name}.{prop}"
