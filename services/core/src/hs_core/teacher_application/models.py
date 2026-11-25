"""Wire models of the teacher application (V01). Everything is optional in a draft: completeness
is judged by `rules.validate_content(final=True)`, so the app can save half-finished steps.

`extra="forbid"` everywhere is the tamper guard: a client that sends `status`, `approved`, `tier`,
`teacher_id` or any other field we do not define is refused with 422 and nothing is written.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

Id = Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9_]{1,36}$")]
Text = Annotated[StrictStr, Field(max_length=500)]  # hard cap; the semantic limits are in rules


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class Crop(_M):
    """Square crop of the chosen photo, as fractions of the image (0..1). V02 applies it."""

    x: Annotated[float, Field(ge=0, le=1)]
    y: Annotated[float, Field(ge=0, le=1)]
    size: Annotated[float, Field(gt=0, le=1)]


class Photo(_M):
    state: Annotated[StrictStr, Field(pattern=r"^(chosen|uploaded)$")]
    file_id: Id | None = None
    crop: Crop | None = None


class Personal(_M):
    full_name: Text | None = None
    title: Text | None = None
    gender: Text | None = None
    date_of_birth: Annotated[StrictStr, Field(max_length=32)] | None = None
    languages: (
        Annotated[list[Annotated[StrictStr, Field(max_length=32)]], Field(max_length=20)] | None
    ) = None
    photo: Photo | None = None


class Qualifications(_M):
    teacher_type: Text | None = None
    registration_number: Text | None = None
    highest_qualification: Text | None = None
    institution: Text | None = None
    years_teaching: StrictInt | None = None
    current_school: Text | None = None


class CurriculumChoice(_M):
    curriculum_id: Id
    level_ids: Annotated[list[Id], Field(max_length=60)] = []
    subject_ids: Annotated[list[Id], Field(max_length=80)] = []


class Referee(_M):
    name: Text | None = None
    phone: Annotated[StrictStr, Field(max_length=40)] | None = None
    relationship: Text | None = None
    email: Text | None = None
    years_known: StrictInt | None = None


class Slot(_M):
    weekday: StrictInt
    start_minute: StrictInt
    end_minute: StrictInt


class Content(_M):
    personal: Personal | None = None
    qualifications: Qualifications | None = None
    curricula: Annotated[list[CurriculumChoice], Field(max_length=6)] | None = None
    referees: Annotated[list[Referee], Field(max_length=4)] | None = None
    referee_consent: StrictBool | None = None
    zone_ids: Annotated[list[Id], Field(max_length=30)] | None = None
    travel_radius_km: StrictInt | None = None
    availability: Annotated[list[Slot], Field(max_length=120)] | None = None


class DraftBody(_M):
    base_revision: Annotated[StrictInt, Field(ge=0, le=1_000_000)]
    step: Annotated[StrictInt, Field(ge=0, le=9)] = 0
    content: Content


class SubmitBody(_M):
    base_revision: Annotated[StrictInt, Field(ge=0, le=1_000_000)]
    declaration: StrictBool
