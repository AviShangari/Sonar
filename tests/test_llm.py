"""llm.py is the only place auth and models are chosen. No test here touches the network."""

import asyncio

import httpx
import pytest

from sonar import llm, secrets
from sonar.config import Settings

KEY = "sk-or-test-key"
TOOL = {"type": "function", "function": {"name": "browser", "parameters": {"type": "object"}}}


@pytest.fixture(autouse=True)
def restore_llm_settings(monkeypatch):
    before, configured = llm._settings, llm._configured
    monkeypatch.setattr(llm, "RETRY_PAUSE_S", 0)  # retries do not really wait
    yield
    llm._settings, llm._configured = before, configured


@pytest.fixture()
def key(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    monkeypatch.setattr(secrets, "ENV_FILE", tmp_path / "missing.env")  # never read the real .env in tests


def answer(content: str = "hi", cost: float | None = 0.002, **message) -> dict:
    data = {"choices": [{"message": {"role": "assistant", "content": content, **message}}]}
    if cost is not None:
        data["usage"] = {"cost": cost}
    return data


def run_chat(handler, role="navigation", tools=None):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await llm.chat(role, [{"role": "user", "content": "x"}], tools, client=client)

    return asyncio.run(go())


def test_each_role_gets_its_own_model_and_only_navigation_an_effort(key) -> None:
    llm.configure(Settings())
    nav, summary = llm.request_body("navigation", [], None), llm.request_body("summary", [], None)
    assert (nav["model"], nav["reasoning"]) == ("openai/gpt-6.1-sol", {"effort": "medium"})
    assert summary["model"] == "anthropic/claude-sonnet-5.5" and "reasoning" not in summary


def test_models_and_effort_come_from_settings(key) -> None:
    llm.configure(Settings(navigation_model="x/nav", navigation_effort="high", summary_model="x/sum"))
    assert llm.request_body("navigation", [], None)["model"] == "x/nav"
    assert llm.request_body("navigation", [], None)["reasoning"] == {"effort": "high"}
    assert llm.request_body("summary", [], None)["model"] == "x/sum"
    llm.configure(Settings(navigation_effort=None))
    assert "reasoning" not in llm.request_body("navigation", [], None)


def test_the_request_holds_only_what_we_pass(key) -> None:
    """The 'context contains only what we pass' rule: messages and tools are the only content fields."""
    llm.configure(Settings())
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "task"}]
    assert set(llm.request_body("summary", messages, None)) == {"model", "messages", "usage"}
    body = llm.request_body("navigation", messages, [TOOL])
    assert set(body) == {"model", "messages", "usage", "tools", "reasoning"} and body["messages"] == messages


def test_chat_sends_the_key_as_a_bearer_token_and_reads_text_tools_and_cost(key) -> None:
    llm.configure(Settings())
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        call = {"id": "c1", "type": "function", "function": {"name": "browser", "arguments": '{"code": "print(1)"}'}}
        data = answer("", cost=0.0123, tool_calls=[call], refusal=None, reasoning="thinking")
        data["usage"].update(prompt_tokens=11, completion_tokens=7, completion_tokens_details={"reasoning_tokens": 5})
        return httpx.Response(200, json=data)

    reply = run_chat(handler, tools=[TOOL])
    assert str(seen[0].url) == "https://openrouter.ai/api/v1/chat/completions"
    assert seen[0].headers["Authorization"] == f"Bearer {KEY}"
    assert reply.cost_usd == 0.0123 and reply.content == ""
    assert (reply.prompt_tokens, reply.completion_tokens, reply.reasoning_tokens) == (11, 7, 5)
    assert [(c.id, c.name, c.arguments) for c in reply.tool_calls] == [("c1", "browser", '{"code": "print(1)"}')]
    # what is sent back next turn keeps the reasoning and the tool calls, and drops fields we do not echo
    assert set(reply.message) == {"role", "content", "tool_calls", "reasoning"}


def test_a_reply_without_a_cost_counts_as_zero(key) -> None:
    llm.configure(Settings())
    assert run_chat(lambda r: httpx.Response(200, json=answer(cost=None))).cost_usd == 0.0


def test_429_and_5xx_are_retried_then_succeed(key) -> None:
    llm.configure(Settings())
    codes = iter([429, 503])

    def handler(request: httpx.Request) -> httpx.Response:
        code = next(codes, 200)
        return httpx.Response(code, json=answer("ok") if code == 200 else {"error": "busy"})

    assert run_chat(handler).content == "ok"


def test_retries_give_up_and_the_error_never_holds_the_key(key) -> None:
    llm.configure(Settings())
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(500, json={"error": "down"})

    with pytest.raises(llm.LLMError) as exc:
        run_chat(handler)
    assert len(calls) == llm.RETRIES + 1 and "HTTP 500" in str(exc.value) and KEY not in str(exc.value)


def test_a_network_failure_is_an_llm_error_without_the_key(key) -> None:
    llm.configure(Settings())

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(llm.LLMError) as exc:
        run_chat(handler)
    assert "ConnectError" in str(exc.value) and KEY not in str(exc.value)


def test_a_client_error_is_not_retried(key) -> None:
    llm.configure(Settings())
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(401, text="bad key")

    with pytest.raises(llm.LLMError, match="HTTP 401"):
        run_chat(handler)
    assert len(calls) == 1


def test_an_error_inside_a_200_reply_is_an_llm_error(key) -> None:
    llm.configure(Settings())
    with pytest.raises(llm.LLMError, match="provider fell over"):
        run_chat(lambda r: httpx.Response(200, json={"error": {"message": "provider fell over"}}))


def test_a_missing_key_fails_at_configure_time_not_mid_run(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(secrets, "ENV_FILE", tmp_path / "missing.env")
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        llm.configure(Settings())


def test_the_usage_lookup_is_off_until_configured(monkeypatch) -> None:
    monkeypatch.setattr(llm, "_configured", False)
    assert asyncio.run(llm.openrouter_usage_usd()) is None  # no network call, no key needed


def test_the_key_is_read_from_the_env_file(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OPENROUTER_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(secrets, "ENV_FILE", env_file)
    assert secrets.openrouter_api_key() == "from-file"
    monkeypatch.delenv("OPENROUTER_API_KEY")  # load_dotenv put it in os.environ; clean up
