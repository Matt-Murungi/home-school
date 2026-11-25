"""M02 (#37): plans, subscriptions, matching, assignments, sessions schema + rules.

Acceptance criteria and edge cases are mapped to tests in memory.md (M02 block).
"""

import copy
import io
import threading
import unittest

from helpers import APPWRITE_DIR

import migrate
import schema_build as sb
import session_rules as rules
from fake_client import FakeAppwriteClient, UniqueViolation

DOMAINS = sb.load_domains()
TABLES = {t["$id"]: t for d in DOMAINS for t in d["tables"]}
DOMAIN = next(d for d in DOMAINS if d["domain"] == "sessions")
IDS = [t["$id"] for t in DOMAIN["tables"]]
REQUIRED = {"plan", "subscription", "match_request", "match_offer", "assignment", "session", "session_checkin",
            "session_confirmation", "cancellation", "dispute"}
SR_7_4_STATES = ["scheduled", "checked_in", "checked_out", "awaiting_confirmation", "confirmed", "disputed", "void",
                 "incomplete", "cancelled_by_parent_late", "cancelled_by_parent_ontime", "cancelled_by_teacher", "no_show"]


def cols(table):
    return {c["key"]: c for c in TABLES[table]["columns"]}


def index_cols(table):
    return {i["key"]: (i["type"], i["columns"]) for i in TABLES[table]["indexes"]}


def mutated(fn):
    domains = copy.deepcopy(DOMAINS)
    fn(next(d for d in domains if d["domain"] == "sessions"))
    return sb.validate(domains)


def tbl(domain, name):
    return next(t for t in domain["tables"] if t["$id"] == name)


def fresh_client():
    """A fake project with every table created (sessions tables and the core tables they point at)."""
    client = FakeAppwriteClient()
    for t in TABLES.values():
        client.ensure_table(t)
    return client


T0 = "2026-12-01T06:00:00.000+00:00"
T1 = "2026-12-01T07:00:00.000+00:00"


def session_row(teacher="t1", child="c1", start=T0, end=T1, **extra):
    row = {"child_id": child, "teacher_id": teacher, "subscription_id": "sub1", "home_location_id": "h1",
           "scheduled_start_at": start, "scheduled_end_at": end, "status": "scheduled", "status_changed_at": start,
           "is_substitute": False}
    row.update(extra)
    return rules.with_session_keys(row)


def attempt_all(fn, n):
    results = []

    def run(i):
        try:
            fn(i)
            results.append("ok")
        except (rules.SessionConflict, UniqueViolation):
            results.append("rejected")

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    return results


