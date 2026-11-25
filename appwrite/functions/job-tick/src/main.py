"""Appwrite Function `job-tick` (runtime python-3.12, schedule "* * * * *").

It does one thing: tell the core service to run whatever jobs are due. All job logic, schedules,
retries and idempotency live in the core service (ADR 0002). Standard library only, so there is
nothing to install or build.

Function variables (set per environment in Appwrite, never in the repo):
  CORE_SERVICE_URL  https base URL of the core service, e.g. https://hs-core-staging.fly.dev
  JOB_TICK_SECRET   shared HMAC secret; the same value as the core service's JOB_TICK_SECRET
"""

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

HTTP_TIMEOUT_SECONDS = 25  # the function timeout (appwrite.config.json) is above this


def _fail(context, status, message):
    context.error(message)
    return context.res.json({"ok": False, "error": message}, status)


def main(context):
    base = os.environ.get("CORE_SERVICE_URL", "").strip()
    secret = os.environ.get("JOB_TICK_SECRET", "")
    if not base or not secret:
        return _fail(context, 500, "CORE_SERVICE_URL and JOB_TICK_SECRET must be set")
    parsed = urllib.parse.urlparse(base)
    local = parsed.hostname in ("localhost", "127.0.0.1", "::1")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
        return _fail(context, 500, "CORE_SERVICE_URL must be https")

    url = base.rstrip("/") + "/internal/jobs/tick"
    execution_id = os.environ.get("APPWRITE_FUNCTION_EXECUTION_ID", "")
    body = json.dumps({"source": "appwrite-cron", "execution_id": execution_id}).encode()
    timestamp = str(int(time.time()))
    signature = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-Job-Timestamp": timestamp,
        "X-Job-Signature": signature,
    }
    if execution_id:
        headers["X-Request-ID"] = execution_id  # correlates core logs with this execution
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            result = json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return _fail(context, 502, f"core service returned {e.code}")
    except Exception as e:  # noqa: BLE001 - network failure; the next tick catches up
        return _fail(context, 502, f"core service unreachable: {type(e).__name__}")
    context.log(f"tick: {result}")
    return context.res.json({"ok": True, "result": result})
