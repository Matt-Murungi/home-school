"""B03 (#71): billing domain schema, the additive columns on subscription/invoice, migration 0011."""

import copy
import io
import json
import re
import unittest

from helpers import APPWRITE_DIR

import migrate
import schema_build as sb
from fake_client import FakeAppwriteClient

DOMAINS = sb.load_domains()
BY_NAME = {d["domain"]: d for d in DOMAINS}
BILLING = BY_NAME["billing"]
TABLES = {t["$id"]: t for t in BILLING["tables"]}
NEW = {"subscription_transition", "billing_credit", "billing_payment", "billing_claim"}
SUB_EXTRA = {
    "version", "anchor_date", "period_index", "cancel_at_period_end", "pending_plan_id",
    "past_due_at", "first_payment_at", "last_payment_ref", "assignment_id", "is_trial", "created_at",
}
INV_EXTRA = {"plan_id", "period_index", "plan_price_ugx", "discount_ugx", "credit_ugx", "credit_ids", "payment_ref"}


def mutated(fn):
    domains = copy.deepcopy(DOMAINS)
    fn({d["domain"]: d for d in domains})
    return sb.validate(domains)


def cols(domain, table):
    return {c["key"]: c for t in BY_NAME[domain]["tables"] if t["$id"] == table for c in t["columns"]}


class Tables(unittest.TestCase):
    def test_exactly_the_four_tables_and_the_validator_passes(self):
        self.assertEqual(set(TABLES), NEW)
        self.assertEqual(sb.validate(DOMAINS), [])
        self.assertEqual(sb.soft_unique_violations(DOMAINS), [])

    def test_convention_row_security_empty_permissions_hard_delete_with_notes(self):
        for tid, t in TABLES.items():
            self.assertIs(t["rowSecurity"], True)
            self.assertEqual(t["$permissions"], [])
            self.assertEqual(BILLING["meta"][tid]["deletion"], "hard", tid)
            self.assertIn("Never soft-deleted", BILLING["meta"][tid]["note"], tid)
            self.assertFalse({"deleted_at", "updated_at"} & set(cols("billing", tid)), tid)

    def test_not_marked_append_only_because_two_tables_move_claimed_to_applied(self):
        for tid in NEW:
            self.assertNotIn("append_only", BILLING["meta"][tid])
        self.assertIn("not marked append-only", BILLING["meta"]["subscription_transition"]["note"])

    def test_delivery_columns_and_the_sweep_and_roll_indexes(self):
        tr = cols("billing", "subscription_transition")
        self.assertFalse(tr["dispatched_at"]["required"])
        self.assertEqual(tr["dispatched_at"]["type"], "datetime")
        self.assertEqual(tr["reason_truncated"]["type"], "boolean")
        self.assertIn("by_status_created", {i["key"] for i in TABLES["subscription_transition"]["indexes"]})
        sub_idx = {i["key"]: i["columns"] for t in BY_NAME["sessions"]["tables"] if t["$id"] == "subscription" for i in t["indexes"]}
        self.assertEqual(sub_idx["by_status_cancelled"], ["status", "cancelled_at"])  # reconciler
        self.assertEqual(sub_idx["by_status_period_start"], ["status", "period_start_at"])  # roll phase B
        self.assertEqual(sub_idx["by_status_period_end"], ["status", "period_end_at"])  # roll phase A (M02)

    def test_no_new_legacy_soft_unique_exemptions(self):
        self.assertEqual(len(sb.SOFT_UNIQUE_LEGACY), 6)

    def test_no_money_columns_and_no_floats(self):
        for tid in NEW:
            for k, c in cols("billing", tid).items():
                self.assertNotEqual(c["type"], "float", (tid, k))
                self.assertFalse(re.search(sb.MONEY_WORDS, k), (tid, k))

    def test_idempotency_uniques(self):
        uniq = lambda t: {tuple(i["columns"]) for i in TABLES[t]["indexes"] if i["type"] == "unique"}  # noqa: E731
        self.assertEqual(uniq("subscription_transition"), {("subscription_id", "to_version")})
        self.assertEqual(uniq("billing_credit"), {("subscription_id", "kind", "ref")})
        self.assertEqual(uniq("billing_payment"), {("provider", "payment_ref")})  # same as M04 payment
        self.assertEqual(uniq("billing_claim"), {("claim_key",)})

    def test_foreign_keys_declared_indexed_or_explained(self):
        rels = {r["from"] for r in BILLING["relations"]}
        self.assertEqual(
            rels,
            {"subscription_transition.subscription_id", "billing_credit.subscription_id",
             "billing_credit.applied_invoice_id", "billing_payment.invoice_id", "billing_claim.plan_id"},
        )
        for r in rels:
            t, c = r.split(".")
            self.assertIn(c, {i["columns"][0] for i in TABLES[t]["indexes"]}, r)
        for tid, m in BILLING["meta"].items():
            for k, why in m.get("non_fk", {}).items():
                self.assertTrue(why.strip(), (tid, k))

    def test_enums_match_the_code(self):
        sub_status = cols("sessions", "subscription")["status"]["elements"]
        for k in ("from_status", "to_status"):
            self.assertEqual(cols("billing", "subscription_transition")[k]["elements"], sub_status)
        self.assertEqual(cols("billing", "billing_payment")["status"]["elements"], ["applying", "applied", "flagged"])
        self.assertEqual(
            cols("billing", "billing_payment")["flag"]["elements"],
            ["amount_mismatch", "late_payment_after_cancellation", "duplicate_payment", "invoice_void"],
        )
        self.assertEqual(
            cols("billing", "subscription_transition")["status"]["elements"], ["claimed", "applied", "delivered"]
        )
        self.assertEqual(cols("billing", "billing_claim")["kind"]["elements"], ["invoice_paid", "child_live", "credit_applied"])
        self.assertEqual(cols("billing", "billing_credit")["kind"]["elements"], ["platform_miss", "late_payment_start"])

    def test_notes_do_not_overclaim(self):
        bad = re.compile(r"guarantee|only way|insert-only", re.I)
        texts = [BILLING["description"]] + [m["note"] for m in BILLING["meta"].values()]
        for t in texts:
            self.assertFalse(bad.search(t), t[:80])


