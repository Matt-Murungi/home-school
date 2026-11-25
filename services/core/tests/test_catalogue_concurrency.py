"""K01: 50 concurrent readers during a publish (no mixed-version pages, no torn pointer)."""

import threading

from fastapi.testclient import TestClient

from catalogue_helpers import CatWorld, auth


def test_fifty_readers_during_a_publish_never_see_a_mixed_or_torn_answer():
    w = CatWorld()
    for i in range(240):
        for v, s in (("v2", "2"), ("v3", "3")):
            w.topic(
                f"x{s}{i:04d}",
                v,
                f"sb{s}m",
                f"lv{s}p3",
                code=f"X{i}",
                title=f"Extra {i}",
                order=10 + i // 2,
            )
    app = w.app()
    start = threading.Barrier(51)
    problems: list[str] = []
    walks: list[list[str]] = []
    lock = threading.Lock()

    def reader(n: int) -> None:
        c = TestClient(app)
        start.wait()
        for _ in range(3):
            seen, cur = [], None
            while True:
                url = "/v1/catalogue/versions/v2/topics?limit=37" + (
                    f"&cursor={cur}" if cur else ""
                )
                r = c.get(url, headers=auth("tok-parentA"))
                if r.status_code != 200:
                    with lock:
                        problems.append(f"page {r.status_code}")
                    return
                body = r.json()
                bad = [t["id"] for t in body["items"] if t["version_id"] != "v2"]
                if bad or body["version_id"] != "v2":
                    with lock:
                        problems.append(f"mixed page {bad[:3]}")
                seen += [t["id"] for t in body["items"]]
                cur = body["next_cursor"]
                if not cur:
                    break
            with lock:
                walks.append(seen)
            p = c.get("/v1/catalogue/curricula/cuA/current", headers=auth("tok-teacher1"))
            ok = p.status_code == 200 and p.json()["version"]["id"] in ("v2", "v3")
            if (
                not ok and p.status_code != 503
            ):  # 503 publish_in_progress is the defined transient answer
                with lock:
                    problems.append(f"pointer {p.status_code} {p.text[:80]}")

    threads = [threading.Thread(target=reader, args=(n,)) for n in range(50)]
    for t in threads:
        t.start()
    start.wait()
    w.publish("v3")  # retire v2, publish v3, while the readers walk v2
    for t in threads:
        t.join(timeout=120)
    assert not any(t.is_alive() for t in threads)
    assert problems == []
    assert len(walks) == 150
    reference = walks[0]
    assert len(reference) == len(set(reference)) > 240
    assert all(wk == reference for wk in walks)  # every walk of v2 is the same complete list
    c = TestClient(app)
    assert (
        c.get("/v1/catalogue/curricula/cuA/current", headers=auth("tok-parentA")).json()["version"][
            "id"
        ]
        == "v3"
    )
    assert (
        c.get("/v1/catalogue/versions/v2", headers=auth("tok-parentA")).json()["version"]["status"]
        == "retired"
    )


def test_concurrent_cold_cache_builds_agree_on_the_etag():
    w = CatWorld()
    app = w.app()
    out: list[str] = []
    gate = threading.Barrier(50)

    def go() -> None:
        c = TestClient(app)
        gate.wait()
        out.append(
            c.get(
                "/v1/catalogue/versions/v1/tree?level_id=lv1p3&subject_id=sb1m",
                headers=auth("tok-parentA"),
            ).headers["etag"]
        )

    ts = [threading.Thread(target=go) for _ in range(50)]
    [t.start() for t in ts]
    [t.join(timeout=60) for t in ts]
    assert len(out) == 50 and len(set(out)) == 1
