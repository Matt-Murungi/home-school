"""N01 (#126): notification service and template store. See README section in memory.md."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from ..jobs.alerts import AlertSink
from ..repository import Repository
from ..settings import Settings
from .channels import StoredInAppChannel, UnconfiguredChannel, UnconfiguredEmailSender
from .config import LiveNotificationConfig, NotificationConfig
from .devices import DeviceRegistry
from .preferences import RepositoryPreferenceStore
from .service import NoDirectory, NotificationService, NullEscalation
from .templates import TemplateStore


def build_notification_service(
    settings: Settings,
    repo: Repository,
    alerts: AlertSink | None = None,
    clock: Callable[[], datetime] | None = None,
    audit: Callable[[dict], None] | None = None,
    app_settings=None,
) -> NotificationService:
    """Wiring until N02 (#127): push and email channels are 'unconfigured' (every send fails
    permanently and loudly), the in-app channel is real (the row is the message)."""
    config = NotificationConfig.from_settings(settings)
    if app_settings is not None:  # H05: stored values win over the environment defaults
        config = LiveNotificationConfig(config, app_settings)
    return NotificationService(
        repo,
        store=TemplateStore(repo, audit=audit),
        push=UnconfiguredChannel(),
        email_sender=UnconfiguredEmailSender(),
        in_app=StoredInAppChannel(),
        directory=NoDirectory(),
        devices=DeviceRegistry(repo),
        preferences=RepositoryPreferenceStore(repo),
        config=config,
        alerts=alerts,
        clock=clock,
        escalation=NullEscalation(),
    )


__all__ = ["NotificationService", "build_notification_service"]
