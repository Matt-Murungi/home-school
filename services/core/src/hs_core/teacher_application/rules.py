"""Validation rules of the application (VER-1), mirrored by the Dart `rules.dart`.

Both sides are tested against `apps/teacher/test/fixtures/application_cases.json`. Issues are
strings `path:code` (for example `referees[1].phone:invalid`) so the app can map them to its own
messages. Draft saves report issues for what is present; submit (`final=True`) also reports what
is missing.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

from ..auth_otp.phone import InvalidPhone, normalize_ug_phone

TITLES = ("mr", "ms", "teacher")
GENDERS = ("female", "male")
LANGUAGES = ("en", "lg", "sw", "nyn", "xog", "teo", "ach", "other")
TEACHER_TYPES = ("registered", "graduate")
QUALIFICATIONS = ("certificate", "diploma", "degree", "postgraduate", "masters", "doctorate")
RELATIONSHIPS = (
    "head_teacher",
    "colleague",
    "supervisor",
    "lecturer",
    "employer",
    "community_leader",
    "other",
)
MIN_AGE, MAX_AGE = 18, 80
MAX_CURRICULA = 3
MAX_ZONES = 10
RADIUS_KM = (1, 50)
YEARS = (0, 60)
DAY_START, DAY_END, GRID = 6 * 60, 21 * 60, 30  # 06:00-21:00 on a 30 minute grid
MAX_SLOTS = 70
PEAK = {  # weekday (ISO 1-7) -> minute range of the protected window (NFR-5)
    **{d: (15 * 60, 20 * 60) for d in range(1, 6)},
    6: (8 * 60, 18 * 60),
    7: (8 * 60, 18 * 60),
}
DEFAULT_MIN_WEEKLY_HOURS = 6

_REG = re.compile(r"[A-Za-z0-9][A-Za-z0-9/ .\-]{1,38}[A-Za-z0-9]", re.ASCII)
_EMAIL = re.compile(r"[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s.]{2,24}")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)
_BAD = {"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"}


@dataclass(frozen=True)
class CatalogueOption:
    curriculum_id: str
    code: str
    name: str
    levels: tuple[tuple[str, str], ...]  # (level id, name) in teaching order
    subjects: tuple[tuple[str, str], ...]  # (subject id, name)


@dataclass
class Context:
    today: date
    catalogue: dict[str, CatalogueOption] = field(default_factory=dict)
    zone_ids: frozenset[str] = frozenset()
    own_phone: str | None = None
    min_weekly_minutes: int = DEFAULT_MIN_WEEKLY_HOURS * 60


def clean_text(value: object) -> str:
    """NFC, no control/format characters, runs of whitespace collapsed, trimmed."""
    if not isinstance(value, str):
        return ""
    s = unicodedata.normalize("NFC", value)
    s = "".join(" " if unicodedata.category(c) == "Zs" or c in "\t\n\r" else c for c in s)
    return re.sub(r" {2,}", " ", s).strip()


def has_bad_chars(s: str) -> bool:
    return any(unicodedata.category(c) in _BAD for c in s)


def _text(path: str, raw, lo: int, hi: int, out: list[str], *, required: bool, letters=True):
    s = clean_text(raw)
    if not s:
        if required:
            out.append(f"{path}:required")
        return s
    if has_bad_chars(s) or (letters and not any(unicodedata.category(c)[0] == "L" for c in s)):
        out.append(f"{path}:invalid")
    elif len(s) < lo:
        out.append(f"{path}:too_short")
    elif len(s) > hi:
        out.append(f"{path}:too_long")
    return s


def age_on(dob: date, today: date) -> int:
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def parse_date(raw: object) -> date | None:
    if not isinstance(raw, str) or _DATE.fullmatch(raw) is None:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _choice(path, raw, allowed, out, required=True):
    if raw in (None, ""):
        if required:
            out.append(f"{path}:required")
    elif raw not in allowed:
        out.append(f"{path}:unknown_choice")


def _personal(p: dict, ctx: Context, final: bool, out: list[str]) -> None:
    _text("personal.full_name", p.get("full_name"), 2, 100, out, required=final)
    _choice("personal.title", p.get("title"), TITLES, out, final)
    _choice("personal.gender", p.get("gender"), GENDERS, out, final)
    dob = p.get("date_of_birth")
    if dob in (None, ""):
        if final:
            out.append("personal.date_of_birth:required")
    else:
        d = parse_date(dob)
        if d is None or d > ctx.today:
            out.append("personal.date_of_birth:invalid")
        else:
            a = age_on(d, ctx.today)
            if a < MIN_AGE:
                out.append("personal.date_of_birth:under_age")
            elif a > MAX_AGE:
                out.append("personal.date_of_birth:invalid")
    langs = p.get("languages")
    if not langs:
        if final:
            out.append("personal.languages:required")
    else:
        if len(langs) > len(LANGUAGES):
            out.append("personal.languages:too_many")
        if any(x not in LANGUAGES for x in langs):
            out.append("personal.languages:unknown_choice")
        if len(set(langs)) != len(langs):
            out.append("personal.languages:duplicate")
    photo = p.get("photo")
    if photo is None:
        if final:
            out.append("personal.photo:required")
    elif photo.get("state") != "chosen" or photo.get("file_id"):
        # V01 uploads nothing. A file id from the client could name someone else's file, so it
        # is refused until V02 uploads the photo itself and verifies who owns the file.
        out.append("personal.photo:file_not_allowed")
    # The crop (fractions in 0..1, side as a fraction of the shorter edge) is bounded by the
    # wire model; x + size is not a valid check because x and y are fractions of the width and
    # height while size is of the shorter edge.


def _qualifications(q: dict, final: bool, out: list[str]) -> None:
    kind = q.get("teacher_type")
    _choice("qualifications.teacher_type", kind, TEACHER_TYPES, out, final)
    reg = clean_text(q.get("registration_number"))
    if reg:
        if has_bad_chars(reg) or _REG.fullmatch(reg) is None:
            out.append("qualifications.registration_number:invalid")
    elif kind == "registered" and final:
        out.append("qualifications.registration_number:required")
    _choice(
        "qualifications.highest_qualification",
        q.get("highest_qualification"),
        QUALIFICATIONS,
        out,
        final,
    )
    _text("qualifications.institution", q.get("institution"), 2, 120, out, required=final)
    yrs = q.get("years_teaching")
    if yrs is None:
        if final:
            out.append("qualifications.years_teaching:required")
    elif isinstance(yrs, bool) or not YEARS[0] <= yrs <= YEARS[1]:
        out.append("qualifications.years_teaching:out_of_range")
    _text("qualifications.current_school", q.get("current_school"), 2, 120, out, required=False)


def _curricula(items, ctx: Context, final: bool, out: list[str]) -> None:
    if not items:
        if final:
            out.append("curricula:required")
        return
    if len(items) > MAX_CURRICULA:
        out.append("curricula:too_many")
    seen: set[str] = set()
    for i, c in enumerate(items):
        base = f"curricula[{i}]"
        cid = c.get("curriculum_id")
        opt = ctx.catalogue.get(cid)
        if opt is None:
            out.append(f"{base}.curriculum_id:unknown_choice")
        if cid in seen:
            out.append(f"{base}.curriculum_id:duplicate")
        seen.add(cid)
        for key, valid in (
            ("level_ids", {x for x, _ in opt.levels} if opt else set()),
            ("subject_ids", {x for x, _ in opt.subjects} if opt else set()),
        ):
            ids = c.get(key) or []
            if not ids:
                if final:
                    out.append(f"{base}.{key}:required")
            elif opt is not None and any(x not in valid for x in ids):
                out.append(f"{base}.{key}:unknown_choice")
            elif len(set(ids)) != len(ids):
                out.append(f"{base}.{key}:duplicate")


def normalise_phone(raw: object) -> str | None:
    try:
        return normalize_ug_phone(raw)
    except InvalidPhone:
        return None


def _referees(items, consent, ctx: Context, final: bool, out: list[str]) -> None:
    items = items or []
    if (final and len(items) != 2) or len(items) > 2:
        out.append("referees:wrong_count")
    phones: list[str | None] = []
    for i, r in enumerate(items):
        b = f"referees[{i}]"
        _text(f"{b}.name", r.get("name"), 2, 100, out, required=final)
        _choice(f"{b}.relationship", r.get("relationship"), RELATIONSHIPS, out, final)
        raw = r.get("phone")
        if raw in (None, ""):
            if final:
                out.append(f"{b}.phone:required")
            phones.append(None)
        else:
            e164 = normalise_phone(raw)
            phones.append(e164)
            if e164 is None:
                out.append(f"{b}.phone:invalid")
            elif e164 == ctx.own_phone:
                out.append(f"{b}.phone:own_number")
        yk = r.get("years_known")
        if yk is None:
            if final:
                out.append(f"{b}.years_known:required")
        elif isinstance(yk, bool) or not 1 <= yk <= 60:
            out.append(f"{b}.years_known:out_of_range")
        email = clean_text(r.get("email"))
        if email and (_EMAIL.fullmatch(email) is None or has_bad_chars(email)):
            out.append(f"{b}.email:invalid")
    real = [p for p in phones if p]
    if len(real) != len(set(real)):
        out.append("referees[1].phone:duplicate")
    if final and consent is not True:
        out.append("referee_consent:required")


def weekly_minutes(slots) -> int:
    return sum(s["end_minute"] - s["start_minute"] for s in slots)


def _availability(slots, ctx: Context, final: bool, out: list[str]) -> None:
    if not slots:
        if final:
            out.append("availability:required")
        return
    if len(slots) > MAX_SLOTS:
        out.append("availability:too_many")
        return
    ok: list[tuple[int, int, int, int]] = []
    for i, s in enumerate(slots):
        wd, a, b = s["weekday"], s["start_minute"], s["end_minute"]
        bad = not 1 <= wd <= 7 or a % GRID or b % GRID or a >= b or a < DAY_START or b > DAY_END
        if bad:
            out.append(f"availability[{i}]:invalid")
        else:
            ok.append((wd, a, b, i))
    ok.sort()
    for prev, cur in zip(ok, ok[1:], strict=False):
        if prev[0] == cur[0] and cur[1] < prev[2]:
            out.append(f"availability[{cur[3]}]:overlap")
    if final and len(ok) == len(slots) and not any(x.endswith(":overlap") for x in out):
        if weekly_minutes(slots) < ctx.min_weekly_minutes:
            out.append("availability:too_few_hours")


def _zones(zone_ids, radius, ctx: Context, final: bool, out: list[str]) -> None:
    if not zone_ids:
        if final:
            out.append("zone_ids:required")
    else:
        if len(zone_ids) > MAX_ZONES:
            out.append("zone_ids:too_many")
        if len(set(zone_ids)) != len(zone_ids):
            out.append("zone_ids:duplicate")
        if any(z not in ctx.zone_ids for z in zone_ids):
            out.append("zone_ids:unknown_choice")
    if radius is None:
        if final:
            out.append("travel_radius_km:required")
    elif isinstance(radius, bool) or not RADIUS_KM[0] <= radius <= RADIUS_KM[1]:
        out.append("travel_radius_km:out_of_range")


def validate_content(content: dict, ctx: Context, *, final: bool) -> list[str]:
    out: list[str] = []
    _personal(content.get("personal") or {}, ctx, final, out)
    _qualifications(content.get("qualifications") or {}, final, out)
    _curricula(content.get("curricula"), ctx, final, out)
    _referees(content.get("referees"), content.get("referee_consent"), ctx, final, out)
    _zones(content.get("zone_ids"), content.get("travel_radius_km"), ctx, final, out)
    _availability(content.get("availability"), ctx, final, out)
    return out


def normalise_content(content: dict) -> dict:
    """What gets stored: cleaned text, E.164 phones where valid, empty strings dropped."""
    c = {k: v for k, v in content.items() if v is not None}

    def clean_map(d: dict, keys: tuple[str, ...]) -> dict:
        out = dict(d)
        for k in keys:
            if isinstance(out.get(k), str):
                out[k] = clean_text(out[k]) or None
        return {k: v for k, v in out.items() if v is not None}

    if "personal" in c:
        c["personal"] = clean_map(c["personal"], ("full_name", "title", "gender", "date_of_birth"))
    if "qualifications" in c:
        c["qualifications"] = clean_map(
            c["qualifications"],
            (
                "teacher_type",
                "registration_number",
                "highest_qualification",
                "institution",
                "current_school",
            ),
        )
    if "referees" in c:
        refs = []
        for r in c["referees"]:
            r = clean_map(r, ("name", "relationship", "email"))
            if r.get("phone"):
                r["phone"] = normalise_phone(r["phone"]) or clean_text(r["phone"])[:32]
            refs.append(r)
        c["referees"] = refs
    return c


def is_peak(weekday: int, start: int, end: int) -> bool:
    lo, hi = PEAK[weekday]
    return start < hi and end > lo
