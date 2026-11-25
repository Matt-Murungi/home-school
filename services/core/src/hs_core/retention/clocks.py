"""Rule clocks that are not a plain column (C03 #141)."""

from __future__ import annotations

from ..repository import Repository

LIVE_SUBSCRIPTION = ("pending_payment", "matching", "active", "paused", "past_due")


def child_clock(repo: Repository, row: dict) -> str | None:
    """Retention of a child counts from the LATER of the child's soft delete and the end of its
    last subscription. None = not due: the child has no clock yet, or still has a live subscription
    (a child of an active family is never purged, whatever its `deleted_at` says)."""
    from appwrite.query import Query

    subs, after = [], None
    while True:
        q = [Query.equal("child_id", [row["$id"]]), Query.order_asc("$id"), Query.limit(100)]
        if after:
            q.append(Query.cursor_after(after))
        page = repo.list("subscription", q)
        subs += page
        if len(page) < 100:
            break
        after = page[-1]["$id"]
    if any(s.get("status") in LIVE_SUBSCRIPTION for s in subs):
        return None
    candidates = [row.get("deleted_at")] + [
        s.get("cancelled_at") or s.get("period_end_at") for s in subs
    ]
    values = [v for v in candidates if isinstance(v, str) and v]
    return max(values) if values else None


CLOCKS = {"child": child_clock}
