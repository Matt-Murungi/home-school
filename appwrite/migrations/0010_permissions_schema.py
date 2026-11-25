"""M05 (#40): permission service bookkeeping tables (access_window, teacher_session_history).

Tables, columns and indexes come from appwrite/schema/permissions.json (source of truth; see schema_build.py). Creates only what
is missing and fails on drift; never alters. Needs the core, sessions and records tables first (foreign keys are indexed
columns, not database constraints). No rows are inserted. Number 0010: 0006 A01, 0007 X01, 0008 S01 and 0009 B03 are taken or in flight; renumber at rebase if
the order changes (only the file name and the test that names it).
"""

import schema_build


def up(ctx):
    schema_build.apply_domain(ctx.client, "permissions")
