"""M03 (#38): curriculum catalogue, session logs, progress and assessments schema + rules.

Acceptance criteria and edge cases are mapped to tests in memory.md (M03 block).
"""

import copy
import io
import json
import threading
import unittest

from helpers import APPWRITE_DIR

import catalogue_rules as rules
import migrate
import schema_build as sb
from fake_client import FakeAppwriteClient, UniqueViolation

DOMAINS = sb.load_domains()
TABLES = {t["$id"]: t for d in DOMAINS for t in d["tables"]}
M03 = {d["domain"]: d for d in DOMAINS if d["domain"] in ("catalogue", "records", "assessments")}
IDS = [t["$id"] for d in M03.values() for t in d["tables"]]
REQUIRED = {"curriculum", "catalogue_version", "level", "level_equivalence", "subject", "strand", "topic", "struggle_tag",
            "session_log", "session_log_topic", "session_log_struggle", "topic_progress", "school_upload", "handover_summary",
            "handover_rating", "question", "assessment_blueprint", "assessment", "assessment_item"}
NOW = "2026-12-01T06:00:00.000+00:00"


def cols(table):
    return {c["key"]: c for c in TABLES[table]["columns"]}


def mutated(fn):
    domains = copy.deepcopy(DOMAINS)
    fn({d["domain"]: d for d in domains})
    return sb.validate(domains)


def fresh_client():
    client = FakeAppwriteClient()
    for t in TABLES.values():
        client.ensure_table(t)
    for sid in ("s1", "s2", "s3", "s9"):  # sessions of child1 (rows injected: only child_id matters to the log rules)
        client.rows.setdefault("session", {})[sid] = {"$id": sid, "child_id": "child1"}
    return client


def make_version(client, status="draft", curriculum="cur1", version=1):
    return client.create_row("catalogue_version", rules.with_version_keys(
        {"curriculum_id": curriculum, "version": version, "status": status}), row_id=f"v{curriculum}_{version}")


def make_level_subject_topic(client, v, code="T1", parent=None):
    vid = v["$id"]
    lv = client.create_row("level", rules.with_level_keys({"catalogue_version_id": vid, "code": "P4", "name": "Primary 4", "sort_order": 4})) \
        if not client.rows.get("level") else next(iter(client.rows["level"].values()))
    sj = client.create_row("subject", rules.with_subject_keys({"catalogue_version_id": vid, "code": "MATH", "name": "Maths"})) \
        if not client.rows.get("subject") else next(iter(client.rows["subject"].values()))
    return client.create_row("topic", rules.with_topic_keys({
        "catalogue_version_id": vid, "subject_id": sj["$id"], "level_id": lv["$id"], "code": code, "title": "Fractions",
        "sort_order": 1, **({"parent_topic_id": parent} if parent else {})}))


class Tables(unittest.TestCase):
    def test_three_domain_files_hold_exactly_the_issue_tables_plus_strand(self):  # AC: tables per SR 5.3
        self.assertEqual(set(M03), {"catalogue", "records", "assessments"})
        self.assertEqual(set(IDS), REQUIRED)
        self.assertEqual(len(IDS), len(set(IDS)))
        for name in M03:
            self.assertTrue((APPWRITE_DIR / "schema" / f"{name}.json").exists())

    def test_sr_5_3_key_fields(self):
        want = {
            "curriculum": {"code", "name"},
            "catalogue_version": {"curriculum_id", "version", "status", "published_at"},
            "level": {"catalogue_version_id", "code", "sort_order"},
            "level_equivalence": {"level_id", "equivalent_level_id"},
            "subject": {"catalogue_version_id", "name"},
            "topic": {"subject_id", "level_id", "code", "title", "description", "sort_order", "parent_topic_id", "catalogue_version_id"},
            "struggle_tag": {"subject_id", "label"},
            "session_log": {"session_id", "child_id", "teacher_id", "submitted_at", "note", "homework", "edited_at"},
            "session_log_topic": {"session_log_id", "topic_id", "mastery"},
            "session_log_struggle": {"session_log_id", "struggle_tag_id", "topic_id"},
            "topic_progress": {"child_id", "topic_id", "current_mastery", "last_session_id", "updated_at"},
            "school_upload": {"child_id", "type", "file_id", "uploaded_at"},
            "handover_summary": {"child_id", "teacher_id", "content"},
            "handover_rating": {"handover_summary_id", "teacher_id", "rating"},
            "question": {"topic_id", "level_id", "format", "stem", "options", "answer_key", "difficulty", "status", "author_id", "approver_id"},
            "assessment_blueprint": {"curriculum_id", "level_id", "subject_id", "type", "rules"},
            "assessment": {"child_id", "blueprint_id", "type", "status", "started_at", "completed_at", "supervised_confirmed"},
            "assessment_item": {"assessment_id", "question_id", "response", "is_correct", "marked_by_id"},
        }
        for table, keys in want.items():
            self.assertTrue(keys <= set(cols(table)), f"{table}: missing {keys - set(cols(table))}")

    def test_enums_match_sr(self):
        self.assertEqual(cols("catalogue_version")["status"]["elements"], ["draft", "published", "retired"])
        self.assertEqual(cols("session_log_topic")["mastery"]["elements"], ["not_yet", "developing", "secure"])
        self.assertEqual(cols("topic_progress")["current_mastery"]["elements"], ["not_yet", "developing", "secure"])
        self.assertEqual(cols("question")["status"]["elements"], ["draft", "in_review", "approved", "retired"])
        self.assertEqual(cols("school_upload")["type"]["elements"], ["report", "current_topics"])
        self.assertEqual(cols("assessment_blueprint")["type"]["elements"], ["practice", "checkpoint"])
        self.assertEqual(cols("question")["format"]["elements"], ["multiple_choice", "short_answer", "picture", "read_aloud"])

    def test_every_foreign_key_is_declared_indexed_and_resolves(self):
        relations = [r for d in M03.values() for r in d["relations"]]
        self.assertGreater(len(relations), 30)
        for r in relations:
            table, col = r["from"].split(".")
            self.assertIn(r["to"], TABLES, r)
            self.assertTrue(any(i["columns"][0] == col for i in TABLES[table]["indexes"]), r)
        # every *_id column is a relation or a documented non_fk (file ids)
        for d in M03.values():
            declared = {r["from"] for r in d["relations"]}
            for t in d["tables"]:
                for c in t["columns"]:
                    if c["key"].endswith("_id") and f"{t['$id']}.{c['key']}" not in declared:
                        self.assertIn(c["key"], d["meta"][t["$id"]].get("non_fk", {}))

    def test_deferred_relations_from_earlier_tickets_now_resolve(self):
        # core/sessions pointed at curriculum, catalogue_version and level before these tables existed
        for d in DOMAINS:
            for r in d["relations"]:
                if r["to"] in ("curriculum", "catalogue_version", "level"):
                    self.assertIn(r["to"], TABLES)

    def test_topics_belong_to_a_catalogue_version(self):  # AC
        c = cols("topic")
        self.assertTrue(c["catalogue_version_id"]["required"])
        self.assertIn(("topic.catalogue_version_id", "catalogue_version"),
                      [(r["from"], r["to"]) for r in M03["catalogue"]["relations"]])
        self.assertIn(("topic.parent_topic_id", "topic"), [(r["from"], r["to"]) for r in M03["catalogue"]["relations"]])
        self.assertFalse(c["parent_topic_id"]["required"])
        self.assertTrue(c["sort_order"]["required"])
        for t in ("level", "subject", "strand"):
            self.assertTrue(cols(t)["catalogue_version_id"]["required"])

    def test_topic_progress_unique_child_topic(self):  # AC
        idx = {i["key"]: i for i in TABLES["topic_progress"]["indexes"]}
        self.assertEqual((idx["unique_child_topic"]["type"], idx["unique_child_topic"]["columns"]), ("unique", ["child_id", "topic_id"]))

    def test_one_log_per_session_unique_declared(self):
        idx = {i["key"]: i for i in TABLES["session_log"]["indexes"]}
        self.assertEqual(idx["unique_log_per_session"]["columns"], ["session_key"])

    def test_deletion_policies(self):
        for t, want in {"topic_progress": "hard", "handover_summary": "immutable", "handover_rating": "immutable"}.items():
            self.assertEqual(next(d for d in M03.values() if t in d["meta"])["meta"][t]["deletion"], want)
        for t in REQUIRED - {"topic_progress", "handover_summary", "handover_rating"}:
            self.assertEqual(next(d for d in M03.values() if t in d["meta"])["meta"][t]["deletion"], "soft", t)

    def test_row_security_and_no_table_permissions_anywhere(self):  # child data sensitivity
        for t in IDS:
            self.assertIs(TABLES[t]["rowSecurity"], True)
            self.assertEqual(TABLES[t]["$permissions"], [])

    def test_curators_and_clients_have_no_direct_write_permission(self):  # "How we'll know": curators have no direct write
        for t in IDS:
            self.assertEqual(sb._validate_table(TABLES[t], next(d for d in M03.values() if t in d["meta"])["meta"][t]), [])
        errors = mutated(lambda ds: ds["catalogue"]["tables"][6].__setitem__("$permissions", ['update("label:curator")']))
        self.assertTrue(any("topic" in e and "permission" in e for e in errors), errors)
        errors = mutated(lambda ds: ds["catalogue"]["tables"][6].__setitem__("$permissions", ['read("label:curator")']))
        self.assertTrue(any("topic" in e for e in errors), errors)

    def test_timestamps_money_and_no_personal_defaults(self):
        for t in IDS:
            for c in TABLES[t]["columns"]:
                if c["key"].endswith("_at"):
                    self.assertEqual(c["type"], "datetime")
                self.assertNotEqual(c["type"], "double")
        text = "".join((APPWRITE_DIR / "schema" / f"{n}.json").read_text() for n in M03)
        self.assertNotRegex(text, r"@[a-z]+\.[a-z]+")
        self.assertNotRegex(text, r"\+?256\d{9}")
        self.assertNotIn("price_", text)

    def test_sensitive_child_tables_are_documented_as_child_data(self):
        for t in ("session_log", "school_upload", "handover_summary", "assessment", "assessment_item"):
            note = next(d for d in M03.values() if t in d["meta"])["meta"][t]["note"].lower()
            self.assertIn("child data", note, t)
        self.assertIn("never sent", M03["assessments"]["meta"]["question"]["note"].lower())  # answer_key stays server side

    def test_validator_rejects_missing_fk_index_and_float(self):
        def drop(ds):
            t = next(x for x in ds["records"]["tables"] if x["$id"] == "session_log")
            t["indexes"] = [i for i in t["indexes"] if i["key"] != "by_session"]
        self.assertTrue(any("session_log.session_id" in e for e in mutated(drop)))

        def flt(ds):
            next(x for x in ds["records"]["tables"] if x["$id"] == "topic_progress")["columns"].append(
                {"key": "score", "type": "double", "required": False})
        self.assertTrue(mutated(flt))

    def test_validator_rejects_undeclared_fk_any_permission_and_float_money(self):  # reviewer mutations
        def undeclared(ds):
            next(x for x in ds["records"]["tables"] if x["$id"] == "school_upload")["columns"].append(
                {"key": "extra_id", "type": "varchar", "size": 36, "required": False})
        self.assertTrue(any("extra_id" in e for e in mutated(undeclared)))
        errors = mutated(lambda ds: ds["records"]["tables"][0].__setitem__("$permissions", ['read("any")']))
        self.assertTrue(any("session_log" in e for e in errors), errors)
        errors = mutated(lambda ds: ds["records"]["tables"][0].__setitem__("$permissions", ['read("users")']))
        self.assertTrue(any("session_log" in e for e in errors), errors)

        def money(ds):
            next(x for x in ds["assessments"]["tables"] if x["$id"] == "question")["columns"].append(
                {"key": "price_ugx", "type": "double", "required": False})
        self.assertTrue(mutated(money))

    def test_every_table_has_a_note(self):
        for d in M03.values():
            for t in d["tables"]:
                self.assertTrue(d["meta"][t["$id"]].get("note"), t["$id"])


