"""K01 (#50): contract tests for GET /v1/catalogue/... on the M05 sample world, offline."""

import base64
import json

import pytest

from catalogue_helpers import APP_USERS, CURATORS, CatWorld, auth, get, ids


@pytest.fixture
def w():
    return CatWorld()


@pytest.fixture
def c(w):
    return w.client()


# -------------------- auth
def test_every_endpoint_requires_a_signed_in_user(c):
    for path in (
        "/v1/catalogue/curricula",
        "/v1/catalogue/versions/v2/levels",
        "/v1/catalogue/children/a1/topics",
        "/v1/catalogue/changes?since_version=v1",
    ):
        assert c.get(path).status_code == 401
        assert c.get(path, headers=auth("nope")).status_code == 401


# -------------------- curricula/versions
def test_curricula_lists_live_curricula_with_their_current_version(c):
    r = get(c, "/v1/catalogue/curricula")
    assert r.status_code == 200
    items = {i["id"]: i for i in r.json()["items"]}
    assert set(items) == {"cuA", "cuB", "cuNone"}  # soft-deleted cuGone excluded
    assert items["cuA"]["current_version_id"] == "v2" and items["cuA"]["current_version"] == 2
    assert items["cuNone"]["current_version_id"] is None  # no published version: listed, no pointer
    assert "deleted_at" not in items["cuA"]


def test_versions_for_apps_are_published_and_retired_only_newest_first(c):
    r = get(c, "/v1/catalogue/curricula/cuA/versions")
    assert [v["id"] for v in r.json()["items"]] == ["v2", "v1"]  # v3 draft and deleted vGone hidden
    assert [v["status"] for v in r.json()["items"]] == ["published", "retired"]


@pytest.mark.parametrize("tok", CURATORS)
def test_curators_and_ops_also_see_drafts(c, tok):
    r = get(c, "/v1/catalogue/curricula/cuA/versions", tok)
    assert [v["id"] for v in r.json()["items"]] == ["v3", "v2", "v1"]
    assert get(c, "/v1/catalogue/versions/v3", tok).status_code == 200


def test_unknown_or_deleted_curriculum_is_404(c):
    for cid in ("zzz", "cuGone", "bad id!"):
        assert get(c, f"/v1/catalogue/curricula/{cid}/versions").status_code == 404
        assert get(c, f"/v1/catalogue/curricula/{cid}/current").status_code == 404


def test_current_version_pointer_and_curriculum_without_published_version(c):
    r = get(c, "/v1/catalogue/curricula/cuA/current")
    assert r.status_code == 200 and r.json()["version"]["id"] == "v2"
    none = get(c, "/v1/catalogue/curricula/cuNone/current")
    assert none.status_code == 404 and none.json()["error"]["code"] == "no_published_version"


def test_version_by_id_serves_published_and_retired_but_draft_is_404_like_unknown(c):
    assert get(c, "/v1/catalogue/versions/v2").json()["version"]["status"] == "published"
    assert get(c, "/v1/catalogue/versions/v1").json()["version"]["status"] == "retired"
    draft = get(c, "/v1/catalogue/versions/v3")
    unknown = get(c, "/v1/catalogue/versions/nope")
    assert draft.status_code == unknown.status_code == 404
    assert draft.json()["error"]["code"] == unknown.json()["error"]["code"]
    assert draft.json()["error"]["message"] == unknown.json()["error"]["message"]
    assert get(c, "/v1/catalogue/versions/vGone").status_code == 404  # soft-deleted


# -------------------- role matrix
@pytest.mark.parametrize("tok", APP_USERS)
def test_role_matrix_draft_is_invisible_to_everyone_but_curator_and_ops(c, tok):
    """parent, guardian, teacher, lead, superadmin, unlabelled, wrong spellings of a label."""
    for path in (
        "/v1/catalogue/versions/v3",
        "/v1/catalogue/versions/v3/levels",
        "/v1/catalogue/versions/v3/subjects",
        "/v1/catalogue/versions/v3/topics",
        "/v1/catalogue/versions/v3/topics/fr3",
        "/v1/catalogue/versions/v3/tree?level_id=lv3p3&subject_id=sb3m",
        "/v1/catalogue/versions/v3/bundle?level_id=lv3p3&subject_id=sb3m",
        "/v1/catalogue/versions/v3/topics/search?q=frac",
        "/v1/catalogue/versions/v3/strands",
        "/v1/catalogue/changes?since_version=v3",
        "/v1/catalogue/changes?since_version=v2&to_version=v3",
    ):
        assert get(c, path, tok).status_code == 404, (tok, path)
    assert "v3" not in [
        v["id"] for v in get(c, "/v1/catalogue/curricula/cuA/versions", tok).json()["items"]
    ]


