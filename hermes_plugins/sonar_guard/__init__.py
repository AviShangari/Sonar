"""Hermes guard plugin: the pre_tool_call policy for Sonar's browsing agent.

Hermes loads this folder as a plugin. It allows ONE tool, `browser_exec`, and every piece of code the agent sends
through it must pass `sonar.navigation.code_check.check_code`, loaded by path from SONAR_CODE_CHECK_PATH (the plugin
runs inside Hermes's own Python, so it cannot import `sonar`). Everything else is blocked: terminal, typing, the
console, deleting notes.

With SONAR_LEARN_MODE=1 the agent may also use Hermes's note tools (`memory`, `skill_view`, `skills_list`,
`skill_manage`). `skill_manage` is limited to create and patch (no delete, no supporting files).

Any error inside this hook BLOCKS the call: Hermes lets a call through when a hook raises, so the hook catches its
own errors. If this plugin fails to LOAD, Hermes logs it and allows everything, so Sonar's start-up check must prove
the plugin is in force before every run. Every `browser_exec` and note call is appended to SONAR_EXEC_LOG as one JSON
line (a `browser_exec` call with no line there means the guard was not in force)."""

from __future__ import annotations

import importlib.util
import json
import os
import time

BROWSER_TOOL = "browser_exec"
LEARN_TOOLS = {"memory", "skill_manage", "skill_view", "skills_list"}
SKILL_ACTIONS_OK = {"create", "patch"}


def _block(message: str) -> dict:
    return {"action": "block", "message": message}


def _learn_mode() -> bool:
    return os.environ.get("SONAR_LEARN_MODE") == "1"


def _load_code_check():
    path = os.environ["SONAR_CODE_CHECK_PATH"]
    spec = importlib.util.spec_from_file_location("sonar_code_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _log(entry: dict) -> None:
    try:
        path = os.environ.get("SONAR_EXEC_LOG")
        if path:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"t": round(time.time(), 1), **entry}) + "\n")
    except Exception:  # noqa: BLE001  (logging must never change the verdict)
        pass


def _check(tool_name: str, args: dict) -> dict | None:
    if tool_name == BROWSER_TOOL:
        if args.get("local") or args.get("session"):
            return _block("sonar_guard: REJECTED: browser_exec 'local' and 'session' options are not allowed")
        code = str(args.get("code", ""))
        cc = _load_code_check()
        try:
            cc.check_code(code)
        except cc.CodeRejected as exc:
            _log({"tool": tool_name, "verdict": "REJECTED", "reason": str(exc), "code": code[:1500]})
            return _block(f"sonar_guard: REJECTED: {exc}")
        _log({"tool": tool_name, "verdict": "allowed", "code": code[:1500]})
        return None
    if tool_name in LEARN_TOOLS and _learn_mode():
        if tool_name == "skill_manage":
            ops = args.get("operations")
            ops = ops if isinstance(ops, list) else [args]  # the old flat shape is still accepted by Hermes
            actions = {str((o or {}).get("action", "")) for o in ops}
            if not actions or not actions <= SKILL_ACTIONS_OK:
                _log({"tool": tool_name, "verdict": "REJECTED", "reason": f"actions {sorted(actions)}"})
                return _block(f"sonar_guard: skill_manage may only create or patch (got {sorted(actions)})")
        _log({"tool": tool_name, "verdict": "allowed", "args": json.dumps(args, ensure_ascii=False)[:6000]})
        return None
    return _block(f"sonar_guard: {tool_name} is not on the allowlist")


def _on_pre_tool_call(tool_name: str = "", args=None, **_):
    try:
        verdict = _check(tool_name, args if isinstance(args, dict) else {})
        if verdict is not None and tool_name != BROWSER_TOOL:
            _log({"tool": tool_name, "verdict": "BLOCKED", "reason": verdict.get("message", "")[:120]})
        return verdict
    except Exception as exc:  # fail closed: Hermes lets the call through if a hook raises
        _log({"tool": tool_name, "verdict": "BLOCKED_ON_ERROR", "reason": type(exc).__name__})
        return _block(f"sonar_guard: blocked because the guard itself failed ({type(exc).__name__})")


def _on_post_tool_call(tool_name: str = "", args=None, result=None, **_):
    """Observer only (cannot allow or block anything): keep the LAST complete BEGIN_ROWS..END_ROWS block that a
    browser_exec call printed, in SONAR_CAPTURE_FILE, so the run can take the rows by reference instead of having
    the model retype them. Rows stay untrusted; the caller validates every line."""
    try:
        path = os.environ.get("SONAR_CAPTURE_FILE")
        if not path or tool_name != BROWSER_TOOL:
            return
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        try:
            data = json.loads(text)
            text = data.get("output", text) if isinstance(data, dict) else text
        except Exception:  # noqa: BLE001
            pass
        start, end = text.rfind("BEGIN_ROWS"), text.rfind("END_ROWS")
        if start != -1 and end > start:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text[start + len("BEGIN_ROWS"):end].strip() + "\n")
    except Exception:  # noqa: BLE001  (observing must never break a call)
        pass


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    ctx.register_hook("post_tool_call", _on_post_tool_call)
