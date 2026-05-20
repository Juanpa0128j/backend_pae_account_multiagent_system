"""Tests for Inngest workflow settings and production guard in app.core.config."""

import pytest

from app.core.config import Settings

VALID_SECRET = "a" * 40


def _settings(**env: str) -> Settings:
    """Build Settings without loading .env so tests are hermetic."""
    return Settings(_env_file=None, **{})  # type: ignore[call-arg]


def test_default_engine_is_inline(monkeypatch):
    # Arrange
    monkeypatch.delenv("WORKFLOW_ENGINE", raising=False)

    # Act
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    # Assert
    assert settings.workflow_engine == "inline"


def test_inngest_dev_default_true(monkeypatch):
    # Arrange
    monkeypatch.delenv("INNGEST_DEV", raising=False)

    # Act
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    # Assert
    assert settings.inngest_dev is True


def test_prod_inngest_missing_keys_raises(monkeypatch):
    # Arrange
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", VALID_SECRET)
    monkeypatch.setenv("WORKFLOW_ENGINE", "inngest")
    monkeypatch.setenv("INNGEST_EVENT_KEY", "")
    monkeypatch.setenv("INNGEST_SIGNING_KEY", "")

    # Act / Assert
    with pytest.raises(ValueError, match="INNGEST_EVENT_KEY"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_prod_inngest_with_keys_ok(monkeypatch):
    # Arrange
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", VALID_SECRET)
    monkeypatch.setenv("WORKFLOW_ENGINE", "inngest")
    monkeypatch.setenv("INNGEST_EVENT_KEY", "evt-key")
    monkeypatch.setenv("INNGEST_SIGNING_KEY", "sign-key")

    # Act
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    # Assert
    assert settings.workflow_engine == "inngest"


def test_prod_inline_no_keys_ok(monkeypatch):
    # Arrange
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", VALID_SECRET)
    monkeypatch.setenv("WORKFLOW_ENGINE", "inline")
    monkeypatch.setenv("INNGEST_EVENT_KEY", "")
    monkeypatch.setenv("INNGEST_SIGNING_KEY", "")

    # Act
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    # Assert
    assert settings.workflow_engine == "inline"
    assert settings.is_production is True