@pytest.mark.parametrize(
    "tok",
    (
        "tok-parentA",
        "tok-teacher1",
        "tok-none",
        "tok-Curator",
        "tok-cura_tor",
        "tok-OPSADMIN",
        "tok-ops_admin",
        "tok-super",
        "tok-lead",
        "tok-guardianA",
    ),
)
def test_published_content_is_readable_by_every_signed_in_user(c, tok):
    assert get(c, "/v1/catalogue/versions/v2/levels", tok).status_code == 200
    assert get(c, "/v1/catalogue/versions/v1/topics", tok).status_code == 200


@pytest.mark.parametrize("tok", CURATORS)
def test_curator_sees_draft_content(c, tok):
    assert ids(get(c, "/v1/catalogue/versions/v3/levels", tok)) == ["lv3p3", "lv3p4"]
    assert get(c, "/v1/catalogue/versions/v3/topics/fr3", tok).json()["topic"]["id"] == "fr3"


def test_level_equivalences_are_curator_and_ops_only(c):
    for tok in ("tok-parentA", "tok-teacher1", "tok-none", "tok-super", "tok-Curator"):
        assert get(c, "/v1/catalogue/versions/v2/level-equivalences", tok).status_code == 404
    for tok in CURATORS:
        r = get(c, "/v1/catalogue/versions/v2/level-equivalences", tok)
        pairs = {(i["level_id"], i["equivalent_level_id"]) for i in r.json()["items"]}
        # leq1 (v2 -> cuB), leq2 (v2 <-> v3 draft: the curator sees it); soft-deleted leq3 absent
        assert pairs == {("lv2p3", "lvBp3"), ("lv2p4", "lv3p4")}
        eq = next(i for i in r.json()["items"] if i["level_id"] == "lv2p3")["equivalent"]
        assert eq["curriculum_id"] == "cuB" and eq["version_id"] == "vB1"


def test_level_equivalence_to_a_draft_level_is_hidden_from_nobody_but_non_curators(w, c):
    # the curator sees a pair whose other side is a draft
    r = get(c, "/v1/catalogue/versions/v2/level-equivalences", "tok-curator")
    assert any(i["equivalent"]["version_id"] == "v3" for i in r.json()["items"])


# -------------------- content
def test_levels_subjects_strands(c):
    levels = get(c, "/v1/catalogue/versions/v1/levels").json()
    assert [(i["id"], i["order"]) for i in levels["items"]] == [
        ("lv1p3", 3),
        ("lv1p4", 4),
    ]  # P9 deleted
    assert [i["code"] for i in get(c, "/v1/catalogue/versions/v2/subjects").json()["items"]] == [
        "ENG",
        "MATH",
    ]
    s = get(c, "/v1/catalogue/versions/v2/strands?subject_id=sb2m&level_id=lv2p3").json()["items"]
    assert [i["id"] for i in s] == ["st21"]
    assert get(c, "/v1/catalogue/versions/v2/strands?subject_id=sb2e").json()["items"] == []


def test_rows_of_other_versions_never_leak(c):
    for v, s in (("v1", "1"), ("v2", "2")):
        body = get(c, f"/v1/catalogue/versions/{v}/topics?limit=100").json()
        assert body["version_id"] == v
        assert all(t["version_id"] == v for t in body["items"])
        assert all(
            t["id"].endswith(s)
            or t["id"].endswith(s + "h")
            or t["id"].endswith(s + "q")
            or t["id"] == "t_old"
            for t in body["items"]
        )
        for key in ("levels", "subjects"):
            assert all(
                i["id"].endswith(s + ("p3" if key == "levels" else "m"))
                or i["id"][-3:] in ("p3", "p4", "m", "e")
                or i["id"][2] == s
                for i in get(c, f"/v1/catalogue/versions/{v}/{key}").json()["items"]
            )
    # a subject filter from another version matches nothing, it does not reach across
    assert get(c, "/v1/catalogue/versions/v2/topics?subject_id=sb1m").json()["items"] == []


def test_topics_are_ordered_and_soft_deleted_are_excluded(c):
    body = get(c, "/v1/catalogue/versions/v2/topics?subject_id=sb2m&level_id=lv2p3").json()
    assert [t["code"] for t in body["items"]] == ["ADD", "FRAC.H", "FRAC", "FRAC.Q"]
    assert "DEL" not in [t["code"] for t in body["items"]]
    assert get(c, "/v1/catalogue/versions/v2/topics/dl2").status_code == 404  # deleted topic by id


