"""Audit contexts of billing's writes (M06 integration, docs/ops/billing.md "Audit").

Billing writes two audited tables, `subscription` and `invoice`, always through the app's
`AuditedRepository`. There is ONE audit row per logical change, so every write carries a
DETERMINISTIC key (derived from the subscription version, the invoice id, the payment reference:
server-side data, never a client value) and a FIXED actor, whoever retries it:

* a subscription transition is attributed to the actor of the caller (parent or admin, from the
  verified principal) and, on repair, to the actor recorded on the transition claim;
* everything else (invoice creation, paying, voiding, reopening, a payment confirmation, a
  teacher assignment, period roll, past-due marks) is attributed to `system` with the job name
  `billing` (`billing-payment:<provider>` for payment confirmations), so a retry from a job, a
  sweep or a callback replays the same audit row instead of adding a second one.
"""

from __future__ import annotations

from ..audit import AuditContext
from ..audit.context import current_audit_context
from ..audit.errors import AuditContextMissing

SYSTEM_JOB = "billing"


def system_ctx(key: str, job: str = SYSTEM_JOB) -> AuditContext:
    return AuditContext.system(job, run_key=key)


def derive(base: AuditContext, key: str) -> AuditContext:
    """Same actor as `base`, a deterministic key, fresh counters."""
    return AuditContext(
        base.actor_id, base.roles, base.source, base.request_id, key, base.ip, base.job
    )


def caller_ctx(ctx: AuditContext | None) -> AuditContext:
    """The context of a user-initiated operation: explicit, or the one set by `audit_scope`;
    otherwise fail closed (a human action without an actor is never recorded as `system`)."""
    found = ctx or current_audit_context.get()
    if found is None:
        raise AuditContextMissing("this billing operation needs an audit context (actor)")
    return found


def claim_ctx(claim: dict, key: str) -> AuditContext:
    """The context a transition was (or would have been) made under, from its claim row."""
    actor = claim.get("actor")
    if actor and actor != "system":
        source = "admin" if str(claim.get("audit_action", "")).endswith("_admin") else "user"
        return AuditContext(actor, (), source, key[:36], key)
    return AuditContext.system(claim.get("job") or SYSTEM_JOB, run_key=key)