class SoftDeleteUniqueRule(unittest.TestCase):
    def test_new_domains_obey_the_rule_without_exemptions(self):
        self.assertEqual(sb.soft_unique_violations([d for d in DOMAINS if d["domain"] in M03], legacy=frozenset()), [])
        self.assertEqual(len(sb.SOFT_UNIQUE_LEGACY), 6)  # pinned by M02: no M03 entry

    def test_raw_unique_on_a_soft_table_is_rejected(self):
        def raw(ds):
            t = next(x for x in ds["catalogue"]["tables"] if x["$id"] == "subject")
            t["indexes"].append({"key": "unique_raw", "type": "unique", "columns": ["code"], "orders": ["ASC"]})
        self.assertTrue(any("subject.unique_raw" in e for e in mutated(raw)))

    def test_soft_delete_frees_the_value_and_restore_recomputes(self):
        c = fresh_client()
        row = c.create_row("subject", rules.with_subject_keys({"catalogue_version_id": "v1", "code": "MATH", "name": "Maths"}))
        c.update_row("subject", row["$id"], sb.soft_delete_patch(TABLES["subject"], NOW))
        again = c.create_row("subject", rules.with_subject_keys({"catalogue_version_id": "v1", "code": "MATH", "name": "Maths"}))
        with self.assertRaises(UniqueViolation):  # restore recomputes keys: taken meanwhile -> 409
            c.update_row("subject", row["$id"], sb.soft_restore_patch(c.get_row("subject", row["$id"]), rules.with_subject_keys))
        c.update_row("subject", again["$id"], sb.soft_delete_patch(TABLES["subject"], NOW))
        c.update_row("subject", row["$id"], sb.soft_restore_patch(c.get_row("subject", row["$id"]), rules.with_subject_keys))

    def test_keys_for_every_derived_table_are_nulled_when_soft_deleted(self):
        for fn, row in [
            (rules.with_curriculum_keys, {"code": "UG"}), (rules.with_level_keys, {"catalogue_version_id": "v", "code": "P4"}),
            (rules.with_topic_keys, {"catalogue_version_id": "v", "subject_id": "s", "level_id": "l", "code": "c"}),
            (rules.with_struggle_tag_keys, {"label": "Carrying"}), (rules.with_session_log_keys, {"session_id": "s"}),
            (rules.with_log_topic_keys, {"session_log_id": "l", "topic_id": "t"}),
            (rules.with_log_struggle_keys, {"session_log_id": "l", "struggle_tag_id": "g"}),
            (rules.with_item_keys, {"assessment_id": "a", "question_id": "q"}),
            (rules.with_blueprint_keys, {"level_id": "l", "subject_id": "s", "type": "practice"}),
            (rules.with_level_equivalence_keys, {"level_id": "a", "equivalent_level_id": "b"}),
            (rules.with_version_keys, {"curriculum_id": "c", "version": 1, "status": "published"})]:
            live = fn(dict(row))
            gone = fn({**row, "deleted_at": NOW})
            keys = [k for k in live if k.endswith("_key")]
            self.assertTrue(keys, fn.__name__)
            for k in keys:
                self.assertIsNotNone(live[k], (fn.__name__, k))
                self.assertIsNone(gone[k], (fn.__name__, k))


class Catalogue(unittest.TestCase):
    def test_curriculum_code_and_level_codes_unique_per_version(self):
        c = fresh_client()
        c.create_row("curriculum", rules.with_curriculum_keys({"code": "UG", "name": "Uganda"}))
        with self.assertRaises(UniqueViolation):
            c.create_row("curriculum", rules.with_curriculum_keys({"code": "ug", "name": "Uganda 2"}))
        c.create_row("level", rules.with_level_keys({"catalogue_version_id": "v1", "code": "P4", "name": "P4", "sort_order": 4}))
        c.create_row("level", rules.with_level_keys({"catalogue_version_id": "v2", "code": "P4", "name": "P4", "sort_order": 4}))
        with self.assertRaises(UniqueViolation):
            c.create_row("level", rules.with_level_keys({"catalogue_version_id": "v1", "code": "P4", "name": "P4 again", "sort_order": 5}))

    def test_version_number_unique_and_one_published_per_curriculum(self):
        c = fresh_client()
        make_version(c, "published", version=1)
        with self.assertRaises(UniqueViolation):
            make_version(c, "draft", version=1)
        with self.assertRaises(UniqueViolation):  # second published for the same curriculum
            c.create_row("catalogue_version", rules.with_version_keys({"curriculum_id": "cur1", "version": 2, "status": "published"}))
        c.create_row("catalogue_version", rules.with_version_keys({"curriculum_id": "cur1", "version": 2, "status": "draft"}))
        c.create_row("catalogue_version", rules.with_version_keys({"curriculum_id": "cur2", "version": 1, "status": "published"}))

    def test_level_equivalence_is_symmetric_and_unique(self):
        self.assertEqual(rules.level_pair_key("b", "a"), rules.level_pair_key("a", "b"))
        c = fresh_client()
        c.create_row("level_equivalence", rules.with_level_equivalence_keys({"level_id": "a", "equivalent_level_id": "b"}))
        with self.assertRaises(UniqueViolation):
            c.create_row("level_equivalence", rules.with_level_equivalence_keys({"level_id": "b", "equivalent_level_id": "a"}))
        with self.assertRaises(ValueError):
            rules.level_pair_key("a", "a")

    def test_struggle_tag_label_unique_per_subject_scope(self):
        c = fresh_client()
        c.create_row("struggle_tag", rules.with_struggle_tag_keys({"label": "Carrying", "subject_id": "s1"}))
        c.create_row("struggle_tag", rules.with_struggle_tag_keys({"label": "Carrying", "subject_id": "s2"}))
        c.create_row("struggle_tag", rules.with_struggle_tag_keys({"label": "Carrying"}))
        with self.assertRaises(UniqueViolation):
            c.create_row("struggle_tag", rules.with_struggle_tag_keys({"label": " carrying ", "subject_id": "s1"}))

    def test_topic_code_unique_within_version_subject_level(self):
        c = fresh_client()
        v = make_version(c)
        make_level_subject_topic(c, v, "T1")
        with self.assertRaises(UniqueViolation):
            make_level_subject_topic(c, v, "T1")
        make_level_subject_topic(c, v, "T2")

    def test_topic_tree_and_ordering_are_index_backed(self):
        c = fresh_client()
        v = make_version(c)
        root = make_level_subject_topic(c, v, "T1")
        make_level_subject_topic(c, v, "T1a", parent=root["$id"])
        self.assertEqual(len(c.list_rows("topic", parent_topic_id=root["$id"])), 1)
        self.assertEqual(c.last_query_index, "by_parent")
        self.assertEqual(len(c.list_rows("topic", catalogue_version_id=v["$id"])), 2)

    def test_check_topic_refs_requires_one_version(self):
        c = fresh_client()
        v1, v2 = make_version(c, version=1), make_version(c, version=2)
        t1 = make_level_subject_topic(c, v1, "T1")
        other = c.create_row("subject", rules.with_subject_keys({"catalogue_version_id": v2["$id"], "code": "ENG", "name": "English"}))
        bad = {**t1, "subject_id": other["$id"]}
        with self.assertRaises(rules.CatalogueError):
            rules.check_topic_refs(c, bad)
        rules.check_topic_refs(c, t1)
        with self.assertRaises(rules.CatalogueError):
            rules.check_topic_refs(c, {**t1, "parent_topic_id": make_level_subject_topic(c, v2, "X")["$id"]})


