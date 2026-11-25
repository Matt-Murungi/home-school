"""`session-generate`: nightly (01:00 Africa/Kampala), idempotent, chunked job (F04 runner).

Registered in jobs/schedule.py.

One chunk = one page of active subscriptions, at most `budget_s` of work (always at least one
subscription), polling `ctx.cancelled`. A failing assignment is isolated (alert + scheduling_issue)
and never blocks the others; re-running a period creates nothing new. Handlers receive no
dependencies from the runner, so the app binds the generator once at startup (`bind`), like
otp-cleanup.
"""

from __future__ import annotations

from ..jobs.model import Continue, Done, JobContext
from .generator import SessionGenerator

_bound: dict = {}


def bind(generator: SessionGenerator, budget_s: float = 6.0, page: int = 20) -> None:
    _bound["generator"] = generator
    _bound["budget_s"] = budget_s
    _bound["page"] = page


def make_handler(generator: SessionGenerator, page: int = 20, budget_s: float = 6.0):
    def handler(ctx: JobContext) -> Done | Continue:
        out = generator.run_chunk(
            ctx.cursor, budget_s=budget_s, page=page, cancelled=ctx.cancelled, run_id=ctx.run_id
        )
        if out.more:
            return Continue(cursor=out.next_cursor, items=out.assignments)
        return Done(detail=f"created={out.created} failures={out.failures}", items=out.assignments)

    return handler


def session_generate(ctx: JobContext) -> Done | Continue:
    generator = _bound.get("generator")
    if generator is None:
        raise RuntimeError("session-generate ran before the generator was bound")
    return make_handler(generator, _bound["page"], _bound["budget_s"])(ctx)
