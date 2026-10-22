"""The ONLY place agents and the browser-use CLI are run.

Everything that needs an agent calls run_browser_agent() or run_text_agent() with a system prompt and
a task. An agent is a short loop of our own around llm.chat() (a plain OpenRouter request): the model
answers or calls a tool, we run the tool, we send the result back, until it answers or a limit is hit.

The browsing agent gets ONE tool, browser(code): we check the Python (navigation/code_check.py), then
pipe it to the browser-use CLI, which is attached to OUR dedicated browser over CDP. Nothing else is
available to any agent: each request holds only our system prompt, the task, and (for browsing) that
one tool. There is no filesystem, shell or settings folder in reach.
"""

import asyncio
import json
import logging
import os
import shutil
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from sonar import budget, llm
from sonar.budget import BudgetExceeded
from sonar.llm import ChatReply, LLMError, Role, ToolCall
from sonar.navigation.code_check import CodeRejected, check_code
from sonar.navigation.session import DATA_DIR

log = logging.getLogger("sonar.agent")

CLI_TIMEOUT_S = 60  # per browser(code) call
MAX_OUTPUT_CHARS = 20_000  # per browser(code) result; a list page of ~50 links fits
CLI_WORK_DIR = DATA_DIR / "browser-use"
NUDGE = "Reply now with your final answer as plain text, in the format the instructions ask for. Do not call a tool."

BROWSER_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "browser",
        "description": "Run read-only browser code (Python subset) in the browser-use CLI. Returns its printed output.",
        "parameters": {
            "type": "object",
            "properties": {"code": {"type": "string", "description": "The Python code to run."}},
            "required": ["code"],
        },
    },
}

ToolRunner = Callable[[ToolCall], Awaitable[str]]


class AgentError(RuntimeError):
    """The agent run failed or hit a limit."""


def cli_path() -> str:
    exe = "browser-use.exe" if os.name == "nt" else "browser-use"
    found = shutil.which("browser-use") or str(Path.home() / ".local" / "bin" / exe)
    if not Path(found).exists():
        raise AgentError("browser-use CLI not found. Install: uv tool install browser-use==0.13.11 --python 3.12")
    return found


def cli_env(port: int) -> dict[str, str]:
    """A clean environment for the CLI: only what Windows or Linux needs, plus our settings.
    Telemetry and update checks off; config and workspace kept under data/."""
    keep = ["SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
            "LOCALAPPDATA", "APPDATA", "PATH", "PATHEXT", "COMSPEC",
            "HOME", "LANG", "LC_ALL", "TMPDIR"]  # the last four are Linux
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env.update(
        BU_CDP_URL=f"http://127.0.0.1:{port}",  # attach to OUR browser, never the user's own Chrome
        BH_TELEMETRY="0", ANONYMIZED_TELEMETRY="false", BH_UPDATE_CHECK="0",
        BH_HOME=str(CLI_WORK_DIR / "bh-home"),
        BH_AGENT_WORKSPACE=str(CLI_WORK_DIR / "bh-workspace"),  # empty: no agent_helpers.py gets loaded
        BH_TAB_MARKER="0",
    )
    return env


async def run_cli(port: int, code: str) -> str:
    for sub in ("bh-home", "bh-workspace"):
        (CLI_WORK_DIR / sub).mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        cli_path(),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        env=cli_env(port), cwd=str(CLI_WORK_DIR),
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(code.encode("utf-8")), CLI_TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        return f"ERROR: timed out after {CLI_TIMEOUT_S}s"
    text = out.decode("utf-8", errors="replace")
    if len(text) > MAX_OUTPUT_CHARS:
        text = text[:MAX_OUTPUT_CHARS] + f"\n...[truncated, {len(text)} chars total]"
    return text


