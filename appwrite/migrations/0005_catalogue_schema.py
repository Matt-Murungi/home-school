"""M03 (#38): curriculum catalogue, session logs and progress, question bank and assessments.

Tables, columns and indexes come from appwrite/schema/{catalogue,records,assessments}.json (source of truth; see
schema_build.py). Creates only what is missing and fails on drift; never alters. Later changes go in a NEW migration calling
`schema_build.apply_domain(ctx.client, "<domain>", only=[...])`. No rows are inserted (curricula, levels and topics are
curator content, not seed data). Needs the core and sessions tables to exist first (foreign keys are indexed columns, not
database constraints).
"""

import schema_build


def up(ctx):
    for domain in ("catalogue", "records", "assessments"):
        schema_build.apply_domain(ctx.client, domain)
