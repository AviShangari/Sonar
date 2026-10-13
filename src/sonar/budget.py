"""Cost caps for one run. Plain counting code; no agent decides anything here.

Four limits (all in config/settings.yaml, None = no limit):
  max_agent_calls   every agent call in the run (list-page reads and summaries)   -> stops all further agent work
  max_run_usd       dollars spent by the OpenRouter key since the run began       -> stops all further agent work
  max_new_per_site  new postings stored per site                                  -> stops that site only
  max_summaries     summaries written per run                                     -> the rest get no summary

Hitting a limit never crashes the run. It is recorded with note_hit(); main then ends the run as
"partial" and the email says why. A site that was cut short does not move its watermark, so the next
run re-reads what was missed. Counters live in memory for one run only.
"""

import asyncio
import logging
from dataclasses import dataclass, field

from sonar import llm
from sonar.config import Settings

log = logging.getLogger("sonar.budget")


class BudgetExceeded(RuntimeError):
    """A run-wide limit was reached; no more agent calls may be made."""


@dataclass
class RunBudget:
    max_agent_calls: int | None = None
    max_new_per_site: int | None = None
    max_summaries: int | None = None
    max_run_usd: float | None = None
    max_call_usd: float | None = None
    agent_calls: int = 0
    sdk_cost_usd: float = 0.0  # the SDK's own figure, summed; only for comparing with OpenRouter's bill
    start_usage: float | None = None  # the key's lifetime spend when the run began
    exhausted: bool = False  # a run-wide limit was hit: no more agent calls
    hits: dict[str, str] = field(default_factory=dict)  # key -> sentence for the email (a key is noted once)

    @classmethod
    def from_settings(cls, s: Settings) -> "RunBudget":
        return cls(s.max_agent_calls, s.max_new_per_site, s.max_summaries, s.max_run_usd, s.max_call_usd)

    def note_hit(self, key: str, message: str, stop_all: bool = False) -> None:
        if key not in self.hits:
            log.warning("step=budget outcome=limit_hit key=%s detail=%s", key, message)
            self.hits[key] = message
        self.exhausted = self.exhausted or stop_all

    async def begin(self) -> None:
        self.start_usage = await llm.openrouter_usage_usd()

    async def spent_usd(self) -> float | None:
        """Dollars this run has cost according to OpenRouter, or None when unknown."""
        usage = await llm.openrouter_usage_usd()
        return None if usage is None or self.start_usage is None else max(0.0, usage - self.start_usage)

    async def final_spent_usd(self, wait_s: float = 5.0, steady: int = 3, max_wait_s: float = 90.0) -> float | None:
        """Like spent_usd, but for the end of the run: OpenRouter updates a key's spend with a delay of
        10 seconds or more, so wait until `steady` readings in a row agree (or `max_wait_s` passes)."""
        if self.start_usage is None:
            return None
        last, same, waited = None, 1, 0.0
        while True:
            now = await self.spent_usd()
            if now is None:
                return None
            same = same + 1 if now == last else 1
            if same >= steady or waited >= max_wait_s:
                return now
            last = now
            await asyncio.sleep(wait_s)
            waited += wait_s

    async def charge_agent_call(self) -> None:
        """Called before EVERY agent call. Raises BudgetExceeded when a run-wide limit is reached."""
        if self.exhausted:
            raise BudgetExceeded("a cost limit was already reached")
        if self.max_agent_calls is not None and self.agent_calls >= self.max_agent_calls:
            self.note_hit("agent_calls", f"limit of {self.max_agent_calls} agent calls reached", stop_all=True)
            raise BudgetExceeded(self.hits["agent_calls"])
        if self.max_run_usd is not None and (spent := await self.spent_usd()) is not None and spent >= self.max_run_usd:
            self.note_hit("run_usd", f"limit of ${self.max_run_usd:.2f} for the run reached (spent ${spent:.2f})", stop_all=True)
            raise BudgetExceeded(self.hits["run_usd"])
        self.agent_calls += 1

    def summary_line(self, spent: float | None) -> str:
        real = f"${spent:.4f}" if spent is not None else "unknown"
        return f"cost: OpenRouter {real}, SDK-reported ${self.sdk_cost_usd:.4f}, {self.agent_calls} agent calls"

    def notes(self) -> list[str]:
        return [f"COST LIMIT: {m}" for m in self.hits.values()]


current = RunBudget()  # no limits until main calls configure(); tests never hit a cap by accident


def configure(budget: RunBudget) -> None:
    global current
    current = budget
