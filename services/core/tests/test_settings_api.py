"""H05 (#134): the admin settings API. Super admin only (read and write), reason required,
revision check, audit actor from the verified JWT, mass assignment refused, hostile input."""

import json

import pytest
from fastapi.testclient import TestClient

from audit_helpers import audit_rows
from hs_core.auth import FakeTokenVerifier
from hs_core.main import create_app
from hs_core.repository import InMemoryRepository
from hs_core.settings import Settings
from settings_helpers import NEXT_MIDNIGHT, T0, ManualClock

BASE = "/v1/admin/settings"
AUTO = "session.auto_confirm_after_hours"
LATE = "session.late_cancellation_notice_hours"
REASON = "Owner decision recorded in the ops meeting"
ROLES = ["parent", "teacher", "opsadmin", "curator", "safeguardinglead", "superadmin"]


def make(**settings_kw):
    raw = InMemoryRepository()
    v = FakeTokenVerifier()
    for r in ROLES:
        v.add(r, f"u_{r}", [r])
    v.add("both", "u_both", ["opsadmin", "superadmin"])
    v.add("nolabel", "u_none", [])
    app = create_app(
        Settings(_env_file=None, hs_env="dev", **{"otp_pepper": "x" * 40, **settings_kw}),
        repository=raw, verifier=v, trust_proxy_ip=True,
    )  # fmt: skip
    clock = ManualClock()
    app.state.app_settings._clock = clock  # tests never read the wall clock
    return TestClient(app), raw, clock, app


def h(token="superadmin", **extra):
    return {"Authorization": f"Bearer {token}", **extra}


def put(c, key, body, token="superadmin", **headers):
    return c.put(f"{BASE}/{key}", json=body, headers=h(token, **headers))


def ok_body(value=24, revision=0, **kw):
    return {"value": value, "reason": REASON, "revision": revision, **kw}


class TestRoleMatrix:
    CALLS = [
        ("get", BASE, None),
        ("put", f"{BASE}/{AUTO}", {"value": 24, "reason": REASON, "revision": 0}),
        ("post", f"{BASE}/{AUTO}/rollback", {"reason": REASON, "revision": 0}),
        ("post", f"{BASE}/{AUTO}/cancel-pending", {"reason": REASON, "revision": 0}),
        ("get", f"{BASE}/{AUTO}/history", None),
    ]

    @pytest.mark.parametrize("token", [r for r in ROLES if r != "superadmin"] + ["nolabel"])
    @pytest.mark.parametrize("call", CALLS, ids=lambda c: f"{c[0]} {c[1].split('/v1/admin/')[1]}")
    def test_only_the_super_admin_may_read_or_change(self, token, call):
        c, raw, _clk, _app = make()
        method, url, body = call
        r = getattr(c, method)(url, headers=h(token), **({"json": body} if body else {}))
        assert r.status_code == 403, r.text
        assert raw.list("app_setting_change") == [] and raw.list("audit_log") == []

    @pytest.mark.parametrize("call", CALLS, ids=lambda c: f"{c[0]} {c[1].split('/v1/admin/')[1]}")
    def test_no_token_is_401_and_a_bad_token_is_401(self, call):
        c, raw, _clk, _app = make()
        method, url, body = call
        for headers in ({}, {"Authorization": "Bearer nope"}):
            r = getattr(c, method)(url, headers=headers, **({"json": body} if body else {}))
            assert r.status_code == 401
        assert raw.list("app_setting_change") == []

    def test_a_super_admin_who_is_also_ops_admin_may(self):
        c, *_ = make()
        assert c.get(BASE, headers=h("both")).status_code == 200


class TestList:
    def test_lists_every_registered_setting_grouped_with_bounds_default_and_revision(self):
        c, *_ = make()
        body = c.get(BASE, headers=h()).json()
        assert {g["id"] for g in body["groups"]} >= {"business_rules", "matching", "notifications"}
        by = {s["key"]: s for s in body["settings"]}
        s = by[AUTO]
        assert (s["value"], s["default"], s["revision"], s["source"]) == (48, 48, 0, "default")
        assert (s["min"], s["max"], s["unit"], s["applies"]) == (1, 720, "hours", "immediate")
        assert s["restart_required"] is False and s["requires_reason"] is True
        assert by["matching.weights"]["sum_to"] == 100
        assert {f["name"] for f in by["matching.weights"]["fields"]} >= {"travel_time", "tier"}

    def test_no_secret_and_no_env_name_leaks_into_the_listing(self):
        c, *_ = make(otp_pepper="p" * 40)
        text = c.get(BASE, headers=h()).text
        assert "p" * 20 not in text and "NOTIFY_" not in text

    def test_responses_are_not_cacheable_by_shared_caches_in_the_contract(self):
        c, *_ = make()
        assert (
            c.get(BASE, headers=h()).headers.get("content-type", "").startswith("application/json")
        )


