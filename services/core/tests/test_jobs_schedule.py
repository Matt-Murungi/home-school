"""One file lists every scheduled job (schedule.py); heartbeat sample job."""

import ast
import pathlib
from datetime import timedelta

from hs_core.jobs import schedule
from hs_core.jobs.model import Done, JobContext

SRC = pathlib.Path(schedule.__file__).parent


def test_every_job_is_listed_in_schedule_py_with_unique_names():
    names = [j.name for j in schedule.JOBS]
    assert len(names) == len(set(names)) and "heartbeat" in names


def test_heartbeat_runs_every_five_minutes_and_logs_a_line(caplog):
    hb = next(j for j in schedule.JOBS if j.name == "heartbeat")
    assert hb.schedule.every == timedelta(minutes=5)
    caplog.set_level("INFO", logger="hs_core.jobs.heartbeat")
    out = hb.handler(JobContext("heartbeat", "r", "heartbeat:x", None, {}, None, 0, None))
    assert isinstance(out, Done)
    assert any(r.getMessage() == "heartbeat" for r in caplog.records)


def test_schedules_are_declared_only_in_schedule_py():
    """No other module may construct a JobSpec or Schedule (apart from the definitions)."""
    offenders = []
    for path in SRC.glob("*.py"):
        if path.name in {"schedule.py", "model.py"}:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") in {
                "JobSpec",
                "Schedule",
            }:
                offenders.append(path.name)
    assert offenders == []


def test_schedule_py_has_every_job_with_a_schedule_or_marks_it_on_demand():
    assert all(j.schedule is not None for j in schedule.JOBS)
