"""Phone OTP sign-in (A01, #43): the core service owns the code; Appwrite issues the session."""

from __future__ import annotations

import logging
import secrets
from typing import Any

from ..settings import Settings
from .accounts import AppwriteUserDirectory, FakeUserDirectory, UserDirectory
from .policy import OtpPolicy
from .service import OtpError, OtpService
from .sms import AfricasTalkingSender, SmsSender, UnconfiguredSmsSender

__all__ = ["OtpError", "OtpService", "build_otp_service", "build_user_directory"]

log = logging.getLogger("hs_core.auth_otp")


def build_sms_sender(settings: Settings) -> SmsSender:
    key = settings.africastalking_api_key.get_secret_value()
    if key and settings.africastalking_username:
        return AfricasTalkingSender(
            api_key=key,
            username=settings.africastalking_username,
            sender_id=settings.africastalking_sender_id,
        )
    return UnconfiguredSmsSender()


def build_user_directory(settings: Settings) -> UserDirectory:
    if settings.appwrite_endpoint and settings.appwrite_api_key.get_secret_value():
        from appwrite.client import Client

        client = (
            Client()
            .set_endpoint(settings.appwrite_endpoint)
            .set_project(settings.appwrite_project_id)
            .set_key(settings.appwrite_api_key.get_secret_value())
        )
        return AppwriteUserDirectory.from_client(client)
    return FakeUserDirectory()  # dev only: Settings.problems() demands Appwrite elsewhere


def build_otp_service(
    settings: Settings,
    repo: Any,
    sms: SmsSender | None = None,
    users: UserDirectory | None = None,
    roles: Any | None = None,
) -> OtpService:
    pepper = settings.otp_pepper.get_secret_value()
    if not pepper:  # only reachable in dev (deployed settings require it): per-process pepper
        pepper = secrets.token_hex(32)
    return OtpService(
        repo=repo,
        sms=sms or build_sms_sender(settings),
        users=users or build_user_directory(settings),
        pepper=pepper,
        policy=OtpPolicy.from_settings(settings),
        roles=roles,
    )