class Tables(unittest.TestCase):
    def test_all_issue_tables_exist(self):  # AC: tables per SR 5.2
        self.assertEqual(set(IDS), REQUIRED)

    def test_sr_5_2_key_fields(self):
        want = {
            "plan": {"curriculum_id", "sessions_per_week", "price_ugx", "active"},
            "subscription": {"child_id", "plan_id", "status", "period_start_at", "period_end_at", "preferred_slots"},
            "match_request": {"subscription_id", "type", "status", "attempts"},
            "match_offer": {"match_request_id", "teacher_id", "rank_score", "score_breakdown", "offered_at", "expires_at", "response"},
            "assignment": {"child_id", "teacher_id", "subscription_id", "start_at", "end_at", "is_trial", "end_reason"},
            "session": {"assignment_id", "child_id", "teacher_id", "scheduled_start_at", "scheduled_end_at", "status", "is_substitute"},
            "session_checkin": {"session_id", "type", "lat_e6", "lng_e6", "accuracy_m", "device_time_at", "server_time_at", "within_geofence"},
            "session_confirmation": {"session_id", "parent_response", "responded_at", "auto_confirmed"},
            "cancellation": {"session_id", "by_role", "reason", "is_late"},
            "dispute": {"session_id", "raised_by_id", "reason", "status", "resolution", "resolved_by_id"},
        }
        for table, keys in want.items():
            self.assertLessEqual(keys, set(cols(table)), table)

    def test_subscription_status_enum(self):
        self.assertEqual(cols("subscription")["status"]["elements"],
                         ["pending_payment", "matching", "active", "paused", "past_due", "cancelled"])

    def test_match_request_types(self):
        self.assertEqual(cols("match_request")["type"]["elements"], ["new", "rematch", "substitute"])

    def test_session_status_enum_is_exactly_sr_7_4(self):  # AC: status restricted to SR 7.4 states
        self.assertEqual(sorted(cols("session")["status"]["elements"]), sorted(SR_7_4_STATES))
        self.assertEqual(cols("session")["status"]["format"], "enum")
        self.assertTrue(cols("session")["status"]["required"])

    def test_rules_states_match_schema_enum(self):
        self.assertEqual(sorted(rules.STATES), sorted(cols("session")["status"]["elements"]))

    def test_every_foreign_key_is_indexed_and_resolves(self):
        self.assertEqual(sb.validate(DOMAINS), [])
        rel = {r["from"] for r in DOMAIN["relations"]}
        for t in DOMAIN["tables"]:
            for c in t["columns"]:
                if c["key"].endswith("_id"):
                    self.assertTrue(f"{t['$id']}.{c['key']}" in rel or c["key"] in DOMAIN["meta"][t["$id"]].get("non_fk", {}), c["key"])
        for r in DOMAIN["relations"]:
            table, col = r["from"].split(".")
            self.assertTrue(any(i["columns"][0] == col for i in TABLES[table]["indexes"]), r["from"])

    def test_session_links(self):
        rel = {(r["from"], r["to"]) for r in DOMAIN["relations"]}
        for pair in [("session.assignment_id", "assignment"), ("session.child_id", "child"), ("session.teacher_id", "teacher_profile"),
                     ("session.substitute_for_session_id", "session"), ("assignment.subscription_id", "subscription"),
                     ("match_offer.match_request_id", "match_request"), ("subscription.plan_id", "plan"),
                     ("session_checkin.session_id", "session"), ("dispute.session_id", "session"),
                     ("plan.curriculum_id", "curriculum")]:
            self.assertIn(pair, rel)

    def test_timestamps_money_coordinates(self):  # money integer UGX, UTC, micro-degrees
        self.assertEqual(cols("plan")["price_ugx"]["type"], "integer")
        self.assertEqual(cols("plan")["currency"]["default"], "UGX")
        for k in ("lat_e6", "lng_e6", "accuracy_m"):
            self.assertEqual(cols("session_checkin")[k]["type"], "integer")
        for t in DOMAIN["tables"]:
            for c in t["columns"]:
                if c["key"].endswith("_at"):
                    self.assertEqual(c["type"], "datetime")
                self.assertNotIn(c["type"], ("float", "double"))

    def test_rank_score_is_integer_basis_points_not_float(self):
        self.assertEqual(cols("match_offer")["rank_score"]["type"], "integer")
        self.assertEqual(cols("match_offer")["score_breakdown"]["type"], "text")

    def test_deletion_policies(self):
        meta = {k: v["deletion"] for k, v in DOMAIN["meta"].items()}
        for k in ("session_checkin", "session_confirmation", "cancellation"):
            self.assertEqual(meta[k], "immutable")
        for k in ("plan", "subscription", "match_request", "match_offer", "assignment", "session", "dispute"):
            self.assertEqual(meta[k], "soft")
            self.assertIn("deleted_at", cols(k))

    def test_row_security_and_no_table_permissions(self):
        for t in DOMAIN["tables"]:
            self.assertIs(t["rowSecurity"], True)
            self.assertEqual(t["$permissions"], [])

    def test_trial_and_end_reason(self):  # edge: trial assignment
        self.assertEqual(cols("assignment")["is_trial"]["type"], "boolean")
        self.assertIn("trial_not_continued", cols("assignment")["end_reason"]["elements"])
        self.assertIn("trial_converted", cols("assignment")["end_reason"]["elements"])
        self.assertFalse(cols("assignment")["end_reason"]["required"])

    def test_substitute_session_has_no_assignment(self):  # edge: session without assignment
        self.assertFalse(cols("session")["assignment_id"]["required"])
        self.assertFalse(cols("session")["substitute_for_session_id"]["required"])

    def test_index_for_teacher_sessions_by_day_and_child_sessions_by_period(self):  # AC
        idx = index_cols("session")
        self.assertEqual(idx["by_teacher_day"], ("key", ["teacher_id", "scheduled_start_at"]))
        self.assertEqual(idx["by_child_period"], ("key", ["child_id", "scheduled_start_at"]))
        self.assertEqual(idx["by_status_changed"], ("key", ["status", "status_changed_at"]))

    def test_unique_constraints_declared(self):
        self.assertEqual(index_cols("match_offer")["unique_active_offer"], ("unique", ["active_key"]))
        self.assertEqual(index_cols("assignment")["unique_active_assignment"], ("unique", ["active_key"]))
        self.assertEqual(index_cols("session")["unique_teacher_slot"], ("unique", ["teacher_slot_key"]))
        self.assertEqual(index_cols("session_checkin")["unique_session_type"], ("unique", ["session_id", "type"]))
        self.assertEqual(index_cols("session_confirmation")["unique_session"], ("unique", ["session_id"]))
        self.assertEqual(index_cols("cancellation")["unique_session"], ("unique", ["session_id"]))
        self.assertEqual(index_cols("dispute")["unique_open_dispute"], ("unique", ["open_key"]))
        self.assertEqual(index_cols("plan")["unique_active_plan"], ("unique", ["active_key"]))

    def test_validator_rejects_unindexed_foreign_key(self):
        def f(d):
            t = tbl(d, "cancellation")
            t["indexes"] = [i for i in t["indexes"] if i["columns"][0] != "cancelled_by_id"]
        self.assertTrue(any("cancelled_by_id" in e for e in mutated(f)))

    def test_validator_rejects_float_money(self):
        def f(d):
            next(c for c in tbl(d, "plan")["columns"] if c["key"] == "price_ugx")["type"] = "float"
        self.assertTrue(mutated(f))


