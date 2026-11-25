"""N01 (#126): device_token handling per M04's upsert rule."""

import hashlib

from notify_helpers import Rig

TOK = "fcm-token-AAAA-secret"


def h(t):
    return hashlib.sha256(t.encode()).hexdigest()


def rows(rig):
    return rig.repo.list("device_token")


def test_register_inserts_hash_and_per_row_read_permission():
    rig = Rig()
    rig.devices.register("u1", "parent", "android", TOK)
    (r,) = rows(rig)
    assert r["token_hash"] == h(TOK) and r["user_id"] == "u1" and r.get("revoked_at") is None
    assert r["$permissions"] == ['read("user:u1")']
    assert [t.token for t in rig.devices.active_tokens("u1")] == [TOK]


def test_same_user_reregister_reuses_row_and_clears_revoked():
    rig = Rig()
    rig.devices.register("u1", "parent", "android", TOK)
    rig.devices.unregister("u1", TOK)
    assert rig.devices.active_tokens("u1") == []
    rig.clock.advance(minutes=5)
    rig.devices.register("u1", "parent", "android", TOK)
    assert len(rows(rig)) == 1 and rows(rig)[0]["revoked_at"] is None
    assert len(rig.devices.active_tokens("u1")) == 1


def test_shared_device_token_moves_to_the_new_user_and_never_two_live_rows():
    rig = Rig()
    rig.devices.register("u1", "parent", "android", TOK)
    rig.devices.register("u2", "teacher", "android", TOK)
    (r,) = rows(rig)
    assert r["user_id"] == "u2" and r["$permissions"] == ['read("user:u2")']
    assert rig.devices.active_tokens("u1") == []
    assert len(rig.devices.active_tokens("u2")) == 1


def test_logout_by_previous_owner_does_not_revoke_the_new_owners_token():
    rig = Rig()
    rig.devices.register("u1", "parent", "android", TOK)
    rig.devices.register("u2", "parent", "android", TOK)
    rig.devices.unregister("u1", TOK)
    assert len(rig.devices.active_tokens("u2")) == 1


def test_concurrent_register_of_one_token_leaves_one_row():
    import threading

    rig = Rig()
    ts = [
        threading.Thread(target=rig.devices.register, args=(f"u{i % 3}", "parent", "ios", TOK))
        for i in range(30)
    ]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(rows(rig)) == 1


def test_invalid_input_is_rejected():
    import pytest

    rig = Rig()
    for args in [
        ("u1", "parent", "android", ""),
        ("u1", "nope", "android", TOK),
        ("u1", "parent", "toaster", TOK),
        ("", "parent", "android", TOK),
        ("u1", "parent", "android", "x" * 1025),
    ]:
        with pytest.raises(ValueError):
            rig.devices.register(*args)


def test_token_never_appears_in_logs(caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    rig = Rig()
    rig.devices.register("u1", "parent", "android", TOK)
    rig.devices.unregister("u1", TOK)
    assert TOK not in "\n".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
