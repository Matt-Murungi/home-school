"""GET /v1/catalogue/... (K01). Read only; any signed-in user; drafts only for curator and ops.

Caching: published and retired versions are immutable, so their content is served with a strong
ETag and `Cache-Control: private, max-age=<year>, immutable`; pointers ("current version",
curricula, version lists) get a short max-age; drafts and per-child answers are `no-cache`
(revalidated on every use, with the permission check running BEFORE the 304 is decided).
Errors: unknown ids, soft-deleted rows, drafts a caller may not see and children a caller may not
read are one and the same 404 (nothing to probe).
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends, Query, Request, Response

from ..auth import Principal, current_principal
from ..errors import AppError
from .http import Rendered, render, respond
from .service import (
    CatalogueNotFound,
    CatalogueService,
    InvalidCursor,
    InvalidRequest,
    NoPublishedVersion,
    PublishInProgress,
    Viewer,
    p_curriculum,
    p_version,
)

router = APIRouter(prefix="/v1/catalogue", tags=["catalogue"])


def IdQ():  # noqa: N802 - a fresh Query per parameter (a shared instance would share its alias)
    return Query(default=None, max_length=36)


def CursorQ():  # noqa: N802
    return Query(default=None, max_length=400)


def _svc(request: Request) -> CatalogueService:
    return request.app.state.catalogue


def _viewer(p: Principal = Depends(current_principal)) -> Viewer:  # noqa: B008
    return Viewer.of(p)


def _translate(fn):
    """Service exceptions -> the standard error body."""

    def run(*a, **kw):
        try:
            return fn(*a, **kw)
        except NoPublishedVersion:
            raise AppError(
                404, "no_published_version", "This curriculum has no published version"
            ) from None
        except CatalogueNotFound:
            raise AppError(404, "not_found", "Not found") from None
        except InvalidCursor:
            raise AppError(400, "invalid_cursor", "Cursor is not valid for this request") from None
        except InvalidRequest as e:
            raise AppError(400, "invalid_request", str(e)) from None
        except PublishInProgress:
            raise AppError(
                503,
                "publish_in_progress",
                "Catalogue is being published, retry shortly",
                {"Retry-After": "1"},
            ) from None

    return run


def _cc(request: Request, kind: str) -> str:
    s = request.app.state.settings
    return {
        "immutable": f"private, max-age={s.catalogue_immutable_max_age_s}, immutable",
        "pointer": f"private, max-age={s.catalogue_current_max_age_s}",
        "revalidate": "private, no-cache",
    }[kind]


def _serve(
    request: Request,
    scope: str,
    payload: Callable[[], dict],
    *,
    cache_key: tuple | None = None,
    kind: str = "revalidate",
    max_bytes: int | None = None,
) -> Response:
    """Render (from the LRU for immutable content when `cache_key` is given), apply the size cap,
    answer 200 or 304."""
    svc = _svc(request)
    r: Rendered | None = svc.cache.get(("r", *cache_key)) if cache_key else None
    if r is None:
        r = render(scope, payload())
        if max_bytes is not None and len(r.body) > max_bytes:
            raise AppError(
                413,
                "response_too_large",
                "Too many topics for one response; page through /topics instead",
            )
        if cache_key:
            svc.cache.put(("r", *cache_key), r)
    return respond(request, r, _cc(request, kind))


def _version_kind(svc: CatalogueService, version: dict) -> str:
    return "immutable" if svc.immutable(version) else "revalidate"


# -------------------- curricula and versions
@router.get("/curricula")
def curricula(request: Request, v: Viewer = Depends(_viewer)):  # noqa: B008
    out = _translate(_svc(request).curricula)(v)
    return _serve(request, "curricula", lambda: out, kind="pointer")


@router.get("/curricula/{curriculum_id}/versions")
def versions(curriculum_id: str, request: Request, v: Viewer = Depends(_viewer)):  # noqa: B008
    out = _translate(_svc(request).versions)(v, curriculum_id)
    return _serve(
        request, curriculum_id, lambda: out, kind="revalidate" if v.sees_drafts else "pointer"
    )


@router.get("/curricula/{curriculum_id}/current")
def current(curriculum_id: str, request: Request, v: Viewer = Depends(_viewer)):  # noqa: B008
    row = _translate(_svc(request).current_version)(v, curriculum_id)
    return _serve(request, row["$id"], lambda: {"version": p_version(row)}, kind="pointer")


@router.get("/versions/{version_id}")
def version(version_id: str, request: Request, v: Viewer = Depends(_viewer)):  # noqa: B008
    svc = _svc(request)
    row = _translate(svc.version)(v, version_id)
    return _serve(
        request, version_id, lambda: {"version": p_version(row)}, kind=_version_kind(svc, row)
    )


# -------------------- content of one version
def _content(request: Request, v: Viewer, version_id: str):
    svc = _svc(request)
    return svc, _translate(svc.version)(v, version_id)


def _version_levels(svc, row, request):
    return _serve(
        request,
        row["$id"],
        lambda: svc.levels(row),
        cache_key=("levels", row["$id"]) if svc.immutable(row) else None,
        kind=_version_kind(svc, row),
    )


@router.get("/versions/{version_id}/levels")
def levels(version_id: str, request: Request, v: Viewer = Depends(_viewer)):  # noqa: B008
    svc, row = _content(request, v, version_id)
    return _version_levels(svc, row, request)


@router.get("/versions/{version_id}/subjects")
def subjects(version_id: str, request: Request, v: Viewer = Depends(_viewer)):  # noqa: B008
    svc, row = _content(request, v, version_id)
    return _serve(
        request,
        row["$id"],
        lambda: svc.subjects(row),
        cache_key=("subjects", row["$id"]) if svc.immutable(row) else None,
        kind=_version_kind(svc, row),
    )


@router.get("/versions/{version_id}/strands")
def strands(
    version_id: str,
    request: Request,
    subject_id: str | None = IdQ(),
    level_id: str | None = IdQ(),
    v: Viewer = Depends(_viewer),  # noqa: B008
):
    svc, row = _content(request, v, version_id)
    return _serve(
        request,
        row["$id"],
        lambda: svc.strands(row, subject_id, level_id),
        cache_key=("strands", row["$id"], subject_id, level_id) if svc.immutable(row) else None,
        kind=_version_kind(svc, row),
    )


def _topics(request, svc, row, subject_id, level_id, parent_id, roots, cursor, limit):
    return _serve(
        request,
        row["$id"],
        lambda: _translate(svc.topics_page)(
            row,
            subject_id=subject_id,
            level_id=level_id,
            parent_id=parent_id,
            roots=roots,
            cursor=cursor,
            limit=limit,
        ),
        cache_key=("topics", row["$id"], subject_id, level_id, parent_id, roots, cursor, limit)
        if svc.immutable(row)
        else None,
        kind=_version_kind(svc, row),
    )


@router.get("/versions/{version_id}/topics")
def topics(
    version_id: str,
    request: Request,
    subject_id: str | None = IdQ(),
    level_id: str | None = IdQ(),
    parent_id: str | None = IdQ(),
    roots: bool = False,
    cursor: str | None = CursorQ(),
    limit: int = Query(default=50, ge=1, le=100),
    v: Viewer = Depends(_viewer),  # noqa: B008
):
    svc, row = _content(request, v, version_id)
    return _topics(request, svc, row, subject_id, level_id, parent_id, roots, cursor, limit)


def _search(request, svc, row, text, subject_id, level_id, limit):
    return _serve(
        request,
        row["$id"],
        lambda: _translate(svc.search)(
            row, text, subject_id=subject_id, level_id=level_id, limit=limit
        ),
        cache_key=("search", row["$id"], text, subject_id, level_id, limit)
        if svc.immutable(row)
        else None,
        kind=_version_kind(svc, row),
    )


@router.get("/versions/{version_id}/topics/search")
def topic_search(
    version_id: str,
    request: Request,
    q: str = Query(min_length=1, max_length=80),
    subject_id: str | None = IdQ(),
    level_id: str | None = IdQ(),
    limit: int = Query(default=20, ge=1, le=50),
    v: Viewer = Depends(_viewer),  # noqa: B008
):
    svc, row = _content(request, v, version_id)
    return _search(request, svc, row, q, subject_id, level_id, limit)


@router.get("/versions/{version_id}/topics/{topic_id}")
def topic(version_id: str, topic_id: str, request: Request, v: Viewer = Depends(_viewer)):  # noqa: B008
    svc, row = _content(request, v, version_id)
    return _serve(
        request,
        row["$id"],
        lambda: {"topic": _translate(svc.topic)(row, topic_id)},
        kind=_version_kind(svc, row),
    )


def _tree(request, svc, row, level_id, subject_id):
    return _serve(
        request,
        row["$id"],
        lambda: svc.tree(row, level_id, subject_id),
        cache_key=("tree", row["$id"], level_id, subject_id) if svc.immutable(row) else None,
        kind=_version_kind(svc, row),
        max_bytes=request.app.state.settings.catalogue_bundle_max_bytes,
    )


def _bundle(request, svc, row, level_id, subject_id):
    return _serve(
        request,
        row["$id"],
        lambda: svc.bundle(row, level_id, subject_id),
        cache_key=("bundle", row["$id"], level_id, subject_id) if svc.immutable(row) else None,
        kind=_version_kind(svc, row),
        max_bytes=request.app.state.settings.catalogue_bundle_max_bytes,
    )


@router.get("/versions/{version_id}/tree")
def tree(
    version_id: str,
    request: Request,
    level_id: str = Query(max_length=36),
    subject_id: str = Query(max_length=36),
    v: Viewer = Depends(_viewer),  # noqa: B008
):
    svc, row = _content(request, v, version_id)
    return _tree(request, svc, row, level_id, subject_id)


@router.get("/versions/{version_id}/bundle")
def bundle(
    version_id: str,
    request: Request,
    level_id: str = Query(max_length=36),
    subject_id: str = Query(max_length=36),
    v: Viewer = Depends(_viewer),  # noqa: B008
):
    svc, row = _content(request, v, version_id)
    return _bundle(request, svc, row, level_id, subject_id)


@router.get("/versions/{version_id}/level-equivalences")
def level_equivalences(version_id: str, request: Request, v: Viewer = Depends(_viewer)):  # noqa: B008
    svc, row = _content(request, v, version_id)
    out = _translate(svc.level_equivalences)(v, row)
    return _serve(request, row["$id"], lambda: out)


# -------------------- unversioned
@router.get("/struggle-tags")
def struggle_tags(
    request: Request,
    subject_id: str | None = IdQ(),
    v: Viewer = Depends(_viewer),  # noqa: B008
):
    out = _svc(request).struggle_tags(subject_id)
    return _serve(request, "tags", lambda: out, kind="pointer")


@router.get("/changes")
def changes(
    request: Request,
    since_version: str = Query(max_length=36),
    to_version: str | None = IdQ(),
    v: Viewer = Depends(_viewer),  # noqa: B008
):
    svc = _svc(request)
    since = _translate(svc.version)(v, since_version)
    if to_version:
        to = _translate(svc.version)(v, to_version)
    else:
        to = _translate(svc.current_version)(v, since["curriculum_id"])
    fixed = bool(to_version) and svc.immutable(since) and svc.immutable(to)
    return _serve(
        request,
        to["$id"],
        lambda: _translate(svc.changes)(since, to),
        cache_key=("changes", since["$id"], to["$id"]) if fixed else None,
        kind="immutable" if fixed else "revalidate",
    )


# -------------------- a child's catalogue
@router.get("/children/{child_id}/version")
def child_version(child_id: str, request: Request, v: Viewer = Depends(_viewer)):  # noqa: B008
    svc = _svc(request)
    row = _translate(svc.child_version)(v, child_id)
    cur = svc.current_for_child(row)
    body = {
        "version": p_version(row),
        "curriculum": None,
        "is_current": row.get("status") == "published",
        "current_version_id": cur["$id"] if cur else None,
    }
    c = svc._get("curriculum", row["curriculum_id"])
    body["curriculum"] = p_curriculum(c) if c else None
    return _serve(request, row["$id"], lambda: body)


@router.get("/children/{child_id}/topics")
def child_topics(
    child_id: str,
    request: Request,
    subject_id: str | None = IdQ(),
    level_id: str | None = IdQ(),
    parent_id: str | None = IdQ(),
    roots: bool = False,
    cursor: str | None = CursorQ(),
    limit: int = Query(default=50, ge=1, le=100),
    v: Viewer = Depends(_viewer),  # noqa: B008
):
    svc = _svc(request)
    row = _translate(svc.child_version)(v, child_id)
    return _serve_child(
        _topics(request, svc, row, subject_id, level_id, parent_id, roots, cursor, limit)
    )


@router.get("/children/{child_id}/topics/search")
def child_topic_search(
    child_id: str,
    request: Request,
    q: str = Query(min_length=1, max_length=80),
    subject_id: str | None = IdQ(),
    level_id: str | None = IdQ(),
    limit: int = Query(default=20, ge=1, le=50),
    v: Viewer = Depends(_viewer),  # noqa: B008
):
    svc = _svc(request)
    row = _translate(svc.child_version)(v, child_id)
    return _serve_child(_search(request, svc, row, q, subject_id, level_id, limit))


@router.get("/children/{child_id}/bundle")
def child_bundle(
    child_id: str,
    request: Request,
    level_id: str = Query(max_length=36),
    subject_id: str = Query(max_length=36),
    v: Viewer = Depends(_viewer),  # noqa: B008
):
    svc = _svc(request)
    row = _translate(svc.child_version)(v, child_id)
    return _serve_child(_bundle(request, svc, row, level_id, subject_id))


def _serve_child(resp: Response) -> Response:
    """Per-child answers reveal the child's curriculum: revalidate on every use (the permission
    check has already run, so a revoked teacher never gets a 304)."""
    resp.headers["Cache-Control"] = "private, no-cache"
    return resp
