"""llm.py is the only place auth and models are chosen. No test here touches the network."""

import pytest

from sonar import llm, secrets
from sonar.agent_runner import isolated_options
from sonar.config import Settings


@pytest.fixture(autouse=True)
def restore_llm_settings():
    before = llm._settings
    yield
    llm._settings = before


@pytest.fixture()
def key(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-key")
    monkeypatch.setattr(secrets, "ENV_FILE", tmp_path / "missing.env")  # never read the real .env in tests


def test_subscription_sets_no_model_and_no_auth_environment() -> None:
    llm.configure(Settings(auth="subscription"))
    assert llm.agent_llm_options("navigation") == {} and llm.agent_llm_options("summary") == {}
    assert isolated_options("s", 2, "summary").env == {"ENABLE_CLAUDEAI_MCP_SERVERS": "false"}


def test_openrouter_gives_each_role_its_own_model_and_only_navigation_an_effort(key) -> None:
    llm.configure(Settings(auth="openrouter"))
    nav, summary = llm.agent_llm_options("navigation"), llm.agent_llm_options("summary")
    assert (nav["model"], nav["effort"]) == ("openai/gpt-6.1-sol", "medium")
    assert summary["model"] == "anthropic/claude-sonnet-5.5" and "effort" not in summary


def test_openrouter_points_the_sdk_at_openrouter_with_the_key_as_a_bearer_token(key) -> None:
    llm.configure(Settings(auth="openrouter"))
    env = llm.agent_llm_options("summary")["env"]
    assert env == {
        "ANTHROPIC_BASE_URL": "https://openrouter.ai/api",
        "ANTHROPIC_AUTH_TOKEN": "sk-or-test-key",
        "ANTHROPIC_API_KEY": "",  # empty on purpose: a real Anthropic key would be used instead
    }


def test_models_and_effort_come_from_settings(key) -> None:
    llm.configure(Settings(auth="openrouter", navigation_model="x/nav", navigation_effort="high", summary_model="x/sum"))
    assert llm.agent_llm_options("navigation")["model"] == "x/nav" and llm.agent_llm_options("navigation")["effort"] == "high"
    assert llm.agent_llm_options("summary")["model"] == "x/sum"
    llm.configure(Settings(auth="openrouter", navigation_effort=None))
    assert "effort" not in llm.agent_llm_options("navigation")


def test_isolation_survives_the_api_key_setup(key) -> None:
    llm.configure(Settings(auth="openrouter"))
    options = isolated_options("system", 2, "summary")
    assert options.setting_sources == [] and options.tools == []  # still no disk settings, no tools
    assert options.env["ENABLE_CLAUDEAI_MCP_SERVERS"] == "false" and options.env["ANTHROPIC_AUTH_TOKEN"] == "sk-or-test-key"
    assert options.model == "anthropic/claude-sonnet-5.5"


def test_a_missing_key_fails_at_configure_time_not_mid_run(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(secrets, "ENV_FILE", tmp_path / "missing.env")
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        llm.configure(Settings(auth="openrouter"))
    llm.configure(Settings(auth="subscription"))  # the subscription needs no key


def test_the_key_is_read_from_the_env_file(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OPENROUTER_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(secrets, "ENV_FILE", env_file)
    assert secrets.openrouter_api_key() == "from-file"
    monkeypatch.delenv("OPENROUTER_API_KEY")  # load_dotenv put it in os.environ; clean up
