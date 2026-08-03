import pytest
from pydantic import ValidationError

from ai_agents.config import Settings


def _settings(**overrides: object) -> Settings:
    values = {
        "model_api_key": "test-key",
        "model_base_url": "https://api.example.com/v1",
        "model_name": "test-model",
        "database_url": "postgresql://user:pass@localhost:5432/test",
    }
    values.update(overrides)
    return Settings(**values)


def test_settings_accept_postgres_database_url() -> None:
    settings = _settings()

    assert settings.require_database_url().startswith("postgresql://")
    assert settings.model_api_key.get_secret_value() == "test-key"


def test_settings_reject_non_postgres_database_url() -> None:
    with pytest.raises(ValidationError):
        _settings(database_url="mysql://user:pass@localhost/test")


def test_settings_reject_invalid_pool_range() -> None:
    with pytest.raises(ValidationError):
        _settings(postgres_pool_min_size=5, postgres_pool_max_size=2)
