"""Settings loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from crucible.config import Settings


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for key in (
        "ANTHROPIC_API_KEY",
        "CRUCIBLE_ANTHROPIC_API_KEY",
        "CRUCIBLE_LLM_MODEL",
        "CRUCIBLE_LOG_LEVEL",
    ):
        monkeypatch.delenv(key, raising=False)


def test_defaults() -> None:
    settings = Settings()
    assert settings.anthropic_api_key is None
    assert settings.llm_model == "claude-sonnet-5-5"
    assert settings.llm_max_steps == 30
    assert settings.log_level == "INFO"


def test_reads_conventional_and_prefixed_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("CRUCIBLE_LLM_MODEL", "claude-opus-5-5")
    monkeypatch.setenv("CRUCIBLE_LOG_LEVEL", "DEBUG")
    settings = Settings()
    assert settings.anthropic_api_key is not None
    assert settings.anthropic_api_key.get_secret_value() == "sk-test"
    assert "sk-test" not in repr(settings)
    assert settings.llm_model == "claude-opus-5-5"
    assert settings.log_level == "DEBUG"


def test_reads_dotenv_file(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("CRUCIBLE_LLM_MAX_STEPS=5\n")
    assert Settings().llm_max_steps == 5


def test_rejects_invalid_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRUCIBLE_LLM_MAX_STEPS", "0")
    with pytest.raises(ValueError, match="llm_max_steps"):
        Settings()