class SoftDeleteUniqueRule(unittest.TestCase):
    """Convention for ALL domains: soft-deleted rows must not block re-creation, so unique indexes of soft tables use
    derived nullable `*_key` columns that the core service nulls on soft delete."""

    def test_sessions_domain_obeys_rule(self):
        self.assertEqual(sb.soft_unique_violations([DOMAIN]), [])

    def test_validator_rejects_raw_unique_on_soft_table(self):
        def f(d):
            tbl(d, "plan")["indexes"].append({"key": "unique_raw", "type": "unique", "columns": ["sessions_per_week"], "orders": ["ASC"]})
        self.assertTrue(any("plan.unique_raw" in e and "soft" in e for e in mutated(f)))

    def test_validator_rejects_required_key_column(self):
        def f(d):
            next(c for c in tbl(d, "assignment")["columns"] if c["key"] == "active_key")["required"] = True
        self.assertTrue(any("active_key" in e for e in mutated(f)))

    def test_legacy_exemptions_are_pinned_to_the_exact_six(self):
        # Adding an entry needs an owner decision: new soft tables must use derived *_key columns.
        self.assertEqual(sb.SOFT_UNIQUE_LEGACY, frozenset({
            ("user_profile", "unique_phone"), ("user_profile", "unique_email"), ("parent_profile", "unique_user"),
            ("zone", "unique_name"), ("teacher_profile", "unique_user"), ("teacher_approval", "unique_teacher_curriculum_level")}))

    def test_legacy_exemptions_are_minimal_core_soft_unique_indexes(self):
        core = next(d for d in DOMAINS if d["domain"] == "core")
        soft_unique = {(t["$id"], i["key"]) for t in core["tables"] for i in t["indexes"]
                       if i["type"] == "unique" and core["meta"][t["$id"]]["deletion"] == "soft"}
        self.assertLessEqual(sb.SOFT_UNIQUE_LEGACY, soft_unique)
        without = sb.soft_unique_violations(DOMAINS, legacy=frozenset())
        for table, index in sb.SOFT_UNIQUE_LEGACY:  # each exemption is needed: without it the rule is violated
            self.assertTrue(any(e.startswith(f"{table}.{index}:") for e in without), (table, index))
        # nothing else is violated, and no sessions-domain pair is exempt
        self.assertEqual({tuple(e.split(":")[0].split(".")) for e in without}, set(sb.SOFT_UNIQUE_LEGACY))
        self.assertFalse({t for t, _ in sb.SOFT_UNIQUE_LEGACY} & set(IDS))
        self.assertEqual(sb.soft_unique_violations(DOMAINS), [])

    def test_sessions_domain_needs_no_exemption_and_a_new_one_fails_the_pin(self):
        self.assertEqual(sb.soft_unique_violations([DOMAIN], legacy=frozenset()), [])

    def test_restore_recomputes_keys_and_conflicts_with_a_newer_active_row(self):  # finding 1
        client = fresh_client()
        row = {"child_id": "c1", "teacher_id": "t1", "subscription_id": "s1", "start_at": T0, "is_trial": False}
        a1 = client.create_row("assignment", rules.with_assignment_keys(row))
        client.update_row("assignment", a1["$id"], sb.soft_delete_patch(TABLES["assignment"], T1))
        client.create_row("assignment", rules.with_assignment_keys(dict(row, teacher_id="t2")))  # A2
        patch = sb.soft_restore_patch(client.get_row("assignment", a1["$id"]), rules.with_assignment_keys)
        self.assertEqual(patch, {"deleted_at": None, "active_key": "c1:s1"})
        with self.assertRaises(UniqueViolation):  # 409: the value is taken
            client.update_row("assignment", a1["$id"], patch)
        # a bare deleted_at=None patch is the bug: it silently yields two active assignments
        client.update_row("assignment", a1["$id"], {"deleted_at": None})
        active = [r for r in client.list_rows("assignment", child_id="c1") if not r.get("deleted_at")]
        self.assertEqual(len(active), 2)  # documents why the helper is mandatory

    def test_restore_without_conflict_rebuilds_keys_for_every_table(self):
        client = fresh_client()
        row = rules.with_session_keys(session_row())
        s = client.create_row("session", row)
        client.update_row("session", s["$id"], sb.soft_delete_patch(TABLES["session"], T1))
        self.assertIsNone(client.get_row("session", s["$id"])["teacher_slot_key"])
        client.update_row("session", s["$id"], sb.soft_restore_patch(client.get_row("session", s["$id"]), rules.with_session_keys))
        self.assertEqual(client.get_row("session", s["$id"])["teacher_slot_key"], s["teacher_slot_key"])
        # a cancelled session restored stays released
        c = client.create_row("session", session_row(teacher="t9", status="cancelled_by_teacher"))
        self.assertIsNone(sb.soft_restore_patch(c, rules.with_session_keys)["teacher_slot_key"])

    def test_no_code_clears_deleted_at_without_the_restore_helper(self):  # lint-style
        import re
        root = APPWRITE_DIR.parent
        pattern = re.compile(r"""["']deleted_at["']\s*:\s*None|deleted_at\s*=\s*None""")
        offenders = []
        for base in (root / "appwrite", root / "services" / "core"):
            for path in base.rglob("*.py"):
                rel = path.relative_to(root).as_posix()
                if "/tests/" in rel or "/.venv/" in rel or rel in ("appwrite/schema_build.py",):
                    continue
                if pattern.search(path.read_text()):
                    offenders.append(rel)
        self.assertEqual(offenders, [], "restore must go through schema_build.soft_restore_patch (recomputes *_key columns)")

    def test_soft_delete_patch_nulls_unique_keys(self):
        patch = sb.soft_delete_patch(TABLES["assignment"], "2026-12-02T00:00:00.000+00:00")
        self.assertEqual(patch, {"deleted_at": "2026-12-02T00:00:00.000+00:00", "active_key": None})
        self.assertIsNone(sb.soft_delete_patch(TABLES["session"], "x")["teacher_slot_key"])

    def test_soft_deleted_row_does_not_block_recreation(self):
        client = fresh_client()
        row = {"child_id": "c1", "teacher_id": "t1", "subscription_id": "s1", "start_at": T0, "is_trial": False}
        a1 = client.create_row("assignment", rules.with_assignment_keys(row))
        with self.assertRaises(UniqueViolation):
            client.create_row("assignment", rules.with_assignment_keys(dict(row, teacher_id="t2")))
        client.update_row("assignment", a1["$id"], sb.soft_delete_patch(TABLES["assignment"], T1))
        client.create_row("assignment", rules.with_assignment_keys(dict(row, teacher_id="t2")))