class AdditiveColumns(unittest.TestCase):
    def test_subscription_gets_only_optional_additive_columns_with_indexed_foreign_keys(self):
        sub = cols("sessions", "subscription")
        self.assertTrue(SUB_EXTRA <= set(sub))
        for k in SUB_EXTRA:
            self.assertFalse(sub[k]["required"], k)
        idx = {i["columns"][0] for t in BY_NAME["sessions"]["tables"] if t["$id"] == "subscription" for i in t["indexes"]}
        self.assertTrue({"pending_plan_id", "assignment_id"} <= idx)
        self.assertIn("B03", BY_NAME["sessions"]["meta"]["subscription"]["note"])

    def test_invoice_gets_only_optional_additive_columns_and_amounts_stay_integer(self):
        inv = cols("money", "invoice")
        self.assertTrue(INV_EXTRA <= set(inv))
        for k in INV_EXTRA:
            self.assertFalse(inv[k]["required"], k)
        for k in ("plan_price_ugx", "discount_ugx", "credit_ugx", "amount"):
            self.assertEqual(inv[k]["type"], "integer")
        self.assertIn("by_plan", {i["key"] for t in BY_NAME["money"]["tables"] if t["$id"] == "invoice" for i in t["indexes"]})

    def test_invoice_unique_period_index_is_untouched(self):
        t = next(t for t in BY_NAME["money"]["tables"] if t["$id"] == "invoice")
        self.assertIn(["subscription_id", "period_start_date"], [i["columns"] for i in t["indexes"] if i["type"] == "unique"])