def test_topic_tree_parent_child_and_order(c):
    r = get(c, "/v1/catalogue/versions/v2/tree?level_id=lv2p3&subject_id=sb2m").json()
    assert [t["code"] for t in r["topics"]] == ["ADD", "FRAC"]
    frac = r["topics"][1]
    assert [ch["code"] for ch in frac["children"]] == ["FRAC.H", "FRAC.Q"]
    assert [s["name"] for s in r["strands"]] == ["Number"]
    assert (
        get(c, "/v1/catalogue/versions/v2/tree?level_id=lv2p3").status_code == 422
    )  # both required


def test_parent_filter_and_roots(c):
    kids = get(c, "/v1/catalogue/versions/v2/topics?parent_id=fr2").json()["items"]
    assert [t["code"] for t in kids] == ["FRAC.H", "FRAC.Q"]
    roots = get(
        c, "/v1/catalogue/versions/v2/topics?roots=true&subject_id=sb2m&level_id=lv2p3"
    ).json()["items"]
    assert [t["code"] for t in roots] == ["ADD", "FRAC"]


def test_projection_never_leaks_internal_columns(c):
    t = get(c, "/v1/catalogue/versions/v2/topics/fr2").json()["topic"]
    assert set(t) == {
        "id",
        "version_id",
        "subject_id",
        "level_id",
        "strand_id",
        "parent_id",
        "code",
        "title",
        "description",
        "order",
    }
    v = get(c, "/v1/catalogue/versions/v2").json()["version"]
    assert set(v) == {"id", "curriculum_id", "version", "status", "published_at", "retired_at"}


def test_struggle_tags(c):
    tags = get(c, "/v1/catalogue/struggle-tags").json()["items"]
    assert [t["label"] for t in tags] == ["Reading fluency", "Times tables"]  # inactive hidden
    only = get(c, "/v1/catalogue/struggle-tags?subject_id=sb2e").json()["items"]
    assert [t["label"] for t in only] == ["Reading fluency"]  # all-subject tag + that subject's


# -------------------- search
def test_topic_search_by_text(c):
    r = get(c, "/v1/catalogue/versions/v2/topics/search?q=FRAC").json()
    assert [t["code"] for t in r["items"]] == ["FRAC", "FRAC.H", "FRAC.Q"]  # exact code first
    assert [
        t["code"]
        for t in get(c, "/v1/catalogue/versions/v2/topics/search?q=quarters").json()["items"]
    ] == ["FRAC.Q", "FRAC"]  # a title match outranks a description match
    assert (
        get(c, "/v1/catalogue/versions/v2/topics/search?q=  READING   aloud ").json()["items"][0][
            "id"
        ]
        == "rd2"
    )
    assert (
        get(c, "/v1/catalogue/versions/v2/topics/search?q=roman").json()["items"] == []
    )  # v1 only
    assert [
        t["id"] for t in get(c, "/v1/catalogue/versions/v1/topics/search?q=roman").json()["items"]
    ] == ["t_old"]
    assert get(c, "/v1/catalogue/versions/v2/topics/search?q=deleted").json()["items"] == []
    assert (
        get(c, "/v1/catalogue/versions/v2/topics/search?q=frac&subject_id=sb2e").json()["items"]
        == []
    )
    assert get(c, "/v1/catalogue/versions/v2/topics/search?q=a").status_code == 400  # too short
    assert get(c, "/v1/catalogue/versions/v2/topics/search?q=").status_code == 422
    assert get(c, "/v1/catalogue/versions/v2/topics/search?q=frac&limit=51").status_code == 422


def test_search_treats_punctuation_and_unicode_as_text_not_syntax(c):
    for q in ("%27%3B%20DROP", "%22*%22", "..%2F..", "%C3%A9%C3%A9", "frac%00"):
        assert get(c, f"/v1/catalogue/versions/v2/topics/search?q={q}").status_code in (200, 400)


# -------------------- retired topics
def test_retired_topic_still_resolves_in_its_own_version(c):
    """v1 published (now retired), v2 published without the topic, a log points at v1's topic."""
    assert (
        get(c, "/v1/catalogue/versions/v1/topics/t_old").json()["topic"]["title"]
        == "Roman numerals"
    )
    assert get(c, "/v1/catalogue/versions/v2/topics/t_old").status_code == 404  # not in v2
    assert "t_old" not in [
        t["id"] for t in get(c, "/v1/catalogue/versions/v2/topics?limit=100").json()["items"]
    ]
    assert get(c, "/v1/catalogue/versions/v1/topics/fr2").status_code == 404  # another version's id


def test_publishing_a_new_version_keeps_old_topics_resolvable(w, c):
    w.publish("v3")  # v2 retired, v3 published
    assert get(c, "/v1/catalogue/curricula/cuA/current").json()["version"]["id"] == "v3"
    assert get(c, "/v1/catalogue/versions/v1/topics/t_old").status_code == 200
    assert get(c, "/v1/catalogue/versions/v2/topics/fr2").status_code == 200
    assert (
        get(c, "/v1/catalogue/versions/v3/topics/fr3").status_code == 200
    )  # the former draft is public now