class UniqueBackstops(unittest.TestCase):
    def test_one_active_offer_per_teacher_per_request(self):  # AC
        c = fresh_client()
        offer = {"match_request_id": "r1", "teacher_id": "t1", "rank": 1, "rank_score": 8200, "offered_at": T0, "expires_at": T1,
                 "response": "pending"}
        o1 = c.create_row("match_offer", rules.with_offer_keys(offer))
        with self.assertRaises(UniqueViolation):
            c.create_row("match_offer", rules.with_offer_keys(offer))
        c.create_row("match_offer", rules.with_offer_keys(dict(offer, teacher_id="t2")))
        c.create_row("match_offer", rules.with_offer_keys(dict(offer, match_request_id="r2")))
        c.update_row("match_offer", o1["$id"], rules.resolve_offer_patch("declined", T1))
        c.create_row("match_offer", rules.with_offer_keys(offer))

    def test_one_active_assignment_per_child_per_subscription(self):
        c = fresh_client()
        a = {"child_id": "c1", "teacher_id": "t1", "subscription_id": "s1", "start_at": T0, "is_trial": False}
        first = c.create_row("assignment", rules.with_assignment_keys(a))
        with self.assertRaises(UniqueViolation):
            c.create_row("assignment", rules.with_assignment_keys(dict(a, teacher_id="t2")))
        c.create_row("assignment", rules.with_assignment_keys(dict(a, child_id="c2", subscription_id="s2")))
        c.update_row("assignment", first["$id"], rules.end_assignment_patch("rematch_requested", T1))
        c.create_row("assignment", rules.with_assignment_keys(dict(a, teacher_id="t2")))

    def test_trial_assignment_then_conversion(self):  # edge: trial
        c = fresh_client()
        a = {"child_id": "c1", "teacher_id": "t1", "subscription_id": "s1", "start_at": T0, "is_trial": True}
        trial = c.create_row("assignment", rules.with_assignment_keys(a))
        with self.assertRaises(UniqueViolation):  # a trial counts as the active assignment
            c.create_row("assignment", rules.with_assignment_keys(dict(a, is_trial=False)))
        patch = rules.end_assignment_patch("trial_converted", T1)
        self.assertIsNone(patch["active_key"])
        c.update_row("assignment", trial["$id"], patch)
        c.create_row("assignment", rules.with_assignment_keys(dict(a, is_trial=False)))

    def test_same_start_for_one_teacher_is_rejected_by_backstop(self):  # edge: backstop
        c = fresh_client()
        c.create_row("session", session_row())
        with self.assertRaises(UniqueViolation):
            c.create_row("session", session_row(child="c2"))
        c.create_row("session", session_row(teacher="t2", child="c2"))

    def test_cancelled_session_releases_the_slot(self):
        c = fresh_client()
        s = c.create_row("session", session_row())
        c.update_row("session", s["$id"], rules.transition_patch(s, "cancelled_by_parent_ontime", T1))
        c.create_row("session", session_row(child="c2"))

    def test_sessions_by_day_and_period_lists_are_index_backed(self):
        c = fresh_client()
        c.create_row("session", session_row())
        c.create_row("session", session_row(start="2026-12-02T06:00:00.000+00:00", end="2026-12-02T07:00:00.000+00:00"))
        self.assertEqual(len(c.list_rows("session", teacher_id="t1")), 2)
        self.assertEqual(c.last_query_index, "by_teacher_day")
        c.list_rows("session", child_id="c1")
        self.assertEqual(c.last_query_index, "by_child_period")

    def test_one_checkin_and_one_checkout_per_session(self):
        c = fresh_client()
        base = {"session_id": "s1", "type": "in", "lat_e6": 320000, "lng_e6": 32580000, "accuracy_m": 12,
                "device_time_at": T0, "server_time_at": T0, "within_geofence": True}
        c.create_row("session_checkin", base)
        with self.assertRaises(UniqueViolation):
            c.create_row("session_checkin", base)
        c.create_row("session_checkin", dict(base, type="out"))

    def test_one_confirmation_one_cancellation_per_session(self):
        c = fresh_client()
        c.create_row("session_confirmation", {"session_id": "s1", "parent_response": "confirmed", "auto_confirmed": False})
        with self.assertRaises(UniqueViolation):
            c.create_row("session_confirmation", {"session_id": "s1", "parent_response": "disputed", "auto_confirmed": False})
        row = {"session_id": "s1", "by_role": "parent", "is_late": True, "cancelled_at": T0}
        c.create_row("cancellation", row)
        with self.assertRaises(UniqueViolation):
            c.create_row("cancellation", row)

    def test_one_open_dispute_per_session(self):
        c = fresh_client()
        d = {"session_id": "s1", "raised_by_id": "u1", "reason": "x", "status": "open"}
        first = c.create_row("dispute", rules.with_dispute_keys(d))
        with self.assertRaises(UniqueViolation):
            c.create_row("dispute", rules.with_dispute_keys(d))
        c.update_row("dispute", first["$id"], rules.resolve_dispute_patch("confirmed", "u2", T1))
        c.create_row("dispute", rules.with_dispute_keys(d))

    def test_one_active_plan_per_curriculum_and_frequency(self):
        c = fresh_client()
        p = {"curriculum_id": "cur1", "sessions_per_week": 2, "price_ugx": 400000, "currency": "UGX", "active": True}
        c.create_row("plan", rules.with_plan_keys(p))
        with self.assertRaises(UniqueViolation):
            c.create_row("plan", rules.with_plan_keys(dict(p, price_ugx=450000)))
        c.create_row("plan", rules.with_plan_keys(dict(p, sessions_per_week=3)))
        c.create_row("plan", rules.with_plan_keys(dict(p, active=False)))

    def test_fake_rejects_invalid_rows(self):
        c = fresh_client()
        with self.assertRaises(ValueError):
            c.create_row("session", session_row(status="bogus"))
        with self.assertRaises(ValueError):
            c.create_row("session", {"child_id": "c1"})
        with self.assertRaises(ValueError):
            c.create_row("session", session_row(not_a_column=1))


