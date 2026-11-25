"""Notification errors. Messages carry variable NAMES and template keys only, never values."""

from __future__ import annotations


class NotificationError(Exception):
    """Base class."""


class TemplateError(NotificationError):
    code = "template_error"

    def __init__(self, message: str, names: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.names = names


class MissingVariables(TemplateError):
    code = "missing"

    def __init__(self, names) -> None:
        names = tuple(sorted(names))
        super().__init__(f"missing variables: {', '.join(names)}", names)


class ExtraVariables(TemplateError):
    code = "extra"

    def __init__(self, names) -> None:
        names = tuple(sorted(names))
        super().__init__(f"undeclared variables: {', '.join(names)}", names)


class VariableTypeError(TemplateError):
    code = "type"

    def __init__(self, name: str, expected: str) -> None:
        super().__init__(f"variable {name} must be {expected}", (name,))


class TemplateSyntaxError(TemplateError):
    code = "syntax"


class TemplateTooLong(TemplateError):
    code = "too_long"


class UnknownTemplate(TemplateError):
    code = "unknown"


class PermissionDenied(NotificationError):
    pass


class UnknownEvent(NotificationError):
    pass


class RecipientMismatch(NotificationError):
    pass


class InvalidIdempotencyKey(NotificationError, ValueError):
    pass
