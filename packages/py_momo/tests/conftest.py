import pytest

from py_momo.fake import FakeMomo


@pytest.fixture
def fake():
    return FakeMomo()
