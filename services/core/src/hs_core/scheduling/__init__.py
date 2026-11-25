"""Session generation and teacher-serialised session writes (S01 #93).

- `rules.py`      port of appwrite/session_rules.py (state machine, keys, overlap) plus
  Africa/Kampala helpers
- `pattern.py`    preferred slots -> concrete occurrences (local dates, stored as UTC)
- `leases.py`     row-based per-teacher lease (fenced, expiring): serialises session writes
- `writer.py`     overlap-safe create/reschedule/soft-delete: lease + check + insert +
  re-check-after-insert rollback
- `generator.py`  SessionGenerator: generate / regenerate / cancel_future / generate_now / run_chunk
- `job.py`        the nightly `session-generate` job handler (registered in jobs/schedule.py)
- `api.py`        POST /admin/scheduling/generate-now (opsadmin, reason required)
- `issues.py`  admin-visible scheduling issues. Audit: every write of an audited table goes through
  M06's AuditedRepository (see docs/ops/audit.md "Session generation")
"""