class PublishedImmutability(unittest.TestCase):
    """AC: published versions are immutable. Curators have no write permission (schema), the core service rejects edits."""

    def test_assert_editable_only_for_draft(self):
        rules.assert_editable("draft")
        for status in ("published", "retired"):
            with self.assertRaises(rules.VersionImmutable):
                rules.assert_editable(status)
        with self.assertRaises(rules.VersionImmutable):
            rules.assert_editable("archived")

    def test_edit_of_a_draft_topic_works(self):
        c = fresh_client()
        t = make_level_subject_topic(c, make_version(c))
        out = rules.edit_catalogue_row(c, "topic", t["$id"], {"title": "Fractions and decimals"})
        self.assertEqual(out["title"], "Fractions and decimals")

    def test_edit_of_a_published_topic_is_rejected_and_row_unchanged(self):
        c = fresh_client()
        v = make_version(c)
        t = make_level_subject_topic(c, v)
        rules.publish_version(c, v["$id"], NOW)
        with self.assertRaises(rules.VersionImmutable):
            rules.edit_catalogue_row(c, "topic", t["$id"], {"title": "Changed"})
        self.assertEqual(c.get_row("topic", t["$id"])["title"], "Fractions")

    def test_every_version_scoped_table_is_guarded(self):
        c = fresh_client()
        v = make_version(c)
        t = make_level_subject_topic(c, v)
        rows = {"topic": t["$id"], "level": t["level_id"], "subject": t["subject_id"]}
        rules.publish_version(c, v["$id"], NOW)
        for table, rid in rows.items():
            with self.assertRaises(rules.VersionImmutable, msg=table):
                rules.edit_catalogue_row(c, table, rid, {"name": "x"} if table != "topic" else {"title": "x"})
            with self.assertRaises(rules.VersionImmutable, msg=table):
                rules.soft_delete_catalogue_row(c, table, rid, NOW)
        self.assertEqual(set(rules.VERSION_SCOPED_TABLES), {"level", "subject", "strand", "topic"})

    def test_adding_a_topic_to_a_published_version_is_rejected(self):
        c = fresh_client()
        v = make_version(c)
        t = make_level_subject_topic(c, v)
        rules.publish_version(c, v["$id"], NOW)
        with self.assertRaises(rules.VersionImmutable):
            rules.add_catalogue_row(c, "topic", rules.with_topic_keys(
                {"catalogue_version_id": v["$id"], "subject_id": t["subject_id"], "level_id": t["level_id"], "code": "NEW", "title": "x", "sort_order": 9}))
        self.assertEqual(len(c.rows["topic"]), 1)

    def test_retired_version_is_immutable_too(self):
        c = fresh_client()
        v = make_version(c)
        t = make_level_subject_topic(c, v)
        rules.publish_version(c, v["$id"], NOW)
        rules.retire_version(c, v["$id"], NOW)
        with self.assertRaises(rules.VersionImmutable):
            rules.edit_catalogue_row(c, "topic", t["$id"], {"title": "x"})

    def test_version_status_transitions(self):
        c = fresh_client()
        v = make_version(c)
        with self.assertRaises(rules.CatalogueError):
            rules.retire_version(c, v["$id"], NOW)  # draft cannot be retired
        out = rules.publish_version(c, v["$id"], NOW)
        self.assertEqual((out["status"], out["published_at"], out["published_key"]), ("published", NOW, "cur1"))
        with self.assertRaises(rules.CatalogueError):
            rules.publish_version(c, v["$id"], NOW)
        with self.assertRaises(rules.VersionImmutable):  # status cannot be walked back through the generic edit
            rules.edit_catalogue_row(c, "catalogue_version", v["$id"], {"status": "draft"})
        out = rules.retire_version(c, v["$id"], NOW)
        self.assertEqual((out["status"], out["retired_at"], out["published_key"]), ("retired", NOW, None))

    def test_publishing_a_new_version_retires_the_old_one_and_keeps_one_published(self):
        c = fresh_client()
        v1 = make_version(c, version=1)
        rules.publish_version(c, v1["$id"], NOW)
        v2 = make_version(c, version=2)
        rules.publish_version(c, v2["$id"], NOW)
        self.assertEqual(c.get_row("catalogue_version", v1["$id"])["status"], "retired")
        self.assertEqual(c.get_row("catalogue_version", v2["$id"])["status"], "published")

    def test_only_a_draft_version_can_be_soft_deleted(self):
        c = fresh_client()
        v = make_version(c)
        rules.publish_version(c, v["$id"], NOW)
        with self.assertRaises(rules.VersionImmutable):
            rules.soft_delete_catalogue_row(c, "catalogue_version", v["$id"], NOW)
        d = make_version(c, version=2)
        rules.soft_delete_catalogue_row(c, "catalogue_version", d["$id"], NOW)
        self.assertEqual(c.get_row("catalogue_version", d["$id"])["version_key"], None)

    def test_new_draft_can_be_copied_from_a_published_version(self):
        c = fresh_client()
        v = make_version(c)
        make_level_subject_topic(c, v, "T1")
        rules.publish_version(c, v["$id"], NOW)
        draft = rules.copy_version_as_draft(c, v["$id"])
        self.assertEqual((draft["status"], draft["version"]), ("draft", 2))
        topics = c.list_rows("topic", catalogue_version_id=draft["$id"])
        self.assertEqual([t["code"] for t in topics], ["T1"])
        self.assertNotEqual(topics[0]["$id"], next(iter(c.rows["topic"])))  # new ids; old rows untouched
        self.assertEqual(c.get_row("topic", topics[0]["$id"])["subject_id"] in c.rows["subject"], True)
        rules.edit_catalogue_row(c, "topic", topics[0]["$id"], {"title": "Reworded"})


