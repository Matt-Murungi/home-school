import pytest

from hs_core.settings import ConfigError, Settings, load_settings

GOOD = {
    "HS_ENV": "production",
    "APPWRITE_ENDPOINT": "https://fra.cloud.appwrite.io/v1",
    "APPWRITE_PROJECT_ID": "proj123",
    "APPWRITE_API_KEY": "k" * 64,
    "APPWRITE_DATABASE_ID": "main",
    "JOB_TICK_SECRET": "j" * 40,
}


def env(monkeypatch, values):
    for k in list(GOOD) + ["GIT_SHA", "APP_VERSION", "HS_DEBUG"]:
        monkeypatch.delenv(k, raising=False)
    for k, v in values.items():
        monkeypatch.setenv(k, v)


def test_dev_loads_with_no_variables(monkeypatch):
    env(monkeypatch, {})
    s = load_settings(env_file=None)
    assert s.hs_env == "dev"
    assert s.git_sha == "unknown"


def test_production_valid(monkeypatch):
    env(monkeypatch, GOOD)
    assert load_settings(env_file=None).hs_env == "production"


@pytest.mark.parametrize("env_name", ["staging", "production"])
@pytest.mark.parametrize(
    "missing",
    [
        "APPWRITE_ENDPOINT",
        "APPWRITE_PROJECT_ID",
        "APPWRITE_API_KEY",
        "APPWRITE_DATABASE_ID",
        "JOB_TICK_SECRET",
    ],
)
def test_missing_required_variable_fails_with_clear_error(monkeypatch, env_name, missing):
    values = {**GOOD, "HS_ENV": env_name}
    del values[missing]
    env(monkeypatch, values)
    with pytest.raises(ConfigError) as e:
        load_settings(env_file=None)
    assert missing in str(e.value)


def test_error_does_not_leak_secret_values(monkeypatch):
    env(monkeypatch, {**GOOD, "APPWRITE_API_KEY": "changeme", "APPWRITE_ENDPOINT": "http://x"})
    with pytest.raises(ConfigError) as e:
        load_settings(env_file=None)
    assert "changeme" not in str(e.value)


@pytest.mark.parametrize(
    "bad",
    [
        {"APPWRITE_API_KEY": "changeme"},
        {"APPWRITE_API_KEY": "short"},
        {"APPWRITE_ENDPOINT": "http://fra.cloud.appwrite.io/v1"},
        {"APPWRITE_ENDPOINT": "https://localhost/v1"},
        {"HS_DEBUG": "true"},
    ],
)
def test_production_refuses_insecure_values(monkeypatch, bad):
    env(monkeypatch, {**GOOD, **bad})
    with pytest.raises(ConfigError):
        load_settings(env_file=None)


def test_unknown_environment_rejected(monkeypatch):
    env(monkeypatch, {"HS_ENV": "prod-ish"})
    with pytest.raises(ConfigError):
        load_settings(env_file=None)


def test_blank_values_count_as_missing(monkeypatch):
    env(monkeypatch, {**GOOD, "APPWRITE_PROJECT_ID": ""})
    with pytest.raises(ConfigError):
        load_settings(env_file=None)


def test_api_key_is_secret_in_repr():
    s = Settings(_env_file=None, appwrite_api_key="supersecretvalue")
    assert "supersecretvalue" not in repr(s)


def test_env_example_has_only_empty_values():
    from pathlib import Path

    text = (Path(__file__).parents[1] / ".env.example").read_text()
    for line in text.splitlines():
        if line.strip() and not line.startswith("#"):
            assert line.split("=", 1)[1].strip() == "", line
