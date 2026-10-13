"""Compares the cost the Agent SDK reports with what OpenRouter actually charged, per model role.
Makes a few small paid calls. Needs OPENROUTER_API_KEY in .env:

    .venv\Scripts\python.exe scripts/check_cost.py

Why: `max_call_usd` (the SDK's per-call dollar limit) only protects us if the SDK's figure is close
to the real bill. The SDK computes it from its own price list, which was built for Claude models.
OpenRouter's key-spend counter can lag, so each reading waits until the number stops changing.
"""

import asyncio
import sys

from claude_agent_sdk import ResultMessage, query

from sonar import llm
from sonar.agent_runner import isolated_options
from sonar.config import load_settings

PROMPT = "Write two sentences about why the sky looks blue."


async def settled_usage(after: float | None = None) -> float:
    """The key's spend once it has stopped changing for ~6 seconds (OpenRouter updates it with a delay).
    With `after`, first waits (up to 2 minutes) until the spend has moved past that value."""
    last, steady = -1.0, 0
    for _ in range(60 if after is not None else 0):
        if (now := await llm.openrouter_usage_usd()) is not None and now > after:
            break
        await asyncio.sleep(2)
    while steady < 3:
        await asyncio.sleep(2)
        now = await llm.openrouter_usage_usd()
        if now is None:
            raise SystemExit("could not read the key's spend")
        steady = steady + 1 if now == last else 0
        last = now
    return last


async def one_call(role: str) -> tuple[float, float, int]:
    before = await settled_usage()
    sdk_cost, out_tokens = 0.0, 0
    async for message in query(prompt=PROMPT, options=isolated_options("Be brief.", 2, role)):  # type: ignore[arg-type]
        if isinstance(message, ResultMessage):
            sdk_cost = message.total_cost_usd or 0.0
            out_tokens = (message.usage or {}).get("output_tokens", 0)
    return sdk_cost, await settled_usage(after=before) - before, out_tokens


async def main() -> int:
    settings = load_settings()
    settings.auth = "openrouter"
    llm.configure(settings)
    for role in ("summary", "navigation"):
        sdk, real, tokens = await one_call(role)
        print(f"{role:10} SDK reported ${sdk:.6f}   OpenRouter charged ${real:.6f}   (output tokens: {tokens})")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
