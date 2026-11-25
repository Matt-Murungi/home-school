"""H05 (#134): the registry is a safe, complete declaration (every setting typed, bounded, with a
valid default, never secret-like) and docs/ops/settings.md cannot drift from it."""

import importlib.util
import re
from pathlib import Path

import pytest

from hs_core.app_settings import REGISTRY, AppSettings, env_defaults, validate_value
from hs_core.app_settings.docs import render_table, write_docs
from hs_core.settings import Settings
from settings_helpers import stack

ROOT = Path(__file__).resolve().parents[3]
KEY_RE = re.compile(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+")
# Words that mark a secret, a credential or infrastructure. A setting is non-secret by
# construction: no key, field name or description may carry them.
SECRET_WORDS = (
    "secret", "password", "passwd", "token", "apikey", "api_key", "credential", "pepper", "salt",
    "dsn", "jwt", "private", "signing", "certificate", "bearer", "otp_code", "endpoint", "url",
)  # fmt: skip
# Settings attributes that exist only when B03 / S01 have merged (their env defaults are wired by
# name already; until then the code default applies).
PENDING_ENV = {
    "billing_renewal_lead_days", "billing_grace_days", "billing_past_due_cancel_days",
    "billing_max_pause_days", "session_gen_horizon_days",
}  # fmt: skip


def all_defs():
    return list(REGISTRY.defs.values())


class TestDeclarations:
    def test_there_are_settings_in_every_group_and_every_group_is_titled(self):
        groups = {g for g, _ in REGISTRY.groups}
        assert {d.group for d in all_defs()} <= groups
        assert groups <= {d.group for d in all_defs()}

    @pytest.mark.parametrize("d", all_defs(), ids=lambda d: d.key)
    def test_each_definition_is_complete_and_safe(self, d):
        assert d.key == d.key.strip() and KEY_RE.fullmatch(d.key) and len(d.key) <= 64
        assert d.description.strip() and d.unit.strip()
        assert d.changed_by == ("superadmin",)
        assert d.requires_reason is True
        assert d.restart_required is False  # a setting that needs a deploy is not a setting
        assert d.applies in ("immediate", "next_day")
        if d.kind == "map":
            assert d.fields and len({f.name for f in d.fields}) == len(d.fields)
            for f in d.fields:
                assert f.lo <= f.default <= f.hi
        else:
            assert d.lo is not None and d.hi is not None and d.lo < d.hi
            assert d.lo <= d.default <= d.hi

    @pytest.mark.parametrize("d", all_defs(), ids=lambda d: d.key)
    def test_the_code_default_passes_its_own_validation(self, d):
        assert validate_value(d, d.code_default()) == d.code_default()

    def test_no_setting_can_be_a_secret(self):
        for d in all_defs():
            text = " ".join(
                [d.key, d.env or "", *(f.name for f in d.fields), *(f.env or "" for f in d.fields)]
            ).lower()
            for word in SECRET_WORDS:
                assert word not in text, (d.key, word)

    def test_no_registered_environment_attribute_is_a_secret_field_of_Settings(self):
        secret_fields = {
            n for n, f in Settings.model_fields.items() if "SecretStr" in str(f.annotation)
        }
        for d in all_defs():
            used = {d.env, *(f.env for f in d.fields)} - {None}
            assert not used & secret_fields, d.key

    def test_every_env_attribute_exists_or_belongs_to_an_unmerged_ticket(self):
        fields = set(Settings.model_fields)
        for d in all_defs():
            for name in {d.env, *(f.env for f in d.fields)} - {None}:
                assert name in fields or name in PENDING_ENV, (d.key, name)

    def test_constraints_reference_known_keys_and_hold_for_the_defaults(self):
        for c in REGISTRY.constraints:
            assert set(c.keys) <= set(REGISTRY.defs)
            assert c.check({k: REGISTRY.defs[k].code_default() for k in c.keys}), c.name

    def test_matching_weights_default_to_the_sr_values_and_add_up_to_100(self):
        w = REGISTRY.defs["matching.weights"].code_default()
        assert w == {
            "travel_time": 30, "tier": 20, "struggle_fit": 15, "workload_balance": 15,
            "reliability": 15, "cluster_bonus": 5,
        }  # fmt: skip
        assert sum(w.values()) == 100

    def test_billing_defaults_are_the_b03_owner_defaults(self):
        d = REGISTRY.defs
        assert d["billing.renewal_lead_days"].default == 5  # B03-1
        assert d["billing.grace_days"].default == 3  # B03-2, SR 8
        assert d["billing.past_due_cancel_days"].default == 14  # B03-2
        assert d["billing.max_pause_days"].default == 30  # B03-4
        assert d["scheduling.horizon_days"].default == 28  # S01

    def test_the_late_cancellation_default_equals_the_M02_constant(self):
        spec = importlib.util.spec_from_file_location(
            "session_rules_for_h05", ROOT / "appwrite" / "session_rules.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        d = REGISTRY.defs["session.late_cancellation_notice_hours"]
        assert d.default == mod.LATE_CANCELLATION_NOTICE_HOURS == 24

    def test_the_defaults_of_the_other_tickets_still_match_their_settings_fields(self):
        """If N01/X01 change a default in `Settings`, the registry default must follow."""
        s = Settings(_env_file=None)
        for key, v in env_defaults(s).items():
            d = REGISTRY.defs[key]
            assert v == (d.code_default() if d.kind == "map" else d.default), key


class TestEnvironmentIsTheDefault:
    def test_env_value_is_the_default_when_nothing_is_stored(self):
        s = Settings(_env_file=None, late_cancellation_notice_hours=36, notify_rate_limit=9)
        _raw, svc, _a, _c = stack(defaults=env_defaults(s))
        assert svc.get("session.late_cancellation_notice_hours") == 36
        assert svc.get("notify.rate_limit") == 9

    def test_quiet_hours_follow_both_environment_values(self):
        s = Settings(_env_file=None, notify_quiet_start_hour=22, notify_quiet_end_hour=6)
        _raw, svc, _a, _c = stack(defaults=env_defaults(s))
        assert svc.get("notify.quiet_hours") == {"start_hour": 22, "end_hour": 6}

    def test_an_environment_value_outside_the_bounds_falls_back_to_the_code_default(self):
        events = []
        defaults = {
            "session.late_cancellation_notice_hours": 5000,
            "travel.fallback_circuity": -1.0,
        }
        _raw, svc, _a, _c = stack(defaults=defaults, events=events)
        assert svc.get("session.late_cancellation_notice_hours") == 24
        assert svc.get("travel.fallback_circuity") == 1.4
        assert {n for n, _ in events} == {"env_default_invalid"}

    def test_an_environment_default_never_comes_from_a_secret_setting(self):
        s = Settings(_env_file=None, otp_pepper="p" * 40, job_tick_secret="j" * 40)
        assert not any("p" * 10 in str(v) or "j" * 10 in str(v) for v in env_defaults(s).values())


class TestDocsAreGenerated:
    def test_docs_table_is_current(self):
        text = (ROOT / "docs" / "ops" / "settings.md").read_text()
        begin, end = "<!-- settings:begin -->", "<!-- settings:end -->"
        assert begin in text and end in text
        block = text.split(begin, 1)[1].split(end, 1)[0].strip()
        assert block == render_table().strip(), (
            "docs/ops/settings.md is stale: cd services/core && uv run python "
            "scripts/settings_docs.py --write"
        )

    def test_every_key_appears_in_the_docs_table(self):
        table = render_table()
        for key in REGISTRY.defs:
            assert f"`{key}`" in table

    def test_write_docs_is_idempotent(self, tmp_path):
        f = tmp_path / "settings.md"
        f.write_text("# x\n\n<!-- settings:begin -->\nold\n<!-- settings:end -->\n")
        write_docs(f)
        once = f.read_text()
        write_docs(f)
        assert f.read_text() == once and "old" not in once

    def test_stack_builds_with_the_registry_defaults(self):
        _raw, svc, _a, _c = stack()
        assert isinstance(svc, AppSettings)
        assert all(svc.get(k) == d.code_default() for k, d in REGISTRY.defs.items())
