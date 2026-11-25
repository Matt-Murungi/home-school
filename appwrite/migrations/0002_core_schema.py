"""M01 (#36): core schema: users, roles, profiles, children, locations, consent, teachers, availability, zones.

Tables, columns and indexes come from appwrite/schema/core.json (source of truth; see schema_build.py). Creates only what
is missing, so it is safe on a project where `appwrite push` already created some of it. Later changes to core.json go in
a NEW migration that calls `schema_build.apply_domain(ctx.client, "core", only=[...])`.
No rows are inserted (no seed data, no personal data).
"""

import schema_build


def up(ctx):
    schema_build.apply_domain(ctx.client, "core")
