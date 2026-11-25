from unittest.mock import MagicMock

import pytest
from appwrite.exception import AppwriteException

from hs_core.auth import AppwriteJwtVerifier, AuthUnavailable, InvalidToken


def verifier(account):
    client = MagicMock()
    v = AppwriteJwtVerifier(
        endpoint="https://x/v1", project_id="p", account_factory=lambda jwt: (client, account)
    )
    return v


def test_valid_jwt_maps_labels():
    acct = MagicMock()
    acct.get.return_value = {"$id": "u1", "labels": ["teacher", "weird_label"], "status": True}
    p = verifier(acct).verify("tok")
    assert p.user_id == "u1" and p.roles == ["teacher"]


def test_401_is_invalid_token():
    acct = MagicMock()
    acct.get.side_effect = AppwriteException("expired", 401)
    with pytest.raises(InvalidToken):
        verifier(acct).verify("tok")


def test_blocked_user_rejected():
    acct = MagicMock()
    acct.get.return_value = {"$id": "u1", "labels": ["parent"], "status": False}
    with pytest.raises(InvalidToken):
        verifier(acct).verify("tok")


def test_5xx_and_network_are_unavailable():
    acct = MagicMock()
    acct.get.side_effect = AppwriteException("x", 500)
    with pytest.raises(AuthUnavailable):
        verifier(acct).verify("tok")
    acct.get.side_effect = OSError("down")
    with pytest.raises(AuthUnavailable):
        verifier(acct).verify("tok")


def test_oversized_token_rejected_without_calling_appwrite():
    acct = MagicMock()
    with pytest.raises(InvalidToken):
        verifier(acct).verify("a" * 5000)
    acct.get.assert_not_called()
