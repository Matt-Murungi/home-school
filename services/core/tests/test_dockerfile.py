import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
DF = (ROOT / "Dockerfile").read_text()


def test_multi_stage():
    assert len(re.findall(r"^FROM ", DF, re.M)) >= 2


def test_non_root_user_is_last_user():
    users = re.findall(r"^USER\s+(\S+)", DF, re.M)
    assert users and users[-1] not in {"root", "0"}


def test_no_secrets_in_env_or_arg():
    for line in re.findall(r"^(?:ENV|ARG)\s+.*$", DF, re.M):
        assert not re.search(r"KEY|SECRET|TOKEN|PASSWORD", line, re.I), line


def test_healthcheck_present():
    assert re.search(r"^HEALTHCHECK", DF, re.M)
    assert "/health" in DF


def test_no_copy_of_env_files_and_dockerignore():
    assert ".env" not in re.sub(r"\.env\.example", "", DF)
    ign = (ROOT / ".dockerignore").read_text().split()
    assert ".env" in ign and ".venv" in ign and "tests" in ign


def test_pinned_python_312_base():
    assert re.search(r"^FROM python:3\.12", DF, re.M)
