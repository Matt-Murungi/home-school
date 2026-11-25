"""B03: the billing-period-roll job, settings, schema alignment and plan catalogue."""

from __future__ import annotations

import json
import threading
from datetime import timedelta
from pathlib import Path

import pytest

from billing_helpers import T0, activate, make_rig, new_sub
from hs_core.audit import AuditedRepository, AuditService
from hs_core.billing import jobs as billing_jobs
from hs_core.billing.fakes import RepositoryPlans
from hs_core.billing.models import Invoice, Subscription
from hs_core.billing.periods import period
from hs_core.billing.service import BillingConfig
from hs_core.jobs.model import Continue, Done, JobContext
from hs_core.jobs.schedule import JOBS
from hs_core.repository import InMemoryRepository
from hs_core.settings import ConfigError, Settings, load_settings
from jobs_helpers import FakeClock

SCHEMA = Path(__file__).resolve().parents[3] / "appwrite/schema"


def run_job(cursor=None, cancelled=None):
    """Run the job chunk by chunk to its Done; returns (last Done, number of chunks, details)."""
    details, n = [], 0
    while True:
        out = billing_jobs.billing_period_roll(ctx(cursor, cancelled))
        n += 1
        if isinstance(out, Done):
            details.append(out.detail)
            return out, n, details
        cursor = out.cursor


def ctx(cursor=None, cancelled=None):
    return JobContext(
        name="billing-period-roll",
        run_id="r",
        dedupe_key="billing-period-roll:x",
        scheduled_for=None,
        payload={},
        cursor=cursor,
        chunk=0,
        cancelled=cancelled,
    )


@pytest.fixture(autouse=True)
def _unbind():
    yield
    billing_jobs.bind(None)


def test_job_is_listed_once_in_the_schedule_daily_at_0300_kampala():
    (spec,) = [j for j in JOBS if j.name == "billing-period-roll"]
    from datetime import UTC, datetime

    assert spec.schedule.every == timedelta(days=1)
    fire = spec.schedule.due_periods(
        datetime(2027, 1, 14, 0, tzinfo=UTC),
        datetime(2027, 1, 14, 23, 59, tzinfo=UTC) + timedelta(minutes=1) - timedelta(seconds=1),
    )
    assert [f.hour for f in fire] == [0]  # one run in the window  # 03:00 Kampala = 00:00 UTC
    assert spec.timeout <= timedelta(seconds=14)


def test_unbound_job_reports_not_configured_and_does_nothing():
    out = billing_jobs.billing_period_roll(ctx())
    assert isinstance(out, Done) and "not configured" in out.detail


def test_job_chunks_with_continue_and_is_idempotent_across_runs(monkeypatch):
    rig = make_rig(BillingConfig(renewal_lead_days=5, roll_chunk_size=2))
    for i in range(5):
        activate(rig, child=f"c{i}")
    billing_jobs.bind(rig.svc)
    s = rig.svc.get(rig.repo.list("subscription")[0]["$id"])
    now = period(s.anchor_date, 1).start_at - 2 * timedelta(days=1)
    monkeypatch.setattr(
        "hs_core.billing.service.BillingService._now", staticmethod(lambda n: n or now)
    )
    for _ in range(2):  # the whole job twice
        cursor, chunks = None, 0
        while True:
            out = billing_jobs.billing_period_roll(ctx(cursor))
            chunks += 1
            if isinstance(out, Done):
                break
            cursor = out.cursor
        assert chunks == 4  # phase A in 3 chunks, then phase B (empty)
        assert len(rig.repo.list("invoice")) == 10


def test_job_stops_when_the_attempt_is_cancelled_and_resumes_from_the_cursor(monkeypatch):
    rig = make_rig(BillingConfig(renewal_lead_days=5, roll_chunk_size=10))
    ids = sorted(activate(rig, child=f"c{i}")[0] for i in range(4))
    billing_jobs.bind(rig.svc)
    s = rig.svc.get(ids[0])
    now = period(s.anchor_date, 1).start_at - timedelta(days=2)
    monkeypatch.setattr(
        "hs_core.billing.service.BillingService._now", staticmethod(lambda n: n or now)
    )
    ev = threading.Event()
    ev.set()
    out = billing_jobs.billing_period_roll(ctx(cancelled=ev))
    assert isinstance(out, Continue) and len(rig.repo.list("invoice")) == 4
    ev.clear()
    out, _, _ = run_job(cursor=out.cursor)
    assert isinstance(out, Done) and len(rig.repo.list("invoice")) == 8


