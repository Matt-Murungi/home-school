"""M03 (#38): reference rules for the catalogue, session-log and question domains (stdlib only, no I/O of its own).

Appwrite has no triggers, conditional updates or cross-column constraints (ADR 0002/0012), so the schema carries only
backstops (unique indexes over derived `*_key` columns, no client write permission). This module is the executable
specification the core service ports (K02-K04 curator tools, the session-log service of W/L tickets, the question bank):

- Published versions are immutable: `assert_editable`, `edit_catalogue_row`, `add_catalogue_row`, `soft_delete_catalogue_row`
  (every write to a level, subject, strand, topic or version goes through them); `publish_version` / `retire_version` /
  `copy_version_as_draft` are the only ways a status changes or a new draft appears.
- Logs reference the topic of the CHILD's catalogue version (SR 5.5): `assert_topic_in_child_version`, `submit_session_log`
  (idempotent: one log per session, unique `session_key` is the backstop), `upsert_topic_progress` / `derive_progress` /
  `rebuild_topic_progress` (topic_progress is derived and rebuildable).
- topic_progress is derived from the child's live log entries. SR 5.3 says "logs and assessments": assessment evidence
  (checkpoint and practice results, ASM-8) is added by Q04/Q05, which extend `child_evidence`; until then rebuilds use logs only.
- Handover summaries are immutable generations (`save_handover`, `ensure_handover`, `latest_handover`).
- Questions: two-person rule (`assert_two_person`, `approve_question`, `validate_question_row`), status transitions.

Any client with `list_rows`, `get_row`, `create_row`, `update_row` works (the fake in tests, the Appwrite adapter in
production). Appwrite transactions are not relied on: a multi-row step that fails half way is repaired by re-running it
(every step here is idempotent) or, for topic_progress, by `rebuild_topic_progress`.
"""

from __future__ import annotations

from datetime import datetime, timezone

import schema_build
from fake_client import UniqueViolation  # noqa: F401  (the 409 that the unique indexes raise; the real adapter maps 409 to it)

MASTERY = ("not_yet", "developing", "secure")
EDITABLE_VERSION_STATUSES = frozenset({"draft"})
VERSION_SCOPED_TABLES = ("level", "subject", "strand", "topic")  # rows carry catalogue_version_id
PROTECTED_FIELDS = frozenset({"catalogue_version_id", "curriculum_id", "status", "version", "published_at", "retired_at", "deleted_at"})
QUESTION_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"in_review", "retired"}),
    "in_review": frozenset({"approved", "draft", "retired"}),
    "approved": frozenset({"retired"}),
    "retired": frozenset(),
}


class CatalogueError(ValueError):
    """A catalogue, log or question rule was broken."""


class VersionImmutable(CatalogueError):
    """A write to a published or retired catalogue version (or to something under it)."""


class ChildMismatch(CatalogueError):
    """A session log was submitted for a child that is not the session's child (or the stored log's child)."""


class TwoPersonViolation(CatalogueError):
    """A question was approved by its own author, or without an approver."""


# --- derived unique keys (soft-delete convention: null when the row is soft deleted) ----------------------------------
def _keys(row: dict, **keys) -> dict:
    row = dict(row)
    gone = bool(row.get("deleted_at"))
    for name, value in keys.items():
        row[name] = None if gone else value
    return row


def _norm(text: str) -> str:
    return text.strip().lower()


def level_pair_key(a: str, b: str) -> str:
    if a == b:
        raise ValueError("a level cannot be equivalent to itself")
    return ":".join(sorted((a, b)))


def with_curriculum_keys(row):
    return _keys(row, code_key=_norm(row["code"]))


def with_version_keys(row):
    published = row.get("status") == "published"
    return _keys(row, version_key=f"{row['curriculum_id']}:{row['version']}", published_key=row["curriculum_id"] if published else None)


def with_level_keys(row):
    return _keys(row, code_key=f"{row['catalogue_version_id']}:{_norm(row['code'])}")


def with_level_equivalence_keys(row):
    return _keys(row, pair_key=level_pair_key(row["level_id"], row["equivalent_level_id"]))


def with_subject_keys(row):
    return _keys(row, code_key=f"{row['catalogue_version_id']}:{_norm(row['code'])}")


def with_topic_keys(row):
    return _keys(row, code_key=f"{row['catalogue_version_id']}:{row['subject_id']}:{row['level_id']}:{_norm(row['code'])}")


