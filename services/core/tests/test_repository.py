from unittest.mock import MagicMock

import pytest

from hs_core.repository import (
    AppwriteRepository,
    InMemoryRepository,
    NotFound,
    RepositoryUnavailable,
)


def test_inmemory_crud():
    r = InMemoryRepository()
    row = r.create("child", "c1", {"name": "A"}, permissions=['read("user:p")'])
    assert row["$id"] == "c1" and row["name"] == "A"
    assert r.get("child", "c1")["name"] == "A"
    r.update("child", "c1", {"name": "B"})
    assert r.get("child", "c1")["name"] == "B"
    assert [x["$id"] for x in r.list("child")] == ["c1"]
    r.delete("child", "c1")
    with pytest.raises(NotFound):
        r.get("child", "c1")


def test_inmemory_duplicate_and_missing():
    r = InMemoryRepository()
    r.create("t", "1", {})
    with pytest.raises(ValueError):
        r.create("t", "1", {})
    with pytest.raises(NotFound):
        r.update("t", "zz", {})


def test_inmemory_returns_copies():
    r = InMemoryRepository()
    r.create("t", "1", {"a": 1})
    r.get("t", "1")["a"] = 99
    assert r.get("t", "1")["a"] == 1


def test_inmemory_unavailable():
    r = InMemoryRepository()
    r.available = False
    with pytest.raises(RepositoryUnavailable):
        r.ping()
    with pytest.raises(RepositoryUnavailable):
        r.get("t", "1")


# ---- real adapter, mocked SDK (no network) ----
def make():
    client = MagicMock()
    tables = MagicMock()
    return AppwriteRepository(client=client, database_id="main", tables=tables), client, tables


def test_adapter_ping_ok():
    repo, client, _ = make()
    client.call.return_value = {"status": "pass"}
    repo.ping()
    client.call.assert_called_once_with("get", "/health")


def test_adapter_ping_failure_maps_to_unavailable():
    repo, client, _ = make()
    client.call.side_effect = OSError("connection refused")
    with pytest.raises(RepositoryUnavailable):
        repo.ping()


def test_adapter_get_passes_database_and_maps_404():
    from appwrite.exception import AppwriteException

    repo, _, tables = make()
    tables.get_row.return_value = {"$id": "c1", "name": "A"}
    assert repo.get("child", "c1")["name"] == "A"
    tables.get_row.assert_called_once_with(database_id="main", table_id="child", row_id="c1")
    tables.get_row.side_effect = AppwriteException("nope", 404)
    with pytest.raises(NotFound):
        repo.get("child", "x")


def test_adapter_server_error_maps_to_unavailable():
    from appwrite.exception import AppwriteException

    repo, _, tables = make()
    tables.get_row.side_effect = AppwriteException("boom", 503)
    with pytest.raises(RepositoryUnavailable):
        repo.get("child", "x")


def test_adapter_create_update_delete_list():
    repo, _, tables = make()
    tables.create_row.return_value = {"$id": "1"}
    repo.create("t", "1", {"a": 1}, permissions=['read("user:p")'])
    tables.create_row.assert_called_once_with(
        database_id="main", table_id="t", row_id="1", data={"a": 1}, permissions=['read("user:p")']
    )
    tables.update_row.return_value = {"$id": "1"}
    repo.update("t", "1", {"a": 2}, permissions=None)
    tables.update_row.assert_called_once_with(
        database_id="main", table_id="t", row_id="1", data={"a": 2}, permissions=None
    )
    repo.delete("t", "1")
    tables.delete_row.assert_called_once_with(database_id="main", table_id="t", row_id="1")
    tables.list_rows.return_value = {"total": 1, "rows": [{"$id": "1"}]}
    assert repo.list("t", queries=["q"]) == [{"$id": "1"}]
    tables.list_rows.assert_called_once_with(database_id="main", table_id="t", queries=["q"])


def test_build_repository_from_settings_makes_no_network_call(monkeypatch):
    from hs_core.repository import build_repository
    from hs_core.settings import Settings

    s = Settings(
        _env_file=None,
        hs_env="staging",
        appwrite_endpoint="https://fra.cloud.appwrite.io/v1",
        appwrite_project_id="p",
        appwrite_api_key="k" * 40,
        appwrite_database_id="main",
    )
    assert isinstance(build_repository(s), AppwriteRepository)
    assert isinstance(build_repository(Settings(_env_file=None)), InMemoryRepository)