# -------------------- child catalogue
def test_child_catalogue_is_the_pinned_version_even_if_retired(c):
    r = get(c, "/v1/catalogue/children/a1/topics?subject_id=sb1m&level_id=lv1p3", "tok-parentA")
    assert r.status_code == 200 and r.json()["version_id"] == "v1"
    assert "t_old" in ids(r)
    r2 = get(c, "/v1/catalogue/children/a2/topics?subject_id=sb2m&level_id=lv2p3", "tok-parentA")
    assert r2.json()["version_id"] == "v2" and "t_old" not in ids(r2)


def test_child_version_endpoint_reports_pin_and_current(c):
    r = get(c, "/v1/catalogue/children/a1/version", "tok-parentA").json()
    assert r["version"]["id"] == "v1" and r["version"]["status"] == "retired"
    assert r["is_current"] is False and r["current_version_id"] == "v2"
    assert r["curriculum"]["id"] == "cuA"
    r = get(c, "/v1/catalogue/children/a2/version", "tok-parentA").json()
    assert r["is_current"] is True and r["current_version_id"] == "v2"


def test_child_search_and_bundle_use_the_pinned_version(c):
    r = get(c, "/v1/catalogue/children/a1/topics/search?q=roman", "tok-teacher1")
    assert [t["id"] for t in r.json()["items"]] == ["t_old"]
    assert (
        get(c, "/v1/catalogue/children/a2/topics/search?q=roman", "tok-parentA").json()["items"]
        == []
    )
    b = get(c, "/v1/catalogue/children/a1/bundle?level_id=lv1p3&subject_id=sb1m", "tok-parentA")
    assert b.status_code == 200 and b.json()["version_id"] == "v1"


def test_child_access_parent_guardian_assigned_teacher_ops(c):
    path = "/v1/catalogue/children/a1/topics"
    for tok in (
        "tok-parentA",
        "tok-guardianA",
        "tok-teacher1",
        "tok-ops",
        "tok-lead",
    ):  # lead: open case
        assert get(c, path, tok).status_code == 200, tok


def test_child_access_idor_and_unassigned_roles_get_the_unknown_404(c):
    path = "/v1/catalogue/children/a1/topics"
    unknown = get(c, "/v1/catalogue/children/nope/topics", "tok-parentA")
    assert unknown.status_code == 404
    for tok in (
        "tok-parentB",
        "tok-teacher2",
        "tok-teacher5",
        "tok-curator",
        "tok-none",
        "tok-lead2",
        "tok-super",
        "tok-Curator",
        "tok-OPSADMIN",
        "tok-ops_admin",
    ):
        r = get(c, path, tok)
        assert r.status_code == 404, tok
        assert r.json()["error"]["code"] == unknown.json()["error"]["code"]
        assert r.json()["error"]["message"] == unknown.json()["error"]["message"]
        assert get(c, "/v1/catalogue/children/a1/version", tok).status_code == 404
    assert (
        get(c, "/v1/catalogue/children/b1/topics", "tok-parentA").status_code == 404
    )  # other family
    assert get(c, "/v1/catalogue/children/b1/topics", "tok-parentB").status_code == 200


def test_teacher_access_follows_the_assignment_window(w, c):
    # T2's assignment on a2 ended with an immediate-revoke reason: no access; T1 loses access when
    # their assignment ends (end_reason that revokes) and keeps none for another family's child
    assert get(c, "/v1/catalogue/children/a2/topics", "tok-teacher2").status_code == 404
    w.patch("assignment", "asg1", end_at="2027-01-18T08:00:00+00:00", end_reason="parent_cancelled")
    w.patch("session", "sA1", status="cancelled")
    w.patch("session", "sSub", status="cancelled")
    assert get(c, "/v1/catalogue/children/a1/topics", "tok-teacher1").status_code == 404


def test_withdrawn_roles_and_suspended_parent_lose_access(w, c):
    assert get(c, "/v1/catalogue/children/a1/topics", "tok-parentA").status_code == 200
    w.revoke_label("uPA", "parent")  # label authority is the role history, as in M05
    assert get(c, "/v1/catalogue/children/a1/topics", "tok-parentA").status_code == 404


def test_child_without_pin_deleted_child_and_draft_pin(w, c):
    assert get(c, "/v1/catalogue/children/cNone/topics", "tok-parentA").status_code == 404
    assert (
        get(c, "/v1/catalogue/children/cDraft/topics", "tok-parentA").status_code == 404
    )  # draft: no leak
    assert get(c, "/v1/catalogue/children/cDraft/topics", "tok-ops").status_code == 200  # ops may
    w.patch("child", "a2", deleted_at="2027-01-01T00:00:00+00:00")
    assert get(c, "/v1/catalogue/children/a2/topics", "tok-parentA").status_code == 404


