"""Settings unit tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_horde_service_alerts.settings import HordeAlertsSettings


def test_settings_have_safe_defaults() -> None:
    settings = HordeAlertsSettings()
    assert settings.moderator_cache_ttl_seconds >= 1
    assert "alertname" in settings.public_alert_label_allowlist
    assert "summary" in settings.public_annotation_allowlist
    assert "instance" not in settings.public_alert_label_allowlist


def test_settings_reject_extra_fields() -> None:
    with pytest.raises(ValidationError):
        HordeAlertsSettings(unknown_field="oops")  # type: ignore[call-arg]