def with_struggle_tag_keys(row):
    return _keys(row, label_key=f"{row.get('subject_id') or 'all'}:{_norm(row['label'])}")


def with_session_log_keys(row):
    return _keys(row, session_key=row["session_id"])


def with_log_topic_keys(row):
    return _keys(row, log_topic_key=f"{row['session_log_id']}:{row['topic_id']}")


def with_log_struggle_keys(row):
    return _keys(row, log_struggle_key=f"{row['session_log_id']}:{row['struggle_tag_id']}:{row.get('topic_id') or '-'}")


def with_blueprint_keys(row):
    return _keys(row, blueprint_key=f"{row['level_id']}:{row['subject_id']}:{row['type']}")


def with_item_keys(row):
    return _keys(row, item_key=f"{row['assessment_id']}:{row['question_id']}")


_KEY_BUILDERS = {"level": with_level_keys, "subject": with_subject_keys, "topic": with_topic_keys,
                 "catalogue_version": with_version_keys, "strand": lambda r: dict(r)}


def _table(name: str) -> dict:
    for d in schema_build.load_domains():
        for t in d["tables"]:
            if t["$id"] == name:
                return t
    raise KeyError(name)


# --- published versions are immutable ---------------------------------------------------------------------------------
# Soft delete refuses while live rows still point at the row (no cascade: a cascade could silently remove curator work or
# questions; the curator tools delete leaves first and the error names what is left).
DEPENDANTS: dict[str, tuple[tuple[str, str], ...]] = {
    "catalogue_version": (("level", "catalogue_version_id"), ("subject", "catalogue_version_id"), ("strand", "catalogue_version_id"), ("topic", "catalogue_version_id")),
    "subject": (("topic", "subject_id"), ("strand", "subject_id"), ("assessment_blueprint", "subject_id")),
    "level": (("topic", "level_id"), ("strand", "level_id"), ("level_equivalence", "level_id"), ("level_equivalence", "equivalent_level_id"),
              ("question", "level_id"), ("assessment_blueprint", "level_id")),
    "strand": (("topic", "strand_id"),),
    "topic": (("topic", "parent_topic_id"), ("question", "topic_id")),
}


def assert_editable(version_status: str) -> None:
    """Only a draft version (and what is under it) can change. Published and retired versions never do (SR 5.3)."""
    if version_status not in EDITABLE_VERSION_STATUSES:
        raise VersionImmutable(f"catalogue version is {version_status!r}: only a draft can be edited; copy it as a new draft")


def _live(row: dict) -> bool:
    return not row.get("deleted_at")


def _version_of(client, table: str, row: dict) -> dict:
    version_id = row["$id"] if table == "catalogue_version" else row["catalogue_version_id"]
    return client.get_row("catalogue_version", version_id)


def edit_catalogue_row(client, table: str, row_id: str, patch: dict) -> dict:
    """The only way the core service edits a catalogue row. Rejected unless the row's version is a draft; ids of the
    version/curriculum, the status columns and deleted_at cannot be changed here (publish_version, soft_delete_catalogue_row)."""
    if table not in VERSION_SCOPED_TABLES + ("catalogue_version",):
        raise CatalogueError(f"{table} is not a catalogue table")
    patch = {k: v for k, v in patch.items() if not k.startswith("$")}  # never let a patch rewrite row identity or permissions
    row = client.get_row(table, row_id)
    assert_editable(_version_of(client, table, row)["status"])
    forbidden = PROTECTED_FIELDS & set(patch)
    if forbidden:
        raise CatalogueError(f"{sorted(forbidden)} cannot be edited here")
    merged = _KEY_BUILDERS[table]({**row, **patch})
    _check_refs(client, table, merged)
    keys = {k: v for k, v in merged.items() if k.endswith("_key")}
    return client.update_row(table, row_id, {**patch, **keys})


def add_catalogue_row(client, table: str, data: dict) -> dict:
    """Create a level/subject/strand/topic. The target version must be a draft."""
    if table not in VERSION_SCOPED_TABLES:
        raise CatalogueError(f"{table} is not a version-scoped catalogue table")
    assert_editable(client.get_row("catalogue_version", data["catalogue_version_id"])["status"])
    row = _KEY_BUILDERS[table](data)
    _check_refs(client, table, row)
    return client.create_row(table, row)