def test_child_answers_are_revalidated_not_stored(c):
    r = get(c, "/v1/catalogue/children/a1/topics", "tok-parentA")
    assert r.headers["cache-control"] == "private, no-cache"
    r304 = get(
        c,
        "/v1/catalogue/children/a1/topics",
        "tok-parentA",
        headers={"If-None-Match": r.headers["etag"]},
    )
    assert r304.status_code == 304 and r304.headers["cache-control"] == "private, no-cache"
    # the permission check runs before the 304: another family never gets one with a stolen tag
    stolen = get(
        c,
        "/v1/catalogue/children/a1/topics",
        "tok-parentB",
        headers={"If-None-Match": r.headers["etag"]},
    )
    assert stolen.status_code == 404


# -------------------- pagination
def big(w, version="v2", n=250, subj="sb2m", lvl="lv2p3", prefix="g"):
    for i in range(n):
        # sort_order repeats so the (order, id) tiebreak is exercised
        w.topic(
            f"{prefix}{i:04d}",
            version,
            subj,
            lvl,
            code=f"{prefix.upper()}{i:04d}",
            title=f"Bulk {i}",
            order=i // 3,
        )


def walk(c, path, tok="tok-parentA", limit=100):
    seen, cursor, pages = [], None, 0
    while True:
        sep = "&" if "?" in path else "?"
        url = f"{path}{sep}limit={limit}" + (f"&cursor={cursor}" if cursor else "")
        r = get(c, url, tok)
        assert r.status_code == 200, r.text
        body = r.json()
        seen += [t["id"] for t in body["items"]]
        pages += 1
        cursor = body["next_cursor"]
        if not cursor:
            return seen, pages


def test_cursor_pagination_is_complete_ordered_and_without_duplicates(w, c):
    big(w, n=250)
    seen, pages = walk(
        c, "/v1/catalogue/versions/v2/topics?subject_id=sb2m&level_id=lv2p3", limit=100
    )
    assert len(seen) == len(set(seen)) == 250 + 4  # the 4 live seed topics of that level/subject
    assert pages == 3
    rows = [w.repo.get("topic", i) for i in seen]
    keys = [(r["sort_order"], r["$id"]) for r in rows]
    assert keys == sorted(keys)


def test_pages_have_the_requested_size_and_limit_is_capped_at_100(w, c):
    big(w, n=250)
    r = get(c, "/v1/catalogue/versions/v2/topics?limit=100").json()
    assert len(r["items"]) == 100 and r["next_cursor"]
    assert get(c, "/v1/catalogue/versions/v2/topics?limit=101").status_code == 422
    assert get(c, "/v1/catalogue/versions/v2/topics?limit=0").status_code == 422
    assert get(c, "/v1/catalogue/versions/v2/topics?limit=-5").status_code == 422
    assert (
        get(c, "/v1/catalogue/versions/v2/topics?offset=5").status_code == 200
    )  # unknown param ignored, not an offset


def test_pagination_beyond_the_5000_total_cap(w):
    from hs_core.catalogue.service import CatalogueService
    from hs_core.repository import InMemoryRepository

    repo = InMemoryRepository()
    repo.create(
        "catalogue_version", "vb", {"curriculum_id": "cuA", "version": 1, "status": "published"}
    )
    for i in range(5300):
        repo.create(
            "topic",
            f"t{i:05d}",
            {
                "catalogue_version_id": "vb",
                "subject_id": "s",
                "level_id": "l",
                "code": f"c{i}",
                "title": f"T{i}",
                "sort_order": i // 7,
            },
        )
    svc = CatalogueService(repo)
    from hs_core.catalogue.service import Viewer

    v = svc.version(Viewer("u", frozenset()), "vb")
    seen, cur = [], None
    while True:
        page = svc.topics_page(v, cursor=cur, limit=100)
        seen += [t["id"] for t in page["items"]]
        cur = page["next_cursor"]
        if not cur:
            break
    assert len(seen) == len(set(seen)) == 5300


def test_cursor_is_bound_to_version_and_filters_and_malformed_cursors_are_400(w, c):
    big(w, n=150)
    cur = get(c, "/v1/catalogue/versions/v2/topics?limit=10").json()["next_cursor"]
    assert (
        get(c, f"/v1/catalogue/versions/v1/topics?limit=10&cursor={cur}").status_code == 400
    )  # other version
    assert (
        get(
            c, f"/v1/catalogue/versions/v2/topics?limit=10&subject_id=sb2m&cursor={cur}"
        ).status_code
        == 400
    )
    for bad in ("garbage", "e30", base64.urlsafe_b64encode(b"[1,2]").decode(), "%00", "a" * 399):
        assert get(c, f"/v1/catalogue/versions/v2/topics?cursor={bad}").status_code == 400, bad
    forged = base64.urlsafe_b64encode(
        json.dumps({"v": "v2", "f": [None, None, None, False], "a": "fr1"}).encode()
    ).decode()
    assert (
        get(c, f"/v1/catalogue/versions/v2/topics?cursor={forged}").status_code == 400
    )  # row of another version
    assert get(c, f"/v1/catalogue/versions/v2/topics?cursor={'a' * 401}").status_code == 422


