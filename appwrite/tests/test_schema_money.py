"""M04 (#39): money, tiers, safeguarding, audit, notification schema (domains money, safeguarding, platform).

Acceptance criteria and edge cases are mapped to tests in memory.md (M04 block).
"""

import copy
import io
import re
import unittest

from helpers import APPWRITE_DIR

import migrate
import schema_build as sb
from fake_client import FakeAppwriteClient

DOMAINS = sb.load_domains()
BY_NAME = {d["domain"]: d for d in DOMAINS}
TABLES = {t["$id"]: t for d in DOMAINS for t in d["tables"]}
META = {t: m for d in DOMAINS for t, m in d.get("meta", {}).items()}
MONEY = {t["$id"] for t in BY_NAME["money"]["tables"]}
SAFE = {t["$id"] for t in BY_NAME["safeguarding"]["tables"]}
PLATFORM = {t["$id"] for t in BY_NAME["platform"]["tables"]}
M04 = MONEY | SAFE | PLATFORM
ISSUE_TABLES = {
    "tier", "tier_history", "bonus_rule", "invoice", "payment", "earning", "payout_batch", "payout", "ledger_account",
    "ledger_transaction", "ledger_entry", "feedback", "safeguarding_case", "safeguarding_action", "audit_log",
    "notification",
}
APPEND_ONLY = {"ledger_entry", "ledger_transaction", "audit_log", "safeguarding_action"}


def cols(table):
    return {c["key"]: c for c in TABLES[table]["columns"]}


def idx(table):
    return {i["key"]: i for i in TABLES[table]["indexes"]}


def uniques(table):
    return {tuple(i["columns"]) for i in TABLES[table]["indexes"] if i["type"] == "unique"}


def mutated(fn):
    domains = copy.deepcopy(DOMAINS)
    fn({d["domain"]: d for d in domains})
    return sb.validate(domains)


class Tables(unittest.TestCase):
    def test_domains_exist_and_issue_tables_are_present_once(self):
        self.assertTrue({"money", "safeguarding", "platform"} <= set(BY_NAME))
        self.assertTrue(ISSUE_TABLES <= M04)
        all_ids = [t["$id"] for d in DOMAINS for t in d["tables"]]
        self.assertEqual(len(all_ids), len(set(all_ids)))

    def test_notification_tables_for_n01_are_defined_here_only(self):
        self.assertTrue({"notification", "notification_template", "notification_preference", "device_token"} <= PLATFORM)

    def test_sr_5_4_key_fields(self):
        want = {
            "tier": {"name", "rank", "base_rate", "criteria"},
            "tier_history": {"teacher_id", "from_tier_id", "to_tier_id", "reason", "approved_by_id", "effective_at"},
            "bonus_rule": {"type", "amount", "conditions", "active"},
            "invoice": {"subscription_id", "period_start_date", "period_end_date", "amount", "status", "due_at"},
            "payment": {"invoice_id", "provider", "provider_reference", "amount", "status", "idempotency_key", "raw_callback"},
            "earning": {"teacher_id", "session_id", "type", "amount", "status"},
            "payout_batch": {"period_start_date", "period_end_date", "status", "approved_by_id", "approved_at"},
            "payout": {"batch_id", "teacher_id", "amount", "provider", "provider_reference", "status", "idempotency_key"},
            "ledger_entry": {"transaction_id", "line", "account_id", "side", "amount", "reference_type", "reference_id", "posted_at"},
            "ledger_transaction": {"kind", "idempotency_key", "reference_type", "reference_id", "posted_at"},
            "ledger_account": {"code", "name", "type", "normal_side"},
            "feedback": {"session_id", "child_rating", "parent_comment"},
            "safeguarding_case": {"raised_by_id", "child_id", "teacher_id", "session_id", "category", "description", "status", "assigned_to_id"},
            "safeguarding_action": {"case_id", "action", "by_id", "acted_at"},
            "audit_log": {"actor_id", "action", "entity", "entity_id", "before", "after", "reason", "ip", "occurred_at"},
            "notification": {"user_id", "channel", "template_key", "payload", "status", "sent_at"},
        }
        for table, keys in want.items():
            self.assertTrue(keys <= set(cols(table)), f"{table}: missing {keys - set(cols(table))}")

    def test_payment_raw_callback_is_text_and_provider_columns_exist(self):
        self.assertEqual(cols("payment")["raw_callback"]["type"], "text")
        self.assertEqual(cols("payment")["provider"]["type"], "varchar")
        self.assertEqual(cols("payment")["provider_reference"]["type"], "varchar")
        self.assertFalse(cols("payment")["provider_reference"]["required"])

    def test_audit_log_before_after_are_text_and_actor_optional_for_system(self):
        self.assertEqual(cols("audit_log")["before"]["type"], "text")
        self.assertEqual(cols("audit_log")["after"]["type"], "text")
        self.assertFalse(cols("audit_log")["actor_id"]["required"])

    def test_schema_has_no_validation_errors(self):
        self.assertEqual(sb.validate(DOMAINS), [])