def soft_delete_catalogue_row(client, table: str, row_id: str, now: str) -> dict:
    row = client.get_row(table, row_id)
    assert_editable(_version_of(client, table, row)["status"])
    for dep_table, column in DEPENDANTS.get(table, ()):
        live = [r for r in client.list_rows(dep_table, **{column: row_id}) if _live(r)]
        if live:
            raise CatalogueError(f"{table} still has {len(live)} live {dep_table} row(s) ({column}); delete those first")
    return client.update_row(table, row_id, schema_build.soft_delete_patch(_table(table), now))


def _check_refs(client, table: str, row: dict) -> None:
    if table == "topic":
        check_topic_refs(client, row)
    elif table == "strand":
        check_strand_refs(client, row)


def check_strand_refs(client, strand: dict) -> None:
    """A strand's subject and level must belong to the strand's own catalogue version."""
    for table, col in (("subject", "subject_id"), ("level", "level_id")):
        target = client.get_row(table, strand[col])
        if not _live(target):
            raise CatalogueError(f"strand.{col} points at a deleted {table}")
        if target["catalogue_version_id"] != strand["catalogue_version_id"]:
            raise CatalogueError(f"strand.{col} belongs to another catalogue version")


def check_parent_chain(client, topic: dict) -> None:
    """parent_topic_id must not be the topic itself and the chain of parents must not loop (any length)."""
    me, parent, seen = topic.get("$id"), topic.get("parent_topic_id"), set()
    while parent:
        if parent == me or parent in seen:
            raise CatalogueError("parent_topic_id would create a cycle")
        seen.add(parent)
        parent = client.get_row("topic", parent).get("parent_topic_id")


def check_topic_refs(client, topic: dict) -> None:
    """subject, level, strand and parent topic must belong to the topic's own catalogue version."""
    version = topic["catalogue_version_id"]
    for table, col in (("subject", "subject_id"), ("level", "level_id"), ("strand", "strand_id"), ("topic", "parent_topic_id")):
        if topic.get(col):
            target = client.get_row(table, topic[col])
            if not _live(target):
                raise CatalogueError(f"topic.{col} points at a deleted {table}")
            if target["catalogue_version_id"] != version:
                raise CatalogueError(f"topic.{col} belongs to another catalogue version")
    check_parent_chain(client, topic)


def version_integrity_problems(client, version_id: str) -> list[str]:
    """Live strands and topics whose subject, level, strand or parent is missing, deleted or in another version (legacy data
    written before the reference checks, or by a bypass). Empty list = the version can be published and copied."""
    problems = []
    for table, cols in (("strand", ("subject_id", "level_id")), ("topic", ("subject_id", "level_id", "strand_id", "parent_topic_id"))):
        for row in client.list_rows(table, catalogue_version_id=version_id):
            if not _live(row):
                continue
            for col in cols:
                if not row.get(col):
                    continue
                target_table = {"subject_id": "subject", "level_id": "level", "strand_id": "strand", "parent_topic_id": "topic"}[col]
                try:
                    target = client.get_row(target_table, row[col])
                except KeyError:
                    problems.append(f"{table} {row['$id']}: {col} does not exist")
                    continue
                if not _live(target):
                    problems.append(f"{table} {row['$id']}: {col} points at a deleted {target_table}")
                elif target["catalogue_version_id"] != version_id:
                    problems.append(f"{table} {row['$id']}: {col} belongs to another catalogue version")
    return problems


def publish_version(client, version_id: str, now: str) -> dict:
    """draft -> published. The curriculum's previous published version is retired first (it keeps resolving for the children
    who are mid-term on it). Without transactions a crash between the two writes leaves no published version; running
    publish_version again completes it."""
    v = client.get_row("catalogue_version", version_id)
    if v["status"] != "draft":
        raise CatalogueError(f"only a draft can be published (this one is {v['status']})")
    if problems := version_integrity_problems(client, version_id):
        raise CatalogueError("cannot publish, the draft has broken references (deleted or foreign rows): " + "; ".join(problems[:5]))
    for other in client.list_rows("catalogue_version", curriculum_id=v["curriculum_id"]):
        if other["$id"] != version_id and other["status"] == "published" and _live(other):
            retire_version(client, other["$id"], now)
    return client.update_row("catalogue_version", version_id, with_version_keys({**v, "status": "published", "published_at": now}) | {"status": "published", "published_at": now})


