"""Builders for the V01 teacher application tests: a faked stack and a valid payload."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from hs_core.auth import FakeTokenVerifier
from hs_core.main import create_app
from hs_core.repository import InMemoryRepository
from hs_core.settings import Settings
from hs_core.teacher_application import ApplicationPolicy, ApplicationService
from hs_core.teacher_application.ports import FakeAuditSink, FakeCatalogue, FakeNotifier, FakeZones

T0 = datetime(2026, 11, 25, 9, 0, 0, tzinfo=UTC)
KEY = "k" * 20
TEACHER_PHONE = "+256772123456"


class Clock:
    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw) -> None:
        self.now += timedelta(**kw)


def valid_content() -> dict:
    return {
        "personal": {
            "full_name": "Grace Nakato",
            "title": "ms",
            "gender": "female",
            "date_of_birth": "1992-04-17",
            "languages": ["en", "lg"],
            "photo": {"state": "chosen", "crop": {"x": 0.1, "y": 0.1, "size": 0.8}},
        },
        "qualifications": {
            "teacher_type": "registered",
            "registration_number": "UG/TR/12345",
            "highest_qualification": "degree",
            "institution": "Makerere University",
            "years_teaching": 6,
            "current_school": "Kampala Parents School",
        },
        "curricula": [
            {
                "curriculum_id": "cur_ug",
                "level_ids": ["lvl_p1", "lvl_p2"],
                "subject_ids": ["sub_math", "sub_eng"],
            }
        ],
        "referees": [
            {
                "name": "Peter Okello",
                "phone": "0701 234 567",
                "relationship": "head_teacher",
                "years_known": 4,
                "email": "peter@example.org",
            },
            {
                "name": "Sarah Namuli",
                "phone": "+256 755 000 111",
                "relationship": "colleague",
                "years_known": 2,
            },
        ],
        "referee_consent": True,
        "zone_ids": ["zone_ntinda", "zone_kira"],
        "travel_radius_km": 10,
        "availability": [
            {"weekday": 1, "start_minute": 15 * 60, "end_minute": 18 * 60},
            {"weekday": 3, "start_minute": 15 * 60, "end_minute": 18 * 60},
        ],
    }


class Env:
    def __init__(self, **policy) -> None:
        self.clock = Clock()
        self.repo = InMemoryRepository()
        self.audit, self.notifier = FakeAuditSink(), FakeNotifier()
        self.catalogue, self.zones = FakeCatalogue(), FakeZones()
        self.service = ApplicationService(
            self.repo,
            self.catalogue,
            self.zones,
            self.audit,
            self.notifier,
            ApplicationPolicy(**policy),
            self.clock,
        )
        v = FakeTokenVerifier()
        v.add("t1", "teacher_one", ["teacher"])
        v.add("t2", "teacher_two", ["teacher"])
        v.add("parent", "parent_one", ["parent"])
        v.add("both", "both_one", ["parent", "teacher"])
        v.add("admin", "admin_one", ["opsadmin"])
        self.repo.create("user_profile", "teacher_one", {"phone": TEACHER_PHONE, "name": "x"})
        self.client = TestClient(
            create_app(
                Settings(_env_file=None, hs_env="dev"),
                repository=self.repo,
                verifier=v,
                teacher_application=self.service,
            )
        )

    def h(self, token="t1", key=None) -> dict:
        out = {"Authorization": f"Bearer {token}"}
        if key:
            out["Idempotency-Key"] = key
        return out

    def save(self, content=None, rev=0, token="t1", step=0):
        return self.client.put(
            "/v1/teacher/application/draft",
            json={
                "base_revision": rev,
                "step": step,
                "content": content if content is not None else valid_content(),
            },
            headers=self.h(token),
        )

    def submit(self, rev, key=KEY, token="t1", declaration=True):
        return self.client.post(
            "/v1/teacher/application/submit",
            json={"base_revision": rev, "declaration": declaration},
            headers=self.h(token, key),
        )

    def get(self, token="t1"):
        return self.client.get("/v1/teacher/application", headers=self.h(token))

    def ready(self, token="t1") -> int:
        r = self.save(token=token)
        assert r.status_code == 200, r.text
        return r.json()["revision"]

    def rows(self, table) -> list[dict]:
        return self.repo.list(table)


def deep(c: dict) -> dict:
    return copy.deepcopy(c)