class IdempotencyAndUniqueness(unittest.TestCase):
    def test_payment_and_payout_have_unique_idempotency_keys(self):
        for t in ("payment", "payout"):
            self.assertIn(("idempotency_key",), uniques(t), t)
            self.assertTrue(cols(t)["idempotency_key"]["required"], t)

    def test_ledger_transaction_idempotency_and_single_reversal(self):
        self.assertIn(("idempotency_key",), uniques("ledger_transaction"))
        self.assertIn(("reverses_transaction_id",), uniques("ledger_transaction"))

    def test_ledger_entry_line_is_unique_per_transaction(self):
        self.assertIn(("transaction_id", "line"), uniques("ledger_entry"))

    def test_other_uniques(self):
        self.assertIn(("code",), uniques("ledger_account"))
        self.assertIn(("provider", "provider_reference"), uniques("payment"))
        self.assertIn(("batch_id", "teacher_id"), uniques("payout"))
        self.assertIn(("active_key",), uniques("payout_batch"))
        self.assertIn(("dedupe_key",), uniques("earning"))
        self.assertIn(("subscription_id", "period_start_date"), uniques("invoice"))
        self.assertIn(("active_key",), uniques("feedback"))
        self.assertIn(("active_key",), uniques("tier"))
        self.assertIn(("case_id",), uniques("case_status"))
        self.assertIn(("user_id",), uniques("notification_preference"))

    def test_unique_idempotency_columns_fit_unique_index_limits(self):
        # varchar sum <= 768 chars is enforced by the validator; make sure keys are long enough for uuids/provider refs
        self.assertGreaterEqual(cols("payment")["idempotency_key"]["size"], 64)
        self.assertGreaterEqual(cols("payout")["idempotency_key"]["size"], 64)

    def test_validator_would_reject_unindexed_foreign_key_in_money(self):
        def drop_index(ds):
            t = next(t for t in ds["money"]["tables"] if t["$id"] == "payment")
            t["indexes"] = [i for i in t["indexes"] if i["key"] != "by_invoice"]

        self.assertTrue(any("payment.invoice_id" in e for e in mutated(drop_index)))


class SoftDeleteRule(unittest.TestCase):
    """M02's rule: unique indexes on soft tables only over optional derived *_key columns; no new legacy exemptions."""

    def test_legacy_exemptions_are_not_extended(self):
        self.assertEqual(len(sb.SOFT_UNIQUE_LEGACY), 6)
        self.assertTrue(all(t not in M04 for t, _ in sb.SOFT_UNIQUE_LEGACY))

    def test_no_soft_table_in_m04_has_a_raw_unique_index(self):
        self.assertEqual(sb.soft_unique_violations(DOMAINS), [])

    def test_permanent_records_are_not_soft_deletable(self):
        for t in ("payment", "payout", "invoice", "earning", "ledger_account", "case_status", "notification_preference"):
            self.assertEqual(META[t]["deletion"], "hard", t)
            self.assertNotIn("deleted_at", cols(t), t)
        for t in APPEND_ONLY:
            self.assertNotIn("deleted_at", cols(t), t)

    def test_soft_tables_with_uniques_use_derived_keys_and_patch_nulls_them(self):
        for t in ("tier", "feedback", "payout_batch"):
            self.assertEqual(META[t]["deletion"], "soft")
            patch = sb.soft_delete_patch(TABLES[t], "2026-11-25T00:00:00.000+00:00")
            self.assertIsNone(patch["active_key"], t)