def retire_version(client, version_id: str, now: str) -> dict:
    v = client.get_row("catalogue_version", version_id)
    if v["status"] != "published":
        raise CatalogueError(f"only a published version can be retired (this one is {v['status']})")
    keys = with_version_keys({**v, "status": "retired"})
    return client.update_row("catalogue_version", version_id, {"status": "retired", "retired_at": now, "published_key": keys["published_key"]})


def copy_version_as_draft(client, version_id: str, created_by_id: str | None = None) -> dict:
    """New draft (version + 1) with copies of the live levels, subjects, strands and topics of `version_id`."""
    src = client.get_row("catalogue_version", version_id)
    if problems := version_integrity_problems(client, version_id):  # before anything is written: no half-made draft
        raise CatalogueError("cannot copy, the version has broken references (deleted or foreign rows): " + "; ".join(problems[:5]))
    latest = max(r["version"] for r in client.list_rows("catalogue_version", curriculum_id=src["curriculum_id"]))
    draft = client.create_row("catalogue_version", with_version_keys(
        {"curriculum_id": src["curriculum_id"], "version": latest + 1, "status": "draft",
         **({"created_by_id": created_by_id} if created_by_id else {})}))
    new = {"draft": draft["$id"]}
    ids: dict[str, str] = {}

    def copy(table: str, rows: list[dict], remap: tuple[str, ...]):
        for r in sorted(rows, key=lambda r: r.get("sort_order", 0)):
            if not _live(r):
                continue
            data = {k: v for k, v in r.items() if not k.startswith("$") and k != "parent_topic_id"}
            data["catalogue_version_id"] = new["draft"]
            for col in remap:
                if data.get(col):
                    data[col] = ids[data[col]]
            ids[r["$id"]] = client.create_row(table, _KEY_BUILDERS[table](data))["$id"]

    copy("level", client.list_rows("level", catalogue_version_id=version_id), ())
    copy("subject", client.list_rows("subject", catalogue_version_id=version_id), ())
    copy("strand", client.list_rows("strand", catalogue_version_id=version_id), ("subject_id", "level_id"))
    topics = client.list_rows("topic", catalogue_version_id=version_id)
    copy("topic", topics, ("subject_id", "level_id", "strand_id"))
    for r in topics:  # second pass: parents exist now
        if _live(r) and r.get("parent_topic_id"):
            client.update_row("topic", ids[r["$id"]], {"parent_topic_id": ids[r["parent_topic_id"]]})
    return draft


# --- logs and progress -----------------------------------------------------------------------------------------------
def assert_topic_in_child_version(child: dict, topic: dict) -> None:
    """Logs reference a topic of the child's own catalogue version (SR 5.5), so later versions never rewrite history."""
    if topic["catalogue_version_id"] != child.get("catalogue_version_id"):
        raise CatalogueError("topic is not in the child's catalogue version")


def parse_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _upsert(client, table: str, find, data: dict, keys, patch: dict | None = None) -> dict:
    """Create the row, or update the existing one found by `find()`; the unique index decides races (409 -> update)."""
    existing = find()
    if existing is None:
        try:
            return client.create_row(table, keys(data))
        except UniqueViolation:
            existing = find()
            if existing is None:
                raise
    return client.update_row(table, existing["$id"], patch if patch is not None else data)


