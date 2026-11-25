"""V01 (#56): teacher application tables (application, referees, subjects, server-only claims).

Tables come from appwrite/schema/teacher_application.json (source of truth; see schema_build.py).
Creates only what is missing. Numbered 0008 at branch time; renumber at rebase if another branch in
flight took 0008 (only the file name changes). No rows are inserted.
"""

import schema_build


def up(ctx):
    schema_build.apply_domain(ctx.client, "teacher_application")