class Logs(unittest.TestCase):
    def setup_child_log(self):
        c = fresh_client()
        v = make_version(c)
        t = make_level_subject_topic(c, v)
        child = {"$id": "child1", "catalogue_version_id": v["$id"]}
        return c, v, t, child

    def test_one_log_per_session_backstop(self):  # AC: one per session
        c, v, t, child = self.setup_child_log()
        c.create_row("session_log", rules.with_session_log_keys({"session_id": "s1", "child_id": "child1", "teacher_id": "t1", "submitted_at": NOW}))
        with self.assertRaises(UniqueViolation):
            c.create_row("session_log", rules.with_session_log_keys({"session_id": "s1", "child_id": "child1", "teacher_id": "t1", "submitted_at": NOW}))
        c.create_row("session_log", rules.with_session_log_keys({"session_id": "s2", "child_id": "child1", "teacher_id": "t1", "submitted_at": NOW}))

    def test_submit_is_idempotent_and_marks_edits(self):
        c, v, t, child = self.setup_child_log()
        args = dict(child=child, session_id="s1", teacher_id="t1", topics=[(t["$id"], "developing")], now=NOW)
        first = rules.submit_session_log(c, note="a", **args)
        second = rules.submit_session_log(c, note="b", **{**args, "now": "2026-12-01T09:00:00.000+00:00"})
        self.assertEqual(first["$id"], second["$id"])
        self.assertEqual(len(c.rows["session_log"]), 1)
        self.assertEqual(c.get_row("session_log", first["$id"])["note"], "b")
        self.assertEqual(c.get_row("session_log", first["$id"])["edited_at"], "2026-12-01T09:00:00.000+00:00")
        self.assertEqual(len(c.rows["session_log_topic"]), 1)

    def test_concurrent_submits_create_one_log(self):
        c, v, t, child = self.setup_child_log()
        results = []

        def go():
            try:
                rules.submit_session_log(c, child=child, session_id="s1", teacher_id="t1", topics=[(t["$id"], "secure")], now=NOW)
                results.append("ok")
            except Exception as e:  # pragma: no cover
                results.append(repr(e))

        threads = [threading.Thread(target=go) for _ in range(8)]
        [x.start() for x in threads]
        [x.join() for x in threads]
        self.assertEqual(results, ["ok"] * 8)
        self.assertEqual(len(c.rows["session_log"]), 1)

    def test_log_topic_must_be_in_the_childs_catalogue_version(self):  # SR 5.5
        c, v, t, child = self.setup_child_log()
        v2 = make_version(c, version=2)
        other = make_level_subject_topic(c, v2, "T9")
        rules.assert_topic_in_child_version(child, t)
        with self.assertRaises(rules.CatalogueError):
            rules.assert_topic_in_child_version(child, other)
        with self.assertRaises(rules.CatalogueError):
            rules.submit_session_log(c, child=child, session_id="s1", teacher_id="t1", topics=[(other["$id"], "secure")], now=NOW)
        self.assertEqual(c.rows.get("session_log", {}), {})  # nothing half written

    def test_mastery_is_validated(self):
        c, v, t, child = self.setup_child_log()
        with self.assertRaises(ValueError):
            rules.submit_session_log(c, child=child, session_id="s1", teacher_id="t1", topics=[(t["$id"], "mastered")], now=NOW)

    def test_topic_retired_in_new_version_old_logs_still_resolve(self):  # EDGE
        c = fresh_client()
        v1 = make_version(c, version=1)
        old_topic = make_level_subject_topic(c, v1, "FRAC")
        rules.publish_version(c, v1["$id"], NOW)
        child = {"$id": "child1", "catalogue_version_id": v1["$id"]}
        rules.submit_session_log(c, child=child, session_id="s1", teacher_id="t1", topics=[(old_topic["$id"], "developing")], now=NOW)
        # curators build version 2 WITHOUT the topic and publish it: version 1 retires, the topic is absent from v2
        v2 = rules.copy_version_as_draft(c, v1["$id"])
        for t in c.list_rows("topic", catalogue_version_id=v2["$id"]):
            rules.soft_delete_catalogue_row(c, "topic", t["$id"], NOW)
        rules.publish_version(c, v2["$id"], NOW)
        self.assertEqual(c.get_row("catalogue_version", v1["$id"])["status"], "retired")
        # chain: log -> log_topic -> topic -> catalogue_version, all still resolve and still point at version 1
        (log,) = c.rows["session_log"].values()
        (link,) = c.list_rows("session_log_topic", session_log_id=log["$id"])
        topic = c.get_row("topic", link["topic_id"])
        self.assertEqual(topic["$id"], old_topic["$id"])
        self.assertEqual(topic["catalogue_version_id"], v1["$id"])
        self.assertIsNone(topic.get("deleted_at"))  # the old row was never touched
        self.assertEqual(c.get_row("catalogue_version", topic["catalogue_version_id"])["curriculum_id"], "cur1")
        # the child is still on version 1 (mid-term) and can keep logging against it
        rules.submit_session_log(c, child=child, session_id="s2", teacher_id="t1", topics=[(old_topic["$id"], "secure")], now=NOW)
        # the new version has no live copy of the topic
        live_v2 = [t for t in c.list_rows("topic", catalogue_version_id=v2["$id"]) if not t.get("deleted_at")]
        self.assertEqual(live_v2, [])

    def test_topic_progress_unique_child_topic_on_the_fake(self):  # AC
        c = fresh_client()
        row = {"child_id": "c1", "topic_id": "t1", "current_mastery": "developing", "updated_at": NOW}
        c.create_row("topic_progress", row)
        with self.assertRaises(UniqueViolation):
            c.create_row("topic_progress", row)
        c.create_row("topic_progress", {**row, "topic_id": "t2"})
        c.create_row("topic_progress", {**row, "child_id": "c2"})

    def test_progress_upsert_is_idempotent_and_race_safe(self):
        c = fresh_client()
        errors = []

        def go(i):
            try:
                rules.upsert_topic_progress(c, "c1", "t1", "developing", "s1", NOW)
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
        [x.start() for x in threads]
        [x.join() for x in threads]
        self.assertEqual(errors, [])
        self.assertEqual(len(c.rows["topic_progress"]), 1)
        rules.upsert_topic_progress(c, "c1", "t1", "secure", "s2", NOW)
        (row,) = c.rows["topic_progress"].values()
        self.assertEqual((row["current_mastery"], row["last_session_id"]), ("secure", "s2"))

    def test_submit_updates_progress_and_rebuild_equals_incremental(self):
        c, v, t, child = self.setup_child_log()
        rules.submit_session_log(c, child=child, session_id="s1", teacher_id="t1", topics=[(t["$id"], "not_yet")], now="2026-12-01T06:00:00.000+00:00")
        rules.submit_session_log(c, child=child, session_id="s2", teacher_id="t1", topics=[(t["$id"], "secure")], now="2026-12-08T06:00:00.000+00:00")
        (row,) = c.rows["topic_progress"].values()
        self.assertEqual((row["current_mastery"], row["last_session_id"]), ("secure", "s2"))
        derived = rules.derive_progress([
            {"topic_id": t["$id"], "mastery": "secure", "session_id": "s2", "submitted_at": "2026-12-08T06:00:00.000+00:00"},
            {"topic_id": t["$id"], "mastery": "not_yet", "session_id": "s1", "submitted_at": "2026-12-01T06:00:00.000+00:00"}])
        self.assertEqual(derived[t["$id"]], {"current_mastery": "secure", "last_session_id": "s2", "updated_at": "2026-12-08T06:00:00.000+00:00"})
        # rebuild into an emptied table gives the same row
        before = dict(row)
        c.rows["topic_progress"].clear()
        rules.rebuild_topic_progress(c, "child1", [
            {"topic_id": t["$id"], "mastery": "not_yet", "session_id": "s1", "submitted_at": "2026-12-01T06:00:00.000+00:00"},
            {"topic_id": t["$id"], "mastery": "secure", "session_id": "s2", "submitted_at": "2026-12-08T06:00:00.000+00:00"}])
        (again,) = c.rows["topic_progress"].values()
        for k in ("child_id", "topic_id", "current_mastery", "last_session_id", "updated_at"):
            self.assertEqual(again[k], before[k])

    def test_soft_deleted_log_releases_the_session(self):
        c = fresh_client()
        log = c.create_row("session_log", rules.with_session_log_keys({"session_id": "s1", "child_id": "child1", "teacher_id": "t1", "submitted_at": NOW}))
        c.update_row("session_log", log["$id"], sb.soft_delete_patch(TABLES["session_log"], NOW))
        c.create_row("session_log", rules.with_session_log_keys({"session_id": "s1", "child_id": "child1", "teacher_id": "t1", "submitted_at": NOW}))

    def test_handover_snapshot_and_rating_uniques(self):
        c = fresh_client()
        row = {"child_id": "c1", "teacher_id": "t1", "assignment_id": "a1", "generation": 1,
               "source_key": rules.handover_source_key(assignment_id="a1"), "snapshot_at": NOW, "content": "{}"}
        s = c.create_row("handover_summary", row)
        with self.assertRaises(UniqueViolation):
            c.create_row("handover_summary", row)
        c.create_row("handover_rating", {"handover_summary_id": s["$id"], "teacher_id": "t1", "rating": "yes", "created_at": NOW})
        with self.assertRaises(UniqueViolation):
            c.create_row("handover_rating", {"handover_summary_id": s["$id"], "teacher_id": "t1", "rating": "no", "created_at": NOW})
        self.assertEqual(rules.handover_source_key(assignment_id="a1"), "assignment:a1:gen:1")
        self.assertEqual(rules.handover_source_key(session_id="s1", generation=3), "session:s1:gen:3")
        with self.assertRaises(ValueError):
            rules.handover_source_key()
        with self.assertRaises(ValueError):
            rules.handover_source_key(assignment_id="a1", generation=0)

    def test_handover_tables_have_no_update_columns(self):
        for t in ("handover_summary", "handover_rating"):
            self.assertNotIn("updated_at", cols(t))
            self.assertNotIn("deleted_at", cols(t))


def log_with(c, child, session_id, topics, now, **kw):
    return rules.submit_session_log(c, child=child, session_id=session_id, teacher_id="t1", topics=topics, now=now, **kw)


D1, D2, D3 = "2026-12-01T06:00:00.000+00:00", "2026-12-08T06:00:00.000+00:00", "2026-12-15T06:00:00.000+00:00"