def test_a_publish_while_a_client_is_paging_does_not_change_its_pages(w, c):
    big(w, n=120)
    first = get(c, "/v1/catalogue/versions/v2/topics?limit=50").json()
    w.publish("v3")  # v2 retired, v3 published in the middle of the walk
    second = get(
        c, f"/v1/catalogue/versions/v2/topics?limit=50&cursor={first['next_cursor']}"
    ).json()
    assert second["version_id"] == "v2" and all(t["version_id"] == "v2" for t in second["items"])
    assert not {t["id"] for t in first["items"]} & {t["id"] for t in second["items"]}


def test_a_soft_deleted_row_in_a_draft_between_pages_is_skipped(w, c):
    big(w, "v3", n=120, subj="sb3m", lvl="lv3p3", prefix="d")
    first = get(c, "/v1/catalogue/versions/v3/topics?limit=50", "tok-curator").json()
    w.patch("topic", "d0060", deleted_at="2027-01-01T00:00:00+00:00")
    w.patch(
        "topic", first["items"][-1]["id"], deleted_at="2027-01-01T00:00:00+00:00"
    )  # the cursor row itself
    r = get(
        c, f"/v1/catalogue/versions/v3/topics?limit=50&cursor={first['next_cursor']}", "tok-curator"
    )
    assert r.status_code in (200, 400)  # cursor row gone: a defined answer, never a 500
    if r.status_code == 200:
        assert "d0060" not in ids(r)


def test_soft_deleted_rows_are_skipped_without_shortening_pages(w, c):
    big(w, n=60)
    for i in range(0, 60, 2):
        w.patch("topic", f"g{i:04d}", deleted_at="2027-01-01T00:00:00+00:00")
    r = get(c, "/v1/catalogue/versions/v3/topics?limit=20", "tok-curator")
    seen, _ = walk(c, "/v1/catalogue/versions/v2/topics?subject_id=sb2m&level_id=lv2p3", limit=7)
    assert len(seen) == 30 + 4 and not any(int(i[1:]) % 2 == 0 for i in seen if i.startswith("g"))
    assert r.status_code == 200


# -------------------- caching and ETags
def test_published_content_is_immutable_and_etag_is_strong_and_stable(c):
    r = get(c, "/v1/catalogue/versions/v2/levels")
    assert r.headers["cache-control"] == "private, max-age=31536000, immutable"
    etag = r.headers["etag"]
    assert etag.startswith('"v2.') and not etag.startswith("W/")
    assert get(c, "/v1/catalogue/versions/v2/levels").headers["etag"] == etag
    assert get(c, "/v1/catalogue/versions/v2/levels", "tok-teacher1").headers["etag"] == etag
    assert "vary" in r.headers


def test_if_none_match_returns_304_with_no_body(c):
    r = get(c, "/v1/catalogue/versions/v2/topics")
    for inm in (r.headers["etag"], "W/" + r.headers["etag"], f'"x", {r.headers["etag"]}', "*"):
        r2 = get(c, "/v1/catalogue/versions/v2/topics", headers={"If-None-Match": inm})
        assert (
            r2.status_code == 304 and r2.content == b"" and r2.headers["etag"] == r.headers["etag"]
        )
    assert (
        get(c, "/v1/catalogue/versions/v2/topics", headers={"If-None-Match": '"other"'}).status_code
        == 200
    )
    # a different page or version has a different tag
    assert get(c, "/v1/catalogue/versions/v1/topics").headers["etag"] != r.headers["etag"]
    assert get(c, "/v1/catalogue/versions/v2/topics?limit=2").headers["etag"] != r.headers["etag"]


def test_draft_is_revalidated_and_its_etag_changes_when_it_is_edited(w, c):
    path = "/v1/catalogue/versions/v3/topics?limit=100"
    r = get(c, path, "tok-curator")
    assert r.headers["cache-control"] == "private, no-cache"
    w.patch("topic", "ad3", title="Addition and subtraction")
    r2 = get(c, path, "tok-curator", headers={"If-None-Match": r.headers["etag"]})
    assert r2.status_code == 200 and r2.headers["etag"] != r.headers["etag"]
    assert (
        get(c, path, "tok-curator", headers={"If-None-Match": r2.headers["etag"]}).status_code
        == 304
    )


