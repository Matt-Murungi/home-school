"""Mutation tests (C03): every behaviour the ticket promises is broken on purpose and the matching
check must turn False. A mutant that survives means the suite would not notice the regression."""

import pytest

import retention_checks as chk
from hs_core.retention import holds, periods, purger, run
from hs_core.retention.policy import POLICY  # noqa: F401
from retention_helpers import NOW  # noqa: F401


@pytest.mark.parametrize("name", sorted(chk.ALL))
def test_baseline_every_check_holds(name):
    assert chk.ALL[name]() is True, name


# mutant name -> (patch function, the checks that must now fail)
def _ignore_holds(mp):
    mp.setattr(holds.HoldChecker, "held_ids", lambda self, rule, rows: {})
    mp.setattr(holds.HoldChecker, "is_held", lambda self, rule, row: False)


def _parent_before_child(mp):
    real = purger.RetentionPurger.plan_delete

    def reversed_plan(self, rule, row, now, depth=0):
        plan = real(self, rule, row, now, depth)
        plan.steps.reverse()
        return plan

    mp.setattr(purger.RetentionPurger, "plan_delete", reversed_plan)


def _offset_paging(mp):
    class Q(purger.Query):
        @staticmethod
        def cursor_after(c):
            return purger.Query.offset(100)

    mp.setattr(purger, "Query", Q)


def _purge_inside_retention(mp):
    mp.setattr(purger, "is_due", lambda *a, **k: True)


def _off_by_one_boundary(mp):
    real = periods.is_due

    def ge(clock_value, period, now):
        exp = periods.expiry_date(clock_value, period)
        return exp is not None and periods.kampala_date(now) >= exp

    mp.setattr(purger, "is_due", ge)
    assert real


def _utc_date_instead_of_kampala(mp):
    mp.setattr(periods, "kampala_date", lambda dt: dt.astimezone(periods.UTC).date())


def _skip_dry_run(mp):
    real = purger.RetentionPurger._expire_row

    def ignore(self, rule, row, now, stats):
        self.config.enforce = True
        try:
            return real(self, rule, row, now, stats)
        finally:
            self.config.enforce = False

    mp.setattr(purger.RetentionPurger, "_expire_row", ignore)
    real_an = purger.RetentionPurger._anonymise_row

    def ignore_an(self, rule, row, kinds, stats):
        self.config.enforce = True
        try:
            return real_an(self, rule, row, kinds, stats)
        finally:
            self.config.enforce = False

    mp.setattr(purger.RetentionPurger, "_anonymise_row", ignore_an)


def _default_enforce_on(mp):
    mp.setattr(purger.PurgeConfig, "enforce", True, raising=False)
    # dataclass default lives in __init__: rebuild the class default through a subclass swap
    import dataclasses

    mp.setattr(
        purger, "PurgeConfig", dataclasses.make_dataclass("PurgeConfig", [("enforce", bool, True)])
    )
    mp.setattr(chk, "PurgeConfig", purger.PurgeConfig)


def _row_before_file(mp):
    real = purger.RetentionPurger.erase_row

    def row_first(self, rule, row, now, stats):
        self.repo.delete(rule.table, row["$id"])
        for f in rule.files:
            if row.get(f.column):
                self.files.delete(f.bucket, row[f.column])
        return 1

    mp.setattr(purger.RetentionPurger, "erase_row", row_first)
    assert real


def _no_restrict(mp):
    real = purger.RetentionPurger.plan_delete

    def lax(self, rule, row, now, depth=0):
        return purger._Plan(steps=[(rule, row)])

    mp.setattr(purger.RetentionPurger, "plan_delete", lax)
    assert real


def _delete_money(mp):
    real = purger.RetentionPurger.anonymise_patch

    def deleter(self, rule, row, kinds):
        self.repo.delete(rule.table, row["$id"])
        return {}

    mp.setattr(purger.RetentionPurger, "anonymise_patch", deleter)
    assert real


def _ledger_purgeable(mp):
    mp.setattr(purger.RetentionPurger, "skip_reason_static", staticmethod(lambda rule: None))
    mp.setattr(purger.RetentionPurger, "skip_reason", lambda self, rule: None)
    mp.setattr(purger.RetentionPurger, "_check_append_only", lambda self, rule, row, now: None)


def _append_only_without_switch_or_floor(mp):
    mp.setattr(purger.RetentionPurger, "_check_append_only", lambda self, rule, row, now: None)


def _no_blast_limit(mp):
    mp.setattr(purger.RetentionPurger, "blast_check", lambda self, rule, now: None)


def _no_guard(mp):
    mp.setattr(run, "purge_guard", lambda repo, now: None)


def _no_lease(mp):
    mp.setattr(run, "acquire_lease", lambda repo, owner, now, lease_s, mode: 1)


def _no_quarantine(mp):
    mp.setattr(purger, "SOFT_TABLES", frozenset())


def _count_vanished_as_deleted(mp):
    real = purger.RetentionPurger.__init__

    def init(self, *a, **k):
        real(self, *a, **k)
        self._vanished = _AlwaysFalse()

    mp.setattr(purger.RetentionPurger, "__init__", init)


class _AlwaysFalse(dict):
    def pop(self, k, d=None):
        return False


MUTANTS = {
    "ignore holds": (_ignore_holds, {"held_rows_survive"}),
    "delete parent before child": (_parent_before_child, {"children_deleted_before_parent"}),
    "offset paging": (_offset_paging, {"no_offset_paging"}),
    "purge inside retention": (_purge_inside_retention, {"nothing_purged_inside_retention", "boundary_is_kept_and_next_day_goes"}),
    "off by one at the boundary": (_off_by_one_boundary, {"boundary_is_kept_and_next_day_goes"}),
    "UTC date instead of Kampala date": (_utc_date_instead_of_kampala, {"kampala_date_not_utc_date"}),
    "skip dry run": (_skip_dry_run, {"dry_run_writes_nothing"}),
    "default enforce on": (_default_enforce_on, {"default_config_is_dry_run"}),
    "row before file": (_row_before_file, {"files_deleted_before_row"}),
    "no restrict": (_no_restrict, {"restrict_blocks_parent"}),
    "money deleted": (_delete_money, {"money_is_never_deleted_and_is_minimised"}),
    "ledger purgeable": (_ledger_purgeable, {"ledger_never_purged"}),
    "append-only without switch or floor": (_append_only_without_switch_or_floor, {"append_only_needs_the_switch_and_the_floor"}),
    "no blast limit": (_no_blast_limit, {"blast_radius_refuses"}),
    "no purge guard": (_no_guard, {"guard_refuses_a_clock_before_the_last_run", "guard_refuses_absurd_years"}),
    "no lease": (_no_lease, {"one_of_fifty_runs_wins"}),
    "no quarantine": (_no_quarantine, {"quarantine_before_hard_delete"}),
    "double counting vanished rows": (_count_vanished_as_deleted, {"concurrent_purge_counts_once"}),
}  # fmt: skip


@pytest.mark.parametrize("mutant", sorted(MUTANTS))
def test_mutant_is_killed(mutant, monkeypatch):
    patch, must_fail = MUTANTS[mutant]
    with monkeypatch.context() as mp:
        patch(mp)
        survivors = []
        for name in sorted(must_fail):
            try:
                ok = chk.ALL[name]()
            except Exception:  # noqa: BLE001 - a crash is also a detected regression
                ok = False
            if ok is True:
                survivors.append(name)
        assert not survivors, f"mutant {mutant!r} survived: {survivors}"
