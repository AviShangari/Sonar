"""The ONLY place LLM auth and model choice are configured.

Every agent call goes straight to OpenRouter's chat-completions API with our own HTTP request
(no agent framework in between). The key comes from secrets.py and goes only into the request's
Authorization header. It is never logged and never put in a prompt. Each kind of agent has its
own model, pinned in config/settings.yaml:

  navigation  the browsing agent (model and reasoning effort)
  summary     the summary writer (model only)
"""

import asyncio
import logging
from typing import Any, Literal

import httpx
from pydantic import BaseModel

from sonar.config import Settings
from sonar.secrets import openrouter_api_key

log = logging.getLogger("sonar.llm")

Role = Literal["navigation", "summary"]

CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"  # read-only: how much this key has spent
TIMEOUT_S = 120  # one model reply
RETRIES = 2  # extra tries after a timeout, a 429 or a 5xx
RETRY_PAUSE_S = 2.0  # multiplied by the try number
# What we send back of the model's own message on the next turn. Reasoning models need their reasoning
# fields returned with tool results; anything else the API adds (refusal, annotations...) is left out.
ECHOED_MESSAGE_KEYS = ("role", "content", "tool_calls", "reasoning", "reasoning_details")

_settings = Settings()  # until main calls configure(): the defaults
_configured = False  # False until configure(): tests never touch the network


class LLMError(RuntimeError):
    """A model call failed (network, HTTP error, or a reply we cannot read). Never carries the key."""


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: str  # the model's JSON text, parsed by the caller (it may be malformed)


class ChatReply(BaseModel):
    message: dict[str, Any]  # the model's message, ready to append to the conversation
    content: str  # the text answer ("" when the model only called tools)
    tool_calls: list[ToolCall]
    cost_usd: float  # what OpenRouter says this reply cost
    prompt_tokens: int = 0  # for the log: where the time and money go
    completion_tokens: int = 0
    reasoning_tokens: int = 0  # part of completion_tokens, when the provider reports it


def configure(settings: Settings) -> None:
    """Use these settings from now on. Fails at once (before any run starts) if the key is missing."""
    global _settings, _configured
    openrouter_api_key()  # raises with a clear message if it is not set
    _settings = settings
    _configured = True


def model_for(role: Role) -> str:
    return _settings.navigation_model if role == "navigation" else _settings.summary_model


def request_body(role: Role, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> dict[str, Any]:
    body: dict[str, Any] = {"model": model_for(role), "messages": messages, "usage": {"include": True}}
    if tools:
        body["tools"] = tools
    if role == "navigation" and _settings.navigation_effort:
        body["reasoning"] = {"effort": _settings.navigation_effort}
    return body


def parse_reply(data: dict[str, Any]) -> ChatReply:
    if data.get("error") or not data.get("choices"):
        detail = data.get("error")
        message = detail.get("message") if isinstance(detail, dict) else detail
        raise LLMError(f"the model returned no answer: {str(message or 'empty reply')[:200]}")
    raw = data["choices"][0].get("message") or {}
    tool_calls = [
        ToolCall(id=str(c.get("id") or ""), name=str((c.get("function") or {}).get("name") or ""),
                 arguments=str((c.get("function") or {}).get("arguments") or ""))
        for c in raw.get("tool_calls") or []
    ]
    usage = data.get("usage") or {}
    return ChatReply(
        message={k: raw[k] for k in ECHOED_MESSAGE_KEYS if raw.get(k) is not None},
        content=str(raw.get("content") or ""),
        tool_calls=tool_calls,
        cost_usd=float(usage.get("cost") or 0.0),
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
        reasoning_tokens=int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0),
    )


async def chat(
    role: Role, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
    client: httpx.AsyncClient | None = None,
) -> ChatReply:
    """One model call. Retries timeouts, 429s and 5xx; raises LLMError when it still fails."""
    headers = {"Authorization": f"Bearer {openrouter_api_key()}", "Content-Type": "application/json"}
    body = request_body(role, messages, tools)
    own = client is None
    http = client or httpx.AsyncClient(timeout=TIMEOUT_S)
    try:
        for attempt in range(RETRIES + 1):
            try:
                response = await http.post(CHAT_URL, headers=headers, json=body)
                failure = f"HTTP {response.status_code}" if response.status_code == 429 or response.status_code >= 500 else None
            except httpx.TimeoutException:
                failure = "timed out"
            except httpx.HTTPError as exc:  # httpx errors carry the URL, not the headers, so the key cannot leak here
                failure = f"network error ({type(exc).__name__})"
            if failure is None:
                break
            log.warning("step=llm outcome=retry role=%s attempt=%d error=%s", role, attempt + 1, failure)
            if attempt == RETRIES:
                raise LLMError(f"{failure} after {RETRIES + 1} tries")
            await asyncio.sleep(RETRY_PAUSE_S * (attempt + 1))
    finally:
        if own:
            await http.aclose()
    if response.status_code >= 400:
        raise LLMError(f"HTTP {response.status_code}: {response.text[:200]}")
    try:
        return parse_reply(response.json())
    except ValueError as exc:
        raise LLMError("the model's reply was not JSON") from exc


async def openrouter_usage_usd() -> float | None:
    """Dollars the OpenRouter key has spent in its lifetime (OpenRouter's own bill), or None before
    configure() or when the lookup fails. Reading the cost must never fail a run. Read-only."""
    if not _configured:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            reply = await client.get(OPENROUTER_KEY_URL, headers={"Authorization": f"Bearer {openrouter_api_key()}"})
        reply.raise_for_status()
        return float(reply.json()["data"]["usage"])
    except Exception as exc:  # the key never appears in the message: httpx errors carry the URL, not headers
        log.warning("step=cost outcome=usage_lookup_failed error=%s", type(exc).__name__)
        return None
