"""The ONLY place LLM auth and model choice are configured.

Two ways to authenticate, chosen by `auth` in config/settings.yaml:

  subscription  your Claude login (development). No model is set, so the SDK's default is used.
  openrouter    an OpenRouter API key (scheduled runs). OpenRouter speaks the same protocol as
                Anthropic's API, so the Agent SDK is pointed at it with three environment
                variables, and each kind of agent gets its own model.

The key is read through secrets.py and goes only into the environment of the agent process
that the SDK starts. It is never logged and never put in a prompt.
"""

import logging
from typing import Any, Literal

import httpx

from sonar.config import Settings
from sonar.secrets import openrouter_api_key

log = logging.getLogger("sonar.llm")

Role = Literal["navigation", "summary"]

OPENROUTER_URL = "https://openrouter.ai/api"
OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"  # read-only: how much this key has spent

_settings = Settings()  # until main calls configure(): subscription, so tests never touch the network


def configure(settings: Settings) -> None:
    """Use these settings from now on. Fails at once (before any run starts) if the key is missing."""
    global _settings
    if settings.auth == "openrouter":
        openrouter_api_key()  # raises with a clear message if it is not set
    _settings = settings


async def openrouter_usage_usd() -> float | None:
    """Dollars the OpenRouter key has spent in its lifetime (OpenRouter's own bill), or None when not
    using OpenRouter or the lookup fails. Reading the cost must never fail a run. Read-only."""
    if _settings.auth != "openrouter":
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            reply = await client.get(OPENROUTER_KEY_URL, headers={"Authorization": f"Bearer {openrouter_api_key()}"})
        reply.raise_for_status()
        return float(reply.json()["data"]["usage"])
    except Exception as exc:  # the key never appears in the message: httpx errors carry the URL, not headers
        log.warning("step=cost outcome=usage_lookup_failed error=%s", type(exc).__name__)
        return None


def agent_llm_options(role: Role) -> dict[str, Any]:
    """Extra ClaudeAgentOptions for one kind of agent: its model, reasoning effort and auth environment.
    Empty for the subscription login."""
    s = _settings
    if s.auth == "subscription":
        return {}
    options: dict[str, Any] = {
        "model": s.navigation_model if role == "navigation" else s.summary_model,
        "env": {
            "ANTHROPIC_BASE_URL": OPENROUTER_URL,
            "ANTHROPIC_AUTH_TOKEN": openrouter_api_key(),
            "ANTHROPIC_API_KEY": "",  # must be empty, or the CLI may authenticate against Anthropic directly
        },
    }
    if role == "navigation" and s.navigation_effort:
        options["effort"] = s.navigation_effort
    return options
