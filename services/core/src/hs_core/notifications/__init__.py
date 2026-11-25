"""N01 (#126): notification service and template store. See README section in memory.md."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from ..jobs.alerts import AlertSink
from ..repository import Repository
from ..settings import Settings
from .channels import StoredInAppChannel, UnconfiguredChannel, UnconfiguredEmailSender
from .config import NotificationConfig
from .devices import DeviceRegistry
from .fcm import FcmPushChannel, parse_service_account
from .preferences import RepositoryPreferenceStore
from .resend import ResendEmailSender
from .service import NoDirectory, NotificationService, NullEscalation
from .suppression import SuppressionList
from .templates import TemplateStore


def build_push_channel(settings: Settings, clock: Callable[[], datetime] | None = None):
    if not settings.push_configured():
        return UnconfiguredChannel()
    account = parse_service_account(
        settings.fcm_service_account_json.get_secret_value(), settings.fcm_project_id
    )
    return FcmPushChannel(account, clock=clock)


def build_email_sender(settings: Settings, clock: Callable[[], datetime] | None = None):
    if not settings.email_configured():
        return UnconfiguredEmailSender()
    return ResendEmailSender(
        settings.resend_api_key.get_secret_value(),
        settings.resend_webhook_secret.get_secret_value(),
        settings.email_from.strip(),
        settings.email_reply_to.strip() or None,
        settings.email_from_name,
        clock=clock,
    )


def build_notification_service(
    settings: Settings,
    repo: Repository,
    alerts: AlertSink | None = None,
    clock: Callable[[], datetime] | None = None,
    audit: Callable[[dict], None] | None = None,
) -> NotificationService:
    """Wiring. A real adapter (FCM, Resend) is used ONLY when every setting of its group is present
    and valid (N02); otherwise the channel is the N01 placeholder: every send fails permanently and
    loudly, never a fake success. The in-app channel is real (the row is the message)."""
    return NotificationService(
        repo,
        store=TemplateStore(repo, audit=audit),
        push=build_push_channel(settings, clock),
        email_sender=build_email_sender(settings, clock),
        in_app=StoredInAppChannel(),
        directory=NoDirectory(),
        devices=DeviceRegistry(repo),
        preferences=RepositoryPreferenceStore(repo),
        config=NotificationConfig.from_settings(settings),
        alerts=alerts,
        clock=clock,
        escalation=NullEscalation(),
        suppression=SuppressionList(repo, clock),
        email_from=settings.email_from.strip(),
        email_reply_to=settings.email_reply_to.strip() or None,
    )


__all__ = ["NotificationService", "build_notification_service"]
