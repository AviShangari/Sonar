"""Jev matcher (TypeSafe's decision model, reached through OpenRouter).

One yes/no ("noul") question per posting: is this work the client would bid on? Jev answers with the
probability of yes. At or above the match threshold the listing is a match; from the review threshold up to
the match threshold it is a match flagged "needs review"; below the review threshold it is not a match.

The client's "what we do" text goes in the question's instructions and criteria (config/company.yaml), never
in the posting data. If Jev cannot answer, the result is *unscored*, never a non-match: the next run tries again.
The matcher never raises for a service problem. The API key is sent only as a header and is never logged.
"""

import logging
import time
from pathlib import Path

import httpx
import yaml
from pydantic import BaseModel

from sonar.models import MatchResult
from sonar.secrets import openrouter_api_key

log = logging.getLogger("sonar.matching")

DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
QUESTION = "relevant"
TIMEOUT_SECONDS = 30.0
RETRIES = 2  # extra tries after a timeout, a 429 or a 5xx
BACKOFF_SECONDS = (1.0, 3.0)
BREAKER_AFTER = 5  # this many postings in a row that Jev could not score: stop calling it for the rest of the run


class Company(BaseModel):
    what_we_do: str
    question: str
    yes_means: str
    no_means: str


def load_company(path: Path) -> Company:
    values = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    values["what_we_do"] = (values.get("what_we_do") or "").strip()
    company = Company(**values)
    if not company.what_we_do:
        raise ValueError(f"{path.name} has no 'what_we_do' text yet: write what the company does before using the Jev matcher")
    return company


class JevError(Exception):
    """Jev could not answer this posting. The message is safe to log and store."""

    def __init__(self, message: str, retry: bool = False) -> None:
        super().__init__(message)
        self.retry = retry


class JevMatcher:
    def __init__(
        self, company: Company, api_key: str, model: str = "typesafe/jev-1.13", match_threshold: float = 0.6,
        review_threshold: float = 0.3, max_chars: int = 12000, client: httpx.Client | None = None,
        sleep=time.sleep,
    ) -> None:
        if not 0 <= review_threshold < match_threshold <= 1:
            raise ValueError("thresholds must satisfy 0 <= review < match <= 1")
        self._company, self._model = company, model
        self._match, self._review, self._max_chars = match_threshold, review_threshold, max_chars
        self._client = client or httpx.Client(timeout=TIMEOUT_SECONDS)
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        self._sleep = sleep
        self._failures_in_a_row = 0
        self.total_cost_usd = 0.0

    def request_body(self, text: str) -> dict:
        """The request for one posting. The posting is the only thing in `state`."""
        return {
            "model": self._model,
            "state": {"posting_text": text[: self._max_chars]},
            "questions": {
                QUESTION: {
                    "type": "noul",
                    "instructions": f"{self._company.what_we_do}\n\n{self._company.question}",
                    "criteria": {"true": self._company.yes_means, "false": self._company.no_means},
                }
            },
        }

    def match(self, text: str) -> MatchResult:
        if self._failures_in_a_row >= BREAKER_AFTER:
            return MatchResult(unscored=True, error=f"Jev skipped: {BREAKER_AFTER} postings in a row could not be scored")
        try:
            probability, cost = self._ask(text)
        except JevError as exc:
            self._failures_in_a_row += 1
            if self._failures_in_a_row == BREAKER_AFTER:
                log.error("step=jev outcome=giving_up after=%d last_error=%s", BREAKER_AFTER, exc)
            else:
                log.warning("step=jev outcome=unscored error=%s", exc)
            return MatchResult(unscored=True, error=str(exc))
        self._failures_in_a_row = 0
        self.total_cost_usd += cost or 0.0
        return self.decide(probability, cost)

    def decide(self, probability: float, cost: float | None = None) -> MatchResult:
        if probability >= self._match:
            return MatchResult(score=probability, verdict=True, cost_usd=cost)
        if probability >= self._review:
            return MatchResult(score=probability, verdict=True, needs_review=True, cost_usd=cost)
        return MatchResult(score=probability, verdict=False, cost_usd=cost)

    def _ask(self, text: str) -> tuple[float, float | None]:
        last: JevError | None = None
        for attempt in range(RETRIES + 1):
            if attempt:
                self._sleep(BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)])
            try:
                return self._once(text)
            except JevError as exc:
                last = exc
                if not exc.retry:
                    break
        raise last  # type: ignore[misc]

    def _once(self, text: str) -> tuple[float, float | None]:
        try:
            response = self._client.post(DECISIONS_URL, json=self.request_body(text), headers=self._headers)
        except httpx.TimeoutException:
            raise JevError("timed out", retry=True) from None
        except httpx.HTTPError as exc:  # httpx errors carry the URL, not the headers, so the key cannot leak here
            raise JevError(f"network error ({type(exc).__name__})", retry=True) from None
        if response.status_code == 429 or response.status_code >= 500:
            raise JevError(f"HTTP {response.status_code}", retry=True)
        if response.status_code != 200:
            raise JevError(f"HTTP {response.status_code}: {_error_message(response)}")
        try:
            body = response.json()
            answer = body["answers"][QUESTION]
            if answer.get("type") != "noul":
                raise ValueError("answer is not a yes/no")
            probability = float(answer["noul"])
            if not 0.0 <= probability <= 1.0:
                raise ValueError("probability outside 0 to 1")
            usage = body.get("usage") or {}
            cost = float(usage["cost"]) if usage.get("cost") is not None else None
        except (ValueError, KeyError, TypeError, AttributeError):
            raise JevError("the reply was not in the expected format") from None
        return probability, cost


def _error_message(response: httpx.Response) -> str:
    try:
        message = response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return "no message"
    return str(message)[:200]


def build_jev_matcher(settings) -> JevMatcher:
    """From settings.yaml and company.yaml. Raises at startup when the company text or the key is missing."""
    return JevMatcher(
        load_company(settings.company_path), openrouter_api_key(), settings.jev_model, settings.jev_match_threshold,
        settings.jev_review_threshold, settings.jev_max_chars,
    )
