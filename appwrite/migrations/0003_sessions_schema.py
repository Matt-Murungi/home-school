"""M02 (#37): plans, subscriptions, matching, assignments, sessions.

Tables, columns and indexes come from appwrite/schema/sessions.json (source of truth; see schema_build.py). Creates only
what is missing and fails on drift; never alters. Later changes go in a NEW migration calling
`schema_build.apply_domain(ctx.client, "sessions", only=[...])`. No rows are inserted.
"""

import schema_build


def up(ctx):
    schema_build.apply_domain(ctx.client, "sessions")
