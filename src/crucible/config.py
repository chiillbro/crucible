"""Harness settings from environment variables and an optional ``.env`` file."""

from __future__ import annotations

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Task-specific limits live in ``task.toml``, not here."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="CRUCIBLE_",
        extra="ignore",
    )

    anthropic_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("ANTHROPIC_API_KEY", "CRUCIBLE_ANTHROPIC_API_KEY"),
    )
    llm_model: str = "claude-opus-5-5"
    llm_max_steps: int = Field(default=30, ge=1)
    llm_max_tokens: int = Field(default=4096, ge=256)
    log_level: str = "INFO"
