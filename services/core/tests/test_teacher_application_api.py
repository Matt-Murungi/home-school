"""V01 (#56): teacher application API: roles, draft/resume, validation, tampering, limits."""

import json
import logging
import threading

import pytest

from application_helpers import KEY, TEACHER_PHONE, Env, valid_content


@pytest.fixture
def env():
    return Env()


# -- roles and privacy -------------------------------------------------------------------------
def test_unauthenticated_is_401_everywhere(env):
    for method, path in [
        ("get", ""),
        ("put", "/draft"),
        ("post", "/submit"),
        ("get", "/reference"),
        ("post", "/reopen"),
    ]:
        r = env.client.request(method, "/v1/teacher/application" + path)
        assert r.status_code == 401, path


@pytest.mark.parametrize("token", ["parent", "admin"])
def test_non_teacher_roles_are_refused_and_nothing_is_written(env, token):
    assert env.get(token).status_code == 403
    assert env.save(token=token).status_code == 403
    assert env.submit(0, token=token).status_code == 403
    assert (
        env.client.get("/v1/teacher/application/reference", headers=env.h(token)).status_code == 403
    )
    assert env.rows("teacher_application") == [] and env.rows("teacher_profile") == []


def test_parent_labelled_user_cannot_submit_even_with_a_complete_draft(env):
    env.ready()
    assert env.submit(1, token="parent", key="p" * 20).status_code == 403
    assert env.get().json()["status"] == "draft"


def test_a_user_with_both_labels_can_apply(env):
    assert env.save(token="both").status_code == 200


def test_teacher_sees_only_their_own_application_and_ids_come_from_the_token(env):
    env.ready()
    other = env.get("t2").json()
    assert other["status"] == "none" and other["content"] == {}
    assert env.client.get(
        "/v1/teacher/application/teacher_one", headers=env.h("t2")
    ).status_code in (404, 405)
    r = env.get()
    assert r.headers["cache-control"] == "no-store"
    assert r.json()["content"]["personal"]["full_name"] == "Grace Nakato"


def test_row_is_readable_only_by_its_owner(env):
    env.ready()
    row = env.repo.get("teacher_application", "teacher_one")
    assert row["$permissions"] == ['read("user:teacher_one")']


# -- draft: save, resume, conflict ---------------------------------------------------------------
def test_new_teacher_has_an_empty_editable_application(env):
    j = env.get().json()
    assert (j["status"], j["revision"], j["can_edit"], j["content"]) == ("none", 0, True, {})


def test_progress_is_saved_per_step_and_resumes_at_the_last_step(env):
    r = env.save({"personal": {"full_name": "Grace Nakato"}}, rev=0, step=0)
    assert r.status_code == 200 and r.json()["revision"] == 1
    c = valid_content()
    r = env.save({"personal": c["personal"], "qualifications": c["qualifications"]}, rev=1, step=1)
    assert r.json()["revision"] == 2
    again = env.get().json()  # a fresh install signs in and resumes
    assert again["step"] == 1 and again["revision"] == 2
    assert again["content"]["qualifications"]["institution"] == "Makerere University"


def test_partial_drafts_are_accepted_and_report_issues_without_blocking(env):
    r = env.save({"personal": {"date_of_birth": "2015-01-01", "full_name": "G"}})
    assert r.status_code == 200
    assert "personal.date_of_birth:under_age" in r.json()["issues"]
    assert env.get().json()["content"]["personal"]["date_of_birth"] == "2015-01-01"


def test_stale_base_revision_returns_the_server_draft(env):
    env.save({"personal": {"full_name": "Server Copy"}}, rev=0)
    r = env.save({"personal": {"full_name": "Older Phone"}}, rev=0)
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "draft_conflict" and err["server"]["revision"] == 1
    assert err["server"]["content"]["personal"]["full_name"] == "Server Copy"
    assert env.save({"personal": {"full_name": "Merged"}}, rev=1).status_code == 200


