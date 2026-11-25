"""Rendering and conditional-request handling for catalogue responses.

ETag = `"<scope>.<sha256(body)[:32]>"`: the scope is the catalogue version id (or a fixed word for
unversioned lists), the hash covers the exact bytes served, so any content change changes the tag
and a published version (immutable) keeps the same tag forever. The gzip representation carries
its own tag (`...-gz`), as strong validators must differ per representation.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
from dataclasses import dataclass

from fastapi import Request, Response

GZIP_MIN_BYTES = 1024
_TAG = re.compile(r'\s*(?:W/)?"([^"]*)"\s*')


@dataclass(frozen=True)
class Rendered:
    body: bytes
    etag: str  # identity representation, with quotes
    gz: bytes | None = None

    @property
    def gz_etag(self) -> str:
        return self.etag[:-1] + '-gz"'


def dumps(payload) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode()


def render(scope: str, payload) -> Rendered:
    body = dumps(payload)
    etag = f'"{scope}.{hashlib.sha256(body).hexdigest()[:32]}"'
    gz = gzip.compress(body, 6, mtime=0) if len(body) >= GZIP_MIN_BYTES else None
    return Rendered(body, etag, gz)


def _tags(header: str) -> list[str] | None:
    """`*` -> None (matches anything); else the opaque tags (weak comparison, RFC 9110 13.1.2)."""
    if header.strip() == "*":
        return None
    return _TAG.findall(header)


def _accepts_gzip(request: Request) -> bool:
    for part in request.headers.get("accept-encoding", "").split(","):
        name, _, q = part.strip().partition(";")
        if name.strip().lower() in ("gzip", "x-gzip"):
            return q.strip().replace(" ", "") not in ("q=0", "q=0.0", "q=0.00", "q=0.000")
    return False


def respond(request: Request, r: Rendered, cache_control: str) -> Response:
    use_gz = r.gz is not None and _accepts_gzip(request)
    etag = r.gz_etag if use_gz else r.etag
    headers = {
        "ETag": etag,
        "Cache-Control": cache_control,
        "Vary": "Accept-Encoding, Authorization",
    }
    inm = request.headers.get("if-none-match")
    if inm is not None:
        tags = _tags(inm)
        mine = {r.etag.strip('"'), r.gz_etag.strip('"')}
        if tags is None or mine & set(tags):
            return Response(status_code=304, headers=headers)
    headers["Content-Type"] = "application/json"
    if use_gz:
        headers["Content-Encoding"] = "gzip"
        return Response(r.gz, 200, headers=headers)
    return Response(r.body, 200, headers=headers)