class Round2References(unittest.TestCase):  # finding 1
    def draft(self):
        c = fresh_client()
        v = make_version(c)
        t = make_level_subject_topic(c, v, "T1")
        return c, v, t

    def other_version_rows(self, c):
        v2 = make_version(c, version=2)
        return (c.create_row("subject", rules.with_subject_keys({"catalogue_version_id": v2["$id"], "code": "ENG", "name": "English"})),
                c.create_row("level", rules.with_level_keys({"catalogue_version_id": v2["$id"], "code": "P5", "name": "P5", "sort_order": 5})))

    def test_edit_to_a_subject_or_level_of_another_version_is_rejected(self):
        c, v, t = self.draft()
        subject, level = self.other_version_rows(c)
        for patch in ({"subject_id": subject["$id"]}, {"level_id": level["$id"]}):
            with self.assertRaises(rules.CatalogueError):
                rules.edit_catalogue_row(c, "topic", t["$id"], patch)
        row = c.get_row("topic", t["$id"])
        self.assertEqual((row["subject_id"], row["level_id"]), (t["subject_id"], t["level_id"]))

    def test_edit_strand_reference_to_another_version_is_rejected(self):
        c, v, t = self.draft()
        subject, level = self.other_version_rows(c)
        strand = rules.add_catalogue_row(c, "strand", {"catalogue_version_id": v["$id"], "subject_id": t["subject_id"], "level_id": t["level_id"], "name": "Numbers", "sort_order": 1})
        foreign = c.create_row("strand", {"catalogue_version_id": subject["catalogue_version_id"], "subject_id": subject["$id"], "level_id": level["$id"], "name": "X", "sort_order": 1})
        with self.assertRaises(rules.CatalogueError):
            rules.edit_catalogue_row(c, "topic", t["$id"], {"strand_id": foreign["$id"]})
        with self.assertRaises(rules.CatalogueError):  # strand edit: its subject must be in the strand's version
            rules.edit_catalogue_row(c, "strand", strand["$id"], {"subject_id": subject["$id"]})
        with self.assertRaises(rules.CatalogueError):
            rules.add_catalogue_row(c, "strand", {"catalogue_version_id": v["$id"], "subject_id": subject["$id"], "level_id": t["level_id"], "name": "Bad", "sort_order": 2})
        ok = rules.edit_catalogue_row(c, "topic", t["$id"], {"strand_id": strand["$id"]})
        self.assertEqual(ok["strand_id"], strand["$id"])
        rules.edit_catalogue_row(c, "strand", strand["$id"], {"name": "Numbers and counting"})

    def test_parent_cannot_be_the_topic_itself(self):
        c, v, t = self.draft()
        with self.assertRaises(rules.CatalogueError):
            rules.edit_catalogue_row(c, "topic", t["$id"], {"parent_topic_id": t["$id"]})
        self.assertIsNone(c.get_row("topic", t["$id"]).get("parent_topic_id"))

    def test_parent_cycles_are_rejected_for_any_length(self):
        c, v, a = self.draft()
        chain = [a]
        for i in range(3):
            chain.append(make_level_subject_topic(c, v, f"C{i}", parent=chain[-1]["$id"]))
        for ancestor in chain[:-1]:  # making any ancestor a child of the last one closes a cycle
            with self.assertRaises(rules.CatalogueError, msg=ancestor["code"]):
                rules.edit_catalogue_row(c, "topic", ancestor["$id"], {"parent_topic_id": chain[-1]["$id"]})
        self.assertIsNone(c.get_row("topic", a["$id"]).get("parent_topic_id"))

    def test_reference_checks_happy_path(self):
        c, v, a = self.draft()
        b = make_level_subject_topic(c, v, "B")
        out = rules.edit_catalogue_row(c, "topic", b["$id"], {"parent_topic_id": a["$id"], "title": "Child topic"})
        self.assertEqual(out["parent_topic_id"], a["$id"])
        out = rules.edit_catalogue_row(c, "topic", b["$id"], {"parent_topic_id": None})
        self.assertIsNone(out["parent_topic_id"])
        rules.edit_catalogue_row(c, "topic", b["$id"], {"subject_id": a["subject_id"], "level_id": a["level_id"]})

    def test_parent_in_another_version_is_rejected_on_edit(self):
        c, v, a = self.draft()
        v2 = make_version(c, version=2)
        foreign = make_level_subject_topic(c, v2, "F")
        with self.assertRaises(rules.CatalogueError):
            rules.edit_catalogue_row(c, "topic", a["$id"], {"parent_topic_id": foreign["$id"]})


class Round2Progress(unittest.TestCase):  # finding 2
    def setup(self):
        c = fresh_client()
        v = make_version(c)
        t = make_level_subject_topic(c, v)
        return c, {"$id": "child1", "catalogue_version_id": v["$id"]}, t

    def progress(self, c, topic_id):
        rows = [r for r in c.rows.get("topic_progress", {}).values() if r["topic_id"] == topic_id]
        return rows[0] if rows else None

    def test_resubmitting_the_latest_log_without_the_topic_returns_progress_to_the_earlier_session(self):
        c, child, t = self.setup()
        log_with(c, child, "s1", [(t["$id"], "developing")], D1)
        log_with(c, child, "s2", [(t["$id"], "secure")], D2)
        self.assertEqual(self.progress(c, t["$id"])["current_mastery"], "secure")
        log_with(c, child, "s2", [], D3)
        row = self.progress(c, t["$id"])
        self.assertEqual((row["current_mastery"], row["last_session_id"], row["updated_at"]), ("developing", "s1", D1))

    def test_lowering_mastery_in_the_latest_log_lowers_progress(self):
        c, child, t = self.setup()
        log_with(c, child, "s1", [(t["$id"], "developing")], D1)
        log_with(c, child, "s2", [(t["$id"], "secure")], D2)
        log_with(c, child, "s2", [(t["$id"], "not_yet")], D3)
        self.assertEqual(self.progress(c, t["$id"])["current_mastery"], "not_yet")

    def test_topic_with_no_remaining_evidence_loses_its_progress_row(self):
        c, child, t = self.setup()
        log_with(c, child, "s1", [(t["$id"], "developing")], D1)
        log_with(c, child, "s1", [], D2)
        self.assertIsNone(self.progress(c, t["$id"]))  # absence = no evidence (not "not_yet", which means taught but not understood)
        self.assertEqual(len(c.rows["topic_progress"]), 0)

    def test_editing_an_older_log_does_not_regress_newer_progress(self):
        c, child, t = self.setup()
        log_with(c, child, "s1", [(t["$id"], "developing")], D1)
        log_with(c, child, "s2", [(t["$id"], "secure")], D2)
        log_with(c, child, "s1", [(t["$id"], "not_yet")], D3)  # resubmitted later, but s1 is still the older session
        row = self.progress(c, t["$id"])
        self.assertEqual((row["current_mastery"], row["last_session_id"]), ("secure", "s2"))

    def test_rebuild_overwrites_unconditionally_and_deletes_rows_without_evidence(self):
        c, child, t = self.setup()
        other = make_level_subject_topic(c, c.get_row("catalogue_version", child["catalogue_version_id"]), "T2")
        for tid, m in ((t["$id"], "secure"), (other["$id"], "secure")):
            c.create_row("topic_progress", {"child_id": "child1", "topic_id": tid, "current_mastery": m, "last_session_id": "sX", "updated_at": D3})
        derived = rules.rebuild_topic_progress(c, "child1", [{"topic_id": t["$id"], "mastery": "developing", "session_id": "s1", "submitted_at": D1}])
        row = self.progress(c, t["$id"])
        self.assertEqual((row["current_mastery"], row["updated_at"]), ("developing", D1))  # older evidence than the stored row wins
        self.assertIsNone(self.progress(c, other["$id"]))
        self.assertEqual(set(derived), {t["$id"]})
        again = rules.rebuild_topic_progress(c, "child1", [{"topic_id": t["$id"], "mastery": "developing", "session_id": "s1", "submitted_at": D1}])
        self.assertEqual(again, derived)
        self.assertEqual(len(c.rows["topic_progress"]), 1)

    def test_rebuild_from_the_stored_logs_equals_the_incremental_result(self):
        c, child, t = self.setup()
        log_with(c, child, "s1", [(t["$id"], "developing")], D1)
        log_with(c, child, "s2", [(t["$id"], "secure")], D2)
        before = dict(self.progress(c, t["$id"]))
        c.rows["topic_progress"].clear()
        rules.rebuild_topic_progress(c, "child1")  # collects the evidence from the child's live logs
        after = self.progress(c, t["$id"])
        for k in ("current_mastery", "last_session_id", "updated_at"):
            self.assertEqual(after[k], before[k])

    def test_soft_deleted_logs_and_topics_are_not_evidence(self):
        c, child, t = self.setup()
        log_with(c, child, "s1", [(t["$id"], "developing")], D1)
        (log,) = c.rows["session_log"].values()
        c.update_row("session_log", log["$id"], sb.soft_delete_patch(TABLES["session_log"], D2))
        rules.rebuild_topic_progress(c, "child1")
        self.assertIsNone(self.progress(c, t["$id"]))

    def test_upsert_force_overrides_the_stale_guard(self):
        c = fresh_client()
        rules.upsert_topic_progress(c, "c1", "t1", "secure", "s2", D2)
        rules.upsert_topic_progress(c, "c1", "t1", "developing", "s1", D1)  # stale: ignored
        self.assertEqual(next(iter(c.rows["topic_progress"].values()))["current_mastery"], "secure")
        rules.upsert_topic_progress(c, "c1", "t1", "developing", "s1", D1, force=True)
        self.assertEqual(next(iter(c.rows["topic_progress"].values()))["current_mastery"], "developing")

    def test_derive_progress_is_deterministic_on_ties(self):
        a = {"topic_id": "t", "mastery": "developing", "session_id": "s1", "log_id": "l1", "submitted_at": D1}
        b = {"topic_id": "t", "mastery": "secure", "session_id": "s2", "log_id": "l2", "submitted_at": D1}
        c3 = {"topic_id": "t", "mastery": "not_yet", "session_id": "s2", "log_id": "l3", "submitted_at": "2026-12-01T09:00:00.000+03:00"}  # same instant in UTC+3
        for order in ([a, b], [b, a]):
            self.assertEqual(rules.derive_progress(order)["t"]["last_session_id"], "s2")
        self.assertEqual(rules.derive_progress([b, c3])["t"]["current_mastery"], "not_yet")  # log id breaks the tie after session id
        self.assertEqual(rules.derive_progress([c3, b])["t"]["current_mastery"], "not_yet")

    def test_assessment_evidence_is_documented_as_follow_up(self):
        self.assertIn("Q04", (APPWRITE_DIR / "catalogue_rules.py").read_text())
        self.assertIn("Q05", (APPWRITE_DIR / "README.md").read_text())


