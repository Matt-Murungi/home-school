"""K01: mutation tests. Each rule below is a check function that passes on the real code; each
mutant breaks one rule and the SAME check must then fail (so the tests are not vacuous)."""

import pytest

from catalogue_helpers import CatWorld, get, ids
from hs_core.catalogue import api as catalogue_api
from hs_core.catalogue import http as catalogue_http
from hs_core.catalogue import service as catalogue_service
from hs_core.catalogue.http import Rendered


# ------------------------------------------------------------------------------------------- checks
def check_versions_are_never_mixed(w, c):
    for v, other in (("v1", "v2"), ("v2", "v1")):
        topics = get(c, f"/v1/catalogue/versions/{v}/topics?limit=100").json()["items"]
        assert topics and all(t["version_id"] == v for t in topics)
        assert not {t["id"] for t in topics} & {
            t["$id"] for t in w.repo.list("topic") if t["catalogue_version_id"] == other
        }
    assert ids(get(c, "/v1/catalogue/versions/v2/levels")) == ["lv2p3", "lv2p4"]
    assert get(c, "/v1/catalogue/versions/v2/topics?subject_id=sb1m").json()["items"] == []


def check_soft_deleted_rows_are_excluded(w, c):
    codes = [
        t["code"] for t in get(c, "/v1/catalogue/versions/v2/topics?limit=100").json()["items"]
    ]
    assert "DEL" not in codes
    assert get(c, "/v1/catalogue/versions/v2/topics/dl2").status_code == 404
    assert "P9" not in [
        i["code"] for i in get(c, "/v1/catalogue/versions/v1/levels").json()["items"]
    ]
    assert "cuGone" not in ids(get(c, "/v1/catalogue/curricula"))
    assert "DEL" not in [
        t["code"]
        for t in get(c, "/v1/catalogue/versions/v2/topics/search?q=deleted").json()["items"]
    ]


def check_drafts_are_hidden_from_parents_and_teachers(w, c):
    for tok in ("tok-parentA", "tok-teacher1", "tok-Curator", "tok-cura_tor", "tok-super"):
        assert get(c, "/v1/catalogue/versions/v3/topics", tok).status_code == 404
        assert get(c, "/v1/catalogue/versions/v3", tok).status_code == 404
        assert "v3" not in ids(get(c, "/v1/catalogue/curricula/cuA/versions", tok))
    assert get(c, "/v1/catalogue/versions/v3/topics", "tok-curator").status_code == 200


def check_etag_changes_when_a_draft_is_edited(w, c):
    path = "/v1/catalogue/versions/v3/topics"
    before = get(c, path, "tok-curator").headers["etag"]
    w.patch("topic", "ad3", title="Addition, rewritten")
    r = get(c, path, "tok-curator", headers={"If-None-Match": before})
    assert r.status_code == 200 and r.headers["etag"] != before


def check_if_none_match_gives_304(w, c):
    r = get(c, "/v1/catalogue/versions/v2/topics")
    assert (
        get(
            c, "/v1/catalogue/versions/v2/topics", headers={"If-None-Match": r.headers["etag"]}
        ).status_code
        == 304
    )


def check_other_family_child_is_refused(w, c):
    assert get(c, "/v1/catalogue/children/a1/topics", "tok-parentB").status_code == 404
    assert get(c, "/v1/catalogue/children/b1/topics", "tok-parentA").status_code == 404
    assert get(c, "/v1/catalogue/children/a1/topics", "tok-teacher5").status_code == 404
    assert get(c, "/v1/catalogue/children/a1/topics", "tok-parentA").status_code == 200


def check_child_gets_the_pinned_version_not_the_current_one(w, c):
    r = get(c, "/v1/catalogue/children/a1/topics?limit=100", "tok-parentA")
    assert r.json()["version_id"] == "v1" and "t_old" in ids(r)


def check_cursor_is_version_scoped(w, c):
    for i in range(30):
        w.topic(f"m{i:03d}", "v2", "sb2m", "lv2p3", title=f"M {i}", order=20 + i)
    cur = get(c, "/v1/catalogue/versions/v2/topics?limit=5").json()["next_cursor"]
    assert get(c, f"/v1/catalogue/versions/v1/topics?limit=5&cursor={cur}").status_code == 400


def check_current_pointer_is_the_published_version(w, c):
    assert get(c, "/v1/catalogue/curricula/cuA/current").json()["version"]["id"] == "v2"
    assert get(c, "/v1/catalogue/curricula/cuNone/current").status_code == 404


def check_published_is_immutable_cacheable(w, c):
    assert get(c, "/v1/catalogue/versions/v2/levels").headers["cache-control"].endswith("immutable")
    assert (
        not get(c, "/v1/catalogue/versions/v3/levels", "tok-curator")
        .headers["cache-control"]
        .endswith("immutable")
    )