class ValidatorMutations(unittest.TestCase):
    """What a reviewer would try: each mutation must make validate() fail (and the unmutated schema passes)."""

    def table(self, ds, domain, name):
        return next(t for t in ds[domain]["tables"] if t["$id"] == name)

    def test_unmutated_schema_passes(self):
        self.assertEqual(mutated(lambda ds: None), [])

    def test_undeclared_foreign_key(self):
        def f(ds):
            self.table(ds, "money", "payment")["columns"].append({"key": "owner_id", "type": "varchar", "size": 36, "required": False})

        self.assertTrue(any("payment.owner_id" in e for e in mutated(f)))

    def test_missing_foreign_key_index(self):
        def f(ds):
            t = self.table(ds, "money", "ledger_entry")
            t["indexes"] = [i for i in t["indexes"] if i["key"] != "by_account"]

        self.assertTrue(any("ledger_entry.account_id" in e for e in mutated(f)))

    def test_float_money(self):
        def f(ds):
            c = next(c for c in self.table(ds, "money", "payment")["columns"] if c["key"] == "amount")
            c.pop("min", None)
            c["type"] = "float"

        self.assertTrue(any("payment.amount" in e for e in mutated(f)))

    def test_any_permission(self):
        def f(ds):
            self.table(ds, "money", "payment")["$permissions"] = ['read("any")']

        self.assertTrue(mutated(f))

    def test_table_level_label_read_on_safeguarding_tables(self):
        for t in ("safeguarding_case", "safeguarding_action", "case_status"):
            def f(ds, t=t):
                self.table(ds, "safeguarding", t)["$permissions"] = ['read("label:safeguardinglead")']

            self.assertTrue(mutated(f), t)

    def test_raw_unique_on_soft_table(self):
        def f(ds):
            t = self.table(ds, "money", "bonus_rule")
            t["indexes"].append({"key": "unique_name", "type": "unique", "columns": ["name"], "orders": ["ASC"]})

        self.assertTrue(any("bonus_rule.unique_name" in e for e in mutated(f)))

    def test_append_only_table_with_soft_delete_column(self):
        for t, dom in (("ledger_entry", "money"), ("audit_log", "platform"), ("safeguarding_action", "safeguarding")):
            def f(ds, t=t, dom=dom):
                self.table(ds, dom, t)["columns"].append({"key": "deleted_at", "type": "datetime", "required": False})

            self.assertTrue(any(t in e for e in mutated(f)), t)

    def test_duplicate_idempotency_key_index_is_detected_by_the_schema_tests(self):
        for t in ("payment", "payout"):
            ix = [i for i in TABLES[t]["indexes"] if i["key"] != "unique_idempotency_key"]
            self.assertNotIn(("idempotency_key",), {tuple(i["columns"]) for i in ix if i["type"] == "unique"})
            self.assertIn(("idempotency_key",), uniques(t))  # present now; a mutation removing it is what the line above models

    def test_ledger_entry_transaction_line_uniqueness_is_required(self):
        without = [i for i in TABLES["ledger_entry"]["indexes"] if i["key"] != "unique_transaction_line"]
        self.assertNotIn(("transaction_id", "line"), {tuple(i["columns"]) for i in without if i["type"] == "unique"})
        self.assertIn(("transaction_id", "line"), uniques("ledger_entry"))


