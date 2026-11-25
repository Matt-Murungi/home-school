"""Preferred slots (subscription.preferred_slots JSON text) -> occurrences on Kampala dates."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from . import rules


@dataclass(frozen=True, order=True)
class Slot:
    weekday: int  # ISO: 1 = Monday .. 7 = Sunday
    start_minute: int  # minutes after midnight, Africa/Kampala wall clock
    end_minute: int  # exclusive end, up to 1440

    def ref(self, d: date) -> str:
        """The slot identity on a local date: `<weekday>-<start minute>:<YYYY-MM-DD>`."""
        return f"{self.weekday}-{self.start_minute}:{d.isoformat()}"


@dataclass(frozen=True)
class Occurrence:
    slot: Slot
    date: date  # the Kampala date
    start: datetime  # UTC
    end: datetime  # UTC

    @property
    def ref(self) -> str:
        return self.slot.ref(self.date)


def _valid(weekday, start, end) -> bool:
    ints = all(isinstance(v, int) and not isinstance(v, bool) for v in (weekday, start, end))
    return ints and 1 <= weekday <= 7 and 0 <= start < end <= 1440


def parse_slots(raw) -> tuple[list[Slot], list[str]]:
    """JSON text -> (valid unique slots, reasons for rejected entries). Never raises."""
    if raw is None or raw == "":
        return [], []
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return [], ["preferred_slots is not valid JSON"]
    if not isinstance(data, list):
        return [], ["preferred_slots is not a list"]
    slots: list[Slot] = []
    invalid: list[str] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict) or not _valid(
            item.get("weekday"), item.get("start_minute"), item.get("end_minute")
        ):
            invalid.append(f"entry {i} is not a valid slot")
            continue
        slot = Slot(item["weekday"], item["start_minute"], item["end_minute"])
        if slot not in slots:
            slots.append(slot)
    return slots, invalid


def select_slots(slots: list[Slot], per_week: int) -> tuple[list[Slot], int]:
    """The first `per_week` slots by (weekday, start). Returns (chosen, shortfall) where shortfall =
    missing slots."""
    ordered = sorted(slots)
    chosen = ordered[: max(per_week, 0)]
    return chosen, max(per_week - len(ordered), 0)


def occurrences(slots: list[Slot], first: date, last: date) -> list[Occurrence]:
    """Every occurrence on Kampala dates first..last inclusive, in start order."""
    out: list[Occurrence] = []
    d = first
    while d <= last:
        for slot in slots:
            if slot.weekday == d.isoweekday():
                out.append(
                    Occurrence(
                        slot,
                        d,
                        rules.local_to_utc(d, slot.start_minute),
                        rules.local_to_utc(d, slot.end_minute),
                    )
                )
        d += timedelta(days=1)
    return sorted(out, key=lambda o: (o.start, o.slot))