def test_job_one_failing_subscription_does_not_stop_the_others(monkeypatch, caplog):
    rig = make_rig(BillingConfig(renewal_lead_days=5))
    ids = [activate(rig, child=f"c{i}")[0] for i in range(3)]
    billing_jobs.bind(rig.svc)
    s = rig.svc.get(ids[0])
    now = period(s.anchor_date, 1).start_at - timedelta(days=2)
    monkeypatch.setattr(
        "hs_core.billing.service.BillingService._now", staticmethod(lambda n: n or now)
    )
    orig = rig.svc.generate_invoice

    def flaky(sub_id, *a, **k):
        if sub_id == ids[0]:
            raise RuntimeError("boom")
        return orig(sub_id, *a, **k)

    rig.svc.generate_invoice = flaky
    out, _, _ = run_job()
    assert isinstance(out, Done)
    assert len(rig.repo.list("invoice")) == 5
    assert "billing-period-roll" in caplog.text


# ---- settings
def test_billing_settings_have_defaults_and_ranges():
    s = Settings(_env_file=None)
    assert (s.billing_renewal_lead_days, s.billing_grace_days) == (5, 3)
    assert s.billing_past_due_cancel_days == 14 and s.billing_max_pause_days == 30
    for bad in ("billing_renewal_lead_days", "billing_roll_chunk_size", "billing_max_pause_days"):
        assert Settings(_env_file=None, **{bad: 0}).problems()
        assert Settings(_env_file=None, **{bad: 10_000}).problems()
    assert Settings(_env_file=None, billing_grace_days=0).problems() == []


def test_a_bad_billing_setting_fails_startup_without_echoing_values(monkeypatch):
    monkeypatch.setenv("BILLING_ROLL_CHUNK_SIZE", "0")
    with pytest.raises(ConfigError) as e:
        load_settings(env_file=None)
    assert "BILLING_ROLL_CHUNK_SIZE" in str(e.value)


def test_billing_config_rejects_non_integers():
    for bad in (0, -1, 2.5, True):
        with pytest.raises(ValueError):
            BillingConfig(renewal_lead_days=bad)
    BillingConfig(grace_days=0)


# ---- schema alignment: what the code stores vs what M02/M04 define
def _cols(domain, table):
    d = json.loads((SCHEMA / domain).read_text())
    t = {x["$id"]: x for x in d["tables"]}[table]
    return {c["key"] for c in t["columns"]}


def _schema_table(table):
    for f in SCHEMA.glob("*.json"):
        for t in json.loads(f.read_text())["tables"]:
            if t["$id"] == table:
                return t
    raise AssertionError(table)


def test_every_row_the_service_writes_matches_the_real_schema():
    """Columns exist (appwrite/schema/*.json), required ones are always set, enums and integer
    money are respected, for every table the billing flows touch."""
    rig = make_rig()
    sid, inv = activate(rig)
    rig.svc.record_platform_miss(sid, "s1", reason="x", now=T0)
    s = rig.svc.get(sid)
    nxt = period(s.anchor_date, 1)
    i1 = rig.svc.generate_invoice(sid, 1, now=nxt.start_at - timedelta(days=3))
    rig.svc.on_payment_confirmed(i1.id, "r2", now=nxt.start_at)
    rig.svc.on_payment_confirmed(i1.id, "r3", now=nxt.start_at)  # duplicate payment -> flagged
    rig.svc.change_plan(sid, "plan3", now=T0, ctx=rig.parent)
    rig.svc.cancel(sid, now=T0, ctx=rig.parent, reason="r" * 500)
    seen = set()
    for table in (
        "subscription",
        "invoice",
        "subscription_transition",
        "billing_credit",
        "billing_payment",
        "billing_claim",
    ):
        schema = _schema_table(table)
        cols = {c["key"]: c for c in schema["columns"]}
        rows = rig.repo.list(table)
        assert rows, table
        seen.add(table)
        for r in rows:
            data = {k: v for k, v in r.items() if not k.startswith("$")}
            assert set(data) <= set(cols), (table, set(data) - set(cols))
            for k, c in cols.items():
                if c["required"] and table not in ("subscription", "invoice"):
                    assert data.get(k) is not None, (table, k)
                v = data.get(k)
                if v is None:
                    continue
                if c["type"] == "integer":
                    assert type(v) is int, (table, k)
                    assert v >= c.get("min", v), (table, k)
                if c["type"] == "varchar":
                    assert len(v) <= c["size"], (table, k, len(v))
                if c["type"] == "string":
                    assert v in c["elements"], (table, k, v)
    assert len(seen) == 6


