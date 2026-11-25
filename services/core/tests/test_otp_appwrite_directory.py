"""A01 (#43): AppwriteUserDirectory against a mocked `Users` service (never a live Appwrite)."""

import pytest
from appwrite.exception import AppwriteException

from hs_core.auth_otp.accounts import (
    AppwriteUserDirectory,
    DirectoryConflict,
    DirectoryUnavailable,
)

PHONE = "+256772123456"


class FakeUsers:
    def __init__(self):
        self.calls = []
        self.rows = [
            {
                "$id": "u1",
                "phone": PHONE,
                "labels": ["parent"],
                "status": True,
                "accessedAt": "2026-11-20T10:00:00.000+00:00",
            },
            {"$id": "u2", "phone": "+256772123450", "labels": [], "status": True},
        ]
        self.raise_on = {}

    def _maybe(self, name):
        self.calls.append(name)
        if name in self.raise_on:
            raise self.raise_on[name]

    def list(self, **kw):
        self._maybe("list")
        self.kw = kw
        return {"users": self.rows, "total": len(self.rows)}

    def create(self, **kw):
        self._maybe("create")
        return {"$id": kw["user_id"], "phone": kw["phone"], "labels": [], "status": True}

    def update_phone_verification(self, **kw):
        self._maybe("update_phone_verification")

    def get(self, user_id):
        self._maybe("get")
        return next(r for r in self.rows if r["$id"] == user_id)

    def update_labels(self, user_id, labels):
        self._maybe("update_labels")
        row = self.get(user_id)
        row["labels"] = labels
        return row

    def create_token(self, user_id, expire=None, **kw):
        self._maybe("create_token")
        self.expire = expire
        return {"secret": "S" * 64, "expire": "2026-11-25T09:02:00.000+00:00", "userId": user_id}


def d(users=None):
    u = users or FakeUsers()
    return AppwriteUserDirectory(u), u


def test_find_by_phone_matches_exactly_not_by_search_hit():
    dd, u = d()
    got = dd.find_by_phone(PHONE)
    assert got.id == "u1" and got.labels == ["parent"] and got.active and got.accessed_at
    assert dd.find_by_phone("+256772123451") is None
    assert u.kw["search"] == "+256772123451"


def test_blocked_user_is_inactive():
    dd, u = d()
    u.rows[0]["status"] = False
    assert dd.find_by_phone(PHONE).active is False


def test_create_marks_phone_verified_and_tolerates_that_step_failing():
    dd, u = d()
    assert dd.create("new1", "+256701234567").id == "new1"
    assert "update_phone_verification" in u.calls
    dd2, u2 = d()
    u2.raise_on["update_phone_verification"] = AppwriteException("x", 500)
    assert dd2.create("new2", "+256701234567").id == "new2"


def test_create_conflict_maps_to_directory_conflict():
    dd, u = d()
    u.raise_on["create"] = AppwriteException("exists", 409)
    with pytest.raises(DirectoryConflict):
        dd.create("x", PHONE)


def test_add_label_keeps_existing_labels_and_skips_when_present():
    dd, u = d()
    out = dd.add_label("u1", "teacher")
    assert sorted(out.labels) == ["parent", "teacher"]
    u.calls.clear()
    dd.add_label("u1", "teacher")
    assert "update_labels" not in u.calls


def test_token_ttl_is_passed_and_parsed():
    dd, u = d()
    t = dd.create_token("u1", 120)
    assert u.expire == 120 and t.secret == "S" * 64 and t.expire.year == 2026


def test_token_reply_without_secret_is_unavailable():
    dd, u = d()
    u.create_token = lambda *a, **k: {"secret": "", "expire": ""}
    with pytest.raises(DirectoryUnavailable):
        dd.create_token("u1", 120)


@pytest.mark.parametrize("code", [401, 429, 500, 503])
def test_other_appwrite_errors_and_network_errors_are_unavailable(code):
    dd, u = d()
    u.raise_on["list"] = AppwriteException("boom", code)
    with pytest.raises(DirectoryUnavailable):
        dd.find_by_phone(PHONE)
    u.raise_on["list"] = ConnectionError("down")
    with pytest.raises(DirectoryUnavailable):
        dd.find_by_phone(PHONE)