def submit_session_log(client, *, child: dict, session_id: str, teacher_id: str, topics, now: str,
                       struggles=(), note: str | None = None, homework: str | None = None) -> dict:
    """Create or update the one log of a session (idempotent submit, SR 7.6) with its topics and struggles, then update
    topic_progress. `topics` = [(topic_id, mastery)], `struggles` = [(struggle_tag_id, topic_id or None)]. Validation
    happens before the first write, so a rejected submit leaves nothing behind."""
    try:
        session = client.get_row("session", session_id)
    except KeyError:
        raise CatalogueError(f"unknown session {session_id}") from None
    if session["child_id"] != child["$id"]:
        raise ChildMismatch("the session belongs to another child")
    resolved = {}
    for topic_id, mastery in topics:
        if mastery not in MASTERY:
            raise ValueError(f"mastery must be one of {MASTERY}")
        resolved[topic_id] = client.get_row("topic", topic_id)
        assert_topic_in_child_version(child, resolved[topic_id])
    for _, topic_id in struggles:
        if topic_id:
            assert_topic_in_child_version(child, client.get_row("topic", topic_id))

    def find_log():
        return next((r for r in client.list_rows("session_log", session_id=session_id) if _live(r)), None)

    stored = find_log()
    if stored is not None and stored["child_id"] != child["$id"]:
        raise ChildMismatch("the stored log belongs to another child")
    data = {"session_id": session_id, "child_id": child["$id"], "teacher_id": teacher_id, "submitted_at": now, "note": note, "homework": homework}
    log = _upsert(client, "session_log", find_log, data, with_session_log_keys,
                  patch={"note": note, "homework": homework, "edited_at": now})
    previous = {r["topic_id"] for r in client.list_rows("session_log_topic", session_log_id=log["$id"]) if _live(r)}
    _sync_children(client, log["$id"], "session_log_topic", "topic_id", [(t, {"mastery": m}) for t, m in topics], with_log_topic_keys, now)
    _sync_children(client, log["$id"], "session_log_struggle", "struggle_tag_id",
                   [(f"{g}:{t or '-'}", {"struggle_tag_id": g, "topic_id": t}) for g, t in struggles], with_log_struggle_keys, now, composite=True)
    # progress follows the child's remaining evidence for every topic this submit added, changed or removed
    recompute_topic_progress(client, child["$id"], previous | {t for t, _ in topics})
    return log


def _sync_children(client, log_id, table, id_col, wanted, keys, now, composite=False):
    live = [r for r in client.list_rows(table, session_log_id=log_id) if _live(r)]

    def ident(r):
        return f"{r['struggle_tag_id']}:{r.get('topic_id') or '-'}" if composite else r[id_col]

    by_id = {ident(r): r for r in live}
    for ident_value, fields in wanted:
        base = {"session_log_id": log_id, **fields} if composite else {"session_log_id": log_id, id_col: ident_value, **fields}
        _upsert(client, table, lambda i=ident_value: next((r for r in client.list_rows(table, session_log_id=log_id)
                                                          if _live(r) and ident(r) == i), None), base, keys, patch=fields)
    wanted_ids = {i for i, _ in wanted}
    for i, r in by_id.items():
        if i not in wanted_ids:
            client.update_row(table, r["$id"], schema_build.soft_delete_patch(_table(table), now))


def upsert_topic_progress(client, child_id: str, topic_id: str, mastery: str, session_id: str | None, at: str, force: bool = False) -> dict:
    """One row per (child, topic): the unique index is the backstop for two concurrent first writes. Without `force`,
    evidence older than the stored row does not overwrite it; recompute and rebuild always pass force=True."""
    if mastery not in MASTERY:
        raise ValueError(f"mastery must be one of {MASTERY}")

    def find():
        return next((r for r in client.list_rows("topic_progress", child_id=child_id) if r["topic_id"] == topic_id), None)

    row = find()
    if not force and row is not None and parse_utc(at) < parse_utc(row["updated_at"]):
        return row
    data = {"child_id": child_id, "topic_id": topic_id, "current_mastery": mastery, "last_session_id": session_id, "updated_at": at}
    return _upsert(client, "topic_progress", find, data, lambda d: d, patch={k: data[k] for k in ("current_mastery", "last_session_id", "updated_at")})


def child_evidence(client, child_id: str) -> list[dict]:
    """The child's progress evidence: one entry per live log topic of their live logs. Today logs only; Q04/Q05 add
    assessment results here (SR 5.3: progress is rebuilt from logs AND assessments). At scale page by cursor."""
    out = []
    for log in client.list_rows("session_log", child_id=child_id):
        if not _live(log):
            continue
        for lt in client.list_rows("session_log_topic", session_log_id=log["$id"]):
            if _live(lt):
                out.append({"topic_id": lt["topic_id"], "mastery": lt["mastery"], "session_id": log["session_id"],
                            "log_id": log["$id"], "submitted_at": log["submitted_at"]})
    return out


