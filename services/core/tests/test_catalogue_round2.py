# ruff: noqa: E501
"""K01 round 2: nothing `immutable` may embed mutable state (status, retired_at, ...)."""

import pytest

from catalogue_helpers import CatWorld, get
from hs_core.catalogue import api as catalogue_api

MUTABLE_KEYS = {
    "status", "retired_at", "published_at", "is_current", "current_version_id",
    "current_version", "published_key", "deleted_at",
}  # fmt: skip

# every GET route of the catalogue router -> a representative URL (token default parentA)
URLS = {
    "/v1/catalogue/curricula": "/v1/catalogue/curricula",
    "/v1/catalogue/curricula/{curriculum_id}/versions": "/v1/catalogue/curricula/cuA/versions",
    "/v1/catalogue/curricula/{curriculum_id}/current": "/v1/catalogue/curricula/cuA/current",
    "/v1/catalogue/versions/{version_id}": "/v1/catalogue/versions/v2",
    "/v1/catalogue/versions/{version_id}/levels": "/v1/catalogue/versions/v2/levels",
    "/v1/catalogue/versions/{version_id}/subjects": "/v1/catalogue/versions/v2/subjects",
    "/v1/catalogue/versions/{version_id}/strands": "/v1/catalogue/versions/v2/strands",
    "/v1/catalogue/versions/{version_id}/topics": "/v1/catalogue/versions/v2/topics",
    "/v1/catalogue/versions/{version_id}/topics/search": "/v1/catalogue/versions/v2/topics/search?q=frac",
    "/v1/catalogue/versions/{version_id}/topics/{topic_id}": "/v1/catalogue/versions/v2/topics/fr2",
    "/v1/catalogue/versions/{version_id}/tree": "/v1/catalogue/versions/v2/tree?level_id=lv2p3&subject_id=sb2m",
    "/v1/catalogue/versions/{version_id}/bundle": "/v1/catalogue/versions/v2/bundle?level_id=lv2p3&subject_id=sb2m",
    "/v1/catalogue/versions/{version_id}/level-equivalences": "/v1/catalogue/versions/v2/level-equivalences",
    "/v1/catalogue/struggle-tags": "/v1/catalogue/struggle-tags",
    "/v1/catalogue/changes": "/v1/catalogue/changes?since_version=v1&to_version=v2",
    "/v1/catalogue/children/{child_id}/version": "/v1/catalogue/children/a1/version",
    "/v1/catalogue/children/{child_id}/topics": "/v1/catalogue/children/a1/topics",
    "/v1/catalogue/children/{child_id}/topics/search": "/v1/catalogue/children/a1/topics/search?q=roman",
    "/v1/catalogue/children/{child_id}/bundle": "/v1/catalogue/children/a1/bundle?level_id=lv1p3&subject_id=sb1m",
}  # fmt: skip  # noqa: E501


def keys_in(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from keys_in(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from keys_in(v)


def check_immutable_bodies_hold_no_mutable_state(w, c):
    seen_immutable = 0
    for url in URLS.values():
        tok = "tok-curator" if "level-equivalences" in url else "tok-parentA"
        r = get(c, url, tok)
        assert r.status_code == 200, url
        if "immutable" in r.headers["cache-control"]:
            seen_immutable += 1
            bad = MUTABLE_KEYS & set(keys_in(r.json()))
            assert not bad, f"{url} is immutable but embeds {sorted(bad)}"
    assert seen_immutable >= 8  # the content endpoints really are immutable-cached


def check_version_status_is_never_cached_as_immutable(w, c):
    before = get(c, "/v1/catalogue/versions/v2")
    assert before.json()["version"]["status"] == "published"
    w.publish("v3")
    after = get(c, "/v1/catalogue/versions/v2")
    assert after.json()["version"]["status"] == "retired"  # it did change ...
    for r in (before, after):  # ... so neither answer may say "immutable" (or be cached for long)
        assert "immutable" not in r.headers["cache-control"]
        assert "max-age=31536000" not in r.headers["cache-control"]
    stale = get(c, "/v1/catalogue/versions/v2", headers={"If-None-Match": before.headers["etag"]})
    assert stale.status_code == 200 and stale.headers["etag"] != before.headers["etag"]


def test_every_catalogue_route_is_covered_by_the_walk(c=None):
    w = CatWorld()
    paths = w.app().openapi()["paths"]
    routes = {p for p, ops in paths.items() if p.startswith("/v1/catalogue") and "get" in ops}
    assert routes == set(URLS), "add new catalogue routes to URLS"


def test_walk_passes_on_the_real_code():
    w = CatWorld()
    check_immutable_bodies_hold_no_mutable_state(w, w.client())


def test_version_endpoint_is_revalidated_and_follows_a_publish():
    w = CatWorld()
    check_version_status_is_never_cached_as_immutable(w, w.client())


def test_version_endpoint_cache_kinds():
    w = CatWorld()
    c = w.client()
    assert get(c, "/v1/catalogue/versions/v2").headers["cache-control"] == "private, max-age=60"
    assert get(c, "/v1/catalogue/versions/v1").headers["cache-control"] == "private, max-age=60"
    d = get(c, "/v1/catalogue/versions/v3", "tok-curator")
    assert d.headers["cache-control"] == "private, no-cache"


def test_child_responses_vary_on_authorization():
    w = CatWorld()
    c = w.client()
    for p in (
        "version",
        "topics",
        "topics/search?q=roman",
        "bundle?level_id=lv1p3&subject_id=sb1m",
    ):
        vary = get(c, f"/v1/catalogue/children/a1/{p}").headers["vary"]
        assert "Authorization" in vary and "Accept-Encoding" in vary


def test_publish_in_progress_503_is_not_cacheable():
    w = CatWorld()
    c = w.client()
    w.patch(
        "catalogue_version", "v2", status="retired", retired_at=w.clock.now.isoformat(),
        published_key=None,
    )  # fmt: skip
    r = get(c, "/v1/catalogue/curricula/cuA/current")
    assert r.status_code == 503 and r.headers["cache-control"] == "no-store"
    assert r.headers["retry-after"] == "1"


# ------------------------------------------------------------------------------------------ mutants
def version_endpoint_immutable(mp):
    mp.setattr(catalogue_api, "_cc", lambda request, kind: "private, max-age=31536000, immutable")


def drop_vary(mp):
    orig = catalogue_api.respond

    def respond(request, r, cc):
        resp = orig(request, r, cc)
        del resp.headers["Vary"]
        return resp

    mp.setattr(catalogue_api, "respond", respond)


def check_vary(w, c):
    assert "Authorization" in get(c, "/v1/catalogue/children/a1/version").headers.get("vary", "")


@pytest.mark.parametrize(
    "mutant,check",
    [
        (version_endpoint_immutable, check_version_status_is_never_cached_as_immutable),
        (version_endpoint_immutable, check_immutable_bodies_hold_no_mutable_state),
        (drop_vary, check_vary),
    ],
    ids=["version-immutable-regression", "version-immutable-walk", "vary-dropped"],
)
def test_each_mutant_is_caught(mutant, check):
    w = CatWorld()
    with pytest.MonkeyPatch.context() as mp:
        mutant(mp)
        with pytest.raises(AssertionError):
            check(w, w.client())
