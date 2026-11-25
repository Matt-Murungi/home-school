"""X01 (#85): travel-time cache and usage tables (appwrite/schema/geo.json).

Creates only what is missing (convergent). Later changes to geo.json go in a NEW migration that
calls `schema_build.apply_domain(ctx.client, "geo", only=[...])`. No rows are inserted.
"""

import schema_build


def up(ctx):
    schema_build.apply_domain(ctx.client, "geo")
