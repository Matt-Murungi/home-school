"""Freeze the wall clock for a whole Python process (and its children) from the environment.

Used only by scripts/clock_matrix.py and the CI clock job: put this directory first on PYTHONPATH
and set HS_FROZEN_CLOCK to an ISO-8601 instant WITH an offset (e.g. 2027-03-10T23:30:00+03:00).
Python imports `sitecustomize` at start-up, before any test module, so `datetime.now()`,
`time.time()` and friends all see the frozen instant. Needs `time-machine` importable; if it is
not, the process fails loudly (a silently unfrozen run would be a false green).
HS_FROZEN_TICK=1 lets the frozen clock advance from that instant (default: stays still).
"""

import os

_raw = os.environ.get("HS_FROZEN_CLOCK")
if _raw:
    from datetime import datetime

    import time_machine

    _at = datetime.fromisoformat(_raw)
    if _at.tzinfo is None:
        raise SystemExit(f"HS_FROZEN_CLOCK needs an offset, got {_raw!r}")
    _traveller = time_machine.travel(_at, tick=os.environ.get("HS_FROZEN_TICK") == "1")
    _traveller.start()
