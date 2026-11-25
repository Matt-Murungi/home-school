"""H05 (#134): settings tables (app_setting, app_setting_change).

Tables, columns and indexes come from appwrite/schema/settings.json (source of truth; see schema_build.py). Creates only what
is missing and fails on drift; never alters. Needs the core tables first (app_setting_change.actor_id is an indexed column,
not a database constraint). No rows are inserted. Number 0012: 0008 S01 and 0011 B03 are in flight, 0010 M05 is on main;
renumber at rebase if the order changes (only the file name and the test that names it).
"""

import schema_build


def up(ctx):
    schema_build.apply_domain(ctx.client, "settings")