class Round2Handover(unittest.TestCase):  # finding 3 and #108
    def base(self, **extra):
        return dict(child_id="c1", teacher_id="t1", assignment_id="a1", content="{}", now=NOW, **extra)

    def test_a_new_generation_is_accepted_and_old_ones_stay_readable(self):
        c = fresh_client()
        g1 = rules.save_handover(c, generation=1, **self.base())
        g2 = rules.save_handover(c, generation=2, **{**self.base(), "now": "2026-12-02T06:00:00.000+00:00"})
        self.assertEqual((g1["source_key"], g2["source_key"]), ("assignment:a1:gen:1", "assignment:a1:gen:2"))
        self.assertEqual(len(c.rows["handover_summary"]), 2)
        self.assertEqual(c.get_row("handover_summary", g1["$id"])["generation"], 1)  # unchanged and readable

    def test_duplicate_generation_is_rejected(self):
        c = fresh_client()
        rules.save_handover(c, generation=1, **self.base())
        with self.assertRaises(UniqueViolation):
            rules.save_handover(c, generation=1, **self.base())

    def test_latest_lookup_is_index_backed_and_picks_the_highest_generation(self):
        c = fresh_client()
        for g in (1, 3, 2):
            rules.save_handover(c, generation=g, **self.base())
        latest = rules.latest_handover(c, assignment_id="a1")
        self.assertEqual(latest["generation"], 3)
        self.assertEqual(c.last_query_index, "by_assignment_generation")
        idx = {i["key"]: i for i in TABLES["handover_summary"]["indexes"]}
        self.assertEqual((idx["by_assignment_generation"]["columns"], idx["by_assignment_generation"]["orders"]), (["assignment_id", "generation"], ["ASC", "DESC"]))
        self.assertEqual(idx["by_session_generation"]["columns"], ["session_id", "generation"])
        self.assertIsNone(rules.latest_handover(c, assignment_id="nope"))

    def test_regenerates_only_when_older_than_the_latest_log(self):
        c = fresh_client()
        first = rules.ensure_handover(c, latest_log_at=None, **self.base())
        same = rules.ensure_handover(c, latest_log_at=NOW, **self.base())  # log not newer than the snapshot
        self.assertEqual(first["$id"], same["$id"])
        newer = rules.ensure_handover(c, latest_log_at="2026-12-03T06:00:00.000+00:00", **{**self.base(), "now": "2026-12-03T07:00:00.000+00:00"})
        self.assertEqual(newer["generation"], 2)
        self.assertEqual(len(c.rows["handover_summary"]), 2)
        again = rules.ensure_handover(c, latest_log_at="2026-12-03T06:00:00.000+00:00", **{**self.base(), "now": "2026-12-03T08:00:00.000+00:00"})
        self.assertEqual(again["$id"], newer["$id"])

    def test_concurrent_generation_creates_one_row(self):
        c = fresh_client()
        out = []
        threads = [threading.Thread(target=lambda: out.append(rules.ensure_handover(c, latest_log_at=None, **self.base()))) for _ in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len(c.rows["handover_summary"]), 1)
        self.assertEqual(len({r["$id"] for r in out}), 1)

    def test_substitute_summary_is_keyed_by_session(self):
        c = fresh_client()
        rules.save_handover(c, generation=1, child_id="c1", teacher_id="t2", session_id="s9", content="{}", now=NOW)
        self.assertEqual(rules.latest_handover(c, session_id="s9")["source_key"], "session:s9:gen:1")

    def test_rating_is_yes_partly_no(self):
        self.assertEqual(cols("handover_rating")["rating"]["elements"], ["yes", "partly", "no"])
        self.assertNotIn("useful", cols("handover_rating"))
        self.assertIn("deviation", M03["records"]["meta"]["handover_rating"]["note"])
        self.assertIn("L05", M03["records"]["meta"]["handover_rating"]["note"])  # read receipt lives elsewhere


class Round2LevelEquivalence(unittest.TestCase):
    def setup(self):
        c = fresh_client()
        ug, cam = make_version(c, curriculum="ug", version=1), make_version(c, curriculum="cam", version=1)
        la = c.create_row("level", rules.with_level_keys({"catalogue_version_id": ug["$id"], "code": "P4", "name": "P4", "sort_order": 4}))
        lb = c.create_row("level", rules.with_level_keys({"catalogue_version_id": cam["$id"], "code": "S3", "name": "Stage 3", "sort_order": 3}))
        lc = c.create_row("level", rules.with_level_keys({"catalogue_version_id": ug["$id"], "code": "P5", "name": "P5", "sort_order": 5}))
        return c, ug, cam, la, lb, lc

    def test_pair_must_cross_curricula_and_is_symmetric(self):
        c, ug, cam, la, lb, lc = self.setup()
        row = rules.add_level_equivalence(c, la["$id"], lb["$id"])
        self.assertEqual(row["pair_key"], rules.level_pair_key(la["$id"], lb["$id"]))
        with self.assertRaises(UniqueViolation):
            rules.add_level_equivalence(c, lb["$id"], la["$id"])
        with self.assertRaises(rules.CatalogueError):  # same curriculum
            rules.add_level_equivalence(c, la["$id"], lc["$id"])
        with self.assertRaises(ValueError):
            rules.add_level_equivalence(c, la["$id"], la["$id"])

    def test_retired_version_levels_cannot_get_new_pairs_but_published_can(self):
        c, ug, cam, la, lb, lc = self.setup()
        rules.publish_version(c, ug["$id"], NOW)
        rules.add_level_equivalence(c, la["$id"], lb["$id"])  # mapping is matching data, allowed after publish
        rules.retire_version(c, ug["$id"], NOW)
        with self.assertRaises(rules.VersionImmutable):
            rules.add_level_equivalence(c, lc["$id"], lb["$id"])

    def test_remove_and_recreate(self):
        c, ug, cam, la, lb, lc = self.setup()
        row = rules.add_level_equivalence(c, la["$id"], lb["$id"])
        rules.remove_level_equivalence(c, row["$id"], NOW)
        self.assertIsNone(c.get_row("level_equivalence", row["$id"])["pair_key"])
        rules.add_level_equivalence(c, la["$id"], lb["$id"])

    def test_unknown_or_deleted_level_is_rejected(self):
        c, ug, cam, la, lb, lc = self.setup()
        c.update_row("level", la["$id"], sb.soft_delete_patch(TABLES["level"], NOW))
        with self.assertRaises(rules.CatalogueError):
            rules.add_level_equivalence(c, la["$id"], lb["$id"])