class OverlapAndConcurrency(unittest.TestCase):
    """The service check (inside the write path) plus the backstop. Appwrite has no exclusion constraint."""

    def test_overlap_predicate(self):
        self.assertTrue(rules.overlaps(T0, T1, "2026-12-01T06:30:00.000+00:00", "2026-12-01T07:30:00.000+00:00"))
        self.assertTrue(rules.overlaps(T0, T1, "2026-12-01T05:30:00.000+00:00", "2026-12-01T06:01:00.000+00:00"))
        self.assertTrue(rules.overlaps(T0, T1, T0, T1))
        self.assertFalse(rules.overlaps(T0, T1, T1, "2026-12-01T08:00:00.000+00:00"))  # back to back is fine
        self.assertFalse(rules.overlaps(T0, T1, "2026-12-01T04:00:00.000+00:00", T0))

    def test_partial_overlap_is_rejected_by_service_check(self):  # edge: overlapping sessions
        c = fresh_client()
        rules.create_session(c, session_row())
        for start, end in [("2026-12-01T06:30:00.000+00:00", "2026-12-01T07:30:00.000+00:00"),
                           ("2026-12-01T05:00:00.000+00:00", "2026-12-01T06:15:00.000+00:00")]:
            with self.assertRaises(rules.SessionConflict):
                rules.create_session(c, session_row(child="c2", start=start, end=end))
        rules.create_session(c, session_row(child="c2", start=T1, end="2026-12-01T08:00:00.000+00:00"))
        self.assertEqual(len(c.list_rows("session", teacher_id="t1")), 2)

    def test_cancelled_session_does_not_conflict(self):
        c = fresh_client()
        s = rules.create_session(c, session_row())
        c.update_row("session", s["$id"], rules.transition_patch(s, "cancelled_by_teacher", T1))
        rules.create_session(c, session_row(child="c2", start="2026-12-01T06:30:00.000+00:00", end="2026-12-01T07:30:00.000+00:00"))

    def test_two_concurrent_requests_for_the_same_slot_one_wins(self):
        c = fresh_client()
        gate = threading.Barrier(2)

        def pause():  # both requests pass the service check before either writes: only the backstop is left
            try:
                gate.wait(timeout=2)
            except threading.BrokenBarrierError:
                pass

        results = attempt_all(lambda i: rules.create_session(c, session_row(child=f"c{i}"), pause=pause), 2)
        self.assertEqual(sorted(results), ["ok", "rejected"])
        self.assertEqual(len(c.list_rows("session", teacher_id="t1")), 1)

    def test_many_concurrent_requests_exactly_one_row(self):
        c = fresh_client()
        results = attempt_all(lambda i: rules.create_session(c, session_row(child=f"c{i}")), 12)
        self.assertEqual(results.count("ok"), 1)
        self.assertEqual(len(c.list_rows("session", teacher_id="t1")), 1)

    def test_zero_or_negative_length_session_is_rejected(self):  # finding 3
        c = fresh_client()
        with self.assertRaises(rules.InvalidSession):
            rules.create_session(c, session_row(start=T1, end=T0))
        with self.assertRaises(rules.InvalidSession):
            rules.create_session(c, session_row(start=T0, end=T0))
        self.assertEqual(c.list_rows("session", teacher_id="t1"), [])
        with self.assertRaises(rules.InvalidSession):
            rules.substitute_session_row({**session_row(start=T1, end=T0), "status": "cancelled_by_teacher", "$id": "x"}, "t2")

    def test_other_teacher_same_time_is_fine(self):
        c = fresh_client()
        rules.create_session(c, session_row(teacher="t1"))
        rules.create_session(c, session_row(teacher="t2"))

    def test_substitute_session_without_assignment(self):  # edge: substitute
        c = fresh_client()
        orig = rules.create_session(c, session_row(assignment_id="a1"))
        orig = c.update_row("session", orig["$id"], rules.transition_patch(orig, "cancelled_by_teacher", T1))
        sub = rules.create_session(c, rules.substitute_session_row(orig, "t2"))
        self.assertTrue(sub["is_substitute"])
        self.assertEqual(sub["teacher_id"], "t2")
        self.assertEqual(sub["substitute_for_session_id"], orig["$id"])
        self.assertIsNone(sub.get("assignment_id"))
        self.assertEqual(sub["scheduled_start_at"], orig["scheduled_start_at"])

    def test_substitute_requires_original_cancelled_by_teacher(self):
        c = fresh_client()
        orig = rules.create_session(c, session_row())
        with self.assertRaises(rules.InvalidTransition):
            rules.substitute_session_row(orig, "t2")


