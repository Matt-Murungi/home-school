"""File permissions for the three buckets (SEC-5, ADR 0012) and the Appwrite adapter (mocked)."""

import pytest

from hs_core.permissions.files import AppwriteFileStore
from hs_core.permissions.rules import FILE_BUCKETS, desired_file_permissions
from hs_core.permissions.service import PermissionEvent, Scope, row_created
from hs_core.repository import NotFound, RepositoryUnavailable
from jobs_helpers import T0
from perm_world import World, iso


@pytest.fixture
def w():
    return World().seed()


def fp(w, bucket, table, rid):
    return set(desired_file_permissions(bucket, w.repo.get(table, rid), w.svc.context()))


def test_the_three_buckets_and_their_owner_tables():
    assert {b: v[0] for b, v in FILE_BUCKETS.items()} == {
        "teacher-docs": "teacher_document",
        "school-reports": "school_upload",
        "question-media": "question",
    }
    with pytest.raises(KeyError):
        desired_file_permissions("avatars", {}, None)


def test_teacher_docs_owner_ops_and_the_case_lead_only(w):
    assert fp(w, "teacher-docs", "teacher_document", "td_t1") == {
        'read("user:uT1")',
        'read("label:opsadmin")',
        'read("user:uL1")',
    }
    assert fp(w, "teacher-docs", "teacher_document", "td_t4") == {
        'read("user:uT4")',
        'read("label:opsadmin")',
    }


def test_school_reports_follow_the_family_and_never_reach_teachers(w):
    p = fp(w, "school-reports", "school_upload", "suA1")
    assert p == {'read("user:uPA")', 'read("user:uGA")', 'read("label:opsadmin")'}


def test_question_media_draft_curators_only_approved_signed_in_users(w):
    assert fp(w, "question-media", "question", "qDraft") == {
        'read("label:curator")',
        'read("label:opsadmin")',
    }
    assert fp(w, "question-media", "question", "qApp") == {
        'read("label:curator")',
        'read("label:opsadmin")',
        'read("users")',
    }


def test_no_file_ever_gets_any_or_write_permissions(w):
    for b in FILE_BUCKETS:
        table = FILE_BUCKETS[b][0]
        for r in w.repo.list(table):
            for p in desired_file_permissions(b, r, w.svc.context()):
                assert p.startswith("read(") and "any" not in p


def test_file_permissions_are_written_on_upload_event_and_withdrawn_with_the_row(w):
    w.files.put("teacher-docs", "f_new", [])
    w.add("teacher_document", "tdNew", teacher_id="T4", file_id="f_new", type="cv")
    w.svc.apply(row_created("teacher_document:tdNew:create", "teacher_document", "tdNew"))
    assert w.files.get_permissions("teacher-docs", "f_new") == sorted(
        ['read("user:uT4")', 'read("label:opsadmin")']
    )
    w.patch("teacher_document", "tdNew", deleted_at=iso(T0))
    w.svc.apply(row_created("teacher_document:tdNew:delete", "teacher_document", "tdNew"))
    assert w.files.get_permissions("teacher-docs", "f_new") == []


def test_a_failed_file_revoke_is_reported_like_a_row_revoke(w):
    w.patch("safeguarding_case", "cs1", status="closed")

    def boom(bucket, fid, perms):
        raise RepositoryUnavailable("storage down")

    w.files.update_permissions = boom
    from hs_core.permissions.service import PartialApplication

    with pytest.raises(PartialApplication) as exc:
        w.svc.apply(PermissionEvent("manual", "f1", (Scope("case", "cs1"),)))
    assert exc.value.report.failed_revokes >= 1 and any(
        f[0].startswith("file:") for f in exc.value.report.failed
    )


def test_a_file_that_does_not_exist_yet_is_skipped_not_created(w):
    w.add("teacher_document", "tdGhost", teacher_id="T4", file_id="f_ghost", type="cv")
    r = w.svc.apply(row_created("e", "teacher_document", "tdGhost"))
    assert r.ok and not w.files.exists("teacher-docs", "f_ghost")


# ------------------------------------------------------------------ the Appwrite adapter (mocked)
class FakeStorage:
    def __init__(self):
        self.files = {("b", "f"): {"$permissions": ['read("user:x")']}}
        self.calls = []

    def get_file(self, bucket_id, file_id):
        from appwrite.exception import AppwriteException

        if (bucket_id, file_id) not in self.files:
            raise AppwriteException("not found", 404)
        return self.files[(bucket_id, file_id)]

    def update_file(self, bucket_id, file_id, name=None, permissions=None):
        self.calls.append((bucket_id, file_id, permissions))
        self.files[(bucket_id, file_id)]["$permissions"] = permissions
        return self.files[(bucket_id, file_id)]


def test_appwrite_file_store_maps_sdk_calls_and_errors():
    s = FakeStorage()
    store = AppwriteFileStore(None, storage=s)
    assert store.exists("b", "f") and not store.exists("b", "nope")
    assert store.get_permissions("b", "f") == ['read("user:x")']
    store.update_permissions("b", "f", ['read("label:opsadmin")'])
    assert s.calls == [("b", "f", ['read("label:opsadmin")'])]
    with pytest.raises(NotFound):
        store.get_permissions("b", "nope")

    class Down(FakeStorage):
        def get_file(self, bucket_id, file_id):
            from appwrite.exception import AppwriteException

            raise AppwriteException("boom", 503)

    with pytest.raises(RepositoryUnavailable):
        AppwriteFileStore(None, storage=Down()).get_permissions("b", "f")
