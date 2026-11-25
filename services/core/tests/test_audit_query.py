"""M06 (#41): read side `list_audit` (for H08 later): filters, cursor pagination, visibility."""

from datetime import timedelta

import pytest

from audit_helpers import T0, admin_ctx, audit_rows, build
from hs_core.audit import AuditForbidden, AuditQueryError, can_read_audit, list_audit
from hs_core.repository import InMemoryRepository


def populate(n=120):
    repo, _, audited, clock = build()
    repo.create("tier", "t1", {"rate": 0})
    repo.create("tier", "t2", {"rate": 0})
    for i in range(n):
        tid = "t1" if i % 2 == 0 else "t2"
        actor = "u_a" if i % 3 else "u_b"
        audited.update("tier", tid, {"rate": i + 1}, ctx=admin_ctx(f"q{i}", actor=actor))
    return repo, audited, clock


class TestVisibility:
    @pytest.mark.parametrize("role", ["opsadmin", "superadmin", "safeguardinglead"])
    def test_staff_roles_can_read(self, role):
        assert can_read_audit([role], lead_blanket_read=True)

    @pytest.mark.parametrize("role", ["parent", "guardian", "teacher", "curator"])
    def test_everyone_else_cannot(self, role):
        assert not can_read_audit([role], lead_blanket_read=True)

    def test_no_roles_cannot(self):
        assert not can_read_audit([], lead_blanket_read=True)

    def test_q12_flag_off_removes_the_lead_only(self):
        assert not can_read_audit(["safeguardinglead"], lead_blanket_read=False)
        assert can_read_audit(["opsadmin"], lead_blanket_read=False)
        assert can_read_audit(["superadmin"], lead_blanket_read=False)

    def test_forbidden_raises_and_reads_nothing(self):
        repo = InMemoryRepository()
        with pytest.raises(AuditForbidden):
            list_audit(repo, roles=["teacher"], lead_blanket_read=True)


class TestFilters:
    def test_by_entity_and_entity_id(self):
        repo, *_ = populate(10)
        page = list_audit(repo, roles=["opsadmin"], entity="tier", entity_id="t1")
        assert {i["entity_id"] for i in page.items} == {"t1"} and len(page.items) == 5

    def test_by_entity_only(self):
        repo, *_ = populate(6)
        assert len(list_audit(repo, roles=["opsadmin"], entity="tier").items) == 6
        assert list_audit(repo, roles=["opsadmin"], entity="dispute").items == []

    def test_by_actor(self):
        repo, *_ = populate(9)
        page = list_audit(repo, roles=["opsadmin"], actor_id="u_b")
        assert {i["actor_id"] for i in page.items} == {"u_b"} and len(page.items) == 3

    def test_by_date_range_is_inclusive(self):
        repo, _, clock = populate(10)
        rows = audit_rows(repo)
        lo, hi = rows[2]["occurred_at"], rows[5]["occurred_at"]
        from datetime import datetime

        page = list_audit(
            repo,
            roles=["opsadmin"],
            since=datetime.fromisoformat(lo),
            until=datetime.fromisoformat(hi),
        )
        assert len(page.items) == 4
        assert T0 < clock()  # sanity

    def test_entity_with_actor_is_refused_there_is_no_index_for_the_pair(self):
        repo, *_ = populate(2)
        with pytest.raises(AuditQueryError):
            list_audit(repo, roles=["opsadmin"], entity="tier", actor_id="u_a")

    def test_items_are_parsed_and_newest_first(self):
        repo, *_ = populate(4)
        page = list_audit(repo, roles=["opsadmin"])
        times = [i["occurred_at"] for i in page.items]
        assert times == sorted(times, reverse=True)
        first = page.items[0]
        assert first["status"] == "intent" and first["source"] == "admin" and first["seq"] >= 1
        assert first["after"]["fields"] and first["before"]["fields"] is not None


class TestCursorPagination:
    def test_pages_cover_every_row_once_without_using_total(self):
        repo, *_ = populate(120)
        seen, cursor, pages = [], None, 0
        while True:
            page = list_audit(repo, roles=["opsadmin"], limit=50, cursor=cursor)
            pages += 1
            seen += [i["id"] for i in page.items]
            cursor = page.next_cursor
            if cursor is None:
                break
        assert pages == 3 and len(seen) == 120 == len(set(seen))

    def test_exact_multiple_ends_with_no_cursor(self):
        repo, *_ = populate(100)
        p1 = list_audit(repo, roles=["opsadmin"], limit=100)
        assert len(p1.items) == 100 and p1.next_cursor is None

    def test_limit_is_bounded(self):
        repo, *_ = populate(1)
        for bad in (0, -1, 101):
            with pytest.raises(AuditQueryError):
                list_audit(repo, roles=["opsadmin"], limit=bad)

    def test_bad_cursor_is_rejected(self):
        repo, *_ = populate(1)
        with pytest.raises(AuditQueryError):
            list_audit(repo, roles=["opsadmin"], cursor="not-a-cursor!!")

    def test_never_asks_the_store_for_more_than_one_page_plus_one(self):
        class Spy(InMemoryRepository):
            def __init__(self):
                super().__init__()
                self.limits = []

            def list(self, table, queries=None):
                import json

                for q in queries or []:
                    d = json.loads(q) if isinstance(q, str) else q
                    if d["method"] == "limit":
                        self.limits.append(d["values"][0])
                return super().list(table, queries)

        spy = Spy()
        _, _, audited, _ = build(spy)
        spy.create("tier", "t1", {"rate": 0})
        for i in range(3):
            audited.update("tier", "t1", {"rate": i + 1}, ctx=admin_ctx(f"s{i}"))
        spy.limits.clear()
        list_audit(spy, roles=["opsadmin"], limit=2)
        assert spy.limits == [3]
        assert max(spy.limits) <= 101  # far below the 5,000 list cap
        assert timedelta(0) == timedelta(0)