class StateMachine(unittest.TestCase):
    def test_transition_table_matches_sr_7_4(self):
        t = rules.ALLOWED_TRANSITIONS
        self.assertEqual(t["scheduled"], {"checked_in", "cancelled_by_parent_late", "cancelled_by_parent_ontime", "cancelled_by_teacher", "no_show"})
        self.assertEqual(t["checked_in"], {"checked_out", "incomplete"})
        self.assertEqual(t["checked_out"], {"awaiting_confirmation"})
        self.assertEqual(t["awaiting_confirmation"], {"confirmed", "disputed"})
        self.assertEqual(t["disputed"], {"confirmed", "void"})
        self.assertEqual(t["incomplete"], {"confirmed", "void"})
        for terminal in ("confirmed", "void", "cancelled_by_parent_late", "cancelled_by_parent_ontime", "cancelled_by_teacher", "no_show"):
            self.assertEqual(t[terminal], set(), terminal)
        self.assertEqual(set(t), set(SR_7_4_STATES))

    def test_every_non_listed_transition_is_rejected(self):
        for a in SR_7_4_STATES:
            for b in SR_7_4_STATES:
                if b in rules.ALLOWED_TRANSITIONS[a]:
                    rules.check_transition(a, b)
                else:
                    with self.assertRaises(rules.InvalidTransition, msg=f"{a}->{b}"):
                        rules.check_transition(a, b)

    def test_unknown_state_rejected(self):
        with self.assertRaises(rules.InvalidTransition):
            rules.check_transition("scheduled", "teleported")

    def test_cancelled_late_vs_ontime(self):  # edge: cancellation timing
        self.assertEqual(rules.parent_cancellation_status(True), "cancelled_by_parent_late")
        self.assertEqual(rules.parent_cancellation_status(False), "cancelled_by_parent_ontime")
        self.assertIn("is_late", cols("cancellation"))
        self.assertEqual(cols("cancellation")["by_role"]["elements"], ["parent", "teacher", "admin"])

    def test_is_late_threshold_uses_utc_instants(self):
        start = "2026-12-01T06:00:00.000+00:00"
        self.assertTrue(rules.is_late_cancellation(start, "2026-12-01T05:00:00.000+00:00", notice_hours=24))
        self.assertFalse(rules.is_late_cancellation(start, "2026-11-30T05:59:59.000+00:00", notice_hours=24))
        self.assertFalse(rules.is_late_cancellation(start, "2026-11-30T06:00:00.000+00:00", notice_hours=24))  # exactly 24 h is on time
        self.assertTrue(rules.is_late_cancellation(start, "2026-11-30T06:00:01.000+00:00", notice_hours=24))
        self.assertTrue(rules.is_late_cancellation(start, "2026-11-30T09:00:00.000+03:00", notice_hours=24) is False)

    def test_slot_released_states(self):
        for s in ("cancelled_by_parent_late", "cancelled_by_parent_ontime", "cancelled_by_teacher", "no_show", "void"):
            self.assertIsNone(rules.with_session_keys(session_row(status=s))["teacher_slot_key"])
        for s in ("scheduled", "checked_in", "confirmed", "disputed", "incomplete"):
            self.assertIsNotNone(rules.with_session_keys(session_row(status=s))["teacher_slot_key"])

    def test_transition_patch_updates_status_and_slot_key(self):
        s = session_row()
        p = rules.transition_patch(s, "checked_in", T1)
        self.assertEqual((p["status"], p["status_changed_at"]), ("checked_in", T1))
        self.assertEqual(p["teacher_slot_key"], s["teacher_slot_key"])
        with self.assertRaises(rules.InvalidTransition):
            rules.transition_patch(s, "confirmed", T1)


