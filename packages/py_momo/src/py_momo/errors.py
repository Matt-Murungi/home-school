"""Error taxonomy. `retryable` says whether the same operation may be tried again later.

Permanent: MomoConfigError, ValidationError (InvalidAmount, InvalidMsisdn), AuthError,
RequestRejected (4xx), CallbackRejected.
Retryable: RateLimited, ServerError, TransportError (connection/timeout before a response).
OutcomeUnknown: a create call may or may not have been accepted; the caller must query status
(never resend blindly). It is retryable only via a status query.
"""

from __future__ import annotations


class MomoError(Exception):
    retryable: bool = False


class MomoConfigError(MomoError):
    pass


class ValidationError(MomoError):
    pass


class InvalidAmount(ValidationError):
    pass


class InvalidMsisdn(ValidationError):
    pass


class InvalidReference(ValidationError):
    pass


class AuthError(MomoError):
    """Credentials rejected (token endpoint 401/403, or 401 again after a refresh)."""


class RequestRejected(MomoError):
    def __init__(self, status_code: int, code: str = "", message: str = ""):
        super().__init__(f"MoMo rejected request: HTTP {status_code} {code}".strip())
        self.status_code = status_code
        self.code = code
        self.message = message


class NotFound(RequestRejected):
    pass


class RateLimited(MomoError):
    retryable = True

    def __init__(self, retry_after: float | None = None):
        super().__init__("MoMo rate limit (HTTP 429)")
        self.retry_after = retry_after


class ServerError(MomoError):
    retryable = True

    def __init__(self, status_code: int):
        super().__init__(f"MoMo server error HTTP {status_code}")
        self.status_code = status_code


class TransportError(MomoError):
    retryable = True


class TransportTimeout(TransportError):
    pass


class OutcomeUnknown(MomoError):
    """The create call may have been accepted; status could not be established."""

    retryable = True

    def __init__(self, reference_id: str):
        super().__init__(f"outcome unknown for reference {reference_id}; query status")
        self.reference_id = reference_id


class CallbackRejected(MomoError):
    pass
