"""K01 test world: the M05 sample cast (families, teachers, staff) plus a small catalogue.

Catalogue: curriculum `cuA` has v1 (retired), v2 (published), v3 (draft); `cuNone` has only a draft;
`cuB` has a published version only. v1 has topic `t_old` that v2 does not have (the M03
retired-topic edge case: a log points at it). Children: a1 (family A, teacher T1 assigned)
pinned to v1, a2 pinned
to v2, b1 (family B, teacher T5) pinned to v2.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from hs_core.auth import FakeTokenVerifier
from hs_core.catalogue.service import CatalogueService
from hs_core.main import create_app
from hs_core.permissions.sample import SampleWorld
from hs_core.settings import Settings
from jobs_helpers import T0, FakeClock

TOKENS = {  # token -> (user id, labels)
    "tok-parentA": ("uPA", ["parent"]),
    "tok-parentB": ("uPB", ["parent"]),
    "tok-guardianA": ("uGA", ["guardian"]),
    "tok-teacher1": ("uT1", ["teacher"]),  # assigned to a1
    "tok-teacher2": ("uT2", ["teacher"]),  # assignment on a2 ended (trial not continued)
    "tok-teacher5": ("uT5", ["teacher"]),  # assigned to b1
    "tok-curator": ("uCur", ["curator"]),
    "tok-ops": ("uOps", ["opsadmin"]),
    "tok-super": ("uSup", ["superadmin"]),
    "tok-lead": ("uL1", ["safeguardinglead"]),  # assigned lead of the open case on a1
    "tok-lead2": ("uL2", ["safeguardinglead"]),  # lead without a case
    "tok-none": ("uNone", []),
    # label spellings that are NOT the label (ADR 0012: exact, alphanumeric, case sensitive)
    "tok-Curator": ("uX1", ["Curator"]),
    "tok-cura_tor": ("uX2", ["cura_tor"]),
    "tok-OPSADMIN": ("uX3", ["OPSADMIN"]),
    "tok-ops_admin": ("uX4", ["ops_admin"]),
    "tok-teacherparent": ("uT1", ["Teacher "]),
}
CURATORS = ("tok-curator", "tok-ops")
APP_USERS = (
    "tok-parentA",
    "tok-parentB",
    "tok-teacher1",
    "tok-none",
    "tok-Curator",
    "tok-cura_tor",
    "tok-OPSADMIN",
    "tok-ops_admin",
    "tok-super",
    "tok-lead",
    "tok-lead2",
    "tok-guardianA",
)
T = "2027-01-01T00:00:00+00:00"


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def verifier() -> FakeTokenVerifier:
    v = FakeTokenVerifier()
    for tok, (uid, labels) in TOKENS.items():
        v.add(tok, uid, labels)
    return v


class CatWorld(SampleWorld):
    def __init__(self) -> None:
        super().__init__(FakeClock(T0))
        self.seed()
        self.build_catalogue()

    # -- raw writes -------------------------------------------------------------------------------
    def topic(
        self,
        tid,
        vid,
        subj,
        lvl,
        *,
        code=None,
        title=None,
        parent=None,
        order=1,
        strand=None,
        desc="",
        deleted=False,
    ):
        return self.add(
            "topic",
            tid,
            catalogue_version_id=vid,
            subject_id=subj,
            level_id=lvl,
            strand_id=strand,
            parent_topic_id=parent,
            code=code or tid,
            title=title or f"Title {tid}",
            description=desc,
            sort_order=order,
            deleted_at=T if deleted else None,
        )

    def version(self, vid, cur, number, status, **kw):
        return self.add(
            "catalogue_version",
            vid,
            curriculum_id=cur,
            version=number,
            status=status,
            published_at=kw.get("published_at"),
            retired_at=kw.get("retired_at"),
            created_by_id="uCur",
            published_key=cur if status == "published" else None,
        )

    def publish(self, vid):
        """What publish_version does (M03): retire the curriculum's published one, then publish."""
        v = self.repo.get("catalogue_version", vid)
        for o in self.repo.list("catalogue_version"):
            if (
                o["curriculum_id"] == v["curriculum_id"]
                and o["status"] == "published"
                and o["$id"] != vid
            ):
                self.patch(
                    "catalogue_version",
                    o["$id"],
                    status="retired",
                    retired_at=self.clock.now.isoformat(),
                    published_key=None,
                )
        self.patch(
            "catalogue_version",
            vid,
            status="published",
            published_at=self.clock.now.isoformat(),
            published_key=v["curriculum_id"],
        )

    def build_catalogue(self):
        add = self.add
        for table in (
            "curriculum",
            "catalogue_version",
            "level",
            "subject",
            "strand",
            "topic",
            "level_equivalence",
            "struggle_tag",
        ):
            self.repo._t.pop(table, None)  # the M05 sample's own placeholder catalogue rows
        add("curriculum", "cuA", code="UNEB", name="Uganda Primary")
        add("curriculum", "cuB", code="CAM", name="Cambridge")
        add("curriculum", "cuNone", code="IB", name="IB")
        add("curriculum", "cuGone", code="OLD", name="Deleted", deleted_at=T)
        self.version("v1", "cuA", 1, "retired", published_at=T, retired_at=T)
        self.version("v2", "cuA", 2, "published", published_at=T)
        self.version("v3", "cuA", 3, "draft")
        self.version("vB1", "cuB", 1, "published", published_at=T)
        self.version("vN1", "cuNone", 1, "draft")
        self.version("vGone", "cuA", 9, "published")
        self.patch("catalogue_version", "vGone", deleted_at=T, published_key=None)
        for v, suf in (("v1", "1"), ("v2", "2"), ("v3", "3"), ("vB1", "B"), ("vN1", "N")):
            add(
                "level",
                f"lv{suf}p3",
                catalogue_version_id=v,
                code="P3",
                name="Primary 3",
                sort_order=3,
            )
            add(
                "level",
                f"lv{suf}p4",
                catalogue_version_id=v,
                code="P4",
                name="Primary 4",
                sort_order=4,
            )
            add("subject", f"sb{suf}m", catalogue_version_id=v, code="MATH", name="Mathematics")
            add("subject", f"sb{suf}e", catalogue_version_id=v, code="ENG", name="English")
            add(
                "strand",
                f"st{suf}1",
                catalogue_version_id=v,
                subject_id=f"sb{suf}m",
                level_id=f"lv{suf}p3",
                name="Number",
                sort_order=1,
            )
        add(
            "level",
            "lv1gone",
            catalogue_version_id="v1",
            code="P9",
            name="Deleted",
            sort_order=9,
            deleted_at=T,
        )
        # v1 and v2 topics: parent/child, order, a topic only v1 has, a soft-deleted one
        for v, s in (("v1", "1"), ("v2", "2"), ("v3", "3")):
            m, p3 = f"sb{s}m", f"lv{s}p3"
            self.topic(
                f"fr{s}",
                v,
                m,
                p3,
                code="FRAC",
                title="Fractions",
                order=2,
                strand=f"st{s}1",
                desc="Halves, quarters and thirds",
            )
            self.topic(f"fr{s}h", v, m, p3, code="FRAC.H", title="Halves", parent=f"fr{s}", order=1)
            self.topic(
                f"fr{s}q", v, m, p3, code="FRAC.Q", title="Quarters", parent=f"fr{s}", order=2
            )
            self.topic(f"ad{s}", v, m, p3, code="ADD", title="Addition", order=1, strand=f"st{s}1")
            self.topic(f"rd{s}", v, f"sb{s}e", p3, code="READ", title="Reading aloud", order=1)
            self.topic(f"dl{s}", v, m, p3, code="DEL", title="Deleted topic", order=3, deleted=True)
            self.topic(f"p4{s}", v, m, f"lv{s}p4", code="MUL", title="Multiplication", order=1)
        self.topic("t_old", "v1", "sb1m", "lv1p3", code="OLD", title="Roman numerals", order=4)
        self.topic("tB", "vB1", "sbBm", "lvBp3", code="CAMADD", title="Cambridge addition", order=1)
        add("struggle_tag", "tg1", subject_id=None, label="Reading fluency", active=True)
        add("struggle_tag", "tg2", subject_id="sb2m", label="Times tables", active=True)
        add("struggle_tag", "tg3", subject_id=None, label="Retired tag", active=False)
        add(
            "level_equivalence",
            "leq1",
            level_id="lv2p3",
            equivalent_level_id="lvBp3",
            pair_key="a:b",
        )
        add(
            "level_equivalence",
            "leq2",
            level_id="lv2p4",
            equivalent_level_id="lv3p4",
            pair_key="c:d",
        )
        add(
            "level_equivalence",
            "leq3",
            level_id="lv2p4",
            equivalent_level_id="lvBp4",
            pair_key="e:f",
            deleted_at=T,
        )
        # children pin a catalogue version
        self.patch("child", "a1", curriculum_id="cuA", catalogue_version_id="v1")
        self.patch("child", "a2", curriculum_id="cuA", catalogue_version_id="v2")
        self.patch("child", "b1", curriculum_id="cuA", catalogue_version_id="v2")
        add("child", "cNone", parent_id="PA", name="No Pin")  # no catalogue version
        add("child", "cDraft", parent_id="PA", name="Pinned to a draft", catalogue_version_id="v3")

    # -- app ------------------------------------------------------------------------------------
    def app(self, **settings_kw):
        settings = Settings(_env_file=None, hs_env="dev", **settings_kw)
        self.service = CatalogueService(
            self.repo, clock=self.clock, cache_entries=settings.catalogue_cache_entries
        )
        return create_app(
            settings=settings,
            repository=self.repo,
            verifier=verifier(),
            catalogue_service=self.service,
        )

    def client(self, **kw) -> TestClient:
        return TestClient(self.app(**kw))


def get(client, path, token="tok-parentA", **kw):
    return client.get(path, headers={**auth(token), **kw.pop("headers", {})}, **kw)


def ids(resp):
    return [i["id"] for i in resp.json()["items"]]
