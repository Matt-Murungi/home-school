"""H05 (#134): mutation tests. Each deliberately broken variant of the settings code must FAIL the
contract below (which the real implementation passes). Weaken the code in one of these ways and the
suite goes red."""

from datetime import timedelta

import pytest

import hs_core.app_settings.service as svc_mod
from audit_helpers import FaultyRepository, StepClock, build
from hs_core.app_settings import AppSettings, InvalidValue
from hs_core.app_settings.model import RevisionConflict
from settings_helpers import AUTO, GRACE, REASON, ManualClock, ctx, edit, history


class RawWriter:
    """Mutant: writes go straight to the raw repository, no audit (accepts and drops ctx)."""

    def __init__(self, raw):
        self.raw = raw

    def create(self, table, row_id, data, permissions=None, *, ctx=None, action=None, reason=None):
        return self.raw.create(table, row_id, data, permissions)

    def update(self, table, row_id, data, permissions=None, *, ctx=None, action=None, reason=None):
        return self.raw.update(table, row_id, data, permissions)

    def get(self, *a, **k):
        return self.raw.get(*a, **k)

    def list(self, *a, **k):
        return self.raw.list(*a, **k)


def real(raw=None, clock=None):
    clock = clock or ManualClock()
    raw, _s, audited, _ = build(raw or FaultyRepository(), StepClock())
    return raw, AppSettings(audited, clock=clock), clock


def no_audit(raw=None, clock=None):
    clock = clock or ManualClock()
    raw = raw or FaultyRepository()
    return raw, AppSettings(RawWriter(raw), clock=clock), clock


class NoRevisionCheck(AppSettings):
    def _prepare(self, key, expected_revision):
        return super()._prepare(
            key, self._history_desc(key)[0]["revision"] if self._history_desc(key) else 0
        )


class Retroactive(AppSettings):
    def _effective_instant(self, d, requested):
        return requested or self.now()  # no clamp, no "past" check


class FailsOpenToZero(AppSettings):
    def get(self, key):
        try:
            self._audited.list("app_setting")
        except Exception:  # noqa: BLE001
            return 0
        return super().get(key)


class NoCrossCheck(AppSettings):
    def _check_constraints(self, *a, **k):
        return None


class NoProjectionRepair(AppSettings):
    def reconcile(self, ctx=None):
        return {"activated": 0, "repaired": 0}


def build_variant(cls):
    def make(raw=None, clock=None):
        clock = clock or ManualClock()
        raw, _s, audited, _ = build(raw or FaultyRepository(), StepClock())
        return raw, cls(audited, clock=clock), clock

    return make


def contract(make):
    """Raises AssertionError on a defect."""
    # 1. every change is audited, with the reason
    raw, svc, _c = make()
    edit(svc, AUTO, 24, 0)
    intents = [r for r in raw.list("audit_log") if r["entity"] == "app_setting_change"]
    assert intents and intents[0]["reason"] == REASON, "change is not audited with its reason"

    # 2. bounds are enforced
    raw, svc, _c = make()
    for bad in (-1, 0, 10**9):
        try:
            edit(svc, AUTO, bad, 0)
        except InvalidValue:
            continue
        raise AssertionError(f"out-of-range value {bad} accepted")

    # 3. a change never takes effect in the past
    raw, svc, clock = make()
    try:
        edit(svc, AUTO, 24, 0, effective_at=clock() - timedelta(days=3))
    except InvalidValue:
        pass
    else:
        raise AssertionError("retroactive change accepted")

    # 4. a stale revision is refused
    raw, svc, _c = make()
    edit(svc, AUTO, 24, 0)
    try:
        edit(svc, AUTO, 36, 0)
    except RevisionConflict:
        pass
    else:
        raise AssertionError("stale revision accepted")
    assert len(history(raw, AUTO)) == 1

    # 5. a store outage reads as the default, never zero
    raw, svc, _c = make()
    raw.available = False
    assert svc.get(AUTO) == 48, "outage did not fall back to the default"

    # 6. cross-setting rules are enforced
    raw, svc, _c = make()
    try:
        edit(svc, GRACE, 14, 0)
    except InvalidValue:
        pass
    else:
        raise AssertionError("grace >= cancel limit accepted")

    # 7. a crash between the history row and the value row is repaired
    from audit_helpers import Crash

    raw = FaultyRepository()
    raw, svc, clock = make(raw)
    raw.arm(4, "before", Crash())  # intent, history, intent, [value row]
    try:
        svc.change(AUTO, 24, reason=REASON, expected_revision=0, ctx=ctx())
    except Crash:
        pass
    raw.disarm()
    _r, fresh, _c = make(raw, clock)
    fresh.reconcile(ctx=ctx("system"))
    assert fresh.get(AUTO) == 24, "history ahead of the value row was not repaired"


def test_the_real_implementation_passes_the_contract():
    contract(real)


@pytest.mark.parametrize(
    "name,make",
    [
        ("skips the audit", no_audit),
        ("no revision check", build_variant(NoRevisionCheck)),
        ("retroactive effect", build_variant(Retroactive)),
        ("fails open to zero", build_variant(FailsOpenToZero)),
        ("no cross-setting check", build_variant(NoCrossCheck)),
        ("no projection repair", build_variant(NoProjectionRepair)),
    ],
)
def test_each_mutant_fails_the_contract(name, make):
    with pytest.raises(AssertionError):
        contract(make)


def test_the_mutant_that_skips_bounds_fails_the_contract(monkeypatch):
    monkeypatch.setattr(svc_mod, "validate_value", lambda d, v: v)
    with pytest.raises(AssertionError):
        contract(real)
