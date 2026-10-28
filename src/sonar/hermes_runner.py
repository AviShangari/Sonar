"""The ONLY place the Hermes Agent is started and stopped.

Hermes runs as a local-only background server (`hermes gateway run`) that Sonar talks to over HTTP, one
server per site. Sonar sets everything up and proves the safeguards before the first task:

1. Stop every old Hermes process. A running server keeps the config, plugins and environment it started
   with, so it could silently run without our guard.
2. Copy the guard plugin (`hermes_plugins/sonar_guard`) and a fresh `config.yaml` into Hermes's home folder.
3. Start with a WHITELISTED environment: system paths, the OpenRouter key and our own settings. Site
   passwords and the SMTP password never reach Hermes or the code its agent runs.
4. Preflight: in a fresh process, ask the guard about probe calls. A plugin that fails to load makes Hermes
   allow everything, so the dangerous probes must be blocked and one harmless call allowed, or nothing starts.
5. During a task: stop at once if any model other than the pinned one answers, or the time limit passes.
6. At the end: wait for Hermes's background note review, stop it, read every call's exact cost from
   OpenRouter, and delete the raw code log (it can hold logged-in pages' code).

Hermes keeps its own notes (skills) in its home folder; that folder is persistent on purpose.
"""

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import uuid

import httpx
import yaml

from sonar import budget, llm
from sonar.config import ROOT, HermesSettings
from sonar.navigation.session import DATA_DIR

log = logging.getLogger("sonar.hermes")

PLUGIN_NAME = "sonar_guard"
PLUGIN_SOURCE = ROOT / "hermes_plugins" / PLUGIN_NAME
CODE_CHECK_PATH = ROOT / "src" / "sonar" / "navigation" / "code_check.py"
WORK_DIR = DATA_DIR / "hermes"  # capture file, exec log, server output: deleted or overwritten every run
HEALTH_WAIT_S = 90
QUIET_S = 15  # the agent log must be silent this long before we treat Hermes's background work as finished
MAX_QUIET_WAIT_S = 120
ENV_KEEP = ["SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "PATH", "PATHEXT", "COMSPEC", "TEMP", "TMP", "USERPROFILE",
            "LOCALAPPDATA", "APPDATA", "HOMEDRIVE", "HOMEPATH", "USERNAME", "PROGRAMDATA", "PROGRAMFILES",
            "HOME", "LANG", "LC_ALL", "TMPDIR"]  # the last four are Linux
CALL_RE = re.compile(r"API call #\d+: model=(\S+)")
GEN_RE = re.compile(r"API call #\d+:.*\bid=(gen-\S+)")
TOOL_RE = re.compile(r"[Tt]ool (\w+) (completed|returned error)")

# Probes for the preflight: (tool, arguments, must be blocked). Asked of the guard in a fresh process.
PROBES = r'''
import json, os, sys
sys.path.insert(0, os.environ["SONAR_HERMES_INSTALL"])
import hermes_bootstrap
from hermes_cli import plugins
cases = [
    ("terminal", {"command": "whoami"}, True),
    ("browser_type", {"text": "x"}, True),
    ("browser_exec", {"code": "import os"}, True),
    ("browser_exec", {"code": "print(page_info())"}, False),
]
if os.environ.get("SONAR_LEARN_MODE") == "1":
    cases += [
        ("skill_manage", {"operations": [{"name": "x", "action": "delete"}]}, True),
        ("skill_manage", {"operations": [{"name": "x", "action": "write_file", "file_path": "references/a.md", "file_content": "a"}]}, True),
        ("skill_manage", {"operations": [{"name": "x", "action": "create", "content": "c"}]}, False),
        ("memory", {"action": "add", "target": "memory", "content": "c"}, False),
    ]
out = []
for name, args, must_block in cases:
    msg, _ = plugins._dispatch_pre_tool_call_hooks(name, args)
    out.append({"tool": name, "blocked": bool(msg), "expected_block": must_block})
print(json.dumps(out))
'''