class Convention(unittest.TestCase):
    def test_domain_file_exists_and_is_generated_into_config_and_erd(self):
        self.assertTrue((APPWRITE_DIR / "schema" / "sessions.json").exists())
        self.assertEqual(sb.check(), [])
        self.assertEqual(sb.check_erd(), [])
        erd = (APPWRITE_DIR.parent / "docs" / "erd" / "sessions.md").read_text()
        for t in REQUIRED:
            self.assertIn(t, erd)

    def test_no_personal_data_defaults(self):
        import re
        text = (APPWRITE_DIR / "schema" / "sessions.json").read_text()
        self.assertNotRegex(text, r"@[a-z]+\.[a-z]+")
        self.assertNotRegex(text, r"\+?256\d{9}")

    def test_notes_for_every_table_and_overlap_rule_documented(self):
        for t in IDS:
            self.assertIn("note", DOMAIN["meta"][t])
        self.assertIn("overlap", DOMAIN["meta"]["session"]["note"].lower())
        note = DOMAIN["meta"]["session"]["note"]
        self.assertIn("different starts", note)  # finding 4: residual overlap risk is in the schema notes
        self.assertIn("serialise", note)
        readme = (APPWRITE_DIR / "README.md").read_text()
        self.assertIn("different starts", readme)
        self.assertIn("recompute", readme)  # finding 1: restore rule
        self.assertIn("soft_restore_patch", readme)


