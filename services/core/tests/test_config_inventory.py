"""F09 (#35): the core service's real Settings agree with the secrets inventory and .env.example.

Imports hs_core.settings (not a parse of it) so a new field cannot slip past. Offline.
"""

import json
from pathlib import Path

import pytest
from pydantic import SecretStr

from hs_core.settings import REQUIRED_DEPLOYED, ConfigError, Settings, load_settings

ROOT = Path(__file__).resolve().parents[3]
INV = json.loads((ROOT / "docs/ops/secrets-inventory.json").read_text())
CORE = {e["name"]: e for e in INV["entries"] if e["consumer"] == "core-service"}
FIELDS = list(Settings.model_fields)


def example_values() -> dict[str, str]:
    out = {}
    for line in (Path(__file__).parents[1] / ".env.example").read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k] = v
    return out


def test_env_example_lists_every_setting_with_empty_value():
    ex = example_values()
    assert {f.upper() for f in FIELDS} == set(ex), "settings and .env.example differ"
    assert all(v.strip() == "" for v in ex.values())


@pytest.mark.parametrize("field", FIELDS)
def test_every_setting_is_in_inventory_with_matching_kind(field):
    entry = CORE.get(field.upper())
    assert entry is not None, f"{field.upper()} missing from docs/ops/secrets-inventory.json"
    masked = Settings.model_fields[field].annotation is SecretStr
    assert (entry["kind"] == "server_secret") == masked


@pytest.mark.parametrize("name", [n.upper() for n in REQUIRED_DEPLOYED])
@pytest.mark.parametrize("env_name", ["staging", "production"])
def test_missing_deployed_variable_fails_fast_naming_it(monkeypatch, name, env_name):
    good = {
        "HS_ENV": env_name,
        "APPWRITE_ENDPOINT": "https://fra.cloud.appwrite.io/v1",
        "APPWRITE_PROJECT_ID": "proj",
        "APPWRITE_API_KEY": "k" * 64,  # secret-scan: allow
        "APPWRITE_DATABASE_ID": "main",
    }
    for k in list(good):
        monkeypatch.delenv(k, raising=False)
    for k, v in good.items():
        if k != name:
            monkeypatch.setenv(k, v)
    with pytest.raises(ConfigError) as e:
        load_settings(env_file=None)
    msg = str(e.value)
    assert name in msg
    assert "k" * 32 not in msg


def test_required_deployed_variables_are_inventoried_for_every_environment():
    for n in REQUIRED_DEPLOYED:
        entry = CORE[n.upper()]
        assert set(entry["environments"]) == {"development", "staging", "production"}