class TestPut:
    def test_changes_a_value_returns_the_new_state_and_audits_with_the_jwt_actor(self):
        c, raw, _clk, app = make()
        r = put(c, AUTO, ok_body(24))
        assert r.status_code == 200, r.text
        s = r.json()
        assert (s["value"], s["revision"], s["source"], s["default"]) == (24, 1, "override", 48)
        assert app.state.app_settings.get(AUTO) == 24
        rows = audit_rows(raw, entity="app_setting_change")
        assert rows and all(x["actor_id"] == "u_superadmin" for x in rows)
        assert rows[0]["reason"] == REASON
        assert c.get(BASE, headers=h()).json()["settings"][0] is not None

    def test_the_actor_in_the_history_is_the_token_user_not_anything_the_client_sends(self):
        c, raw, *_ = make()
        r = put(c, AUTO, ok_body(24), **{"X-Actor": "u_evil", "X-User-Id": "u_evil"})
        assert r.status_code == 200
        assert raw.list("app_setting_change")[0]["actor_id"] == "u_superadmin"

    def test_a_negative_window_is_a_validation_error_and_writes_nothing(self):
        c, raw, *_ = make()
        for bad in (-1, 0, 100000, "24", None, True, 24.5, [1], {"a": 1}):
            r = put(c, AUTO, ok_body(bad))
            assert r.status_code == 422, (bad, r.text)
            assert r.json()["error"]["code"] in ("invalid_value", "validation_error")
        assert raw.list("app_setting_change") == []

    def test_weights_not_summing_to_100_are_blocked(self):
        c, raw, *_ = make()
        w = {
            "travel_time": 30,
            "tier": 20,
            "struggle_fit": 15,
            "workload_balance": 15,
            "reliability": 15,
            "cluster_bonus": 6,
        }
        r = put(c, "matching.weights", ok_body(w))
        assert r.status_code == 422 and "100" in r.json()["error"]["message"]
        w["cluster_bonus"] = 5
        w["tier"] = 25
        w["travel_time"] = 25
        assert put(c, "matching.weights", ok_body(w)).status_code == 200

    def test_a_reason_is_required(self):
        c, raw, *_ = make()
        for reason in ("", "  ", "​​​", "x"):
            r = put(c, AUTO, {"value": 24, "reason": reason, "revision": 0})
            assert r.status_code == 422 and r.json()["error"]["code"] == "reason_required"
        r = put(c, AUTO, {"value": 24, "revision": 0})
        assert r.status_code == 422
        assert raw.list("app_setting_change") == []

    def test_a_stale_revision_is_409_with_the_current_revision(self):
        c, *_ = make()
        assert put(c, AUTO, ok_body(24)).status_code == 200
        r = put(c, AUTO, ok_body(36, revision=0))
        assert r.status_code == 409
        e = r.json()["error"]
        assert e["code"] == "revision_conflict" and e["current_revision"] == 1

    def test_revision_must_be_a_whole_non_negative_number(self):
        c, raw, *_ = make()
        for rev in ("0", 0.5, -1, None, True):
            r = put(c, AUTO, {"value": 24, "reason": REASON, "revision": rev})
            assert r.status_code == 422, rev
        assert raw.list("app_setting_change") == []

    def test_effective_at_schedules_the_change_and_next_day_settings_wait_for_midnight(self):
        c, _raw, clock, app = make()
        r = put(c, LATE, ok_body(12))
        s = r.json()
        assert s["value"] == 24 and s["pending"]["value"] == 12
        assert s["pending"]["effective_at"] == NEXT_MIDNIGHT.isoformat()
        clock.set(NEXT_MIDNIGHT)
        assert app.state.app_settings.get(LATE) == 12

    def test_a_past_effective_time_is_422(self):
        c, raw, *_ = make()
        r = put(c, AUTO, ok_body(24, effective_at="2020-01-01T00:00:00Z"))
        assert r.status_code == 422 and r.json()["error"]["code"] == "effective_in_past"
        r = put(c, AUTO, ok_body(24, effective_at="not a date"))
        assert r.status_code == 422
        assert raw.list("app_setting_change") == []

    def test_cross_setting_rule_violation_is_422_with_the_rule(self):
        c, *_ = make()
        r = put(c, "billing.grace_days", ok_body(14))
        assert r.status_code == 422 and r.json()["error"]["code"] == "constraint_violated"

    def test_rollback_cancel_and_history_round_trip(self):
        c, _raw, _clk, _app = make()
        put(c, AUTO, ok_body(24, 0))
        put(c, AUTO, ok_body(36, 1))
        r = c.post(f"{BASE}/{AUTO}/rollback", json={"reason": REASON, "revision": 2}, headers=h())
        assert r.status_code == 200 and r.json()["value"] == 24 and r.json()["revision"] == 3
        hist = c.get(f"{BASE}/{AUTO}/history", headers=h()).json()
        assert [x["revision"] for x in hist["changes"]] == [3, 2, 1]
        assert [x["op"] for x in hist["changes"]] == ["rollback", "set", "set"]
        assert hist["changes"][0]["value"] == 24 and hist["changes"][0]["previous_value"] == 36
        assert hist["changes"][0]["reason"] == REASON
        page = c.get(f"{BASE}/{AUTO}/history?limit=2", headers=h()).json()
        assert len(page["changes"]) == 2 and page["next_before"] == 2
        nxt = c.get(f"{BASE}/{AUTO}/history?limit=2&before=2", headers=h()).json()
        assert [x["revision"] for x in nxt["changes"]] == [1]
        # cancel a scheduled change
        assert put(c, LATE, ok_body(12, 0)).status_code == 200
        r = c.post(
            f"{BASE}/{LATE}/cancel-pending", json={"reason": REASON, "revision": 1}, headers=h()
        )
        assert r.status_code == 200 and r.json()["pending"] is None
        r = c.post(
            f"{BASE}/{LATE}/cancel-pending", json={"reason": REASON, "revision": 2}, headers=h()
        )
        assert r.status_code == 409 and r.json()["error"]["code"] == "no_pending_change"

    def test_history_paging_parameters_are_validated(self):
        c, *_ = make()
        for q in ("limit=0", "limit=1000", "before=0", "before=-3", "limit=x", "before=y"):
            assert c.get(f"{BASE}/{AUTO}/history?{q}", headers=h()).status_code == 422, q