ALL_CHECKS = [
    check_versions_are_never_mixed,
    check_soft_deleted_rows_are_excluded,
    check_drafts_are_hidden_from_parents_and_teachers,
    check_etag_changes_when_a_draft_is_edited,
    check_if_none_match_gives_304,
    check_other_family_child_is_refused,
    check_child_gets_the_pinned_version_not_the_current_one,
    check_cursor_is_version_scoped,
    check_current_pointer_is_the_published_version,
    check_published_is_immutable_cacheable,
]


@pytest.mark.parametrize("check", ALL_CHECKS, ids=lambda f: f.__name__)
def test_the_checks_pass_on_the_real_code(check):
    w = CatWorld()
    check(w, w.client())


# ------------------------------------------------------------------------------------------ mutants
def mix_versions(mp):
    orig = catalogue_service.CatalogueService._topic_eq

    def mutant(self, version_id, subject_id, level_id, parent_id):
        eq = orig(self, version_id, subject_id, level_id, parent_id)
        eq.pop("catalogue_version_id")
        return eq

    mp.setattr(catalogue_service.CatalogueService, "_topic_eq", mutant)


def mix_versions_in_levels(mp):
    orig = catalogue_service.CatalogueService._all
    mp.setattr(
        catalogue_service.CatalogueService,
        "_all",
        lambda self, table, **eq: orig(
            self, table, **{k: v for k, v in eq.items() if k != "catalogue_version_id"}
        ),
    )


def ignore_soft_delete(mp):
    mp.setattr(catalogue_service, "_live", lambda row: row is not None)


def serve_drafts_to_everyone(mp):
    mp.setattr(catalogue_service.CatalogueService, "_visible", lambda self, viewer, version: True)


def serve_drafts_to_any_labelled_user(mp):  # case-insensitive label match: "Curator" would count
    mp.setattr(
        catalogue_service.Viewer,
        "sees_drafts",
        property(
            lambda self: (
                any(r.lower().replace("_", "") in ("curator", "opsadmin") for r in self.roles)
                or True
            )
        ),
    )


def etag_ignores_content(mp):
    def render(scope, payload):
        return Rendered(catalogue_http.dumps(payload), f'"{scope}.const"')

    mp.setattr(catalogue_api, "render", render)


def ignore_if_none_match(mp):
    from fastapi import Response

    def respond(request, r, cache_control):
        return Response(r.body, 200, headers={"ETag": r.etag, "Cache-Control": cache_control})

    mp.setattr(catalogue_api, "respond", respond)


def allow_any_child(mp):
    mp.setattr(
        catalogue_service.CatalogueService, "_may_read_child", lambda self, viewer, child: True
    )


def use_current_version_for_children(mp):
    orig = catalogue_service.CatalogueService.child_version

    def mutant(self, viewer, child_id):
        v = orig(self, viewer, child_id)
        return self.current_version(viewer, v["curriculum_id"])

    mp.setattr(catalogue_service.CatalogueService, "child_version", mutant)


def current_is_the_oldest_published(mp):
    mp.setattr(
        catalogue_service.CatalogueService,
        "current_version",
        lambda self, viewer, cid: {
            "$id": "v1",
            "curriculum_id": cid,
            "version": 1,
            "status": "retired",
        },
    )


def drafts_get_immutable_headers(mp):
    mp.setattr(catalogue_service.CatalogueService, "immutable", staticmethod(lambda v: True))


MUTANTS = [
    (mix_versions, check_versions_are_never_mixed),
    (mix_versions_in_levels, check_versions_are_never_mixed),
    (ignore_soft_delete, check_soft_deleted_rows_are_excluded),
    (serve_drafts_to_everyone, check_drafts_are_hidden_from_parents_and_teachers),
    (serve_drafts_to_any_labelled_user, check_drafts_are_hidden_from_parents_and_teachers),
    (etag_ignores_content, check_etag_changes_when_a_draft_is_edited),
    (ignore_if_none_match, check_if_none_match_gives_304),
    (allow_any_child, check_other_family_child_is_refused),
    (use_current_version_for_children, check_child_gets_the_pinned_version_not_the_current_one),
    (current_is_the_oldest_published, check_current_pointer_is_the_published_version),
    (drafts_get_immutable_headers, check_published_is_immutable_cacheable),
]


@pytest.mark.parametrize("mutant,check", MUTANTS, ids=lambda x: x.__name__)
def test_each_mutant_is_caught(mutant, check):
    w = CatWorld()
    with pytest.MonkeyPatch.context() as mp:
        mutant(mp)
        c = w.client()
        with pytest.raises(AssertionError):
            check(w, c)