def test_future_revision_is_a_conflict_too(env):
    assert env.save(rev=5).status_code == 409


def test_two_saves_from_the_same_revision_cannot_both_win(env):
    env.save({"personal": {"full_name": "Base"}}, rev=0)
    out = []

    def go(i):
        out.append(env.save({"personal": {"full_name": f"Phone {i}"}}, rev=1).status_code)

    ts = [threading.Thread(target=go, args=(i,)) for i in range(12)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(out) == [200] + [409] * 11
    assert env.get().json()["revision"] == 2


def test_two_first_saves_race_to_create_one_application(env):
    out = []
    ts = [
        threading.Thread(target=lambda: out.append(env.save(rev=0).status_code)) for _ in range(10)
    ]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert out.count(200) == 1 and out.count(409) == 9
    assert len(env.rows("teacher_application")) == 1


def test_typed_columns_follow_the_draft_for_admin_filters(env):
    env.ready()
    row = env.repo.get("teacher_application", "teacher_one")
    assert (row["teacher_type"], row["years_teaching"], row["travel_radius_km"]) == (
        "registered",
        6,
        10,
    )
    assert row["current_school"] == "Kampala Parents School"


# -- tampering, size, unicode ------------------------------------------------------------------
@pytest.mark.parametrize(
    "patch",
    [
        {"status": "approved"},
        {"teacher_id": "teacher_two"},
        {"tier": "expert"},
        {"approved": True},
        {"personal": {"full_name": "A B", "approved": True}},
        {"qualifications": {"status": "active"}},
        {
            "curricula": [
                {"curriculum_id": "cur_ug", "approved": True, "level_ids": [], "subject_ids": []}
            ]
        },
        {"referees": [{"name": "A B", "verified": True}]},
    ],
)
def test_unknown_or_privileged_fields_are_refused_and_nothing_changes(env, patch):
    r = env.client.put(
        "/v1/teacher/application/draft",
        json={"base_revision": 0, "content": patch},
        headers=env.h(),
    )
    assert r.status_code == 422
    assert env.rows("teacher_application") == []


def test_body_level_tampering_is_refused(env):
    for extra in ({"status": "approved"}, {"teacher_id": "teacher_two"}, {"revision": 99}):
        r = env.client.put(
            "/v1/teacher/application/draft",
            json={"base_revision": 0, "content": {}, **extra},
            headers=env.h(),
        )
        assert r.status_code == 422
    env.ready()
    r = env.client.post(
        "/v1/teacher/application/submit",
        json={"base_revision": 1, "declaration": True, "status": "approved"},
        headers=env.h(key=KEY),
    )
    assert r.status_code == 422
    assert env.get().json()["status"] == "draft"


def test_client_claiming_a_status_via_other_wrong_types_is_refused(env):
    r = env.client.put(
        "/v1/teacher/application/draft", json={"base_revision": "0", "content": {}}, headers=env.h()
    )
    assert r.status_code == 422
    r = env.client.put(
        "/v1/teacher/application/draft",
        json={"base_revision": 0, "content": {"qualifications": {"years_teaching": "6"}}},
        headers=env.h(),
    )
    assert r.status_code == 422


def test_oversized_text_is_refused_or_reported_never_stored_whole(env):
    r = env.save({"personal": {"full_name": "A" * 10_000}})
    assert r.status_code == 422
    r = env.save({"personal": {"full_name": "A" * 300}})
    assert r.status_code == 200 and "personal.full_name:too_long" in r.json()["issues"]


def test_oversized_body_is_413(env):
    big = {"base_revision": 0, "content": {"personal": {"full_name": "A B"}}, "pad": "x" * 70_000}
    r = env.client.put(
        "/v1/teacher/application/draft",
        content=json.dumps(big),
        headers={**env.h(), "content-type": "application/json"},
    )
    assert r.status_code == 413


def test_unicode_is_kept_normalised_and_hostile_characters_are_flagged(env):
    r = env.save(
        {
            "personal": {"full_name": "  Nakató   Njéri 😀 王小明 "},
            "qualifications": {"institution": "Université de Kigali"},
        }
    )
    assert r.status_code == 200
    stored = env.get().json()["content"]["personal"]["full_name"]
    assert stored == "Nakató Njéri 😀 王小明"
    r = env.save({"personal": {"full_name": "Gr\u0000ace"}}, rev=1)
    assert "personal.full_name:invalid" in r.json()["issues"]
    r = env.save({"personal": {"full_name": "‮Grace"}}, rev=2)  # right-to-left override
    assert "personal.full_name:invalid" in r.json()["issues"]
    r = env.save({"personal": {"full_name": "'; DROP TABLE users; --"}}, rev=3)
    assert r.status_code == 200  # stored as data; the repository never builds queries from it


# -- reference data ------------------------------------------------------------------------------
def test_reference_lists_published_catalogue_and_zones(env):
    j = env.client.get("/v1/teacher/application/reference", headers=env.h()).json()
    assert [c["name"] for c in j["curricula"]] == ["Ugandan", "Cambridge", "American"]
    assert j["curricula"][0]["levels"][0] == {"id": "lvl_p1", "name": "Primary 1"}
    assert {z["name"] for z in j["zones"]} == {"Ntinda", "Kira", "Kololo", "Muyenga"}


def test_catalogue_outage_is_a_retriable_503_not_a_validation_error(env):
    env.catalogue.fail = True
    assert env.save().status_code == 503
    assert env.client.get("/v1/teacher/application/reference", headers=env.h()).status_code == 503


def test_storage_outage_is_503(env):
    env.repo.available = False
    assert env.get().status_code == 503


def test_unknown_curriculum_or_zone_in_a_draft_is_reported(env):
    c = valid_content()
    c["curricula"][0]["level_ids"] = ["lvl_nope"]
    c["zone_ids"] = ["zone_nowhere"]
    issues = env.save(c).json()["issues"]
    assert "curricula[0].level_ids:unknown_choice" in issues and "zone_ids:unknown_choice" in issues


# -- rate limits ---------------------------------------------------------------------------------
def test_draft_saves_are_rate_limited_per_teacher_per_hour_and_reset():
    e = Env(draft_saves_per_hour=5)
    rev = 0
    for _ in range(5):
        rev = e.save({"personal": {"full_name": "Gr"}}, rev=rev).json()["revision"]
    r = e.save({"personal": {"full_name": "Gr"}}, rev=rev)
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    assert r.json()["error"]["code"] == "rate_limited"
    assert e.save(token="t2").status_code == 200  # another teacher is unaffected
    e.clock.advance(hours=1)
    assert e.save({"personal": {"full_name": "Gr"}}, rev=rev).status_code == 200


def test_submit_attempts_are_rate_limited():
    e = Env(submits_per_hour=3)
    for i in range(3):
        assert e.submit(0, key=f"key-{i}-" + "x" * 12).status_code == 422  # no draft yet
    assert e.submit(0, key="key-9-" + "x" * 12).status_code == 429


def test_rate_limit_rows_hold_no_personal_data(env):
    env.ready()
    for r in env.rows("teacher_application_claim"):
        assert "Grace" not in json.dumps(r) and TEACHER_PHONE not in json.dumps(r)


# -- logs ----------------------------------------------------------------------------------------
def test_requests_do_not_log_personal_data(env, caplog):
    caplog.set_level(logging.DEBUG)
    env.ready()
    env.submit(1)
    text = " ".join(f"{r.getMessage()} {getattr(r, '__dict__', {})}" for r in caplog.records)
    for secret in (
        "Grace",
        "Peter Okello",
        "701234567",
        "peter@example.org",
        "Makerere",
        "1992-04-17",
    ):
        assert secret not in text
