"""Drift check (ADR 0012): compare stored `$permissions` with `desired_permissions`, read only.

`Auditor.step` is one resumable chunk (cursor in, cursor out) so the `permission-audit` job can
spread a full pass over several ticks; `audit_all` runs every chunk (CI script, tests). Nothing
here writes: repairs are `permission-reconcile`'s job (the ticket asks for a report-only audit;
ADR 0012 wording has the audit repair too: logged in memory.md).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .context import PermissionContext, RepoFacts
from .policy import DEFAULT_POLICY, Policy
from .queries import cursor_after, limit, order_asc
from .repo_errors import NotFound
from .rules import FILE_BUCKETS, SPECS, desired_file_permissions, desired_permissions
from .strings import canonical, forbidden_in

ROWS_PER_CHUNK = 150
SAMPLE_CAP = 25  # how many example rows the report keeps per category


def audited_tables() -> list[str]:
    return sorted(SPECS)


@dataclass
class AuditSummary:
    rows: int = 0
    files: int = 0
    over_grants: int = 0  # stored entry the rules do not want: high severity
    under_grants: int = 0  # wanted entry missing: low severity
    malformed: int = 0  # rows the rules could not evaluate (invalid id ...): skipped, counted
    counts: dict[str, int] = field(default_factory=dict)  # rows audited per table
    forbidden: int = 0  # any/guests, write actions, unparsable, `users` outside the catalogue
    over_samples: list[str] = field(default_factory=list)
    under_samples: list[str] = field(default_factory=list)
    forbidden_samples: list[str] = field(default_factory=list)
    malformed_samples: list[str] = field(default_factory=list)

    @property
    def drifted(self) -> int:
        return self.over_grants + self.under_grants + self.forbidden + self.malformed

    @property
    def clean(self) -> bool:
        return self.drifted == 0

    @property
    def high_severity(self) -> bool:
        return self.over_grants > 0 or self.forbidden > 0 or self.malformed > 0

    @property
    def drift_rate(self) -> float:
        total = self.rows + self.files
        return 0.0 if total == 0 else round(self.drifted / total, 6)

    @property
    def empty_tables(self) -> list[str]:
        """Client-readable tables in which the audit saw no row at all."""
        return sorted(
            t for t, s in SPECS.items() if not s.service_only and self.counts.get(t, 0) == 0
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "malformed": self.malformed,
            "malformed_samples": self.malformed_samples,
            "counts": self.counts,
            "empty_tables": self.empty_tables,
            "rows": self.rows,
            "files": self.files,
            "over_grants": self.over_grants,
            "under_grants": self.under_grants,
            "forbidden": self.forbidden,
            "drift_rate": self.drift_rate,
            "over_samples": self.over_samples,
            "under_samples": self.under_samples,
            "forbidden_samples": self.forbidden_samples,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> AuditSummary:
        keys = {"rows", "files", "over_grants", "under_grants", "forbidden", "malformed"}
        s = AuditSummary(**{k: d[k] for k in keys})
        s.counts = dict(d["counts"])
        s.malformed_samples = list(d["malformed_samples"])
        s.over_samples, s.under_samples, s.forbidden_samples = (
            list(d["over_samples"]),
            list(d["under_samples"]),
            list(d["forbidden_samples"]),
        )
        return s


def _note(samples: list[str], text: str) -> None:
    if len(samples) < SAMPLE_CAP:
        samples.append(text)


def compare(
    summary: AuditSummary,
    label: str,
    have: tuple[str, ...],
    want: tuple[str, ...],
    allow_users: bool,
) -> None:
    over, under = set(have) - set(want), set(want) - set(have)
    bad = forbidden_in(have, allow_users_read=allow_users)
    if bad:
        summary.forbidden += 1
        _note(summary.forbidden_samples, f"{label}: {sorted(bad)}")
    if over:
        summary.over_grants += 1
        _note(summary.over_samples, f"{label}: {sorted(over)}")
    if under:
        summary.under_grants += 1
        _note(summary.under_samples, f"{label}: {sorted(under)}")


class Auditor:
    def __init__(
        self,
        repo: Any,
        files: Any | None,
        clock: Callable[[], datetime],
        labels: Any | None = None,
        policy: Policy = DEFAULT_POLICY,
    ) -> None:
        self.repo, self.files, self.clock, self.labels, self.policy = (
            repo,
            files,
            clock,
            labels,
            policy,
        )

    def _ctx(self) -> PermissionContext:
        return PermissionContext(RepoFacts(self.repo, self.labels), self.clock(), self.policy)

    def start(self) -> dict:
        return {"stage": 0, "after": None, "summary": AuditSummary().to_dict()}

    def _stages(self) -> list[tuple[str, str]]:
        tables = [("table", t) for t in audited_tables()]
        files = [("files", b) for b in sorted(FILE_BUCKETS) if self.files is not None]
        return tables + files

    def step(
        self,
        cursor: dict,
        rows_budget: int = ROWS_PER_CHUNK,
        stop: Callable[[], bool] | None = None,
    ) -> tuple[dict | None, AuditSummary]:
        """Check up to `rows_budget` rows. Returns (next cursor or None when finished, summary)."""
        stages = self._stages()
        summary = AuditSummary.from_dict(cursor["summary"])
        stage, after = cursor["stage"], cursor["after"]
        ctx = self._ctx()
        left = rows_budget
        while stage < len(stages) and left > 0 and not (stop and stop()):
            kind, name = stages[stage]
            table = name if kind == "table" else FILE_BUCKETS[name][0]
            asked = left
            q = [order_asc("$id"), limit(asked)] + ([cursor_after(after)] if after else [])
            try:
                rows = self.repo.list(table, q)
            except NotFound:
                rows = []  # a planned table that does not exist yet
            for row in rows:
                if kind == "table":
                    spec = SPECS[table]
                    summary.rows += 1
                    summary.counts[table] = summary.counts.get(table, 0) + 1
                    try:
                        want = desired_permissions(table, row, ctx)
                    except Exception as exc:  # noqa: BLE001 - one bad row must not stop the audit
                        summary.malformed += 1
                        _note(
                            summary.malformed_samples, f"{table}/{row['$id']}: {type(exc).__name__}"
                        )
                        after = row["$id"]
                        continue
                    compare(
                        summary,
                        f"{table}/{row['$id']}",
                        canonical(row.get("$permissions")),
                        want,
                        spec.public is not None,
                    )
                else:
                    self._audit_files(name, row, ctx, summary)
                after = row["$id"]
            left -= len(rows)
            if len(rows) < asked:
                stage, after = stage + 1, None  # this stage is exhausted
        done = stage >= len(stages)
        cursor_out = {"stage": stage, "after": after, "summary": summary.to_dict()}
        return (None if done else cursor_out), summary

    def _audit_files(
        self, bucket: str, row: dict, ctx: PermissionContext, summary: AuditSummary
    ) -> None:
        try:
            want = desired_file_permissions(bucket, row, ctx)
        except Exception as exc:  # noqa: BLE001
            summary.malformed += 1
            _note(summary.malformed_samples, f"file:{bucket}/{row['$id']}: {type(exc).__name__}")
            return
        for col in FILE_BUCKETS[bucket][1]:
            fid = row.get(col)
            if not fid or not self.files.exists(bucket, fid):
                continue
            summary.files += 1
            compare(
                summary,
                f"file:{bucket}/{fid}",
                canonical(self.files.get_permissions(bucket, fid)),
                want,
                bucket == "question-media",
            )


def audit_all(
    repo: Any,
    files: Any | None = None,
    clock: Callable[[], datetime] | None = None,
    labels: Any | None = None,
    policy: Policy = DEFAULT_POLICY,
    rows_per_chunk: int = ROWS_PER_CHUNK,
) -> AuditSummary:
    a = Auditor(repo, files, clock or (lambda: datetime.now(UTC)), labels, policy)
    cursor: dict | None = a.start()
    summary = AuditSummary()
    while cursor is not None:
        cursor, summary = a.step(cursor, rows_per_chunk)
    return summary


def deadline(seconds: float) -> Callable[[], bool]:
    end = time.monotonic() + seconds
    return lambda: time.monotonic() > end


def dumps(summary: AuditSummary) -> str:
    return json.dumps(summary.to_dict(), sort_keys=True)