class Round3Liveness(unittest.TestCase):  # finding 1
    def draft(self):
        c = fresh_client()
        v = make_version(c)
        t = make_level_subject_topic(c, v, "T1")
        return c, v, t

    def test_topic_added_onto_a_soft_deleted_subject_is_rejected(self):
        c, v, t = self.draft()
        spare = c.create_row("subject", rules.with_subject_keys({"catalogue_version_id": v["$id"], "code": "ENG", "name": "English"}))
        rules.soft_delete_catalogue_row(c, "subject", spare["$id"], NOW)
        with self.assertRaises(rules.CatalogueError):
            rules.add_catalogue_row(c, "topic", rules.with_topic_keys(
                {"catalogue_version_id": v["$id"], "subject_id": spare["$id"], "level_id": t["level_id"], "code": "N", "title": "x", "sort_order": 3}))
        with self.assertRaises(rules.CatalogueError):
            rules.edit_catalogue_row(c, "topic", t["$id"], {"subject_id": spare["$id"]})

    def test_deleted_level_strand_and_parent_are_rejected_on_add_and_edit(self):
        c, v, t = self.draft()
        lvl = c.create_row("level", rules.with_level_keys({"catalogue_version_id": v["$id"], "code": "P9", "name": "P9", "sort_order": 9}))
        strand = rules.add_catalogue_row(c, "strand", {"catalogue_version_id": v["$id"], "subject_id": t["subject_id"], "level_id": t["level_id"], "name": "S", "sort_order": 1})
        parent = make_level_subject_topic(c, v, "PAR")
        rules.soft_delete_catalogue_row(c, "level", lvl["$id"], NOW)
        rules.soft_delete_catalogue_row(c, "strand", strand["$id"], NOW)
        rules.soft_delete_catalogue_row(c, "topic", parent["$id"], NOW)
        for patch in ({"level_id": lvl["$id"]}, {"strand_id": strand["$id"]}, {"parent_topic_id": parent["$id"]}):
            with self.assertRaises(rules.CatalogueError, msg=str(patch)):
                rules.edit_catalogue_row(c, "topic", t["$id"], patch)
        with self.assertRaises(rules.CatalogueError):
            rules.add_catalogue_row(c, "strand", {"catalogue_version_id": v["$id"], "subject_id": t["subject_id"], "level_id": lvl["$id"], "name": "S2", "sort_order": 2})

    def test_soft_delete_refuses_while_live_dependants_exist(self):
        c, v, t = self.draft()
        child = make_level_subject_topic(c, v, "CH", parent=t["$id"])
        strand = rules.add_catalogue_row(c, "strand", {"catalogue_version_id": v["$id"], "subject_id": t["subject_id"], "level_id": t["level_id"], "name": "S", "sort_order": 1})
        for table, rid in (("subject", t["subject_id"]), ("level", t["level_id"]), ("topic", t["$id"])):
            with self.assertRaises(rules.CatalogueError, msg=table):
                rules.soft_delete_catalogue_row(c, table, rid, NOW)
        with self.assertRaises(rules.CatalogueError):  # the draft version still has live content
            rules.soft_delete_catalogue_row(c, "catalogue_version", v["$id"], NOW)
        rules.edit_catalogue_row(c, "topic", child["$id"], {"strand_id": strand["$id"]})
        with self.assertRaises(rules.CatalogueError):
            rules.soft_delete_catalogue_row(c, "strand", strand["$id"], NOW)
        # leaves first works, then the parents
        rules.soft_delete_catalogue_row(c, "topic", child["$id"], NOW)
        rules.soft_delete_catalogue_row(c, "strand", strand["$id"], NOW)
        rules.soft_delete_catalogue_row(c, "topic", t["$id"], NOW)
        rules.soft_delete_catalogue_row(c, "subject", t["subject_id"], NOW)
        rules.soft_delete_catalogue_row(c, "level", t["level_id"], NOW)
        rules.soft_delete_catalogue_row(c, "catalogue_version", v["$id"], NOW)

    def test_questions_and_blueprints_also_block_deleting_their_topic_subject_level(self):
        c, v, t = self.draft()
        c.create_row("question", {"topic_id": t["$id"], "level_id": t["level_id"], "format": "short_answer", "stem": "?", "answer_key": "x",
                                  "difficulty": "easy", "status": "draft", "author_id": "u1"})
        with self.assertRaises(rules.CatalogueError):
            rules.soft_delete_catalogue_row(c, "topic", t["$id"], NOW)
        c.create_row("assessment_blueprint", rules.with_blueprint_keys({"curriculum_id": "cur1", "level_id": t["level_id"], "subject_id": t["subject_id"], "type": "practice", "rules": "{}"}))
        with self.assertRaises(rules.CatalogueError):
            rules.soft_delete_catalogue_row(c, "subject", t["subject_id"], NOW)

    def test_publish_then_copy_does_not_crash(self):
        c, v, t = self.draft()
        make_level_subject_topic(c, v, "T2", parent=t["$id"])
        rules.publish_version(c, v["$id"], NOW)
        draft = rules.copy_version_as_draft(c, v["$id"])
        self.assertEqual(len(c.list_rows("topic", catalogue_version_id=draft["$id"])), 2)

    def test_legacy_live_topic_under_a_deleted_subject_fails_clearly(self):
        c, v, t = self.draft()
        c.update_row("subject", t["subject_id"], {"deleted_at": NOW})  # legacy data written before the rule (bypassing the service)
        with self.assertRaises(rules.CatalogueError) as cm:  # publish refuses broken content
            rules.publish_version(c, v["$id"], NOW)
        self.assertIn("deleted", str(cm.exception))
        self.assertEqual(c.get_row("catalogue_version", v["$id"])["status"], "draft")
        c.update_row("catalogue_version", v["$id"], {"status": "published"})  # forced into a published state
        with self.assertRaises(rules.CatalogueError) as cm:  # copy: a clear error, never a KeyError
            rules.copy_version_as_draft(c, v["$id"])
        self.assertIn("deleted", str(cm.exception))
        self.assertEqual(len(c.rows["catalogue_version"]), 1)  # no half-made draft left behind

    def test_legacy_live_child_under_a_deleted_parent_fails_clearly(self):
        c, v, t = self.draft()
        child = make_level_subject_topic(c, v, "CH", parent=t["$id"])
        c.update_row("topic", t["$id"], {"deleted_at": NOW})
        c.update_row("catalogue_version", v["$id"], {"status": "published"})
        with self.assertRaises(rules.CatalogueError):
            rules.copy_version_as_draft(c, v["$id"])

    def test_edit_patch_cannot_forge_row_identity(self):
        c, v, t = self.draft()
        out = rules.edit_catalogue_row(c, "topic", t["$id"], {"title": "New", "$id": "forged", "$createdAt": "x", "$permissions": ['read("any")']})
        self.assertEqual((out["$id"], out["title"]), (t["$id"], "New"))
        self.assertNotIn("forged", c.rows["topic"])
        self.assertNotIn("$permissions", c.get_row("topic", t["$id"]))


class Round3ChildDrift(unittest.TestCase):  # finding 2
    def setup(self):
        c = fresh_client()
        v = make_version(c)
        t = make_level_subject_topic(c, v)
        return c, {"$id": "child1", "catalogue_version_id": v["$id"]}, {"$id": "child2", "catalogue_version_id": v["$id"]}, t

    def test_wrong_child_on_first_submit_is_rejected_and_nothing_is_written(self):
        c, ch1, ch2, t = self.setup()
        with self.assertRaises(rules.ChildMismatch):
            log_with(c, ch2, "s1", [(t["$id"], "secure")], D1)  # session s1 belongs to child1
        for table in ("session_log", "session_log_topic", "topic_progress"):
            self.assertEqual(c.rows.get(table, {}), {})

    def test_wrong_child_on_resubmit_is_rejected_and_original_progress_untouched(self):
        c, ch1, ch2, t = self.setup()
        log_with(c, ch1, "s1", [(t["$id"], "developing")], D1)
        before = {k: dict(v) for k, v in c.rows["topic_progress"].items()}
        with self.assertRaises(rules.ChildMismatch):
            log_with(c, ch2, "s1", [(t["$id"], "secure")], D2)
        self.assertEqual(c.rows["topic_progress"], before)
        (log,) = c.rows["session_log"].values()
        self.assertEqual(log["child_id"], "child1")
        self.assertEqual(len(c.rows["session_log_topic"]), 1)

    def test_existing_log_with_a_drifted_child_id_is_rejected_even_if_the_session_matches(self):
        c, ch1, ch2, t = self.setup()
        log_with(c, ch1, "s1", [(t["$id"], "developing")], D1)
        (log,) = c.rows["session_log"].values()
        c.update_row("session_log", log["$id"], {"child_id": "other"})  # corrupted data
        with self.assertRaises(rules.ChildMismatch):
            log_with(c, ch1, "s1", [(t["$id"], "secure")], D2)

    def test_unknown_session_is_rejected(self):
        c, ch1, ch2, t = self.setup()
        with self.assertRaises(rules.CatalogueError):
            log_with(c, ch1, "nope", [(t["$id"], "secure")], D1)

    def test_matching_child_still_works(self):
        c, ch1, ch2, t = self.setup()
        log_with(c, ch1, "s1", [(t["$id"], "secure")], D1)
        log_with(c, ch1, "s1", [(t["$id"], "developing")], D2)
        self.assertEqual(next(iter(c.rows["topic_progress"].values()))["current_mastery"], "developing")


class Round3Handover(unittest.TestCase):
    def test_assignment_and_session_together_is_an_error(self):
        c = fresh_client()
        with self.assertRaises(rules.CatalogueError):
            rules.save_handover(c, child_id="c1", teacher_id="t1", content="{}", now=NOW, assignment_id="a1", session_id="s1")
        with self.assertRaises(ValueError):
            rules.handover_source_key(assignment_id="a1", session_id="s1")
        self.assertEqual(c.rows.get("handover_summary", {}), {})


