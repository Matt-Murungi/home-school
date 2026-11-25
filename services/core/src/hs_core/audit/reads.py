"""Reads of safeguarding case rows are audited (#41): case endpoints (G tickets) MUST read through
these helpers. The read row is written BEFORE the data is returned, so if the audit store is down
the case is not shown (fail closed). Rows hold ids only, never case content.

`AuditedRepository.get/list` refuse these tables at run time; an AST tripwire
(tripwire.find_unaudited_case_reads) flags literal reads elsewhere. Both are conventions that
protect code which uses them; neither stops code holding the raw repository.
"""

from __future__ import annotations

from typing import Any

from .context import AuditContext
from .registry import CASE_TABLES
from .repository import AuditedRepository

MAX_LOGGED_IDS = 200


def _check(table: str) -> None:
    if table not in CASE_TABLES:
        raise ValueError(f"{table} is not a safeguarding case table")


def read_audited(
    repo: AuditedRepository,
    ctx: AuditContext,
    table: str,
    row_id: str,
    *,
    purpose: str | None = None,
) -> dict[str, Any]:
    _check(table)
    repo.service.record(
        ctx, action=f"{table}.read", entity=table, entity_id=row_id, reason=purpose, op="read"
    )
    return repo._raw_get(table, row_id)


def list_audited(
    repo: AuditedRepository,
    ctx: AuditContext,
    table: str,
    queries: list[str] | None = None,
    *,
    purpose: str | None = None,
) -> list[dict[str, Any]]:
    _check(table)
    rows = repo._raw_list(table, queries)
    ids = [r["$id"] for r in rows]
    repo.service.record(
        ctx,
        action=f"{table}.list",
        entity=table,
        entity_id="list",
        after={"ids": ids[:MAX_LOGGED_IDS], "count": len(ids)},
        reason=purpose,
        op="read",
    )
    return rows
