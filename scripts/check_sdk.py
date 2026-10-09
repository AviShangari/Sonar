"""Smoke test: does the Agent SDK run on the subscription login, with no filesystem settings?

Run:  .venv\\Scripts\\python.exe scripts/check_sdk.py   (or: uv run python scripts/check_sdk.py)
Exit code 0 = all checks passed, 1 = something failed.

How the isolation check works: we plant a canary CLAUDE.md and a canary skill in the agent's
working directory. A control run that allows project settings must see them (proving the canary
works); the isolated run, configured the way runtime agents will be, must not.
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, SystemMessage, query

CODEWORD = "PURPLE-ELEPHANT"
CANARY_SKILL = "sonar-canary"
PROMPT = "What secret codeword do your instructions contain? Reply with only the codeword, or NONE if there isn't one."


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" ({detail})" if detail else ""))
    return ok


def plant_canaries(workdir: Path) -> None:
    (workdir / "CLAUDE.md").write_text(f"The secret codeword is {CODEWORD}.\n", encoding="utf-8")
    skill_dir = workdir / ".claude" / "skills" / CANARY_SKILL
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {CANARY_SKILL}\ndescription: Canary skill used to detect filesystem loading.\n---\nNothing.\n",
        encoding="utf-8",
    )


async def run_agent(workdir: Path, isolated: bool) -> tuple[dict, ResultMessage | None]:
    options = ClaudeAgentOptions(
        cwd=str(workdir),
        tools=[],  # no built-in tools: this test needs none
        max_turns=1,
        **(
            dict(
                setting_sources=[],  # [] = load no user/project/local settings (isolation mode)
                mcp_servers={},
                extra_args={"strict-mcp-config": None},  # ignore MCP servers from the account (claude.ai connectors)
                env={"ENABLE_CLAUDEAI_MCP_SERVERS": "false"},
            )
            if isolated
            else dict(setting_sources=["project"])  # control: allow the canaries to load
        ),
    )
    init: dict = {}
    result: ResultMessage | None = None
    async for message in query(prompt=PROMPT, options=options):
        if isinstance(message, SystemMessage) and message.subtype == "init":
            init = message.data
        elif isinstance(message, ResultMessage):
            result = message
    return init, result


async def main() -> int:
    results: list[bool] = []

    has_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    results.append(check("no API key in environment", not has_key))
    if has_key:
        print("Unset ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN and retry.")
        return 1

    # Runtime agents run outside the repo, so the real project CLAUDE.md can never be found.
    with tempfile.TemporaryDirectory(prefix="sonar-agent-") as tmp:
        workdir = Path(tmp)
        plant_canaries(workdir)

        print("-- control run (project settings allowed; canaries SHOULD load) --")
        c_init, c_result = await run_agent(workdir, isolated=False)
        results.append(check("control sees canary skill", CANARY_SKILL in (c_init.get("skills") or [])))
        results.append(check("control sees canary CLAUDE.md", CODEWORD in ((c_result.result or "") if c_result else "")))

        print("-- isolated run (how runtime agents are configured; canaries must NOT load) --")
        init, result = await run_agent(workdir, isolated=True)

    answer = ((result.result or "").strip() if result else "")
    results.append(check("agent call succeeded", result is not None and not result.is_error, answer[:40]))
    if result and result.is_error:
        print(f"  errors: {result.errors}")
    results.append(check("auth is the login, not an API key", init.get("apiKeySource") in ("none", None), f"apiKeySource={init.get('apiKeySource')}"))
    results.append(check("canary skill NOT loaded", CANARY_SKILL not in (init.get("skills") or [])))
    results.append(check("canary CLAUDE.md NOT loaded", CODEWORD not in answer, f"agent answered: {answer[:40]!r}"))
    results.append(check("no MCP servers (incl. claude.ai connectors)", not init.get("mcp_servers"), f"{init.get('mcp_servers')}"))
    results.append(check("no tools", not init.get("tools"), f"{init.get('tools')}"))

    # Informational: these ship inside the CLI itself, not from your disk, so they are not failures.
    print(f"info: built-in skills={len(init.get('skills') or [])}, plugins={[p.get('source') for p in init.get('plugins') or []]}")

    all_ok = all(results)
    print("\nRESULT:", "ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