class RoundTwoNotes(unittest.TestCase):
    def test_payment_note_has_no_stale_soft_delete_sentence(self):
        self.assertNotIn("after a soft delete", META["payment"]["note"])

    def test_retention_pointer_on_payment_payout_invoice(self):
        for t in ("payment", "payout", "invoice"):
            note = META[t]["note"]
            self.assertIn("DP-6", note, t)
            self.assertIn("DP-5", note, t)
            self.assertIn("lawyer-brief", note, t)

    def test_device_token_has_unique_hash_and_sensitivity_note(self):
        self.assertIn(("token_hash",), uniques("device_token"))
        self.assertTrue(cols("device_token")["token_hash"]["required"])
        self.assertEqual(cols("device_token")["token_hash"]["size"], 64)
        note = META["device_token"]["note"].lower()
        for word in ("sensitive", "never logged", "per-row", "token_hash"):
            self.assertIn(word, note)
        self.assertEqual(TABLES["device_token"]["$permissions"], [])

    def test_notification_and_audit_notes(self):
        self.assertIn("quiet hours", META["notification"]["note"].lower())
        self.assertIn("global", META["notification"]["note"].lower())
        self.assertIn("summaris", META["audit_log"]["note"].lower())


OVERCLAIM = re.compile(r"(?<!intended )insert-only[^.;(]{0,40}core service|only way(?! out of draft)|guarantee", re.I)


def overclaims(text):
    return OVERCLAIM.findall(text)


class DocConsistency(unittest.TestCase):
    """Schema notes must not claim enforcement that does not exist (the AppendOnlyRepository is opt-in, unused)."""

    def all_texts(self):
        for d in DOMAINS:
            yield f"{d['domain']} description", d.get("description", "")
            for t, m in d.get("meta", {}).items():
                yield f"{d['domain']}.{t}", m.get("note", "")

    def test_no_schema_note_overclaims_enforcement(self):
        for where, text in self.all_texts():
            self.assertEqual(overclaims(text), [], where)

    def test_overclaim_detector_fails_on_mutations(self):
        for bad in (
            "Insert-only in the core service (AppendOnlyRepository).",
            "insert-only repository in the core service",
            "This is the only way to write it",
            "The wrapper guarantees append-only",
        ):
            self.assertTrue(overclaims(bad), bad)
        self.assertEqual(overclaims("INTENDED insert-only via AppendOnlyRepository (opt-in, not wired yet)"), [])

    def test_append_only_notes_say_intended_opt_in_not_wired(self):
        for t in APPEND_ONLY:
            note = META[t]["note"]
            self.assertIn("INTENDED", note, t)
            self.assertIn("opt-in", note, t)
            self.assertIn("not wired yet", note, t)
            self.assertIn("appwrite/README.md", note, t)
        for name in ("money", "platform", "safeguarding"):
            self.assertNotIn("insert-only repository in the core service", BY_NAME[name]["description"])

    def test_device_token_upsert_rule_for_a_different_user(self):
        note = META["device_token"]["note"]
        for phrase in (
            "different user",
            "look up by token_hash",
            "update user_id",
            "rewrite the row's read permission",
            "clear revoked_at",
            "set last_seen_at",
            "never keep two live rows for one token",
            "revoke on logout",
        ):
            self.assertIn(phrase, note.lower())


