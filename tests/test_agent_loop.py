"""The agent loop in agent_runner.py, with a scripted stand-in for llm.chat. No network, no browser."""

import asyncio
import json

import pytest

from sonar import agent_runner, budget, llm
from sonar.agent_runner import AgentError
from sonar.budget import BudgetExceeded, RunBudget
from sonar.llm import ChatReply, LLMError, ToolCall


@pytest.fixture(autouse=True)
def fresh_budget():
    budget.configure(RunBudget())
    yield
    budget.configure(RunBudget())  # never leak a limit into other tests


class Script:
    """Replaces llm.chat: hands out the scripted replies in order and records every request."""

    def __init__(self, monkeypatch, *replies: ChatReply | Exception) -> None:
        self.replies = list(replies)
        self.requests: list[tuple[str, list[dict], list | None]] = []
        monkeypatch.setattr(llm, "chat", self.chat)

    async def chat(self, role, messages, tools=None, client=None):
        self.requests.append((role, [dict(m) for m in messages], tools))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def text(content: str, cost: float = 0.0) -> ChatReply:
    return ChatReply(message={"role": "assistant", "content": content}, content=content, tool_calls=[], cost_usd=cost)


def tool_call(code: str = "print(1)", cost: float = 0.0, name: str = "browser", arguments: str | None = None, **extra) -> ChatReply:
    args = arguments if arguments is not None else json.dumps({"code": code})
    call = ToolCall(id="call_1", name=name, arguments=args)
    message = {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": name, "arguments": args}}], **extra}
    return ChatReply(message=message, content="", tool_calls=[call], cost_usd=cost)


@pytest.fixture()
def cli(monkeypatch):
    """Replaces the browser-use CLI: records the code it was given and returns a fixed page."""
    ran: list[str] = []

    async def fake_run_cli(port: int, code: str) -> str:
        ran.append(code)
        return "PAGE-OUTPUT"

    monkeypatch.setattr(agent_runner, "run_cli", fake_run_cli)
    return ran


def browse(max_turns: int = 5, timeout_s: int = 30) -> str:
    return asyncio.run(agent_runner.run_browser_agent("SYSTEM", "TASK", 9333, max_turns, timeout_s))


def test_a_tool_call_runs_the_code_and_the_result_goes_back_to_the_model(monkeypatch, cli) -> None:
    script = Script(monkeypatch, tool_call("print('hi')"), text("FINAL"))
    assert browse() == "FINAL"
    assert cli == ["print('hi')"]
    role, messages, tools = script.requests[1]
    assert role == "navigation" and [t["function"]["name"] for t in tools] == ["browser"]
    assert messages[-1] == {"role": "tool", "tool_call_id": "call_1", "content": "PAGE-OUTPUT"}


def test_the_first_request_holds_only_our_system_prompt_and_task(monkeypatch, cli) -> None:
    script = Script(monkeypatch, text("FINAL"))
    browse()
    _, messages, _ = script.requests[0]
    assert messages == [{"role": "system", "content": "SYSTEM"}, {"role": "user", "content": "TASK"}]


def test_the_models_message_is_sent_back_whole_with_its_reasoning(monkeypatch, cli) -> None:
    script = Script(monkeypatch, tool_call(reasoning="step by step", reasoning_details=[{"type": "reasoning.text"}]), text("FINAL"))
    browse()
    sent_back = script.requests[1][1][2]
    assert sent_back["role"] == "assistant" and sent_back["reasoning"] == "step by step"
    assert sent_back["reasoning_details"] == [{"type": "reasoning.text"}] and sent_back["tool_calls"]


def test_rejected_code_is_never_run_and_the_model_is_told(monkeypatch, cli) -> None:
    script = Script(monkeypatch, tool_call("import os"), text("FINAL"))
    assert browse() == "FINAL"
    assert cli == []
    assert script.requests[1][1][-1]["content"].startswith("REJECTED:")


@pytest.mark.parametrize("arguments", ["not json", "{}", "[]", '{"code": 5}'])
def test_bad_tool_arguments_become_an_error_result_not_a_crash(monkeypatch, cli, arguments) -> None:
    script = Script(monkeypatch, tool_call(arguments=arguments), text("FINAL"))
    assert browse() == "FINAL"
    assert cli == [] and script.requests[1][1][-1]["content"].startswith("ERROR:")


def test_an_unknown_tool_name_is_refused(monkeypatch, cli) -> None:
    script = Script(monkeypatch, tool_call(name="bash"), text("FINAL"))
    assert browse() == "FINAL"
    assert cli == [] and "no tool named 'bash'" in script.requests[1][1][-1]["content"]


def test_an_empty_answer_gets_one_nudge_then_the_answer_is_used(monkeypatch, cli) -> None:
    script = Script(monkeypatch, text(""), text("FINAL"))
    assert browse() == "FINAL"
    assert script.requests[1][1][-1] == {"role": "user", "content": agent_runner.NUDGE}


def test_two_empty_answers_fail_with_the_shape_of_the_last_message(monkeypatch, cli) -> None:
    Script(monkeypatch, text(""), text(""))
    with pytest.raises(AgentError, match="no final answer"):
        browse()


def test_running_out_of_turns_fails(monkeypatch, cli) -> None:
    Script(monkeypatch, tool_call(), tool_call(), tool_call())
    with pytest.raises(AgentError, match="within 3 turns"):
        browse(max_turns=3)


def test_a_model_call_that_fails_is_an_agent_error(monkeypatch, cli) -> None:
    Script(monkeypatch, LLMError("HTTP 500 after 3 tries"))
    with pytest.raises(AgentError, match="HTTP 500"):
        browse()


def test_the_whole_run_timeout_is_an_agent_error(monkeypatch, cli) -> None:
    async def slow(*args, **kwargs):
        await asyncio.sleep(5)

    monkeypatch.setattr(llm, "chat", slow)
    with pytest.raises(AgentError, match="exceeded"):
        browse(timeout_s=0.05)  # type: ignore[arg-type]


def test_every_model_reply_is_logged_with_its_tokens_time_and_cost(monkeypatch, cli, caplog) -> None:
    first = tool_call(cost=0.01).model_copy(update={"prompt_tokens": 900, "completion_tokens": 300, "reasoning_tokens": 250})
    Script(monkeypatch, first, text("FINAL", cost=0.02))
    with caplog.at_level("INFO", logger="sonar.agent"):
        browse()
    lines = [r.getMessage() for r in caplog.records if "step=model_reply" in r.getMessage()]
    assert len(lines) == 2
    assert "role=navigation turn=1" in lines[0] and "prompt_tokens=900 completion_tokens=300 reasoning_tokens=250" in lines[0]
    assert "tool_calls=1" in lines[0] and "turn=2" in lines[1] and "call_cost_usd=0.0300" in lines[1]


def test_a_text_agent_has_no_tools_and_returns_its_answer(monkeypatch) -> None:
    script = Script(monkeypatch, text("SUMMARY"))
    assert asyncio.run(agent_runner.run_text_agent("SYSTEM", "TASK")) == "SUMMARY"
    role, messages, tools = script.requests[0]
    assert role == "summary" and tools is None
    assert messages == [{"role": "system", "content": "SYSTEM"}, {"role": "user", "content": "TASK"}]


def test_a_text_agent_that_asks_for_a_tool_is_told_there_are_none(monkeypatch) -> None:
    script = Script(monkeypatch, tool_call(), text("SUMMARY"))
    assert asyncio.run(agent_runner.run_text_agent("SYSTEM", "TASK")) == "SUMMARY"
    assert script.requests[1][1][-1]["content"].startswith("ERROR: no tools")


def test_every_call_is_counted_and_its_cost_summed(monkeypatch, cli) -> None:
    Script(monkeypatch, tool_call(cost=0.01), text("FINAL", cost=0.02), text("SUMMARY", cost=0.005))
    browse()
    asyncio.run(agent_runner.run_text_agent("S", "T"))
    assert budget.current.agent_calls == 2 and budget.current.reported_cost_usd == pytest.approx(0.035)


def test_a_call_over_the_per_call_dollar_limit_stops_and_is_noted(monkeypatch, cli) -> None:
    budget.configure(RunBudget(max_call_usd=0.05))
    Script(monkeypatch, tool_call(cost=0.03), tool_call(cost=0.03))
    with pytest.raises(BudgetExceeded):
        browse()
    assert "call_usd" in budget.current.hits


def test_the_per_call_limit_counts_each_call_on_its_own(monkeypatch, cli) -> None:
    budget.configure(RunBudget(max_call_usd=0.05))
    Script(monkeypatch, text("A", cost=0.04), text("B", cost=0.04))
    assert asyncio.run(agent_runner.run_text_agent("S", "T")) == "A"
    assert asyncio.run(agent_runner.run_text_agent("S", "T")) == "B"  # 0.08 in total, 0.04 each: fine


def test_a_run_wide_limit_stops_the_call_before_any_model_request(monkeypatch, cli) -> None:
    budget.configure(RunBudget(max_agent_calls=0))
    script = Script(monkeypatch, text("never asked"))
    with pytest.raises(BudgetExceeded):
        browse()
    assert script.requests == []