class Questions(unittest.TestCase):
    def q(self, **extra):
        row = {"topic_id": "t1", "level_id": "l1", "format": "multiple_choice", "stem": "1+1?", "options": "[\"1\", \"2\"]", "answer_key": "2", "difficulty": "easy",
               "status": "draft", "author_id": "u1"}
        row.update(extra)
        return row

    def test_two_person_rule(self):  # edge: author != approver
        rules.assert_two_person("u1", "u2")
        with self.assertRaises(rules.TwoPersonViolation):
            rules.assert_two_person("u1", "u1")
        for bad in ("", None):
            with self.assertRaises(rules.TwoPersonViolation):
                rules.assert_two_person("u1", bad)

    def test_approve_question_flow(self):
        c = fresh_client()
        q = c.create_row("question", self.q())
        with self.assertRaises(rules.CatalogueError):  # draft cannot be approved directly
            rules.approve_question(c, q["$id"], "u2", NOW)
        rules.transition_question(c, q["$id"], "in_review", NOW)
        with self.assertRaises(rules.TwoPersonViolation):
            rules.approve_question(c, q["$id"], "u1", NOW)
        self.assertEqual(c.get_row("question", q["$id"])["status"], "in_review")
        out = rules.approve_question(c, q["$id"], "u2", NOW)
        self.assertEqual((out["status"], out["approver_id"], out["approved_at"]), ("approved", "u2", NOW))
        rules.transition_question(c, q["$id"], "retired", NOW)

    def test_transition_table(self):
        self.assertEqual(rules.QUESTION_TRANSITIONS["retired"], frozenset())
        self.assertEqual(set(rules.QUESTION_TRANSITIONS), set(cols("question")["status"]["elements"]))
        with self.assertRaises(rules.CatalogueError):
            rules.check_question_transition("approved", "draft")
        rules.check_question_transition("in_review", "draft")  # sent back

    def test_validate_question_row_catches_bad_data(self):
        self.assertEqual(rules.validate_question_row(self.q()), [])
        self.assertEqual(rules.validate_question_row(self.q(status="approved", approver_id="u2", approved_at=NOW)), [])
        self.assertTrue(rules.validate_question_row(self.q(status="approved")))  # no approver
        self.assertTrue(rules.validate_question_row(self.q(status="approved", approver_id="u1", approved_at=NOW)))  # self approval
        self.assertTrue(rules.validate_question_row(self.q(status="draft", approver_id="u2")))  # approver before approval
        self.assertTrue(rules.validate_question_row(self.q(format="picture")))  # picture needs an image
        self.assertTrue(rules.validate_question_row(self.q(format="read_aloud")))  # read aloud needs audio
        self.assertTrue(rules.validate_question_row(self.q(options=None)))  # multiple choice needs options
        self.assertEqual(rules.validate_question_row(self.q(format="short_answer", options=None)), [])
        self.assertEqual(rules.validate_question_row(self.q(format="picture", image_file_id="f1")), [])

    def test_approved_question_is_not_editable(self):
        rules.assert_question_editable("draft")
        for s in ("in_review", "approved", "retired"):
            with self.assertRaises(rules.CatalogueError):
                rules.assert_question_editable(s)

    def test_only_approved_questions_may_be_assembled(self):
        c = fresh_client()
        a = c.create_row("question", self.q(status="approved", approver_id="u2", approved_at=NOW))
        c.create_row("question", self.q(topic_id="t1"))
        got = rules.approved_questions_for_topic(c, "t1")
        self.assertEqual([r["$id"] for r in got], [a["$id"]])

    def test_question_indexes_for_assembly(self):
        keys = {i["key"]: i["columns"] for i in TABLES["question"]["indexes"]}
        self.assertEqual(keys["by_topic_status"], ["topic_id", "status"])
        self.assertEqual(keys["by_level_status"], ["level_id", "status"])


class Assessments(unittest.TestCase):
    def test_blueprint_unique_per_level_subject_type(self):
        c = fresh_client()
        row = {"curriculum_id": "cur1", "level_id": "l1", "subject_id": "s1", "type": "checkpoint", "rules": "{}"}
        c.create_row("assessment_blueprint", rules.with_blueprint_keys(row))
        with self.assertRaises(UniqueViolation):
            c.create_row("assessment_blueprint", rules.with_blueprint_keys(row))
        c.create_row("assessment_blueprint", rules.with_blueprint_keys({**row, "type": "practice"}))

    def test_one_item_per_question_per_assessment(self):
        c = fresh_client()
        row = {"assessment_id": "a1", "question_id": "q1", "position": 1}
        c.create_row("assessment_item", rules.with_item_keys(row))
        with self.assertRaises(UniqueViolation):
            c.create_row("assessment_item", rules.with_item_keys({**row, "position": 2}))

    def test_curator_marker_may_not_be_the_childs_own_teacher(self):  # ASM-7
        rules.assert_marker_allowed("curator1", ["teacherA"])
        with self.assertRaises(rules.CatalogueError):
            rules.assert_marker_allowed("teacherA", ["teacherA"])

    def test_assessments_of_a_child_are_index_backed(self):
        c = fresh_client()
        c.create_row("assessment", {"child_id": "c1", "blueprint_id": "b1", "type": "practice", "status": "in_progress", "started_at": NOW})
        self.assertEqual(len(c.list_rows("assessment", child_id="c1")), 1)
        self.assertEqual(c.last_query_index, "by_child_started")


class Convention(unittest.TestCase):
    def test_domain_files_are_generated_into_config_and_erd(self):
        self.assertEqual(sb.check(), [])
        self.assertEqual(sb.check_erd(), [])
        for name, d in M03.items():
            erd = (APPWRITE_DIR.parent / "docs" / "erd" / f"{name}.md").read_text()
            for t in d["tables"]:
                self.assertIn(t["$id"], erd)
        config = json.loads((APPWRITE_DIR / "appwrite.config.json").read_text())
        self.assertTrue(REQUIRED <= {t["$id"] for t in config["tables"]})

    def test_buckets_for_child_uploads_and_question_media(self):
        config = json.loads((APPWRITE_DIR / "appwrite.config.json").read_text())
        buckets = {b["$id"]: b for b in config["buckets"]}
        for name in ("school-reports", "question-media"):
            self.assertIn(name, buckets)
            self.assertIs(buckets[name]["fileSecurity"], True)
            self.assertEqual(buckets[name]["$permissions"], [])
            self.assertTrue(buckets[name]["encryption"])
        self.assertNotIn("svg", buckets["school-reports"]["allowedFileExtensions"])

    def test_catalogue_rules_has_no_io_and_no_secrets(self):
        text = (APPWRITE_DIR / "catalogue_rules.py").read_text()
        for bad in ("urllib", "requests", "os.environ", "open("):
            self.assertNotIn(bad, text)

    def test_readme_documents_catalogue_rules(self):
        readme = (APPWRITE_DIR / "README.md").read_text()
        for word in ("assert_editable", "published", "two-person", "topic_progress"):
            self.assertIn(word, readme)


class Migration(unittest.TestCase):
    MIGRATIONS = str(APPWRITE_DIR / "migrations")

    def run_migrations(self, client):
        out = io.StringIO()
        result = migrate.run(client, "staging", self.MIGRATIONS, out=out, sleep=lambda s: None)
        return result, out.getvalue()

    def version(self):
        names = sorted(p.name for p in (APPWRITE_DIR / "migrations").glob("*_catalogue_schema.py"))
        self.assertEqual(len(names), 1)
        return int(names[0][:4])

    def test_migration_script_exists_and_versions_are_unique(self):
        v = self.version()
        versions = [int(p.name[:4]) for p in (APPWRITE_DIR / "migrations").glob("[0-9]*.py")]
        self.assertEqual(versions.count(v), 1)
        self.assertGreater(v, 2)

    def test_applies_to_a_fresh_project(self):  # AC: schema applies
        client = FakeAppwriteClient()
        result, out = self.run_migrations(client)
        self.assertEqual(result.exit_code, 0, out)
        self.assertIn(self.version(), result.applied)
        for tid in IDS:
            self.assertEqual(set(client.tables[tid]["columns"]), set(cols(tid)), tid)
            self.assertEqual(set(client.tables[tid]["indexes"]), {i["key"] for i in TABLES[tid]["indexes"]}, tid)
            self.assertIs(client.specs[tid]["rowSecurity"], True)
            self.assertEqual(client.specs[tid]["$permissions"], [])

    def test_rerun_is_a_noop(self):
        client = FakeAppwriteClient()
        self.run_migrations(client)
        before = (copy.deepcopy(client.tables), len(client.log), copy.deepcopy(client.specs))
        result, out = self.run_migrations(client)
        self.assertEqual(result.applied, [])
        self.assertIn(self.version(), result.skipped)
        self.assertEqual((client.tables, len(client.log), client.specs), before)

    def test_async_processing_semantics(self):
        client = FakeAppwriteClient(polls_until_available=3)
        result, out = self.run_migrations(client)
        self.assertEqual(result.exit_code, 0, out)
        for tid in IDS:
            self.assertTrue(all(s == "available" for s in client.column_statuses(tid).values()), tid)
            self.assertTrue(all(s == "available" for s in client.index_statuses(tid).values()), tid)

    def test_partial_resume_converges(self):
        client = FakeAppwriteClient()
        sb.apply_domain(client, "core")
        client.ensure_table(TABLES["curriculum"])
        client.ensure_table(TABLES["topic"])  # a half-applied earlier run (its indexes wait on columns)
        result, out = self.run_migrations(client)
        self.assertEqual(result.exit_code, 0, out)
        self.assertTrue(set(IDS) <= set(client.tables))

    def test_migration_inserts_no_rows(self):
        text = next((APPWRITE_DIR / "migrations").glob("*_catalogue_schema.py")).read_text()
        self.assertNotIn("create_row", text)


class DiffLive(unittest.TestCase):
    def test_diff_with_empty_snapshot_lists_repo_only_tables(self):
        import diff_live
        report = diff_live.diff(DOMAINS, {"databases": [{"id": "main", "tables": []}]}, only=sorted(REQUIRED))
        self.assertEqual(set(report["tables_repo_only"]), REQUIRED)


if __name__ == "__main__":
    unittest.main()
