"""Live check of the OpenRouter setup. Makes a few tiny paid calls. Run once after putting
OPENROUTER_API_KEY in .env:

    .venv\Scripts\python.exe scripts/check_openrouter.py

Checks, for each of the two roles in llm.py (navigation model, summary model):
  1. the model answers in plain text through our own agent loop (agent_runner.run_agent);
  2. the navigation model can call a tool (our `browser(code)` tool is a tool call, so this is the
     ability the browsing agent depends on), uses the tool's result, and ends with a text answer;
and prints the cost OpenRouter reports for each. It does not touch any website and does not
change the database.
"""

import asyncio
import json
import sys

from sonar import budget, llm
from sonar.agent_runner import run_agent
from sonar.config import load_settings
from sonar.llm import ToolCall

SYSTEM = "Follow the user's instruction exactly. Be brief."
ADD_TOOL = {
    "type": "function",
    "function": {
        "name": "add",
        "description": "Adds two whole numbers. Always use this tool for sums.",
        "parameters": {
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        },
    },
}


async def add(call: ToolCall) -> str:
    args = json.loads(call.arguments)
    return str(args["a"] + args["b"] + 1000)  # +1000: proves the tool ran


async def main() -> int:
    llm.configure(load_settings())
    failures = 0

    for role in ("summary", "navigation"):
        before = budget.current.reported_cost_usd
        answer = await run_agent(role, SYSTEM, "Reply with exactly the word: pong", None, add, 4, 120)  # type: ignore[arg-type]
        ok = "pong" in answer.lower()
        failures += not ok
        cost = budget.current.reported_cost_usd - before
        print(f"[{'ok' if ok else 'FAIL'}] {role} ({llm.model_for(role)}): text answer={answer.strip()[:60]!r} cost=${cost:.5f}")

    before = budget.current.reported_cost_usd
    answer = await run_agent(
        "navigation", SYSTEM, "Use the add tool for 2 + 3 and tell me only the number it returned.", [ADD_TOOL], add, 4, 120,
    )
    ok = "1005" in answer
    failures += not ok
    cost = budget.current.reported_cost_usd - before
    print(f"[{'ok' if ok else 'FAIL'}] navigation: tool call (expects 1005) answer={answer.strip()[:60]!r} cost=${cost:.5f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
