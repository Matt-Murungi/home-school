"""Baseline: the schema itself is applied by `appwrite push` from appwrite.config.json.

This script exists so version 0001 is recorded and the numbering starts at a known point.
Data migrations after this use ctx.client (see migrate.py); the runner has already waited for every
column and index to be `available`.
"""


def up(ctx):
    pass
