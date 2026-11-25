"""A01 (#43): `otp_challenge` table (phone OTP challenges and rate-limit slots, core-service only).

Tables come from appwrite/schema/auth.json (source of truth; see schema_build.py). Creates only what is missing.
Numbered 0006 at rebase time: 0003 sessions (M02, merged), 0004 money (M04) and geo (X01), 0005 catalogue (M03) are taken by branches in flight.
No rows are inserted.
"""

import schema_build


def up(ctx):
    schema_build.apply_domain(ctx.client, "auth")