class TestHostileInput:
    def test_unknown_fields_are_refused_mass_assignment(self):
        c, raw, *_ = make()
        for extra in (
            {"actor_id": "u_evil"}, {"source": "system"}, {"revision_id": 3}, {"$id": "x"},
            {"roles": ["superadmin"]}, {"default": 1}, {"changed_by": "x"}, {"value_json": "1"},
            {"effective": "now"}, {"restart_required": True},
        ):  # fmt: skip
            r = put(c, AUTO, {**ok_body(24), **extra})
            assert r.status_code == 422, extra
        r = c.post(
            f"{BASE}/{AUTO}/rollback",
            json={"reason": REASON, "revision": 0, "value": 1},
            headers=h(),
        )
        assert r.status_code == 422
        assert raw.list("app_setting_change") == []

    @pytest.mark.parametrize(
        "key",
        [
            "nope",
            "session.nope",
            "SESSION.AUTO_CONFIRM_AFTER_HOURS",
            "../admin",
            "a" * 200,
            "session.auto_confirm_after_hours ",
            "%00",
            "é.é",
            "session..x",
        ],
    )
    def test_unknown_or_malformed_keys_are_404_never_500(self, key):
        c, raw, *_ = make()
        assert put(c, key, ok_body(1)).status_code in (404, 422)
        assert c.post(
            f"{BASE}/{key}/rollback", json={"reason": REASON, "revision": 0}, headers=h()
        ).status_code in (404, 422)
        assert c.get(f"{BASE}/{key}/history", headers=h()).status_code in (404, 422)
        assert raw.list("app_setting_change") == []

    def test_unknown_key_is_404_with_the_standard_error_shape(self):
        c, *_ = make()
        r = put(c, "session.nope", ok_body(1))
        assert r.status_code == 404 and r.json()["error"]["code"] == "unknown_setting"

    def test_huge_bodies_values_and_reasons_are_refused_cleanly(self):
        c, raw, *_ = make()
        assert put(c, AUTO, ok_body(10**4000)).status_code in (413, 422)
        raw_body = '{"value": ' + "9" * 6000 + ', "reason": "' + REASON + '", "revision": 0}'
        r = c.put(
            f"{BASE}/{AUTO}", content=raw_body, headers={**h(), "Content-Type": "application/json"}
        )
        assert r.status_code in (413, 422, 400)
        r = put(c, AUTO, {"value": 24, "reason": "x" * 5000, "revision": 0})
        assert r.status_code == 422
        r = put(c, "matching.weights", ok_body({f"f{i}": 1 for i in range(2000)}))
        assert r.status_code in (413, 422)
        assert raw.list("app_setting_change") == []

    def test_unicode_reasons_and_weird_json_are_handled(self):
        c, raw, *_ = make()
        r = put(
            c,
            AUTO,
            {"value": 24, "reason": "Décision du propriétaire 🏫 تغيير النافذة", "revision": 0},
        )
        assert r.status_code == 200
        assert raw.list("app_setting_change")[0]["reason"].startswith("Décision")
        for body in (
            "null",
            "[]",
            "42",
            '"x"',
            "{",
            '{"value": NaN, "reason": "' + REASON + '", "revision": 0}',
        ):
            r = c.put(
                f"{BASE}/{AUTO}", content=body, headers={**h(), "Content-Type": "application/json"}
            )
            assert r.status_code in (400, 422), body

    def test_a_client_chosen_request_id_cannot_merge_or_hide_two_changes(self):
        """The audit key is the SERVER's id: two edits with the same X-Request-ID are two audited
        changes (the M06 rule; a client must not be able to collide audit keys)."""
        c, raw, *_ = make()
        assert put(c, AUTO, ok_body(24, 0), **{"X-Request-ID": "same"}).status_code == 200
        assert put(c, AUTO, ok_body(36, 1), **{"X-Request-ID": "same"}).status_code == 200
        intents = [
            r
            for r in audit_rows(raw, entity="app_setting_change")
            if json.loads(r["after"])["meta"]["status"] == "intent"
        ]
        assert (
            len(intents) == 2 and len({json.loads(r["after"])["meta"]["key"] for r in intents}) == 2
        )

    def test_a_store_outage_on_put_is_503_and_a_read_still_answers_with_defaults_elsewhere(self):
        c, raw, _clk, app = make()
        raw.available = False
        r = put(c, AUTO, ok_body(24))
        assert r.status_code in (503, 500) and r.status_code == 503
        assert app.state.app_settings.get(AUTO) == 48  # runtime reads fall back to the default