def derive_progress(entries) -> dict:
    """Pure: {topic_id: {current_mastery, last_session_id, updated_at}} from entries
    [{topic_id, mastery, session_id, submitted_at, log_id?}]. The latest entry per topic wins; ties on the instant are
    broken by session_id, then log_id, so the result never depends on input order."""
    out: dict[str, dict] = {}
    for e in sorted(entries, key=lambda e: (parse_utc(e["submitted_at"]), e["session_id"], e.get("log_id") or "")):
        out[e["topic_id"]] = {"current_mastery": e["mastery"], "last_session_id": e["session_id"], "updated_at": e["submitted_at"]}
    return out


def recompute_topic_progress(client, child_id: str, topic_ids) -> dict:
    """Recompute the given topics from the child's remaining evidence: overwrite where evidence exists, DELETE the row where
    none is left. Absence means 'no evidence'; resetting to not_yet would claim the topic was taught but not understood."""
    topic_ids = set(topic_ids)
    derived = derive_progress([e for e in child_evidence(client, child_id) if e["topic_id"] in topic_ids])
    _apply_progress(client, child_id, topic_ids, derived)
    return derived


def _apply_progress(client, child_id, scope, derived) -> None:
    for topic_id, p in derived.items():
        upsert_topic_progress(client, child_id, topic_id, p["current_mastery"], p["last_session_id"], p["updated_at"], force=True)
    for row in client.list_rows("topic_progress", child_id=child_id):
        if row["topic_id"] in scope and row["topic_id"] not in derived:
            client.delete_row("topic_progress", row["$id"])  # derived table: hard delete is allowed (README, M03)


def rebuild_topic_progress(client, child_id: str, entries=None) -> dict:
    """Rebuild a child's topic_progress from `entries` (default: the child's stored evidence). Unconditional: stored rows are
    overwritten even when the evidence is older, and rows without any evidence are deleted."""
    derived = derive_progress(child_evidence(client, child_id) if entries is None else entries)
    existing = {r["topic_id"] for r in client.list_rows("topic_progress", child_id=child_id)}
    _apply_progress(client, child_id, existing | set(derived), derived)
    return derived


# --- handover summaries: immutable generations -------------------------------------------------------------------------
def handover_source_key(*, assignment_id: str | None = None, session_id: str | None = None, generation: int = 1) -> str:
    if generation < 1:
        raise ValueError("generation starts at 1")
    if assignment_id and session_id:
        raise ValueError("a handover summary belongs to an assignment or a session, not both")
    if assignment_id:
        return f"assignment:{assignment_id}:gen:{generation}"
    if session_id:
        return f"session:{session_id}:gen:{generation}"
    raise ValueError("a handover summary needs an assignment or a session")


def save_handover(client, *, child_id: str, teacher_id: str, content: str, now: str, generation: int = 1,
                  assignment_id: str | None = None, session_id: str | None = None) -> dict:
    """Insert one generation. A duplicate of the same generation is a UniqueViolation (409); rows are never edited."""
    if assignment_id and session_id:
        raise CatalogueError("a handover summary belongs to an assignment or a session, not both")
    row = {"child_id": child_id, "teacher_id": teacher_id, "generation": generation, "snapshot_at": now, "content": content,
           "source_key": handover_source_key(assignment_id=assignment_id, session_id=session_id, generation=generation)}
    if assignment_id:
        row["assignment_id"] = assignment_id
    else:
        row["session_id"] = session_id
    return client.create_row("handover_summary", row)


def latest_handover(client, *, assignment_id: str | None = None, session_id: str | None = None) -> dict | None:
    key, value = ("assignment_id", assignment_id) if assignment_id else ("session_id", session_id)
    rows = client.list_rows("handover_summary", **{key: value})
    return max(rows, key=lambda r: r["generation"], default=None)


def ensure_handover(client, *, latest_log_at: str | None, now: str, **kwargs) -> dict:
    """Idempotent generation (L04 #106): keep the latest snapshot unless a log is newer than it, then store the next
    generation. A concurrent generator that loses the unique-key race returns the winner's row."""
    where = {k: kwargs.get(k) for k in ("assignment_id", "session_id") if kwargs.get(k)}
    latest = latest_handover(client, **where)
    if latest and (latest_log_at is None or parse_utc(latest["snapshot_at"]) >= parse_utc(latest_log_at)):
        return latest
    try:
        return save_handover(client, now=now, generation=(latest["generation"] + 1) if latest else 1, **kwargs)
    except UniqueViolation:
        return latest_handover(client, **where)