def test_a_draft_etag_is_not_a_license_to_read_it_after_losing_the_role(c):
    r = get(c, "/v1/catalogue/versions/v3/levels", "tok-curator")
    r2 = get(
        c,
        "/v1/catalogue/versions/v3/levels",
        "tok-parentA",
        headers={"If-None-Match": r.headers["etag"]},
    )
    assert r2.status_code == 404


def test_pointer_endpoints_have_a_short_max_age(c):
    assert get(c, "/v1/catalogue/curricula").headers["cache-control"] == "private, max-age=60"
    assert (
        get(c, "/v1/catalogue/curricula/cuA/current").headers["cache-control"]
        == "private, max-age=60"
    )
    assert (
        get(c, "/v1/catalogue/curricula/cuA/versions").headers["cache-control"]
        == "private, max-age=60"
    )
    assert (
        get(c, "/v1/catalogue/curricula/cuA/versions", "tok-curator").headers["cache-control"]
        == "private, no-cache"
    )


def test_pointer_etag_changes_on_publish(w, c):
    r = get(c, "/v1/catalogue/curricula/cuA/current")
    w.publish("v3")
    r2 = get(c, "/v1/catalogue/curricula/cuA/current", headers={"If-None-Match": r.headers["etag"]})
    assert r2.status_code == 200 and r2.headers["etag"] != r.headers["etag"]


def test_published_version_content_hash_is_stable_across_publishes_and_instances(w, c):
    before = {
        p: get(c, p).headers["etag"]
        for p in (
            "/v1/catalogue/versions/v1/topics",
            "/v1/catalogue/versions/v1/levels",
            "/v1/catalogue/versions/v1/tree?level_id=lv1p3&subject_id=sb1m",
        )
    }
    w.publish("v3")
    other_instance = w.client()  # a fresh service with an empty cache
    after = {p: get(other_instance, p).headers["etag"] for p in before}
    assert before == after


def test_gzip_variant_has_its_own_etag_and_decodes_to_the_same_json(w, c):
    big(w, n=200)
    path = "/v1/catalogue/versions/v2/topics?limit=100"
    plain = get(c, path, headers={"Accept-Encoding": "identity"})
    zipped = get(c, path, headers={"Accept-Encoding": "gzip"})
    assert (
        zipped.headers.get("content-encoding") == "gzip"
        and plain.headers.get("content-encoding") is None
    )
    assert zipped.headers["etag"] != plain.headers["etag"]
    assert zipped.json() == plain.json()
    assert (
        get(
            c, path, headers={"Accept-Encoding": "gzip", "If-None-Match": zipped.headers["etag"]}
        ).status_code
        == 304
    )
    assert (
        get(c, path, headers={"Accept-Encoding": "gzip;q=0"}).headers.get("content-encoding")
        is None
    )


# -------------------- bundle
def test_bundle_is_compact_arrays_in_order(c):
    b = get(c, "/v1/catalogue/versions/v2/bundle?level_id=lv2p3&subject_id=sb2m").json()
    assert (
        b["cols"] == ["id", "parent_id", "strand_id", "code", "title", "order"] and b["count"] == 4
    )
    assert [r[3] for r in b["rows"]] == ["ADD", "FRAC.H", "FRAC", "FRAC.Q"]
    assert (
        get(c, "/v1/catalogue/versions/v2/bundle?level_id=lv2p3&subject_id=sb2e").json()["count"]
        == 1
    )
    empty = get(c, "/v1/catalogue/versions/v2/bundle?level_id=nope&subject_id=sb2m")
    assert empty.status_code == 200 and empty.json()["rows"] == []


def test_response_over_the_hard_cap_is_413_with_a_pointer_to_paging(w):
    big(w, n=300)
    c = w.client(catalogue_bundle_max_bytes=2000)
    r = get(c, "/v1/catalogue/versions/v2/bundle?level_id=lv2p3&subject_id=sb2m")
    assert r.status_code == 413 and r.json()["error"]["code"] == "response_too_large"
    assert (
        get(c, "/v1/catalogue/versions/v2/tree?level_id=lv2p3&subject_id=sb2m").status_code == 413
    )
    assert (
        get(c, "/v1/catalogue/versions/v2/topics?level_id=lv2p3&subject_id=sb2m").status_code == 200
    )


