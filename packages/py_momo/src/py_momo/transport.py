"""HTTP transport abstraction. The real one uses urllib with a timeout; tests inject a fake."""

from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol

from .errors import TransportError, TransportTimeout


@dataclass
class HttpRequest:
    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes | None = None
    timeout: float = 15.0


@dataclass
class HttpResponse:
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""


class Transport(Protocol):
    def send(self, request: HttpRequest) -> HttpResponse:
        """Return a response for ANY HTTP status. Raise TransportTimeout/TransportError only
        when no response was received."""
        ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: the request carries credentials (Bearer, subscription key, Basic
    API key) and a followed 3xx would re-send them to another host and turn a POST into a GET.
    redirect_request returning None makes urllib raise HTTPError, which we return as a response."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


class UrllibTransport:
    def send(self, request: HttpRequest) -> HttpResponse:
        req = urllib.request.Request(
            request.url, data=request.body, method=request.method, headers=request.headers
        )
        try:
            with _OPENER.open(req, timeout=request.timeout) as r:  # noqa: S310
                return HttpResponse(r.status, dict(r.headers.items()), r.read())
        except urllib.error.HTTPError as e:
            return HttpResponse(e.code, dict(e.headers.items()), e.read())
        except TimeoutError as e:
            raise TransportTimeout("request timed out") from e
        except urllib.error.URLError as e:
            if isinstance(e.reason, TimeoutError):
                raise TransportTimeout("request timed out") from e
            raise TransportError(f"connection error: {type(e.reason).__name__}") from e
        except OSError as e:
            raise TransportError(f"connection error: {type(e).__name__}") from e
