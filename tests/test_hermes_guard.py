import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "hermes_plugins" / "sonar_guard" / "__init__.py"
CODE_CHECK = ROOT / "src" / "sonar" / "navigation" / "code_check.py"

GOOD_CODE = "print(page_info())"
BAD_CODE = "import os"


@pytest.fixture
def guard(monkeypatch, tmp_path):
    monkeypatch.setenv("SONAR_CODE_CHECK_PATH", str(CODE_CHECK))
    monkeypatch.setenv("SONAR_EXEC_LOG", str(tmp_path / "exec.jsonl"))
    monkeypatch.delenv("SONAR_LEARN_MODE", raising=False)
    monkeypatch.delenv("SONAR_CAPTURE_FILE", raising=False)
    spec = importlib.util.spec_from_file_location("sonar_guard_under_test", PLUGIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def blocked(verdict) -> bool:
    return isinstance(verdict, dict) and verdict.get("action") == "block"


def log_lines(tmp_path) -> list[dict]:
    path = tmp_path / "exec.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_browser_exec_with_checked_code_is_allowed(guard, tmp_path):
    assert guard._on_pre_tool_call("browser_exec", {"code": GOOD_CODE}) is None
    assert log_lines(tmp_path)[-1]["verdict"] == "allowed"


def test_browser_exec_with_rejected_code_is_blocked_and_logged(guard, tmp_path):
    assert blocked(guard._on_pre_tool_call("browser_exec", {"code": BAD_CODE}))
    assert log_lines(tmp_path)[-1]["verdict"] == "REJECTED"


@pytest.mark.parametrize("option", ["local", "session"])
def test_browser_exec_options_are_blocked(guard, option):
    assert blocked(guard._on_pre_tool_call("browser_exec", {"code": GOOD_CODE, option: True}))


@pytest.mark.parametrize(
    "tool",
    ["terminal", "process_manage", "browser_type", "browser_press", "browser_console", "browser_navigate",
     "browser_snapshot", "execute_code", "write_file", "web_search", ""],
)
def test_every_other_tool_is_blocked(guard, tool):
    assert blocked(guard._on_pre_tool_call(tool, {}))


@pytest.mark.parametrize("tool", ["memory", "skill_view", "skills_list", "skill_manage"])
def test_note_tools_are_blocked_unless_learn_mode(guard, tool):
    assert blocked(guard._on_pre_tool_call(tool, {"action": "create"}))


def test_learn_mode_allows_note_tools_but_only_create_and_patch(guard, monkeypatch):
    monkeypatch.setenv("SONAR_LEARN_MODE", "1")
    assert guard._on_pre_tool_call("memory", {}) is None
    assert guard._on_pre_tool_call("skill_view", {"name": "x"}) is None
    assert guard._on_pre_tool_call("skills_list", {}) is None
    assert guard._on_pre_tool_call("skill_manage", {"action": "create"}) is None
    assert guard._on_pre_tool_call("skill_manage", {"operations": [{"action": "patch"}, {"action": "create"}]}) is None
    assert blocked(guard._on_pre_tool_call("skill_manage", {"action": "delete"}))
    assert blocked(guard._on_pre_tool_call("skill_manage", {"operations": [{"action": "patch"}, {"action": "delete"}]}))
    assert blocked(guard._on_pre_tool_call("skill_manage", {"action": "write_file"}))
    assert blocked(guard._on_pre_tool_call("skill_manage", {}))


def test_learn_mode_does_not_open_other_tools(guard, monkeypatch):
    monkeypatch.setenv("SONAR_LEARN_MODE", "1")
    assert blocked(guard._on_pre_tool_call("terminal", {}))


def test_a_crash_inside_the_hook_blocks(guard, monkeypatch):
    def boom():
        raise RuntimeError("code_check missing")

    monkeypatch.setattr(guard, "_load_code_check", boom)
    assert blocked(guard._on_pre_tool_call("browser_exec", {"code": GOOD_CODE}))


def test_missing_code_check_path_blocks(guard, monkeypatch, tmp_path):
    monkeypatch.delenv("SONAR_CODE_CHECK_PATH")
    assert blocked(guard._on_pre_tool_call("browser_exec", {"code": GOOD_CODE}))
    assert log_lines(tmp_path)[-1]["verdict"] == "BLOCKED_ON_ERROR"


def test_non_dict_args_do_not_get_through(guard):
    assert blocked(guard._on_pre_tool_call("terminal", "ls"))
    assert blocked(guard._on_pre_tool_call("terminal", None))


def test_no_switch_can_disable_the_guard(guard, monkeypatch):
    # The spike had env switches that broke the plugin on purpose; production must not.
    source = PLUGIN.read_text(encoding="utf-8")
    assert "SONAR_BREAK_IMPORT" not in source
    assert "SONAR_HOOK_CRASH" not in source
    assert "SONAR_EXEC_MODE" not in source


def test_register_hooks_both_events(guard):
    seen = {}

    class Ctx:
        def register_hook(self, name, fn):
            seen[name] = fn

    guard.register(Ctx())
    assert set(seen) == {"pre_tool_call", "post_tool_call"}


def test_post_hook_keeps_the_last_row_block(guard, monkeypatch, tmp_path):
    capture = tmp_path / "rows.txt"
    monkeypatch.setenv("SONAR_CAPTURE_FILE", str(capture))
    first = json.dumps({"output": "BEGIN_ROWS\nROW|a|https://x.test/1|2026-10-01\nEND_ROWS"})
    second = "noise BEGIN_ROWS\nROW|b|https://x.test/2|null\nEND_ROWS more"
    guard._on_post_tool_call("browser_exec", {}, first)
    guard._on_post_tool_call("browser_exec", {}, second)
    assert capture.read_text(encoding="utf-8") == "ROW|b|https://x.test/2|null\n"


def test_post_hook_ignores_other_tools_and_incomplete_blocks(guard, monkeypatch, tmp_path):
    capture = tmp_path / "rows.txt"
    monkeypatch.setenv("SONAR_CAPTURE_FILE", str(capture))
    guard._on_post_tool_call("terminal", {}, "BEGIN_ROWS\nROW|a|b|c\nEND_ROWS")
    guard._on_post_tool_call("browser_exec", {}, "BEGIN_ROWS\nROW|a|b|c")
    assert not capture.exists()
