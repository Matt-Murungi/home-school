"""C03 (#141): retention bookkeeping tables (retention_hold, deletion_request, retention_run).

Tables, columns and indexes come from appwrite/schema/retention.json (source of truth; see schema_build.py). Creates only what
is missing and fails on drift; never alters. Needs the core schema first (user_profile; foreign keys are indexed columns, not
database constraints). No rows are inserted. Number 0011: 0010 is M05; renumber at rebase if another ticket took it (only the
file name and the test that names it).
"""

import schema_build


def up(ctx):
    schema_build.apply_domain(ctx.client, "retention")
