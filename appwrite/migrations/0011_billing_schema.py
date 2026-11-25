"""B03 (#71): billing support tables (appwrite/schema/billing.json) and the additive optional
columns B03 put on `subscription` (sessions.json) and `invoice` (money.json).

Creates only what is missing (convergent). New columns on the two older tables are added through
`ensure_table`, which adds missing columns/indexes and leaves existing rows untouched. No rows are
inserted. Later changes go in a NEW migration.
"""

import schema_build


def up(ctx):
    schema_build.apply_domain(ctx.client, "sessions", only=["subscription"])
    schema_build.apply_domain(ctx.client, "money", only=["invoice"])
    schema_build.apply_domain(ctx.client, "billing")