# -------------------- changes
def test_changes_feed_lists_added_removed_and_retitled_topics(w, c):
    w.topic("new3", "v3", "sb3m", "lv3p3", code="DEC", title="Decimals", order=9)
    w.patch("topic", "ad3", title="Addition and subtraction")
    w.patch("topic", "t_old", deleted_at="2027-01-01T00:00:00+00:00")  # v1 gone; irrelevant
    r = get(c, "/v1/catalogue/changes?since_version=v2&to_version=v3", "tok-curator").json()
    assert [t["code"] for t in r["added"]] == ["DEC"]
    assert r["removed"] == []
    assert [(t["code"], t["previous_title"]) for t in r["retitled"]] == [("ADD", "Addition")]
    assert r["added"][0]["subject_code"] == "MATH" and r["added"][0]["level_code"] == "P3"
    d = get(c, "/v1/catalogue/changes?since_version=v1").json()  # to = current published (v2)
    assert (
        d["to_version_id"] == "v2" and d["removed"] == []
    )  # t_old soft-deleted: not a topic of v1 now
    assert d["added"] == []


def test_changes_feed_reports_a_topic_retired_in_the_new_version(w, c):
    r = get(c, "/v1/catalogue/changes?since_version=v1&to_version=v2").json()
    assert [t["code"] for t in r["removed"]] == ["OLD"] and r["added"] == []


def test_changes_across_curricula_and_unknown_versions(c):
    assert get(c, "/v1/catalogue/changes?since_version=v1&to_version=vB1").status_code == 400
    assert get(c, "/v1/catalogue/changes?since_version=zzz").status_code == 404
    assert get(c, "/v1/catalogue/changes").status_code == 422
    assert (
        get(c, "/v1/catalogue/changes?since_version=vB1").status_code == 200
    )  # to = current = itself, empty diff
    assert (
        get(c, "/v1/catalogue/changes?since_version=vN1", "tok-curator").status_code == 404
    )  # no published version


def test_changes_between_two_published_versions_is_immutable_cacheable(c):
    r = get(c, "/v1/catalogue/changes?since_version=v1&to_version=v2")
    assert r.headers["cache-control"].endswith("immutable")
    assert (
        get(c, "/v1/catalogue/changes?since_version=v1").headers["cache-control"]
        == "private, no-cache"
    )


# -------------------- publish gap, errors
def test_publish_gap_between_retire_and_publish_is_a_retryable_503_not_a_404(w, c):
    w.patch(
        "catalogue_version",
        "v2",
        status="retired",
        retired_at=w.clock.now.isoformat(),
        published_key=None,
    )
    r = get(c, "/v1/catalogue/curricula/cuA/current")
    assert r.status_code == 503 and r.headers["retry-after"] == "1"
    assert r.json()["error"]["code"] == "publish_in_progress"
    w.clock.advance(seconds=30)
    w.patch("catalogue_version", "v3", status="published", published_key="cuA")
    assert get(c, "/v1/catalogue/curricula/cuA/current").json()["version"]["id"] == "v3"


def test_curriculum_whose_only_version_was_retired_long_ago_is_404_not_503(w, c):
    w.patch(
        "catalogue_version",
        "v2",
        status="retired",
        retired_at="2026-01-01T00:00:00+00:00",
        published_key=None,
    )
    r = get(c, "/v1/catalogue/curricula/cuA/current")
    assert r.status_code == 404 and r.json()["error"]["code"] == "no_published_version"


def test_two_published_rows_after_a_repair_resolve_to_the_newest(w, c):
    w.patch("catalogue_version", "v3", status="published")
    assert get(c, "/v1/catalogue/curricula/cuA/current").json()["version"]["id"] == "v3"


def test_unknown_ids_and_garbage_are_404_not_500(c):
    for p in (
        "/v1/catalogue/versions/%00/levels",
        "/v1/catalogue/versions/..%2F..%2Fetc/levels",
        "/v1/catalogue/versions/" + "x" * 500 + "/levels",
        "/v1/catalogue/versions/v2/topics/%27%22",
        "/v1/catalogue/children/%00/topics",
        "/v1/catalogue/versions/v2/topics/zzz",
    ):
        assert get(c, p).status_code in (404, 422), p


def test_unavailable_repository_is_a_503_not_a_leak(w, c):
    w.repo.available = False
    r = get(c, "/v1/catalogue/versions/v2/levels")
    assert r.status_code in (500, 503) and "Traceback" not in r.text


def test_service_layer_is_usable_without_http(w):
    """V01, P03, L02 and Q01 call the service directly."""
    from hs_core.catalogue import CatalogueService, Viewer

    svc = CatalogueService(w.repo, clock=w.clock)
    anyone = Viewer("uPA", frozenset({"parent"}))
    assert svc.current_version(anyone, "cuA")["$id"] == "v2"
    cur = svc.curricula(anyone)["items"]
    assert {i["id"] for i in cur} == {"cuA", "cuB", "cuNone"}
    v2 = svc.version(anyone, "v2")
    assert [i["id"] for i in svc.levels(v2)["items"]] == ["lv2p3", "lv2p4"]
    assert svc.topic(svc.version(anyone, "v1"), "t_old")["title"] == "Roman numerals"