class MoneyRules(unittest.TestCase):
    def test_money_columns_are_integer_ugx_and_no_floats(self):
        for t in M04:
            for k, c in cols(t).items():
                self.assertNotIn(c["type"], ("float", "double"), f"{t}.{k}")
                if re.search(r"(amount|base_rate)$", k):
                    self.assertEqual(c["type"], "integer", f"{t}.{k}")
        for t in ("payment", "payout", "ledger_entry", "ledger_transaction", "invoice", "earning", "bonus_rule"):
            self.assertIn("currency", cols(t), t)
            self.assertEqual(cols(t)["currency"]["default"], "UGX")

    def test_positive_amounts_where_zero_or_negative_make_no_sense(self):
        for t in ("payment", "payout", "ledger_entry"):
            self.assertEqual(cols(t)["amount"]["min"], 1, t)
        self.assertEqual(cols("ledger_transaction")["total_amount"]["min"], 1)
        self.assertEqual(cols("ledger_transaction")["entry_count"]["min"], 2)
        self.assertNotIn("min", cols("earning")["amount"])  # adjustments may be negative

    def test_ledger_entry_is_double_entry_shaped(self):
        side = cols("ledger_entry")["side"]
        self.assertEqual(side["elements"], ["debit", "credit"])
        self.assertTrue(side["required"])
        self.assertEqual(cols("ledger_account")["normal_side"]["elements"], ["debit", "credit"])
        self.assertNotIn("balance", " ".join(cols("ledger_account")))  # balances derived, never stored

    def test_timestamps_are_utc_datetimes(self):
        for t in M04:
            for k, c in cols(t).items():
                if c["type"] == "datetime":
                    self.assertTrue(k.endswith("_at"), f"{t}.{k}")
                if k.endswith("_at"):
                    self.assertEqual(c["type"], "datetime", f"{t}.{k}")

    def test_payout_second_confirmation_columns_sec_11(self):
        c = cols("payout_batch")
        self.assertTrue({"approved_by_id", "approved_at", "confirmed_by_id", "confirmed_at"} <= set(c))

    def test_feedback_rating_range(self):
        self.assertEqual((cols("feedback")["child_rating"]["min"], cols("feedback")["child_rating"]["max"]), (1, 5))


class AppendOnlyAndPermissions(unittest.TestCase):
    def test_append_only_tables_are_marked_immutable_in_schema(self):
        marked = {t for t, m in META.items() if m.get("append_only")}
        self.assertEqual(marked, APPEND_ONLY)
        for t in APPEND_ONLY:
            self.assertEqual(META[t]["deletion"], "immutable", t)
            for k in ("deleted_at", "updated_at", "revoked_at"):
                self.assertNotIn(k, cols(t), f"{t}.{k}")

    def test_no_client_write_or_public_permissions_on_any_m04_table(self):
        for t in M04:
            self.assertIs(TABLES[t]["rowSecurity"], True, t)
            self.assertEqual(TABLES[t]["$permissions"], [], t)

    def test_clients_cannot_update_or_delete_append_only_rows(self):
        """Edge case: update of a ledger row is rejected for clients by permissions: the table has no permissions at all and
        rows are created by the core service with read-only grants; the validator refuses any write grant."""
        for p in ('update("any")', 'delete("users")', 'update("label:opsadmin")', 'create("any")'):
            def add(ds, p=p):
                next(t for t in ds["money"]["tables"] if t["$id"] == "ledger_entry")["$permissions"] = [p]

            self.assertTrue(mutated(add), p)

    def test_safeguarding_tables_have_no_label_read_on_the_table(self):
        for t in SAFE:
            self.assertEqual(TABLES[t]["$permissions"], [], t)
        for p in ('read("label:safeguardinglead")', 'read("label:opsadmin")'):
            def add(ds, p=p):
                next(t for t in ds["safeguarding"]["tables"] if t["$id"] == "safeguarding_case")["$permissions"] = [p]

            self.assertTrue(mutated(add), p)

    def test_reporter_status_is_a_separate_table_without_case_content(self):
        self.assertTrue(set(cols("case_status")).isdisjoint({"description", "outcome", "category", "teacher_id", "child_id"}))

    def test_validator_rejects_soft_delete_columns_on_append_only(self):
        def add(ds):
            next(t for t in ds["money"]["tables"] if t["$id"] == "ledger_entry")["columns"].append(
                {"key": "deleted_at", "type": "datetime", "required": False}
            )

        self.assertTrue(any("ledger_entry" in e for e in mutated(add)))


