"""S01 (#93): session generation support.

Adds generated_for, generation_key (unique), is_trial and overridden to `session` (sessions domain) and creates the
`scheduling` domain tables (teacher_schedule_lease, scheduling_issue). `ensure_table` is convergent: it adds only what is
missing and fails on drift, so projects that already ran 0003 get the new columns and index. No rows are inserted.
"""

import schema_build


def up(ctx):
    schema_build.apply_domain(ctx.client, "sessions", only=["session"])
    schema_build.apply_domain(ctx.client, "scheduling")
