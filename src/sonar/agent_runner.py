"""The ONLY place the Claude Agent SDK and the browser-use CLI are invoked.

Everything that needs an agent calls run_browser_agent() with a system prompt and a task.
The agent gets ONE tool, browser(code): we check the Python (navigation/code_check.py), then
pipe it to the browser-use CLI, which is attached to OUR dedicated browser over CDP.
"""

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    ToolUseBlock,
    create_sdk_mcp_server,
    query,
    tool,
)

from sonar.navigation.code_check import CodeRejected, check_code
from sonar.navigation.session import DATA_DIR

log = logging.getLogger("sonar.agent")

CLI_TIMEOUT_S = 60  # per browser(code) call
MAX_OUTPUT_CHARS = 20_000  # per browser(code) result; a list page of ~50 links fits
CLI_WORK_DIR = DATA_DIR / "browser-use"


class AgentError(RuntimeError):
    """The agent run failed or hit a limit."""


def cli_path() -> str:
    found = shutil.which("browser-use") or str(Path.home() / ".local" / "bin" / "browser-use.exe")
    if not Path(found).exists():
        raise AgentError("browser-use CLI not found. Install: uv tool install browser-use==0.13.11 --python 3.12")
    return found


def cli_env(port: int) -> dict[str, str]:
    """A clean environment for the CLI: only what Windows needs, plus our settings.
    Telemetry and update checks off; config and workspace kept under data/."""
    keep = ["SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
            "LOCALAPPDATA", "APPDATA", "PATH", "PATHEXT", "COMSPEC"]
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


async def run_browser_agent(
    system_prompt: str, task: str, port: int, max_turns: int = 15, timeout_s: int = 300
) -> str:
    """Run one agent task and return its final message. Raises AgentError on any failure."""

    @tool("browser", "Run read-only browser code (Python subset) in the browser-use CLI. Returns its printed output.", {"code": str})
    async def browser_tool(args: dict) -> dict:
        code = args["code"]
        try:
            check_code(code)
        except CodeRejected as exc:
            return {"content": [{"type": "text", "text": f"REJECTED: {exc}"}], "is_error": True}
        return {"content": [{"type": "text", "text": await run_cli(port, code)}]}

    options = ClaudeAgentOptions(
        cwd=tempfile.mkdtemp(prefix="sonar-agent-"),  # outside the repo
        setting_sources=[],  # load no CLAUDE.md, skills or settings
        tools=[],  # no built-in tools: no Bash, no file access
        mcp_servers={"browser": create_sdk_mcp_server("browser", tools=[browser_tool])},
        allowed_tools=["mcp__browser__browser"],
        extra_args={"strict-mcp-config": None},
        env={"ENABLE_CLAUDEAI_MCP_SERVERS": "false"},
        system_prompt=system_prompt,
        max_turns=max_turns,
    )
    result: ResultMessage | None = None
    try:
        async with asyncio.timeout(timeout_s):
            async for message in query(prompt=task, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, ToolUseBlock):
                            log.info("agent code:\n%s", block.input.get("code", ""))
                elif isinstance(message, ResultMessage):
                    result = message
    except TimeoutError as exc:
        raise AgentError(f"agent run exceeded {timeout_s}s") from exc
    if result is None or result.is_error or not result.result:
        raise AgentError(f"agent run failed: {getattr(result, 'subtype', 'no result')}")
    return result.result
