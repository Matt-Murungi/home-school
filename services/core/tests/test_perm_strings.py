"""Permission strings and labels (M05 #40): clients hold read only; labels are exact."""

import pytest

from hs_core.permissions import policy
from hs_core.permissions.strings import (
    canonical,
    forbidden_in,
    parse,
    read_label,
    read_user,
)


def test_read_strings_have_the_appwrite_shape():
    assert read_user("abc123") == 'read("user:abc123")'
    assert read_label("opsadmin") == 'read("label:opsadmin")'


@pytest.mark.parametrize("bad", ["", "a b", 'x"y', "a" * 37, "-lead", None, 5])
def test_user_ids_that_could_inject_a_permission_are_rejected(bad):
    with pytest.raises(ValueError):
        read_user(bad)


@pytest.mark.parametrize(
    "label",
    [
        "ops_admin",
        "OpsAdmin",
        "opsadmin ",
        "OPSADMIN",
        "safeguarding_lead",
        "super_admin",
        "",
        "admin",
    ],
)
def test_underscore_and_case_variants_are_not_labels_and_grant_nothing(label):
    assert not policy.is_label(label)
    with pytest.raises(ValueError):
        read_label(label)
    assert policy.clean_labels([label, "teacher"]) == {"teacher"}


def test_the_seven_labels_are_alphanumeric_lowercase():
    assert set(policy.LABELS) == {
        "parent",
        "guardian",
        "teacher",
        "opsadmin",
        "curator",
        "safeguardinglead",
        "superadmin",
    }
    assert all(label.isalnum() and label.islower() for label in policy.LABELS)


def test_sr_role_names_map_to_labels_and_unknown_names_raise():
    assert policy.roles_to_labels(["ops_admin", "safeguarding_lead", "teacher"]) == [
        "opsadmin",
        "safeguardinglead",
        "teacher",
    ]
    for bad in (["ops-admin"], ["Teacher"], ["root"]):
        with pytest.raises(ValueError):
            policy.roles_to_labels(bad)


def test_labels_are_only_ever_set_from_the_checked_list():
    """The core service builds the list for users.updateLabels with roles_to_labels: a typo can
    neither grant nor silently drop a role (clients cannot set labels at all: server API only)."""
    assert policy.roles_to_labels(["opsadmin", "opsadmin"]) == ["opsadmin"]


def test_parse_round_trip_and_canonical_order():
    p = parse('read("label:opsadmin")')
    assert (p.action, p.role, p.kind) == ("read", "label:opsadmin", "label")
    assert canonical(['read("user:b")', 'read("user:a")', 'read("user:a")']) == (
        'read("user:a")',
        'read("user:b")',
    )
    with pytest.raises(ValueError):
        parse("read(user:a)")


@pytest.mark.parametrize(
    "perm,bad",
    [
        ('read("any")', True),
        ('read("guests")', True),
        ('read("users")', True),
        ('create("user:x")', True),
        ('update("user:x")', True),
        ('delete("label:opsadmin")', True),
        ('write("user:x")', True),
        ('read("team:x")', True),
        ("garbage", True),
        ('read("user:x")', False),
        ('read("label:opsadmin")', False),
    ],
)
def test_forbidden_entries(perm, bad):
    assert bool(forbidden_in([perm])) is bad


def test_users_read_is_allowed_only_when_the_row_is_published_catalogue_content():
    assert forbidden_in(['read("users")'], allow_users_read=True) == []
    assert forbidden_in(['read("any")'], allow_users_read=True) == ['read("any")']
