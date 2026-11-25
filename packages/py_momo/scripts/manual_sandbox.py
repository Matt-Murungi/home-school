#!/usr/bin/env python3
"""MANUAL sandbox smoke test. NOT run in CI, NOT run by agents (needs the owner's credentials).

Calls the real MTN sandbox. Refuses to run unless --yes-call-the-network is given and the
environment variables below are set (export them yourself; never commit them):

  MOMO_SUBSCRIPTION_KEY_COLLECTION  MOMO_SUBSCRIPTION_KEY_DISBURSEMENT
  MOMO_API_USER_COLLECTION  MOMO_API_KEY_COLLECTION
  MOMO_API_USER_DISBURSEMENT  MOMO_API_KEY_DISBURSEMENT
  MOMO_CALLBACK_URL (optional)   MOMO_TEST_MSISDN (sandbox test number, e.g. 46733123450)

Sandbox API user/key are created once via the portal (see README, "Owner steps").
"""

import os
import sys
import time

from py_momo import (
    Environment,
    HttpCollectionsClient,
    HttpDisbursementsClient,
    MomoConfig,
    PollSchedule,
    new_reference_id,
    poll_until_final,
)


def need(name):
    v = os.environ.get(name)
    if not v:
        sys.exit(f"missing environment variable {name}")
    return v


def main():
    if "--yes-call-the-network" not in sys.argv:
        sys.exit("refusing to run: pass --yes-call-the-network to call the MTN sandbox")
    msisdn = need("MOMO_TEST_MSISDN")
    cb = os.environ.get("MOMO_CALLBACK_URL")
    sched = PollSchedule(delays=(2, 2, 5), deadline_seconds=60)
    for label, cls, ids in (
        ("collections", HttpCollectionsClient, "COLLECTION"),
        ("disbursements", HttpDisbursementsClient, "DISBURSEMENT"),
    ):
        cfg = MomoConfig(
            Environment.SANDBOX,
            need(f"MOMO_SUBSCRIPTION_KEY_{ids}"),
            need(f"MOMO_API_USER_{ids}"),
            need(f"MOMO_API_KEY_{ids}"),
            callback_url=cb,
            min_amount_ugx=None,
            max_amount_ugx=None,
        )
        client = cls(cfg)
        ref = new_reference_id()
        if label == "collections":
            client.request_to_pay(
                reference_id=ref, amount_ugx=100, payer_msisdn=msisdn, external_id=ref
            )
        else:
            client.transfer(reference_id=ref, amount_ugx=100, payee_msisdn=msisdn, external_id=ref)
        res = poll_until_final(client.get_status, ref, sched, sleep=time.sleep)
        print(label, "reference", ref, "->", res.status, res.reason)


if __name__ == "__main__":
    main()
