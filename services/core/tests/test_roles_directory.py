"""A03: the Appwrite Users adapter methods the role service needs (mock-tested only)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from appwrite.exception import AppwriteException

from hs_core.auth_otp.accounts import AppwriteUserDirectory
from hs_core.roles.directory import DirectoryUnavailable
from hs_core.settings import Settings


def directory():
    users = MagicMock()
    return AppwriteUserDirectory(users), users


def test_get_maps_labels_and_status_and_404_is_none():
    d, users = directory()
    users.get.return_value = {
        "$id": "u1",
        "phone": "+256772123456",
        "labels": ["teacher"],
        "status": True,
    }
    u = d.get("u1")
    assert u.id == "u1" and u.labels == ["teacher"] and u.active
    users.get.side_effect = AppwriteException("nf", 404)
    assert d.get("u1") is None


@pytest.mark.parametrize("exc", [AppwriteException("x", 500), OSError("net")])
def test_get_outage_is_unavailable_not_none(exc):
    d, users = directory()
    users.get.side_effect = exc
    with pytest.raises(DirectoryUnavailable):
        d.get("u1")


def test_remove_label_keeps_the_other_labels_and_skips_the_write_when_absent():
    d, users = directory()
    users.get.return_value = {"$id": "u1", "labels": ["parent", "teacher"]}
    users.update_labels.return_value = {"$id": "u1", "labels": ["parent"]}
    d.remove_label("u1", "teacher")
    users.update_labels.assert_called_once_with(user_id="u1", labels=["parent"])
    users.update_labels.reset_mock()
    users.get.return_value = {"$id": "u1", "labels": ["parent"]}
    d.remove_label("u1", "teacher")
    users.update_labels.assert_not_called()


def test_labelled_queries_by_label_in_id_order_with_a_cursor():
    d, users = directory()
    users.list.return_value = {"users": [{"$id": "a", "labels": ["curator"]}]}
    out = d.labelled("curator", "prev", 25)
    assert [u.id for u in out] == ["a"]
    q = users.list.call_args.kwargs["queries"]
    joined = " ".join(q)
    assert "curator" in joined and "labels" in joined and "cursorAfter" in joined
    assert "25" in joined


def test_role_reconcile_settings_are_range_checked():
    assert Settings(_env_file=None, role_reconcile_grace_s=10).problems()
    assert Settings(_env_file=None, role_reconcile_grace_s=600).problems() == []


def test_directory_labels_feed_m05_with_clean_labels_only():
    from hs_core.auth_otp.accounts import FakeUserDirectory
    from hs_core.permissions.context import RepoFacts
    from hs_core.repository import InMemoryRepository
    from hs_core.roles.labels import DirectoryLabels

    users = FakeUserDirectory()
    uid = users.add("+256772000001", ["teacher", "OpsAdmin", "ops_admin"])
    labels = DirectoryLabels(users)
    assert labels.labels_of(uid) == {"teacher"}
    assert labels.labels_of("nobody") == frozenset()
    assert RepoFacts(InMemoryRepository(), labels).labels(uid) == {"teacher"}


def test_directory_outage_is_a_retriable_repository_error_for_m05():
    from hs_core.auth_otp.accounts import FakeUserDirectory
    from hs_core.repository import RepositoryUnavailable
    from hs_core.roles.labels import DirectoryLabels

    users = FakeUserDirectory()
    uid = users.add("+256772000001", ["teacher"])
    users.available = False
    with pytest.raises(RepositoryUnavailable):
        DirectoryLabels(users).labels_of(uid)


def test_end_sessions_deletes_every_session_of_the_user():
    d, users = directory()
    d.end_sessions("u1")
    users.delete_sessions.assert_called_once_with(user_id="u1")
    users.delete_sessions.side_effect = AppwriteException("x", 500)
    with pytest.raises(DirectoryUnavailable):
        d.end_sessions("u1")