# --- level equivalence (matching only) -------------------------------------------------------------------------------
def add_level_equivalence(client, level_id: str, equivalent_level_id: str) -> dict:
    """Pair two levels of DIFFERENT curricula (CUR-4). Mapping is matching metadata, so it may be added to levels of a
    draft or published version, but not to a retired one. The pair is symmetric (pair_key)."""
    level_pair_key(level_id, equivalent_level_id)  # rejects a level paired with itself
    curricula = []
    for level_id_ in (level_id, equivalent_level_id):
        level = client.get_row("level", level_id_)
        if not _live(level):
            raise CatalogueError("level is deleted")
        version = client.get_row("catalogue_version", level["catalogue_version_id"])
        if version["status"] == "retired":
            raise VersionImmutable("retired catalogue versions get no new level equivalences")
        curricula.append(version["curriculum_id"])
    if curricula[0] == curricula[1]:
        raise CatalogueError("level equivalence maps levels of different curricula")
    return client.create_row("level_equivalence", with_level_equivalence_keys({"level_id": level_id, "equivalent_level_id": equivalent_level_id}))


def remove_level_equivalence(client, equivalence_id: str, now: str) -> dict:
    return client.update_row("level_equivalence", equivalence_id, schema_build.soft_delete_patch(_table("level_equivalence"), now))


# --- questions --------------------------------------------------------------------------------------------------------
def assert_two_person(author_id: str, approver_id: str | None) -> None:
    """A question needs an approver who is not its author (SR 7.7, ASM-2). The approver must also be a curator or senior
    teacher: a role check the core service does against role_assignment."""
    if not approver_id:
        raise TwoPersonViolation("a question needs an approver")
    if approver_id == author_id:
        raise TwoPersonViolation("a question cannot be approved by its author")


def check_question_transition(old: str, new: str) -> None:
    if old not in QUESTION_TRANSITIONS or new not in QUESTION_TRANSITIONS:
        raise CatalogueError(f"unknown question status in {old!r} -> {new!r}")
    if new not in QUESTION_TRANSITIONS[old]:
        raise CatalogueError(f"question transition {old} -> {new} is not allowed")


def assert_question_editable(status: str) -> None:
    """Only a draft can be edited; an approved question is retired and replaced, never changed under the papers using it."""
    if status != "draft":
        raise CatalogueError(f"question is {status!r}: only a draft can be edited")


def transition_question(client, question_id: str, new: str, now: str) -> dict:
    q = client.get_row("question", question_id)
    if new == "approved":
        raise CatalogueError("use approve_question (two-person rule)")
    check_question_transition(q["status"], new)
    patch = {"status": new}
    if new == "in_review":
        patch["submitted_at"] = now
    return client.update_row("question", question_id, patch)


def approve_question(client, question_id: str, approver_id: str, now: str) -> dict:
    q = client.get_row("question", question_id)
    check_question_transition(q["status"], "approved")
    assert_two_person(q["author_id"], approver_id)
    return client.update_row("question", question_id, {"status": "approved", "approver_id": approver_id, "approved_at": now})


def validate_question_row(q: dict) -> list[str]:
    """Data check for a question row (used by the core service before writes and by audits of stored data)."""
    e = []
    if q.get("status") == "approved":
        try:
            assert_two_person(q.get("author_id"), q.get("approver_id"))
        except TwoPersonViolation as ex:
            e.append(str(ex))
        if not q.get("approved_at"):
            e.append("an approved question needs approved_at")
    elif q.get("status") in ("draft", "in_review") and q.get("approver_id"):
        e.append("approver_id is set before approval")
    fmt = q.get("format")
    if fmt == "multiple_choice" and not q.get("options"):
        e.append("multiple choice needs options")
    if fmt == "picture" and not q.get("image_file_id"):
        e.append("a picture question needs image_file_id")
    if fmt == "read_aloud" and not q.get("audio_file_id"):
        e.append("a read-aloud question needs audio_file_id")
    return e


def approved_questions_for_topic(client, topic_id: str) -> list[dict]:
    return [q for q in client.list_rows("question", topic_id=topic_id) if q["status"] == "approved" and _live(q)]


def assert_marker_allowed(marker_id: str, child_teacher_user_ids) -> None:
    """Short answers are marked by a curator who is not the child's own teacher (ASM-7)."""
    if marker_id in set(child_teacher_user_ids):
        raise CatalogueError("a child's own teacher cannot mark their short answers")