async def browser_tool(call: ToolCall, port: int) -> str:
    """Run one browser(code) call from the model. Problems come back as text the model can act on."""
    if call.name != "browser":
        return f"ERROR: there is no tool named '{call.name}'. The only tool is browser(code)."
    try:
        code = json.loads(call.arguments)["code"]
    except (ValueError, KeyError, TypeError):
        return 'ERROR: the tool arguments must be JSON like {"code": "..."}.'
    if not isinstance(code, str):
        return "ERROR: code must be a string."
    log.info("agent code:\n%s", code)
    try:
        check_code(code)
    except CodeRejected as exc:
        return f"REJECTED: {exc}"
    output = await run_cli(port, code)
    log.info("agent code output (first 600 characters):\n%s", output[:600])
    return output or "(no output)"


async def no_tools(call: ToolCall) -> str:
    return "ERROR: no tools are available. Answer in plain text."


async def run_browser_agent(
    system_prompt: str, task: str, port: int, max_turns: int = 15, timeout_s: int = 300
) -> str:
    """Run one browsing task and return the agent's final message. Raises AgentError on any failure."""

    async def run_tool(call: ToolCall) -> str:
        return await browser_tool(call, port)

    return await run_agent("navigation", system_prompt, task, [BROWSER_TOOL], run_tool, max_turns, timeout_s)


async def run_text_agent(system_prompt: str, task: str, max_turns: int = 2, timeout_s: int = 120) -> str:
    """Run an agent with NO tools (it can only answer in text) and return its answer.
    Raises AgentError on any failure."""
    return await run_agent("summary", system_prompt, task, None, no_tools, max_turns, timeout_s)


async def run_agent(
    role: Role, system_prompt: str, task: str, tools: list[dict[str, Any]] | None, run_tool: ToolRunner,
    max_turns: int, timeout_s: int,
) -> str:
    await budget.current.charge_agent_call()  # raises BudgetExceeded once a run-wide limit is hit
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}, {"role": "user", "content": task}]
    try:
        async with asyncio.timeout(timeout_s):
            return await converse(role, messages, tools, run_tool, max_turns)
    except TimeoutError as exc:
        raise AgentError(f"agent run exceeded {timeout_s}s") from exc


async def converse(
    role: Role, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None, run_tool: ToolRunner, max_turns: int
) -> str:
    """The loop: ask the model; run the tools it calls; repeat until it answers in text."""
    spent = 0.0  # what this one call has cost so far
    nudged = False
    last: ChatReply | None = None
    began = time.monotonic()
    for turn in range(1, max_turns + 1):
        asked = time.monotonic()
        try:
            last = await llm.chat(role, messages, tools)
        except LLMError as exc:
            raise AgentError(f"agent run failed on turn {turn}: {exc}") from exc
        spent = add_cost(spent, last)
        log.info(
            "step=model_reply role=%s turn=%d seconds=%.1f prompt_tokens=%d completion_tokens=%d reasoning_tokens=%d "
            "cost_usd=%.4f tool_calls=%d call_seconds=%.1f call_cost_usd=%.4f",
            role, turn, time.monotonic() - asked, last.prompt_tokens, last.completion_tokens, last.reasoning_tokens,
            last.cost_usd, len(last.tool_calls), time.monotonic() - began, spent,
        )
        messages.append({"role": "assistant", **last.message})
        if last.tool_calls:
            for call in last.tool_calls:
                messages.append({"role": "tool", "tool_call_id": call.id, "content": await run_tool(call)})
            continue
        if last.content.strip():
            return last.content
        if nudged:  # asked once already for a plain-text answer and still got none
            break
        nudged = True
        messages.append({"role": "user", "content": NUDGE})
    # Never page text here: the agent only sees what the tool returned, and we log just the message's shape.
    shape = sorted(last.message) if last else "none"
    raise AgentError(f"agent run failed: no final answer within {max_turns} turns (last message held: {shape})")


def add_cost(spent: float, reply: ChatReply) -> float:
    """Add a reply's cost to the run total and to this call's total; stop the call past max_call_usd."""
    budget.current.reported_cost_usd += reply.cost_usd
    spent += reply.cost_usd
    limit = budget.current.max_call_usd
    if limit is not None and spent > limit:
        msg = f"one agent call passed the ${limit} per-call limit"
        budget.current.note_hit("call_usd", msg)
        raise BudgetExceeded(msg)
    return spent