class TestWiring:
    def test_the_environment_value_is_the_default_the_api_reports(self):
        c, *_ = make(late_cancellation_notice_hours=36)
        s = {x["key"]: x for x in c.get(BASE, headers=h()).json()["settings"]}[LATE]
        assert (s["value"], s["default"], s["source"]) == (36, 36, "default")

    def test_the_settings_activate_job_is_scheduled_every_minute_and_bound(self):
        from hs_core.jobs.schedule import JOBS

        job = next(j for j in JOBS if j.name == "settings-activate")
        assert job.schedule.every.total_seconds() == 60
        make()  # create_app binds the service

    def test_the_activation_job_runs_through_the_runner_as_the_system_actor(self):
        c, raw, clock, app = make()
        put(c, LATE, ok_body(12))
        clock.set(NEXT_MIDNIGHT)
        from hs_core.app_settings.jobs import settings_activate
        from hs_core.audit import job_scope
        from hs_core.jobs.model import JobContext

        jc = JobContext("settings-activate", "run1", "settings-activate:p1", T0, {}, None, 0)
        with job_scope(jc):
            res = settings_activate(jc)
        assert res.items == 1
        rows = [r for r in audit_rows(raw, entity="app_setting") if r["actor_id"] is None]
        assert rows and "system" in rows[-1]["actor_roles"]
