"""Settings unit tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_horde_service_alerts.settings import HordeAlertsSettings


def test_settings_have_safe_defaults() -> None:
    settings = HordeAlertsSettings()
    assert settings.moderator_cache_ttl_seconds >= 1
    assert settings.enable_db is True
    assert settings.enable_background_tasks is True
    assert settings.status_evaluator_interval_seconds > 0
    assert settings.maintenance_runner_interval_seconds > 0
    assert settings.history_retention_days >= 90
    assert settings.backfill_on_startup is False


def test_settings_reject_extra_fields() -> None:
    with pytest.raises(ValidationError):
        HordeAlertsSettings(unknown_field="oops")  # type: ignore[call-arg]