class Migration(unittest.TestCase):
    MIGRATIONS = str(APPWRITE_DIR / "migrations")

    def run_migrations(self, client):
        out = io.StringIO()
        result = migrate.run(client, "staging", self.MIGRATIONS, out=out, sleep=lambda s: None)
        return result, out.getvalue()

    def sessions_version(self):
        names = sorted(p.name for p in (APPWRITE_DIR / "migrations").glob("*_sessions_schema.py"))
        self.assertEqual(len(names), 1)
        return int(names[0][:4])

    def test_migration_script_exists_and_versions_are_unique(self):
        v = self.sessions_version()
        versions = [int(p.name[:4]) for p in (APPWRITE_DIR / "migrations").glob("[0-9]*.py")]
        self.assertEqual(versions.count(v), 1)
        self.assertGreater(v, 2)

    def test_applies_to_a_fresh_project(self):
        client = FakeAppwriteClient()
        result, out = self.run_migrations(client)
        self.assertEqual(result.exit_code, 0, out)
        self.assertIn(self.sessions_version(), result.applied)
        for tid in IDS:
            self.assertEqual(set(client.tables[tid]["columns"]), set(cols(tid)), tid)
            self.assertEqual(set(client.tables[tid]["indexes"]), {i["key"] for i in TABLES[tid]["indexes"]}, tid)
            self.assertIs(client.specs[tid]["rowSecurity"], True)
            self.assertEqual(client.specs[tid]["$permissions"], [])

    def test_rerun_is_a_noop(self):
        client = FakeAppwriteClient()
        self.run_migrations(client)
        before = (copy.deepcopy(client.tables), len(client.log), copy.deepcopy(client.specs))
        result, out = self.run_migrations(client)
        self.assertEqual(result.applied, [])
        self.assertIn(self.sessions_version(), result.skipped)
        self.assertEqual((client.tables, len(client.log), client.specs), before)

    def test_async_processing_semantics(self):
        client = FakeAppwriteClient(polls_until_available=3)
        result, out = self.run_migrations(client)
        self.assertEqual(result.exit_code, 0, out)
        for tid in IDS:
            self.assertTrue(all(s == "available" for s in client.column_statuses(tid).values()))
            self.assertTrue(all(s == "available" for s in client.index_statuses(tid).values()))

    def test_partial_resume_converges(self):
        client = FakeAppwriteClient()
        sb.apply_domain(client, "core")
        client.ensure_table(TABLES["plan"])  # a half-applied earlier run
        result, out = self.run_migrations(client)
        self.assertEqual(result.exit_code, 0, out)
        self.assertTrue(set(IDS) <= set(client.tables))

    def test_migration_inserts_no_rows(self):
        text = next((APPWRITE_DIR / "migrations").glob("*_sessions_schema.py")).read_text()
        self.assertNotIn("create_row", text)


class DiffLive(unittest.TestCase):
    def test_diff_with_empty_snapshot_lists_repo_only_tables(self):
        import diff_live
        report = diff_live.diff(DOMAINS, {"databases": [{"id": "main", "tables": []}]}, only=list(REQUIRED))
        self.assertEqual(set(report["tables_repo_only"]), REQUIRED)


if __name__ == "__main__":
    unittest.main()
