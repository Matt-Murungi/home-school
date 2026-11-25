"""CatalogueService: read-only access to the curriculum catalogue (K01, SR 5.3/7.6, ADR 0012).

Rules (ported from the executable reference `appwrite/catalogue_rules.py`, M03):
- a version is `draft` (editable, curators only), `published` (immutable, at most one per
  curriculum, `published_key`) or `retired` (immutable, still served: logs and children pinned to
  it keep resolving);
- every row of level/subject/strand/topic belongs to exactly ONE version (`catalogue_version_id`):
  every read here is scoped by the version id taken from the URL or from the child, so a page can
  never mix versions and a publish while a client pages changes nothing (cursors are
  version-scoped);
- soft-deleted rows (`deleted_at`) are never returned;
- who sees drafts follows M05 (`rules.CUR_OPS`); everyone else gets 404 for a draft, exactly like
  an unknown id (no probing of unpublished work).

Nothing here writes. V01, P03 (add child), L02 (session log) and Q01 call this service directly.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import islice

from ..permissions import queries as q
from ..permissions.context import PermissionContext, RepoFacts, parse_dt
from ..permissions.rules import CUR_OPS, desired_permissions
from ..repository import NotFound, Repository
from .cache import LruCache

PUBLIC_STATUSES = ("published", "retired")
DRAFT_ROLES = frozenset(CUR_OPS)  # M05: who reads draft catalogue rows (curator, ops admin)
MAX_PAGE = 100
DEFAULT_PAGE = 50
MAX_SEARCH = 50
MAX_CHANGES = 1000
SCAN_PAGE = (
    500  # rows per repository call when a whole version is read (Appwrite allows up to 5,000)
)
# retire-then-publish is two writes (no transactions): a short window with no published version
PUBLISH_GAP_S = 10
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,35}")


class CatalogueNotFound(LookupError):
    """Unknown id, soft-deleted row, a draft the caller may not see, or an unreadable child."""


class NoPublishedVersion(CatalogueNotFound):
    """The curriculum exists but has no published version (yet)."""


class PublishInProgress(RuntimeError):
    """A publish is between its retire and publish writes: retry in a second."""


class InvalidCursor(ValueError): ...


class InvalidRequest(ValueError): ...


@dataclass(frozen=True)
class Viewer:
    user_id: str
    roles: frozenset[str]

    @staticmethod
    def of(principal) -> Viewer:
        return Viewer(principal.user_id, frozenset(principal.roles))

    @property
    def sees_drafts(self) -> bool:
        return bool(self.roles & DRAFT_ROLES)


def _live(row: dict | None) -> bool:
    return row is not None and not row.get("deleted_at")


def _sort_key(row: dict) -> tuple:
    return (row.get("sort_order") if row.get("sort_order") is not None else 0, row["$id"])


# projections: only public fields, never *_key columns, permissions or editor ids
def p_curriculum(r: dict) -> dict:
    return {"id": r["$id"], "code": r.get("code"), "name": r.get("name")}


def p_version(r: dict) -> dict:
    return {
        "id": r["$id"],
        "curriculum_id": r.get("curriculum_id"),
        "version": r.get("version"),
        "status": r.get("status"),
        "published_at": r.get("published_at"),
        "retired_at": r.get("retired_at"),
    }


def p_level(r: dict) -> dict:
    return {"id": r["$id"], "code": r.get("code"), "name": r.get("name"), "order": _sort_key(r)[0]}


def p_subject(r: dict) -> dict:
    return {"id": r["$id"], "code": r.get("code"), "name": r.get("name")}


def p_strand(r: dict) -> dict:
    return {
        "id": r["$id"],
        "subject_id": r.get("subject_id"),
        "level_id": r.get("level_id"),
        "name": r.get("name"),
        "order": _sort_key(r)[0],
    }


def p_topic(r: dict) -> dict:
    return {
        "id": r["$id"],
        "version_id": r.get("catalogue_version_id"),
        "subject_id": r.get("subject_id"),
        "level_id": r.get("level_id"),
        "strand_id": r.get("strand_id"),
        "parent_id": r.get("parent_topic_id"),
        "code": r.get("code"),
        "title": r.get("title"),
        "description": r.get("description"),
        "order": _sort_key(r)[0],
    }


def p_tag(r: dict) -> dict:
    return {"id": r["$id"], "subject_id": r.get("subject_id"), "label": r.get("label")}


def _norm(text: str) -> str:
    return " ".join(text.casefold().split())


class CatalogueService:
    def __init__(
        self,
        repo: Repository,
        *,
        clock: Callable[[], datetime] | None = None,
        cache_entries: int = 32,
    ) -> None:
        self.repo = repo
        self.clock = clock or (lambda: datetime.now(UTC))
        self.cache = LruCache(cache_entries)

    # -------------------- low-level reads
    def _get(self, table: str, row_id: str) -> dict | None:
        if not isinstance(row_id, str) or not _ID.fullmatch(row_id):
            return None
        try:
            row = self.repo.get(table, row_id)
        except NotFound:
            return None
        return row if _live(row) else None

    def _all(self, table: str, **eq: str) -> list[dict]:
        queries = [q.equal(col, [val]) for col, val in eq.items()]
        return [r for r in q.iter_rows(self.repo, table, queries) if _live(r)]

    def _scan(
        self,
        table: str,
        eq: dict[str, str],
        after: str | None = None,
        pred=None,
        page_size: int = SCAN_PAGE,
    ) -> Iterator[dict]:
        """Live rows of one table in (sort_order, id) order after the row `after`, by cursor, never
        offset; soft-deleted rows are skipped, so a page may need more than one repository call."""
        queries = [q.equal(col, [val]) for col, val in eq.items()]
        queries += [q.order_asc("sort_order"), q.order_asc("$id")]
        while True:
            page = [*queries, q.limit(page_size)]
            if after:
                page.append(q.cursor_after(after))
            try:
                rows = self.repo.list(table, page)
            except ValueError:
                raise InvalidCursor("cursor row no longer exists") from None
            for r in rows:
                if _live(r) and (pred is None or pred(r)):
                    yield r
            if len(rows) < page_size:
                return
            after = rows[-1]["$id"]

    # -------------------- curricula and versions
    def _curriculum(self, curriculum_id: str) -> dict:
        row = self._get("curriculum", curriculum_id)
        if row is None:
            raise CatalogueNotFound("curriculum")
        return row

    def _visible(self, viewer: Viewer, version: dict) -> bool:
        return _live(version) and (version.get("status") in PUBLIC_STATUSES or viewer.sees_drafts)

    def version(self, viewer: Viewer, version_id: str) -> dict:
        """The raw version row; draft = 404 unless curator/ops (same answer as an unknown id)."""
        row = self._get("catalogue_version", version_id)
        if row is None or not self._visible(viewer, row):
            raise CatalogueNotFound("version")
        return row

    @staticmethod
    def immutable(version: dict) -> bool:
        return version.get("status") in PUBLIC_STATUSES

    def curricula(self, viewer: Viewer) -> dict:
        versions = [v for v in self._all("catalogue_version") if v.get("status") == "published"]
        current = {v["curriculum_id"]: v for v in versions}
        items = []
        for c in sorted(self._all("curriculum"), key=lambda r: (str(r.get("code")), r["$id"])):
            cur = current.get(c["$id"])
            items.append(
                {
                    **p_curriculum(c),
                    "current_version_id": cur["$id"] if cur else None,
                    "current_version": cur.get("version") if cur else None,
                }
            )
        return {"items": items}

    def versions(self, viewer: Viewer, curriculum_id: str) -> dict:
        self._curriculum(curriculum_id)
        rows = [
            v
            for v in self._all("catalogue_version", curriculum_id=curriculum_id)
            if self._visible(viewer, v)
        ]
        rows.sort(key=lambda r: (-(r.get("version") or 0), r["$id"]))
        return {"curriculum_id": curriculum_id, "items": [p_version(v) for v in rows]}

    def current_version(self, viewer: Viewer, curriculum_id: str) -> dict:
        self._curriculum(curriculum_id)
        rows = self._all("catalogue_version", curriculum_id=curriculum_id)
        published = [v for v in rows if v.get("status") == "published"]
        if published:  # the unique published_key allows one; take the newest if a repair left two
            return max(published, key=lambda r: (r.get("version") or 0, r["$id"]))
        now = self.clock()
        recent = [
            t
            for v in rows
            if v.get("status") == "retired" and (t := parse_dt(v.get("retired_at"))) is not None
        ]
        if (
            recent
            and any(v.get("status") == "draft" for v in rows)
            and now - max(recent) < timedelta(seconds=PUBLISH_GAP_S)
        ):
            raise PublishInProgress
        raise NoPublishedVersion("no published version")

    # -------------------- version content
    def levels(self, version: dict) -> dict:
        rows = sorted(self._all("level", catalogue_version_id=version["$id"]), key=_sort_key)
        return {"version_id": version["$id"], "items": [p_level(r) for r in rows]}

    def subjects(self, version: dict) -> dict:
        rows = self._all("subject", catalogue_version_id=version["$id"])
        rows.sort(key=lambda r: (str(r.get("code")).casefold(), r["$id"]))
        return {"version_id": version["$id"], "items": [p_subject(r) for r in rows]}

    def strands(self, version: dict, subject_id: str | None, level_id: str | None) -> dict:
        eq = {"catalogue_version_id": version["$id"]}
        if subject_id:
            eq["subject_id"] = subject_id
        if level_id:
            eq["level_id"] = level_id
        rows = sorted(self._all("strand", **eq), key=_sort_key)
        return {"version_id": version["$id"], "items": [p_strand(r) for r in rows]}

    def _topic_eq(self, version_id, subject_id, level_id, parent_id) -> dict[str, str]:
        eq = {"catalogue_version_id": version_id}
        for col, val in (
            ("subject_id", subject_id),
            ("level_id", level_id),
            ("parent_topic_id", parent_id),
        ):
            if val:
                eq[col] = val
        return eq

    def topics_page(
        self,
        version: dict,
        *,
        subject_id: str | None = None,
        level_id: str | None = None,
        parent_id: str | None = None,
        roots: bool = False,
        cursor: str | None = None,
        limit: int = DEFAULT_PAGE,
    ) -> dict:
        """One page of topics in (order, id) order. The cursor is bound to the version and the
        filters; a cursor of another version or query is refused, never reinterpreted."""
        if not 1 <= limit <= MAX_PAGE:
            raise InvalidRequest(f"limit must be between 1 and {MAX_PAGE}")
        vid = version["$id"]
        flt = [subject_id, level_id, parent_id, roots]
        after = self._decode_cursor(cursor, vid, flt)
        if after:
            row = self._get("topic", after)
            if row is None or row.get("catalogue_version_id") != vid:
                raise InvalidCursor("cursor does not belong to this version")
        eq = self._topic_eq(vid, subject_id, level_id, parent_id)
        pred = (lambda r: not r.get("parent_topic_id")) if roots else None
        rows = list(
            islice(
                self._scan("topic", eq, after, pred, page_size=min(SCAN_PAGE, limit + 10)),
                limit + 1,
            )
        )
        more = len(rows) > limit
        rows = rows[:limit]
        return {
            "version_id": vid,
            "items": [p_topic(r) for r in rows],
            "next_cursor": self._encode_cursor(vid, flt, rows[-1]["$id"]) if more else None,
        }

    @staticmethod
    def _encode_cursor(vid: str, flt: list, last_id: str) -> str:
        raw = json.dumps({"v": vid, "f": flt, "a": last_id}, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str | None, vid: str, flt: list) -> str | None:
        if not cursor:
            return None
        try:
            raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
            d = json.loads(raw)
            ok = isinstance(d, dict) and d.get("v") == vid and d.get("f") == flt
            last = d.get("a") if ok else None
        except (binascii.Error, ValueError, UnicodeDecodeError):
            raise InvalidCursor("malformed cursor") from None
        if not ok or not isinstance(last, str) or not _ID.fullmatch(last):
            raise InvalidCursor("cursor does not match this request")
        return last

    def topic(self, version: dict, topic_id: str) -> dict:
        """Version-scoped lookup: a log that points at a topic of a retired version resolves it here
        even after a newer version was published without that topic."""
        row = self._get("topic", topic_id)
        if row is None or row.get("catalogue_version_id") != version["$id"]:
            raise CatalogueNotFound("topic")
        return p_topic(row)

    def _version_topics(self, version: dict) -> list[dict]:
        key = ("topics", version["$id"])
        if self.immutable(version) and (hit := self.cache.get(key)) is not None:
            return hit
        rows = [p_topic(r) for r in self._scan("topic", {"catalogue_version_id": version["$id"]})]
        if self.immutable(version):
            self.cache.put(key, rows)
        return rows

    def tree(self, version: dict, level_id: str, subject_id: str) -> dict:
        """Topics of (version, level, subject) as nested parents and children, plus strands."""
        topics = [
            t
            for t in self._version_topics(version)
            if t["level_id"] == level_id and t["subject_id"] == subject_id
        ]
        nodes = {t["id"]: {**t, "children": []} for t in topics}
        roots, placed = [], set()
        for t in topics:  # already in (order, id) order
            parent = nodes.get(t["parent_id"]) if t["parent_id"] else None
            (parent["children"] if parent else roots).append(nodes[t["id"]])
            placed.add(t["id"])
        # a parent loop cannot be written (M03); legacy data must not hide topics or loop forever
        reached: set[str] = set()
        stack = list(roots)
        while stack:
            n = stack.pop()
            reached.add(n["id"])
            stack.extend(n["children"])
        for t in topics:
            if t["id"] not in reached:
                roots.append(nodes[t["id"]])
                n_stack = [nodes[t["id"]]]
                while n_stack:
                    n = n_stack.pop()
                    reached.add(n["id"])
                    n["children"] = [c for c in n["children"] if c["id"] not in reached]
                    n_stack.extend(n["children"])
        strands = [
            p_strand(r)
            for r in sorted(self._all("strand", catalogue_version_id=version["$id"]), key=_sort_key)
            if r.get("subject_id") == subject_id and r.get("level_id") == level_id
        ]
        return {
            "version_id": version["$id"],
            "level_id": level_id,
            "subject_id": subject_id,
            "strands": strands,
            "topics": roots,
        }

    def bundle(self, version: dict, level_id: str, subject_id: str) -> dict:
        """Compact flat form for offline apps: column names once, then one array per topic."""
        rows = [
            t
            for t in self._version_topics(version)
            if t["level_id"] == level_id and t["subject_id"] == subject_id
        ]
        cols = ["id", "parent_id", "strand_id", "code", "title", "order"]
        return {
            "version_id": version["$id"],
            "level_id": level_id,
            "subject_id": subject_id,
            "count": len(rows),
            "cols": cols,
            "rows": [[t[c] for c in cols] for t in rows],
        }

    def search(
        self,
        version: dict,
        text: str,
        *,
        subject_id: str | None = None,
        level_id: str | None = None,
        limit: int = 20,
    ) -> dict:
        """Case-insensitive text search over code, title and description of one version. Every
        word must appear; ranks: code, title prefix, title, description."""
        if not 1 <= limit <= MAX_SEARCH:
            raise InvalidRequest(f"limit must be between 1 and {MAX_SEARCH}")
        words = _norm(text).split()
        if not words or sum(len(w) for w in words) < 2:
            raise InvalidRequest("search text must have at least 2 characters")
        whole = " ".join(words)
        scored: list[tuple[int, int, str, dict]] = []
        for t in self._version_topics(version):
            if (subject_id and t["subject_id"] != subject_id) or (
                level_id and t["level_id"] != level_id
            ):
                continue
            code, title, desc = (
                _norm(t["code"] or ""),
                _norm(t["title"] or ""),
                _norm(t["description"] or ""),
            )
            if not all(w in code or w in title or w in desc for w in words):
                continue
            rank = (
                0
                if code == whole
                else 1
                if title.startswith(whole)
                else 2
                if whole in title
                else 3
                if all(w in title for w in words)
                else 4
            )
            scored.append((rank, t["order"], t["id"], t))
        scored.sort(key=lambda s: s[:3])
        return {
            "version_id": version["$id"],
            "query": whole,
            "items": [s[3] for s in scored[:limit]],
            "truncated": len(scored) > limit,
        }

    # -------------------- unversioned and cross-curriculum
    def struggle_tags(self, subject_id: str | None) -> dict:
        rows = [r for r in self._all("struggle_tag") if r.get("active", True)]
        if subject_id:
            rows = [r for r in rows if r.get("subject_id") in (subject_id, None)]
        rows.sort(key=lambda r: (_norm(r.get("label") or ""), r["$id"]))
        return {"items": [p_tag(r) for r in rows]}

    def level_equivalences(self, viewer: Viewer, version: dict) -> dict:
        """Pairs of levels (this version's level, a level of another curriculum). Matching metadata
        that M05 keeps off the apps' reads: curator/ops only. The other side must be visible too."""
        if not viewer.sees_drafts:
            raise CatalogueNotFound("level equivalences")
        mine = {r["$id"] for r in self._all("level", catalogue_version_id=version["$id"])}
        pairs: dict[tuple[str, str], dict] = {}
        for col, other in (
            ("level_id", "equivalent_level_id"),
            ("equivalent_level_id", "level_id"),
        ):
            if not mine:
                break
            for r in q.iter_rows(self.repo, "level_equivalence", [q.equal(col, sorted(mine))]):
                if not _live(r):
                    continue
                far = self._get("level", r[other])
                far_v = self._get("catalogue_version", far["catalogue_version_id"]) if far else None
                if far is None or far_v is None or not self._visible(viewer, far_v):
                    continue
                pairs[(r[col], r[other])] = {
                    "level_id": r[col],
                    "equivalent_level_id": r[other],
                    "equivalent": {
                        **p_level(far),
                        "version_id": far_v["$id"],
                        "curriculum_id": far_v.get("curriculum_id"),
                    },
                }
        return {"version_id": version["$id"], "items": [pairs[k] for k in sorted(pairs)]}

    def changes(self, since: dict, to: dict) -> dict:
        """Topics added, removed and retitled between two versions of one curriculum. Topic ids
        differ per version (copy-as-draft), so topics are matched by (subject code, level code,
        topic code). Interface for K04; capped, with `truncated`."""
        if since.get("curriculum_id") != to.get("curriculum_id"):
            raise InvalidRequest("versions belong to different curricula")

        def index(v: dict) -> dict[tuple, dict]:
            subj = {
                r["$id"]: r.get("code") for r in self._all("subject", catalogue_version_id=v["$id"])
            }
            lev = {
                r["$id"]: r.get("code") for r in self._all("level", catalogue_version_id=v["$id"])
            }
            out = {}
            for t in self._version_topics(v):
                out[(subj.get(t["subject_id"]), lev.get(t["level_id"]), _norm(t["code"] or ""))] = t
            return out

        a, b = index(since), index(to)

        def brief(t: dict, key: tuple) -> dict:
            return {
                "id": t["id"],
                "code": t["code"],
                "title": t["title"],
                "subject_code": key[0],
                "level_code": key[1],
            }

        added = [brief(b[k], k) for k in sorted(b.keys() - a.keys(), key=str)]
        removed = [brief(a[k], k) for k in sorted(a.keys() - b.keys(), key=str)]
        retitled = [
            {**brief(b[k], k), "previous_title": a[k]["title"]}
            for k in sorted(a.keys() & b.keys(), key=str)
            if a[k]["title"] != b[k]["title"]
        ]
        total = len(added) + len(removed) + len(retitled)
        return {
            "since_version_id": since["$id"],
            "to_version_id": to["$id"],
            "added": added[:MAX_CHANGES],
            "removed": removed[:MAX_CHANGES],
            "retitled": retitled[:MAX_CHANGES],
            "truncated": any(len(x) > MAX_CHANGES for x in (added, removed, retitled)),
            "total": total,
        }

    # -------------------- a child's pinned catalogue
    def child_version(self, viewer: Viewer, child_id: str) -> dict:
        """The version pinned on the child (`child.catalogue_version_id`). Permission: exactly who
        may read the child row under M05 (parent, guardian with grant `children`, a teacher while
        assigned, ops, an assigned safeguarding lead); anyone else, and an unknown child, gets the
        same 404. Retired versions are served (history); drafts only to curator/ops."""
        child = self._get("child", child_id)
        if child is None or not self._may_read_child(viewer, child):
            raise CatalogueNotFound("child")
        vid = child.get("catalogue_version_id")
        if not vid:
            raise CatalogueNotFound("child has no catalogue version")
        return self.version(viewer, vid)

    def _may_read_child(self, viewer: Viewer, child: dict) -> bool:
        ctx = PermissionContext(RepoFacts(self.repo), self.clock())
        live = ctx.child_row(child["$id"])
        if live is None:
            return False
        grants = desired_permissions("child", live, ctx)  # the M05 rule, not a copy of it
        mine = {f'read("user:{viewer.user_id}")', *(f'read("label:{r}")' for r in viewer.roles)}
        return bool(mine & set(grants))

    def current_for_child(self, version: dict) -> dict | None:
        """The curriculum's published version (what a NEW child would be pinned to), if any."""
        try:
            return self.current_version(Viewer("", frozenset()), version["curriculum_id"])
        except (CatalogueNotFound, PublishInProgress):
            return None