class Relations(unittest.TestCase):
    def test_every_foreign_key_is_indexed_and_resolves_or_is_deferred(self):
        for d in (BY_NAME["money"], BY_NAME["safeguarding"], BY_NAME["platform"]):
            for r in d["relations"]:
                table, col = r["from"].split(".")
                self.assertTrue(any(i["columns"][0] == col for i in TABLES[table]["indexes"]), r["from"])
                if "deferred" in r:
                    self.assertIn(r["to"], {"session", "subscription"})
                else:
                    self.assertIn(r["to"], TABLES, r)

    def test_polymorphic_references_are_documented_non_fk(self):
        for t in ("ledger_entry", "ledger_transaction", "audit_log"):
            self.assertIn("reference_id" if t != "audit_log" else "entity_id", META[t]["non_fk"])

    def test_core_tier_relation_now_resolves(self):
        core = BY_NAME["core"]
        rel = next(r for r in core["relations"] if r["from"] == "teacher_profile.tier_id")
        self.assertIn("tier", TABLES)  # target exists; core.json still marks it deferred (M01 file is not edited here)
        self.assertEqual(rel["to"], "tier")


class Generated(unittest.TestCase):
    def test_generated_config_and_erd_up_to_date(self):
        self.assertEqual(sb.check(), [])
        self.assertEqual(sb.check_erd(), [])

    def test_erd_mentions_every_table(self):
        for name in ("money", "safeguarding", "platform"):
            text = (APPWRITE_DIR.parent / "docs" / "erd" / f"{name}.md").read_text()
            for t in BY_NAME[name]["tables"]:
                self.assertIn(t["$id"], text)

    def test_config_contains_the_tables(self):
        import json

        config = json.loads((APPWRITE_DIR / "appwrite.config.json").read_text())
        self.assertTrue(M04 <= {t["$id"] for t in config["tables"]})


class Migration(unittest.TestCase):
    MIGRATIONS = str(APPWRITE_DIR / "migrations")

    def run_migrations(self, client):
        out = io.StringIO()
        result = migrate.run(client, "staging", self.MIGRATIONS, out=out, sleep=lambda s: None)
        return result, out.getvalue()

    def test_money_migration_exists_and_is_numbered_after_core(self):
        names = sorted(p.name for p in (APPWRITE_DIR / "migrations").glob("*.py"))
        self.assertIn("0004_money_schema.py", names)

    def test_applies_to_a_fresh_project_with_async_columns(self):
        client = FakeAppwriteClient()
        result, out = self.run_migrations(client)
        self.assertEqual(result.exit_code, 0, out)
        self.assertIn(4, result.applied)
        for tid in M04:
            self.assertEqual(set(client.tables[tid]["columns"]), set(cols(tid)), tid)
            self.assertEqual(set(client.tables[tid]["indexes"]), set(idx(tid)), tid)
            self.assertTrue(all(s == "available" for s in client.tables[tid]["columns"].values()), tid)
            self.assertIs(client.specs[tid]["rowSecurity"], True)
            self.assertEqual(client.specs[tid]["$permissions"], [])

    def test_unique_indexes_reach_the_fake(self):
        client = FakeAppwriteClient()
        self.run_migrations(client)
        for t in ("payment", "payout"):
            self.assertEqual(client.tables[t]["indexes"]["unique_idempotency_key"], "available")
            self.assertEqual(client.specs[t]["indexes"]["unique_idempotency_key"]["type"], "unique")

    def test_rerun_is_a_noop(self):
        client = FakeAppwriteClient()
        self.run_migrations(client)
        before = (copy.deepcopy(client.tables), len(client.log), copy.deepcopy(client.specs))
        result, out = self.run_migrations(client)
        self.assertEqual(result.applied, [])
        self.assertEqual((client.tables, len(client.log), client.specs), before)

    def test_partial_project_converges(self):
        client = FakeAppwriteClient()
        client.ensure_table(copy.deepcopy(TABLES["payment"]))
        result, out = self.run_migrations(client)
        self.assertEqual(result.exit_code, 0, out)
        self.assertTrue(M04 <= set(client.tables))

    def test_no_seed_rows_or_personal_data(self):
        text = (APPWRITE_DIR / "migrations" / "0004_money_schema.py").read_text()
        self.assertNotIn("create_row", text)
        self.assertNotIn("seed", (APPWRITE_DIR / "migrations" / "0004_money_schema.py").name)


if __name__ == "__main__":
    unittest.main()