class HermesError(RuntimeError):
    """Hermes could not be started safely, or a task failed or hit a limit."""


def stop_hermes() -> int:
    """Stop every Hermes process and return how many are still running. Matches by program name (hermes, or
    Python running hermes_cli), never by a command line that merely mentions Hermes: that once killed the
    developer's own shell."""
    if os.name == "nt":
        select = ("{ ($_.Name -match '^hermes') -or (($_.Name -match '^pythonw?\\.exe$') -and $_.CommandLine -match "
                  "'hermes_cli' -and $_.CommandLine -notmatch '-c ') }")
        script = (f"$sel = {select}; Get-CimInstance Win32_Process | Where-Object $sel | ForEach-Object "
                  "{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; Start-Sleep 2; "
                  "@(Get-CimInstance Win32_Process | Where-Object $sel).Count")
        out = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True).stdout
        return int(out.strip() or 0)
    subprocess.run(["pkill", "-f", "hermes_cli"], capture_output=True)
    time.sleep(2)
    left = subprocess.run(["pgrep", "-f", "hermes_cli"], capture_output=True, text=True).stdout.split()
    return len(left)


def child_env(cfg: HermesSettings, extra: dict[str, str] | None = None) -> dict[str, str]:
    """The environment Hermes and the code its agent runs get: whitelisted names only, then our own settings.
    Hermes strips only the secret names it knows, so anything not listed here (SMTP and site passwords) must
    never be in it."""
    env = {k: os.environ[k] for k in ENV_KEEP if k in os.environ}
    env.update(
        HERMES_HOME=str(cfg.home_path), PYTHONUTF8="1", SONAR_CODE_CHECK_PATH=str(CODE_CHECK_PATH),
        SONAR_EXEC_LOG=str(WORK_DIR / "exec.jsonl"), SONAR_CAPTURE_FILE=str(WORK_DIR / "capture.txt"),
        SONAR_HERMES_INSTALL=str(cfg.install_path),
    )
    if cfg.learn:
        env["SONAR_LEARN_MODE"] = "1"
    env.update(extra or {})
    return env


def render_config(cfg: HermesSettings, cdp_port: int) -> str:
    """Hermes's config.yaml for a run: our browser over CDP, exec mode, only the toolsets we want, the guard
    plugin, the pinned model. Written fresh every run, so nothing left over can change what Hermes may do."""
    toolsets = ["browser", "terminal"] + (["memory", "skills"] if cfg.learn else [])  # terminal is only the switch that turns browser_exec on
    config = {
        "browser": {"snapshot_threshold": cfg.snapshot_threshold, "cdp_url": f"http://127.0.0.1:{cdp_port}", "backend": "browser-use"},
        "plugins": {"enabled": [PLUGIN_NAME]},
        "platform_toolsets": {"api_server": toolsets},
        "agent": {"max_turns": cfg.max_turns},
        "model": {"default": cfg.model, "provider": cfg.provider},
    }
    return "# Written by Sonar (hermes_runner.py) at the start of every run. Edit config/hermes.yaml instead.\n" + yaml.safe_dump(config)


def prepare_home(cfg: HermesSettings, cdp_port: int) -> None:
    """Put the guard plugin and the run's config into Hermes's home folder (notes and memory there stay)."""
    home = cfg.home_path
    plugin_dir = home / "plugins" / PLUGIN_NAME
    if plugin_dir.exists():
        shutil.rmtree(plugin_dir)
    shutil.copytree(PLUGIN_SOURCE, plugin_dir, ignore=shutil.ignore_patterns("__pycache__"))
    (home / "config.yaml").write_text(render_config(cfg, cdp_port), encoding="utf-8")
    WORK_DIR.mkdir(parents=True, exist_ok=True)