class ValidatorMutations(unittest.TestCase):
    def table(self, ds, name):
        return next(t for t in ds["billing"]["tables"] if t["$id"] == name)

    def test_unmutated_passes(self):
        self.assertEqual(mutated(lambda ds: None), [])

    def test_undeclared_foreign_key(self):
        def f(ds):
            self.table(ds, "billing_payment")["columns"].append({"key": "owner_id", "type": "varchar", "size": 36, "required": False})

        self.assertTrue(any("billing_payment.owner_id" in e for e in mutated(f)))

    def test_missing_foreign_key_index(self):
        def f(ds):
            t = self.table(ds, "billing_payment")
            t["indexes"] = [i for i in t["indexes"] if i["key"] != "by_invoice"]

        self.assertTrue(any("billing_payment.invoice_id" in e for e in mutated(f)))

    def test_float_money(self):
        def f(ds):
            t = next(t for t in ds["money"]["tables"] if t["$id"] == "invoice")
            next(c for c in t["columns"] if c["key"] == "credit_ugx")["type"] = "float"

        self.assertTrue(any("invoice.credit_ugx" in e for e in mutated(f)))

    def test_any_and_users_permissions(self):
        for perm in ('read("any")', 'read("users")', 'create("users")'):
            def f(ds, perm=perm):
                self.table(ds, "billing_credit")["$permissions"] = [perm]

            self.assertTrue(mutated(f), perm)

    def test_row_security_off(self):
        def f(ds):
            self.table(ds, "billing_claim")["rowSecurity"] = False

        self.assertTrue(mutated(f))

    def test_raw_unique_on_a_soft_table(self):
        def f(ds):
            ds["billing"]["meta"]["billing_credit"]["deletion"] = "soft"
            self.table(ds, "billing_credit")["columns"].append({"key": "deleted_at", "type": "datetime", "required": False})

        self.assertTrue(any("billing_credit" in e and "unique" in e for e in mutated(f)))

    def test_subscription_foreign_key_without_index_is_caught(self):
        def f(ds):
            t = next(t for t in ds["sessions"]["tables"] if t["$id"] == "subscription")
            t["indexes"] = [i for i in t["indexes"] if i["key"] != "by_assignment"]

        self.assertTrue(any("subscription.assignment_id" in e for e in mutated(f)))


class Migration(unittest.TestCase):
    MIGRATIONS = str(APPWRITE_DIR / "migrations")

    def run_migrations(self, client):
        out = io.StringIO()
        return migrate.run(client, "staging", self.MIGRATIONS, out=out, sleep=lambda s: None), out.getvalue()

    def test_script_exists_and_applies_only_billing_and_the_two_extended_tables(self):
        files = sorted((APPWRITE_DIR / "migrations").glob("*_billing_schema.py"))
        self.assertEqual(len(files), 1)
        text = files[0].read_text()
        self.assertIn('apply_domain(ctx.client, "billing")', text)
        self.assertNotIn("create_row", text)

    def test_applies_to_a_fresh_project_with_async_columns_and_rerun_is_a_noop(self):
        client = FakeAppwriteClient()
        result, out = self.run_migrations(client)
        self.assertEqual(result.exit_code, 0, out)
        for tid, t in TABLES.items():
            self.assertEqual(set(client.tables[tid]["columns"]), {c["key"] for c in t["columns"]}, tid)
            self.assertEqual(set(client.tables[tid]["indexes"]), {i["key"] for i in t["indexes"]}, tid)
            self.assertTrue(all(s == "available" for s in client.tables[tid]["columns"].values()), tid)
            self.assertIs(client.specs[tid]["rowSecurity"], True)
        self.assertTrue(SUB_EXTRA <= set(client.tables["subscription"]["columns"]))
        self.assertTrue(INV_EXTRA <= set(client.tables["invoice"]["columns"]))
        self.assertEqual(client.specs["billing_credit"]["indexes"]["unique_subscription_kind_ref"]["type"], "unique")
        before = (copy.deepcopy(client.tables), len(client.log), copy.deepcopy(client.specs))
        result, out = self.run_migrations(client)
        self.assertEqual(result.applied, [])
        self.assertEqual((client.tables, len(client.log), client.specs), before)

    def test_older_tables_get_the_new_columns_added_without_touching_existing_ones(self):
        client = FakeAppwriteClient()
        old = copy.deepcopy(next(t for t in BY_NAME["sessions"]["tables"] if t["$id"] == "subscription"))
        old["columns"] = [c for c in old["columns"] if c["key"] not in SUB_EXTRA]
        old["indexes"] = [i for i in old["indexes"] if i["columns"][0] not in ("pending_plan_id", "assignment_id")]
        client.ensure_table(old)
        result, out = self.run_migrations(client)
        self.assertEqual(result.exit_code, 0, out)
        self.assertTrue(SUB_EXTRA <= set(client.tables["subscription"]["columns"]))

    def test_generated_config_and_erd_are_current(self):
        self.assertEqual(sb.check(), [])
        self.assertEqual(sb.check_erd(), [])
        config = json.loads((APPWRITE_DIR / "appwrite.config.json").read_text())
        self.assertTrue(NEW <= {t["$id"] for t in config["tables"]})


if __name__ == "__main__":
    unittest.main()
