"""Live check of the OpenRouter setup. Makes a few tiny paid calls. Run once after putting
OPENROUTER_API_KEY in .env:

    .venv\Scripts\python.exe scripts/check_openrouter.py

Checks, for each of the two roles in llm.py (navigation model, summary model):
  1. the agent answers in plain text and the reply names the model that actually answered;
  2. the navigation agent can call a tool (our `browser(code)` tool is a tool call, so this is the
     ability the browsing agent depends on) and uses the tool's result.
It does not touch any website and does not change the database.
"""

import asyncio
import sys

from claude_agent_sdk import AssistantMessage, ResultMessage, create_sdk_mcp_server, query, tool

from sonar import llm
from sonar.agent_runner import isolated_options
from sonar.config import load_settings


async def ask(role: str, prompt: str, **extra) -> tuple[str, str, float | None]:
    """(answer, model that replied, reported cost) for one tiny agent run with the real options."""
    options = isolated_options("Follow the user's instruction exactly. Be brief.", 4, role, **extra)  # type: ignore[arg-type]
    answer, model, cost = "", "?", None
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            model = message.model
        elif isinstance(message, ResultMessage):
            answer, cost = message.result or "", message.total_cost_usd
    return answer.strip(), model, cost


async def main() -> int:
    settings = load_settings()
    settings.auth = "openrouter"  # this check is about the key, whatever settings.yaml says
    llm.configure(settings)
    failures = 0

    for role in ("summary", "navigation"):
        answer, model, cost = await ask(role, "Reply with exactly the word: pong")
        ok = "pong" in answer.lower()
        failures += not ok
        print(f"[{'ok' if ok else 'FAIL'}] {role}: text answer={answer[:60]!r} model={model} cost={cost}")

    @tool("add", "Adds two whole numbers. Always use this tool for sums.", {"a": int, "b": int})
    async def add(args: dict) -> dict:
        return {"content": [{"type": "text", "text": str(args["a"] + args["b"] + 1000)}]}  # +1000: proves the tool ran

    answer, model, cost = await ask(
        "navigation", "Use the add tool for 2 + 3 and tell me only the number it returned.",
        mcp_servers={"calc": create_sdk_mcp_server("calc", tools=[add])}, allowed_tools=["mcp__calc__add"],
    )
    ok = "1005" in answer
    failures += not ok
    print(f"[{'ok' if ok else 'FAIL'}] navigation: tool call (expects 1005) answer={answer[:60]!r} model={model} cost={cost}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