def preflight(cfg: HermesSettings) -> tuple[bool, str]:
    """Free check (no model, no site): the guard must block the dangerous probes and allow the harmless one."""
    result = subprocess.run([sys.executable, "-I", "-c", PROBES], env=child_env(cfg), capture_output=True, text=True, timeout=120)
    try:
        probes = json.loads(result.stdout.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        return False, "the probe did not run: " + (result.stderr or result.stdout)[-300:]
    wrong = [p for p in probes if p["blocked"] != p["expected_block"]]
    if wrong:
        return False, "the guard answered wrongly: " + json.dumps(wrong)
    return True, f"the guard behaved as expected on all {len(probes)} probes"


class HermesServer:
    """One running Hermes server for one site. `start()` does the safe start-up, `ask()` sends a message into
    one conversation (the first message starts it, later ones continue it), `stop()` shuts everything down
    and books the exact cost."""

    def __init__(self, cfg: HermesSettings, cdp_port: int, site_name: str) -> None:
        self.cfg = cfg
        self.cdp_port = cdp_port
        self.site_name = site_name
        self.capture_path = WORK_DIR / "capture.txt"
        self.cost_usd = 0.0
        self._exec_log = WORK_DIR / "exec.jsonl"
        self._agent_log = cfg.home_path / "logs" / "agent.log"
        self._api_key = uuid.uuid4().hex + uuid.uuid4().hex
        self._label = uuid.uuid4().hex[:8]
        self._session_id: str | None = None
        self._process: subprocess.Popen | None = None
        self._log_start = 0
        self._abort: str | None = None

    @property
    def _base_url(self) -> str:
        return f"http://127.0.0.1:{self.cfg.api_port}"

    async def start(self) -> None:
        left = await asyncio.to_thread(stop_hermes)
        if left:
            raise HermesError(f"{left} Hermes process(es) are still running after the stop; not starting")
        if not self.cfg.executable_path.exists():
            raise HermesError(f"Hermes is not installed at {self.cfg.executable_path} (see config/hermes.yaml)")
        await asyncio.to_thread(prepare_home, self.cfg, self.cdp_port)
        self._exec_log.unlink(missing_ok=True)
        self.capture_path.unlink(missing_ok=True)
        ok, why = await asyncio.to_thread(preflight, self.cfg)
        if not ok:
            raise HermesError(f"preflight refused to start Hermes: {why}")
        log.info("site=%s step=hermes_preflight outcome=ok detail=%s", self.site_name, why)
        env = child_env(self.cfg, {
            "OPENROUTER_API_KEY": llm.key_for_hermes(), "API_SERVER_KEY": self._api_key, "API_SERVER_ENABLED": "true",
            "API_SERVER_HOST": "127.0.0.1", "API_SERVER_PORT": str(self.cfg.api_port),
        })
        self._agent_log.parent.mkdir(parents=True, exist_ok=True)
        self._log_start = self._agent_log.stat().st_size if self._agent_log.exists() else 0
        self._process = subprocess.Popen(
            [str(self.cfg.executable_path), "gateway", "run"], env=env, stdout=open(WORK_DIR / "server.out", "w"),
            stderr=subprocess.STDOUT,
        )
        async with httpx.AsyncClient(timeout=3) as client:
            for _ in range(HEALTH_WAIT_S // 2):
                await asyncio.sleep(2)
                try:
                    if (await client.get(f"{self._base_url}/health")).status_code == 200:
                        log.info("site=%s step=hermes_start outcome=ok", self.site_name)
                        return
                except httpx.HTTPError:
                    pass
        await self.stop()
        raise HermesError("the Hermes server did not become ready; see data/hermes/server.out")

    def _new_log(self) -> str:
        if not self._agent_log.exists():
            return ""
        with self._agent_log.open("rb") as fh:
            fh.seek(self._log_start)
            return fh.read().decode("utf-8", "replace")

    async def _watch(self) -> None:
        """Money and safety guard: stop Hermes at the first model call by a model other than the pinned one."""
        seen = self._log_start
        while True:
            await asyncio.sleep(2)
            if not self._agent_log.exists():
                continue
            with self._agent_log.open("rb") as fh:
                fh.seek(seen)
                chunk = fh.read()
            seen += len(chunk)
            wrong = [m for m in CALL_RE.findall(chunk.decode("utf-8", "replace")) if m != self.cfg.model]
            if wrong:
                self._abort = f"model {wrong[0]} answered but {self.cfg.model} is pinned"
                self._kill_process()
                return

    def _kill_process(self) -> None:
        if self._process and self._process.poll() is None:
            self._process.kill()
        stop_hermes()

    async def ask(self, message: str) -> str:
        """Send one message into the conversation and return Hermes's final answer (text)."""
        await budget.current.charge_agent_call()  # raises BudgetExceeded once a run-wide limit is hit
        headers = {"Authorization": f"Bearer {self._api_key}"}
        if self._session_id:
            headers["X-Hermes-Session-Id"] = self._session_id
        else:
            message = f"(task label: {self._label})\n{message}"  # an identical first message would resume an old paused conversation (HTTP 409)
        body = {"model": "hermes-agent", "messages": [{"role": "user", "content": message}], "stream": False}
        watcher = asyncio.create_task(self._watch())
        began = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self.cfg.task_seconds + 30) as client:
                async with asyncio.timeout(self.cfg.task_seconds):
                    reply = await client.post(f"{self._base_url}/v1/chat/completions", headers=headers, json=body)
        except (TimeoutError, httpx.HTTPError) as exc:
            self._kill_process()
            raise HermesError(self._abort or f"the Hermes task failed ({type(exc).__name__}) after {time.monotonic() - began:.0f}s") from exc
        finally:
            watcher.cancel()
        if self._abort:
            raise HermesError(self._abort)
        if reply.status_code != 200:
            raise HermesError(f"Hermes answered HTTP {reply.status_code}: {reply.text[:200]}")
        self._session_id = reply.headers.get("X-Hermes-Session-Id") or self._session_id
        self._check_guard_was_in_force()
        try:
            content = reply.json()["choices"][0]["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise HermesError("Hermes sent a reply that was not in the expected shape") from exc
        log.info("site=%s step=hermes_task seconds=%.0f outcome=ok", self.site_name, time.monotonic() - began)
        return content

    def _check_guard_was_in_force(self) -> None:
        """Second safety check: browser code that ran with no line in the guard's log means the guard was not loaded."""
        ran = sum(1 for tool, status in TOOL_RE.findall(self._new_log()) if tool == "browser_exec" and status == "completed")
        logged = 0
        if self._exec_log.exists():
            logged = sum(1 for line in self._exec_log.read_text(encoding="utf-8").splitlines() if '"allowed"' in line)
        if ran and not logged:
            self._kill_process()
            raise HermesError("browser code ran but the guard logged nothing: the guard was not in force; run stopped")

    async def _settle(self) -> None:
        """Hermes may review the conversation and patch its notes after the answer; stopping it earlier would lose that."""
        last, quiet, waited = -1, 0, 0
        while quiet < QUIET_S and waited < MAX_QUIET_WAIT_S and self._process and self._process.poll() is None:
            await asyncio.sleep(3)
            waited += 3
            size = self._agent_log.stat().st_size if self._agent_log.exists() else 0
            quiet = quiet + 3 if size == last else 0
            last = size

    async def _book_cost(self) -> None:
        ids = GEN_RE.findall(self._new_log())
        costs = [await llm.generation_cost_usd(i) for i in ids]
        self.cost_usd = sum(c for c in costs if c is not None)
        unpriced = sum(1 for c in costs if c is None)
        budget.current.reported_cost_usd += self.cost_usd
        log.info("site=%s step=hermes_cost calls=%d unpriced=%d cost_usd=%.4f", self.site_name, len(ids), unpriced, self.cost_usd)

    async def stop(self) -> None:
        started = self._process is not None
        try:
            if started and self._process.poll() is None and not self._abort:
                await self._settle()
        finally:
            await asyncio.to_thread(self._kill_process)
            self._exec_log.unlink(missing_ok=True)  # raw code from a possibly logged-in session: never left on disk
            self.capture_path.unlink(missing_ok=True)
        if started:
            await self._book_cost()
            self._process = None
