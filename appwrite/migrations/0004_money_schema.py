"""M04 (#39): money, tiers, safeguarding, audit and notification tables.

Tables, columns and indexes come from appwrite/schema/{money,safeguarding,platform}.json (see schema_build.py). Creates only
what is missing and fails on drift (never alters). Later changes go in a NEW migration calling
`schema_build.apply_domain(ctx.client, <domain>, only=[...])`. No rows are inserted (no seed data).
Order matters only for readability: indexes are created per table after its columns are available; foreign keys are
plain indexed varchar(36) columns, so no table needs to exist before another.
"""

import schema_build


def up(ctx):
    for domain in ("money", "safeguarding", "platform"):
        schema_build.apply_domain(ctx.client, domain)
