"""Appwrite Function `job-tick` (runtime python-3.12, schedule "* * * * *").

It does one thing: tell the core service to run whatever jobs are due. All job
logic, retries and idempotency live in the core service (ADR 0002). Standard
library only, so the function has no dependencies to build.

Function variables: CORE_SERVICE_URL, JOB_TICK_SECRET.
"""
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request


def main(context):
    url = os.environ["CORE_SERVICE_URL"].rstrip("/") + "/internal/jobs/tick"
    secret = os.environ["JOB_TICK_SECRET"]
    body = json.dumps({"source": "appwrite-cron", "execution_id": os.environ.get("APPWRITE_FUNCTION_EXECUTION_ID", "")}).encode()
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json", "X-Job-Timestamp": ts, "X-Job-Signature": sig})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            result = json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        context.error(f"core service returned {e.code}")
        return context.res.json({"ok": False, "status": e.code}, 502)
    except Exception as e:  # noqa: BLE001 — network failure; the next tick catches up
        context.error(f"core service unreachable: {e}")
        return context.res.json({"ok": False}, 502)
    context.log(f"tick: {result}")
    return context.res.json({"ok": True, "result": result})
