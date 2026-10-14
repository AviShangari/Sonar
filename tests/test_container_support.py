"""The small pieces that let Sonar run in a Linux container."""

import pytest

from sonar import agent_runner
from sonar.config import load_settings


def test_headless_env_var_overrides_settings_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SONAR_HEADLESS", raising=False)
    settings_file = load_settings().headless  # whatever config/settings.yaml says
    monkeypatch.setenv("SONAR_HEADLESS", "true")
    assert load_settings().headless is True
    monkeypatch.setenv("SONAR_HEADLESS", "0")
    assert load_settings().headless is settings_file  # anything but true/1/yes leaves the file in charge


def test_cli_env_keeps_linux_variables_and_nothing_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", "/home/sonar")
    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-secret")
    monkeypatch.setenv("SONAR_SMTP_PASSWORD", "secret")
    env = agent_runner.cli_env(9333)
    assert env["HOME"] == "/home/sonar"
    assert env["LANG"] == "C.UTF-8"
    assert env["BU_CDP_URL"] == "http://127.0.0.1:9333"
    assert "OPENROUTER_API_KEY" not in env and "SONAR_SMTP_PASSWORD" not in env
