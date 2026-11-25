"""Error taxonomy. `retryable` says whether the same operation may be tried again later.

Permanent: MomoConfigError, ValidationError (InvalidAmount, InvalidMsisdn), AuthError,
RequestRejected (4xx), CallbackRejected.
Retryable: RateLimited, ServerError, TransportError (connection/timeout before a response).
OutcomeUnknown: a create call may or may not have been accepted; the caller must query status
with the SAME reference (never resend blindly, never mint a new reference). It is retryable only
via a status query.

AuthError / RequestRejected / NotFound are permanent ONLY for the call that raised them: from a
status poll they say nothing about the payment (it may exist). `create` calls convert them into
OutcomeUnknown whenever a POST may have been accepted; they escape a create only as the definite
refusal of the POST itself.
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
    """A create (RequestToPay/Transfer) may or may not exist at MTN and status could not tell.

    Caller contract: persist nothing new. Poll `get_status(reference_id)` with the SAME reference
    until it answers definitively (a result, or a definite 404 meaning MTN never saw it). NEVER
    mint a new reference for the same business operation (double payment). For payouts: hold the
    payout and alert a human. `detail` says what is known; `__cause__` is the last error.
    """

    retryable = True

    def __init__(self, reference_id: str, detail: str = ""):
        super().__init__(
            f"outcome unknown for reference {reference_id}; query status"
            + (f" ({detail})" if detail else "")
        )
        self.reference_id = reference_id
        self.detail = detail


class CallbackRejected(MomoError):
    pass
