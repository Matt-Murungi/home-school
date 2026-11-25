"""A01 (#43): `otp_challenge` table (phone OTP challenges and rate-limit slots, core-service only).

Tables come from appwrite/schema/auth.json (source of truth; see schema_build.py). Creates only what is missing.
Numbered 0005 on purpose: M02 (sessions) and M04 (money) branches already claim 0003 and a later schema ticket 0004.
No rows are inserted.
"""

import schema_build


def up(ctx):
    schema_build.apply_domain(ctx.client, "auth")
