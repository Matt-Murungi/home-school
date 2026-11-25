"""Repository and Storage additions for retention (C03): count, new fake query methods, adapters."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from appwrite.query import Query

from hs_core.repository import AppwriteRepository, InMemoryRepository, count_rows
from hs_core.retention.files import AppwriteRetentionFileStore
from retention_helpers import RecRepo


def seeded():
    r = InMemoryRepository()
    for i in range(250):
        r.create(
            "t", f"r{i:03d}", {"s": "anon-x" if i % 2 else "real", "n": None if i % 5 == 0 else i}
        )
    return r


def test_fake_supports_the_new_query_methods_and_null_never_matches_a_comparison():
    r = seeded()
    assert len(r.list("t", [Query.not_starts_with("s", "anon-")])) == 125
    assert len(r.list("t", [Query.starts_with("s", "anon-")])) == 125
    assert len(r.list("t", [Query.is_null("n")])) == 50
    assert len(r.list("t", [Query.is_not_null("n")])) == 200
    assert len(r.list("t", [Query.select(["$id"]), Query.limit(5)])) == 5


def test_count_ignores_limit_order_and_cursor():
    r = seeded()
    q = [
        Query.is_not_null("n"),
        Query.limit(10),
        Query.order_asc("$id"),
        Query.cursor_after("r010"),
    ]
    assert r.count("t", q) == 200 and count_rows(r, "t", q) == 200


def test_count_rows_falls_back_to_cursor_paging_without_a_native_count():
    class NoCount:
        def __init__(self, inner):
            self.inner, self.lists = inner, []

        def list(self, table, queries=None):
            self.lists.append(queries)
            return self.inner.list(table, queries)

    nc = NoCount(seeded())
    assert count_rows(nc, "t", [Query.is_not_null("n")]) == 200
    assert len(nc.lists) == 3  # 100 + 100 + 0..: pages of 100 by cursor, no offsets
    assert not any('"offset"' in q for qs in nc.lists for q in qs)
    assert count_rows(NoCount(seeded()), "t", cap=100) == 100


def test_appwrite_repository_count_uses_total_with_limit_one():
    tables = MagicMock()
    tables.list_rows.return_value = {"rows": [], "total": 4321}
    repo = AppwriteRepository(MagicMock(), "main", tables=tables)
    assert (
        repo.count("child", [Query.equal("a", ["b"]), Query.limit(50), Query.order_asc("$id")])
        == 4321
    )
    queries = tables.list_rows.call_args.kwargs["queries"]
    assert queries[-1] == Query.limit(1) and Query.limit(50) not in queries


def test_appwrite_file_store_delete_and_list_with_mocks():
    storage = MagicMock()
    store = AppwriteRetentionFileStore(MagicMock(), storage=storage)
    assert store.delete("teacher-docs", "f1") is True
    storage.delete_file.assert_called_once_with(bucket_id="teacher-docs", file_id="f1")
    from appwrite.exception import AppwriteException

    storage.delete_file.side_effect = AppwriteException("gone", 404)
    assert store.delete("teacher-docs", "f1") is False  # already deleted: idempotent
    storage.delete_file.side_effect = AppwriteException("boom", 500)
    import pytest

    from hs_core.repository import RepositoryUnavailable

    with pytest.raises(RepositoryUnavailable):
        store.delete("teacher-docs", "f1")
    storage.list_files.return_value = SimpleNamespace(
        files=[
            SimpleNamespace(
                to_dict=lambda: {"$id": "a", "$createdAt": "2026-01-01T10:00:00.000+00:00"}
            )
        ]
    )
    files = store.list_files("school-reports", after="0", limit=500)
    assert [f.id for f in files] == ["a"] and files[0].created_at.year == 2026
    kw = storage.list_files.call_args.kwargs
    assert kw["bucket_id"] == "school-reports"
    assert Query.limit(100) in kw["queries"] and Query.cursor_after("0") in kw["queries"]


def test_recrepo_helper_is_a_repository():
    assert RecRepo().count("x") == 0
