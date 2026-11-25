"""Mutation tests: break one key invariant at a time and prove the checks (which the normal suite
also runs on the real code) fail. If a mutation survives, the suite has a hole."""

from __future__ import annotations

import pytest

import hs_core.ledger.appwrite_store as aw
import hs_core.ledger.memory as mem
import hs_core.ledger.service as service
import ledger_checks as checks
from hs_core.append_only import AppendOnlyRepository
from hs_core.repository import Conflict


@pytest.mark.parametrize("check", checks.ALL, ids=lambda c: c.__name__)
def test_checks_pass_on_the_real_code(check):
    check()


def _noop(*_a, **_k):
    return None


MUTATIONS = {
    "balance check removed": (service, "_assert_balanced", _noop, "check_unbalanced_is_rejected"),
    "amount validation removed": (
        service,
        "check_amount",
        lambda v, what="": v,
        "check_bad_amounts_are_rejected",
    ),
    "idempotency content check removed (appwrite store)": (
        aw,
        "differences",
        lambda *_: [],
        "check_same_key_different_content_conflicts",
    ),
    "idempotency content check removed (memory store)": (
        mem,
        "differences",
        lambda *_: [],
        "check_same_key_different_content_conflicts",
    ),
    "seal skipped": (
        aw.AppwriteLedgerStore,
        "_seal",
        lambda self, tx: True,
        "check_posted_transaction_is_visible",
    ),
    "readers count unsealed lines": (
        aw.AppwriteLedgerStore,
        "_sealed",
        lambda self, ids, known, unsealed: known.update(ids),
        "check_unsealed_lines_are_invisible",
    ),
    "update allowed on entries": (
        AppendOnlyRepository,
        "update",
        lambda self, table, row_id, data, *a, **k: self._inner.update(table, row_id, data),
        "check_entries_are_append_only",
    ),
    "delete allowed on entries": (
        AppendOnlyRepository,
        "delete",
        lambda self, table, row_id, *a, **k: self._inner.delete(table, row_id),
        "check_entries_are_append_only",
    ),
}


@pytest.mark.parametrize("name", MUTATIONS)
def test_each_mutation_makes_its_check_fail(name, monkeypatch):
    target, attr, replacement, check_name = MUTATIONS[name]
    check = getattr(checks, check_name)
    check()  # green before the mutation
    monkeypatch.setattr(target, attr, replacement)
    with pytest.raises(AssertionError):
        check()


def test_replay_that_rewrites_instead_of_returning_the_original_is_caught(monkeypatch):
    """Idempotency removed entirely: every post appends again under a fresh id."""
    import itertools

    counter = itertools.count()
    orig = service.transaction_id_for
    monkeypatch.setattr(service, "transaction_id_for", lambda key: orig(f"{key}#{next(counter)}"))
    with pytest.raises((AssertionError, Conflict)):  # the unique index is a second line of defence
        checks.check_replay_does_not_double_count()
