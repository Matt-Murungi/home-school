import pytest
from fastapi.testclient import TestClient

from hs_core.auth import FakeTokenVerifier
from hs_core.main import create_app
from hs_core.repository import InMemoryRepository
from hs_core.settings import Settings


@pytest.fixture
def repo():
    return InMemoryRepository()


@pytest.fixture
def verifier():
    v = FakeTokenVerifier()
    v.add("parent-token", user_id="u_parent", labels=["parent"])
    v.add("admin-token", user_id="u_admin", labels=["opsadmin", "superadmin"])
    v.add("teacher-token", user_id="u_teacher", labels=["teacher"])
    v.add("unlabelled-token", user_id="u_none", labels=[])
    v.add("expired-token", user_id="u_old", labels=["parent"], expired=True)
    return v


@pytest.fixture
def settings():
    return Settings(_env_file=None, hs_env="dev", app_version="1.2.3", git_sha="abc1234")


@pytest.fixture
def client(settings, repo, verifier):
    return TestClient(create_app(settings=settings, repository=repo, verifier=verifier))