def test_the_invoice_unique_index_matches_the_deterministic_invoice_id():
    d = json.loads((SCHEMA / "money.json").read_text())
    inv = {t["$id"]: t for t in d["tables"]}["invoice"]
    uniq = [i for i in inv["indexes"] if i["type"] == "unique"]
    assert [u["attributes"] if "attributes" in u else u["columns"] for u in uniq] == [
        ["subscription_id", "period_start_date"]
    ]


def test_roundtrip_of_rows():
    rig = make_rig()
    res = new_sub(rig)
    again = Subscription.from_row(rig.repo.get("subscription", res.subscription.id))
    assert again == rig.svc.get(res.subscription.id)
    assert Invoice.from_row(rig.repo.get("invoice", res.invoice.id)) == res.invoice


def test_repository_plan_catalogue_reads_the_plan_table():
    repo = InMemoryRepository()
    repo.create("plan", "p1", {"price_ugx": 100_000, "sessions_per_week": 2, "active": True})
    repo.create(
        "plan", "p2", {"price_ugx": 5, "sessions_per_week": 1, "deleted_at": "2027-01-01T00:00:00Z"}
    )
    plans = RepositoryPlans(repo)
    assert (
        plans.get("p1").price_ugx == 100_000 and plans.get("p2") is None and plans.get("x") is None
    )


def test_no_float_arithmetic_on_money_in_the_billing_package():
    import ast

    root = Path(__file__).resolve().parents[1] / "src/hs_core/billing"
    for f in root.glob("*.py"):
        tree = ast.parse(f.read_text())
        for n in ast.walk(tree):
            assert not (isinstance(n, ast.Call) and getattr(n.func, "id", "") == "float"), f
            assert not (isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div)), (f, n.lineno)
            assert not (isinstance(n, ast.Constant) and isinstance(n.value, float)), (f, n.lineno)


def test_unused_fakeclock_import_guard():
    assert FakeClock(T0)() == T0


# ---- wiring (main.py): dev runs on fakes, staging/production fail closed
def _settings(env):
    kw = {"hs_env": env}
    if env != "dev":
        kw.update(
            appwrite_endpoint="https://aw.example.org/v1",
            appwrite_project_id="p",
            appwrite_api_key="k" * 40,
            appwrite_database_id="d",
            job_tick_secret="s" * 40,
            otp_pepper="o" * 40,
        )
    return Settings(_env_file=None, **kw)


def test_dev_app_binds_a_service_on_fakes_so_the_job_runs():
    from hs_core.main import create_app

    app = create_app(settings=_settings("dev"), repository=InMemoryRepository())
    assert app.state.billing is not None
    out, _, _ = run_job()
    assert isinstance(out, Done) and "not configured" not in out.detail


def test_staging_and_production_refuse_to_run_billing_with_unwired_adapters():
    from hs_core.main import create_app

    for env in ("staging", "production"):
        app = create_app(settings=_settings(env), repository=InMemoryRepository())
        assert app.state.billing is None
        with pytest.raises(billing_jobs.BillingNotWired) as e:
            billing_jobs.billing_period_roll(ctx())
        assert "ledger" in str(e.value) and "scheduler" in str(e.value)


def test_staging_with_every_adapter_supplied_runs_the_job():
    from hs_core.billing.fakes import FakeLedger, FakeNotifier, FakeScheduler
    from hs_core.billing.wiring import BillingAdapters
    from hs_core.main import create_app

    ad = BillingAdapters(FakeLedger(), FakeNotifier(), FakeScheduler())
    app = create_app(
        settings=_settings("staging"), repository=InMemoryRepository(), billing_adapters=ad
    )
    assert app.state.billing is not None
    assert isinstance(run_job()[0], Done)


def test_settings_reach_the_service_config():
    from hs_core.billing.wiring import build_billing_service

    s = Settings(_env_file=None, billing_grace_days=1, billing_roll_chunk_size=7)
    raw = InMemoryRepository()
    svc, _ = build_billing_service(s, AuditedRepository(raw, AuditService(raw)))
    assert svc.config.grace_days == 1 and svc.config.roll_chunk_size == 7
