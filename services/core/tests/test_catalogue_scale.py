"""K01: size and time budgets (NFR-1: < 2 s on 3G/4G) with realistic Appwrite-style ids."""

import hashlib
import time

import pytest
from fastapi.testclient import TestClient

from catalogue_helpers import auth, verifier
from hs_core.catalogue.service import CatalogueService
from hs_core.main import create_app
from hs_core.repository import InMemoryRepository
from hs_core.settings import Settings


def rid(prefix: str, i: int) -> str:
    return hashlib.sha1(f"{prefix}:{i}".encode()).hexdigest()[
        :20
    ]  # 20 hex chars, like Appwrite ids


def make(n_math=2000, n_other=500, **settings):
    repo = InMemoryRepository()
    repo.create("curriculum", "cu", {"code": "UNEB", "name": "UNEB"})
    repo.create(
        "catalogue_version", "vv", {"curriculum_id": "cu", "version": 1, "status": "published"}
    )
    repo.create("catalogue_version", "vd", {"curriculum_id": "cu", "version": 2, "status": "draft"})
    for lv in ("lv", "lv4"):
        repo.create(
            "level", lv, {"catalogue_version_id": "vv", "code": lv, "name": lv, "sort_order": 1}
        )
    for sb in ("sbm", "sbe"):
        repo.create("subject", sb, {"catalogue_version_id": "vv", "code": sb, "name": sb})
    topics = [("sbm", "lv", n_math), ("sbe", "lv", n_other), ("sbm", "lv4", n_other)]
    for subj, lvl, n in topics:
        for i in range(n):
            tid = rid(subj + lvl, i)
            parent = rid(subj + lvl, (i - 1) // 8) if i >= 8 else None  # a wide, shallow tree
            repo.create(
                "topic",
                tid,
                {
                    "catalogue_version_id": "vv",
                    "subject_id": subj,
                    "level_id": lvl,
                    "strand_id": rid("st", i % 12),
                    "parent_topic_id": parent,
                    "code": f"M{i // 100}.{i % 100}",
                    "title": f"Topic {i}: working with numbers and measures in context {i % 37}",
                    "description": "A short description of what the learner should be able to do.",
                    "sort_order": i,
                },
            )
    app = create_app(
        Settings(_env_file=None, hs_env="dev", **settings),
        repository=repo,
        verifier=verifier(),
        catalogue_service=CatalogueService(repo),
    )
    return repo, TestClient(app)


@pytest.fixture(scope="module")
def world():
    return make()


def test_bundle_for_a_2000_topic_subject_fits_the_budget(world):
    _, c = world
    path = "/v1/catalogue/versions/vv/bundle?level_id=lv&subject_id=sbm"
    t0 = time.perf_counter()
    plain = c.get(path, headers={**auth("tok-parentA"), "Accept-Encoding": "identity"})
    cold = time.perf_counter() - t0
    assert plain.status_code == 200 and plain.json()["count"] == 2000
    raw = len(plain.content)
    gz = c.get(path, headers={**auth("tok-parentA"), "Accept-Encoding": "gzip"})
    assert gz.headers["content-encoding"] == "gzip"
    wire = int(
        gz.headers["content-length"]
    )  # httpx decodes .content: the header is the bytes on the wire
    print(f"bundle 2000 topics: raw {raw} B, gzip {wire} B, cold {cold * 1000:.0f} ms")
    assert raw < 1_048_576  # under the hard cap
    assert raw < 400_000  # ~ <200 B per topic
    assert wire < 100_000  # 100 KB gzip: ~ 2 s on a 400 kbit/s 3G link
    assert cold < 5.0
    t0 = time.perf_counter()
    for _ in range(20):
        assert c.get(path, headers=auth("tok-parentA")).status_code == 200
    assert (time.perf_counter() - t0) / 20 < 0.3  # warm
    # a topic budget: raw bytes per topic stays small, so the cap is a safety net not a limit
    assert raw / 2000 < 200


def test_the_hard_cap_protects_a_very_large_subject():
    _, c = make(n_math=3000, n_other=10, catalogue_bundle_max_bytes=150_000)
    r = c.get(
        "/v1/catalogue/versions/vv/bundle?level_id=lv&subject_id=sbm", headers=auth("tok-parentA")
    )
    assert r.status_code == 413
    # paging still works for it
    r = c.get(
        "/v1/catalogue/versions/vv/topics?level_id=lv&subject_id=sbm&limit=100",
        headers=auth("tok-parentA"),
    )
    assert r.status_code == 200 and len(r.json()["items"]) == 100


def test_304_costs_no_body(world):
    _, c = world
    path = "/v1/catalogue/versions/vv/bundle?level_id=lv&subject_id=sbm"
    tag = c.get(path, headers=auth("tok-parentA")).headers["etag"]
    r = c.get(path, headers={**auth("tok-parentA"), "If-None-Match": tag})
    assert r.status_code == 304 and len(r.content) == 0


def test_search_is_under_300_ms_on_a_3000_topic_version(world):
    _, c = world
    base = "/v1/catalogue/versions/vv/topics/search"
    c.get(
        f"{base}?q=warmup", headers=auth("tok-parentA")
    )  # builds the in-process index once per version
    worst = 0.0
    for q in ("numbers", "measures in context 5", "M12.", "TOPIC 1999", "zzzz-no-match", "a b"):
        t0 = time.perf_counter()
        r = c.get(f"{base}?q={q}&limit=20", headers=auth("tok-parentA"))
        worst = max(worst, time.perf_counter() - t0)
        assert r.status_code in (200, 400)
    print(f"search worst warm {worst * 1000:.0f} ms over 3000 topics")
    assert worst < 0.3


def test_search_on_seed_sized_data_is_cold_fast():
    _, c = make(n_math=200, n_other=50)
    t0 = time.perf_counter()
    r = c.get("/v1/catalogue/versions/vv/topics/search?q=numbers", headers=auth("tok-parentA"))
    assert r.status_code == 200 and r.json()["items"]
    assert time.perf_counter() - t0 < 0.3


def test_full_walk_of_a_3000_topic_subject_by_cursor(world):
    repo, c = world
    seen, cur = [], None
    while True:
        url = "/v1/catalogue/versions/vv/topics?subject_id=sbm&level_id=lv&limit=100" + (
            f"&cursor={cur}" if cur else ""
        )
        body = c.get(url, headers=auth("tok-parentA")).json()
        seen += [t["id"] for t in body["items"]]
        cur = body["next_cursor"]
        if not cur:
            break
    assert len(seen) == len(set(seen)) == 2000


def test_the_tree_of_a_big_subject_nests_without_losing_topics(world):
    _, c = world
    r = c.get(
        "/v1/catalogue/versions/vv/tree?level_id=lv&subject_id=sbm", headers=auth("tok-parentA")
    )
    assert r.status_code == 200

    def count(nodes):
        return sum(1 + count(n["children"]) for n in nodes)

    assert count(r.json()["topics"]) == 2000
