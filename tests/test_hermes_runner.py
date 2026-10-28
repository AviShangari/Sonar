import asyncio
from pathlib import Path

import pytest
import yaml

from sonar import hermes_runner
from sonar.config import HERMES_PATH, HermesSettings, load_hermes_settings
from sonar.hermes_runner import HermesServer, child_env, prepare_home, render_config


def settings(tmp_path: Path, **over) -> HermesSettings:
    values = dict(home=str(tmp_path / "home"), install_dir=str(tmp_path / "install"), executable=str(tmp_path / "hermes.exe"))
    return HermesSettings(**{**values, **over})


def test_the_real_hermes_config_file_loads() -> None:
    cfg = load_hermes_settings(HERMES_PATH)
    assert cfg.model == "openai/gpt-6.1-sol" and cfg.provider == "openrouter"
    assert cfg.task_seconds > 0 and cfg.max_turns > 0


def test_the_environment_hermes_gets_is_a_whitelist(monkeypatch, tmp_path) -> None:
    for name in ("SONAR_SMTP_PASSWORD", "SONAR_ARIBA_PASSWORD", "SONAR_ARIBA_USERNAME", "AWS_SECRET_ACCESS_KEY", "SOME_TOKEN"):
        monkeypatch.setenv(name, "secret")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-parent")
    env = child_env(settings(tmp_path))
    for name in ("SONAR_SMTP_PASSWORD", "SONAR_ARIBA_PASSWORD", "SONAR_ARIBA_USERNAME", "AWS_SECRET_ACCESS_KEY", "SOME_TOKEN"):
        assert name not in env
    assert "OPENROUTER_API_KEY" not in env  # only the server start adds it, explicitly
    assert env["HERMES_HOME"] == str(tmp_path / "home")
    assert env["SONAR_CODE_CHECK_PATH"].endswith("code_check.py")


def test_learn_mode_flag_follows_the_setting(tmp_path) -> None:
    assert child_env(settings(tmp_path, learn=True))["SONAR_LEARN_MODE"] == "1"
    assert "SONAR_LEARN_MODE" not in child_env(settings(tmp_path, learn=False))


def test_rendered_config_allows_only_the_toolsets_we_want(tmp_path) -> None:
    config = yaml.safe_load(render_config(settings(tmp_path), 9333))
    assert config["browser"]["cdp_url"] == "http://127.0.0.1:9333"
    assert config["browser"]["backend"] == "browser-use"
    assert config["plugins"]["enabled"] == ["sonar_guard"]
    assert config["model"] == {"default": "openai/gpt-6.1-sol", "provider": "openrouter"}
    toolsets = config["platform_toolsets"]["api_server"]
    assert toolsets == ["browser", "terminal", "memory", "skills"]
    # nothing that could read files, browse the web, run code or write files on its own
    assert not {"file", "web", "code_execution", "computer_use", "delegation"} & set(toolsets)
    without_notes = yaml.safe_load(render_config(settings(tmp_path, learn=False), 9333))
    assert without_notes["platform_toolsets"]["api_server"] == ["browser", "terminal"]


def test_prepare_home_installs_the_repo_guard_and_keeps_notes(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(hermes_runner, "WORK_DIR", tmp_path / "work")
    cfg = settings(tmp_path)
    notes = cfg.home_path / "skills" / "site-x" / "SKILL.md"
    notes.parent.mkdir(parents=True)
    notes.write_text("saved note", encoding="utf-8")
    stale = cfg.home_path / "plugins" / "sonar_guard" / "leftover.py"
    stale.parent.mkdir(parents=True)
    stale.write_text("old", encoding="utf-8")
    prepare_home(cfg, 9333)
    installed = cfg.home_path / "plugins" / "sonar_guard"
    assert (installed / "__init__.py").read_text(encoding="utf-8") == (hermes_runner.PLUGIN_SOURCE / "__init__.py").read_text(encoding="utf-8")
    assert not stale.exists()  # the plugin folder is replaced, not merged
    assert notes.read_text(encoding="utf-8") == "saved note"
    assert (cfg.home_path / "config.yaml").exists()


def test_start_refuses_when_hermes_processes_survive_the_stop(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(hermes_runner, "stop_hermes", lambda: 2)
    with pytest.raises(hermes_runner.HermesError, match="still running"):
        asyncio.run(HermesServer(settings(tmp_path), 9333, "demo").start())


def test_start_refuses_when_hermes_is_not_installed(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(hermes_runner, "stop_hermes", lambda: 0)
    with pytest.raises(hermes_runner.HermesError, match="not installed"):
        asyncio.run(HermesServer(settings(tmp_path), 9333, "demo").start())


def test_start_refuses_when_the_preflight_fails_and_never_starts_the_server(tmp_path, monkeypatch) -> None:
    cfg = settings(tmp_path)
    Path(cfg.executable).write_text("", encoding="utf-8")
    monkeypatch.setattr(hermes_runner, "stop_hermes", lambda: 0)
    monkeypatch.setattr(hermes_runner, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(hermes_runner, "preflight", lambda c: (False, "the guard answered wrongly"))
    started: list = []
    monkeypatch.setattr(hermes_runner.subprocess, "Popen", lambda *a, **k: started.append(a))
    with pytest.raises(hermes_runner.HermesError, match="preflight refused"):
        asyncio.run(HermesServer(cfg, 9333, "demo").start())
    assert started == []


def test_stopping_a_server_that_never_started_books_no_cost(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(hermes_runner, "stop_hermes", lambda: 0)
    monkeypatch.setattr(hermes_runner, "WORK_DIR", tmp_path / "work")
    server = HermesServer(settings(tmp_path), 9333, "demo")
    asyncio.run(server.stop())
    assert server.cost_usd == 0.0
