"""N02 (#127): email provider bookkeeping tables (`email_suppression`, `webhook_event`).

Tables come from appwrite/schema/email.json (source of truth; see schema_build.py). Creates only what is missing and fails on drift.
Number 0011 is provisional: renumber to the next free number at rebase (only the file name and the test that names it). No rows are inserted.
"""

import schema_build


def up(ctx):
    schema_build.apply_domain(ctx.client, "email")
