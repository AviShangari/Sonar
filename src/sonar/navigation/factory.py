"""Picks the navigator from config/settings.yaml. The only place that knows the modes by name."""

from collections.abc import Callable
from datetime import date

from playwright.async_api import BrowserContext

from sonar.config import load_hermes_settings
from sonar.navigation.base import Navigator
from sonar.navigation.dom import DomNavigator, agent_collector
from sonar.navigation.guards import GuardState
from sonar.navigation.hermes import HermesCollector
from sonar.navigation.screenshots import FailureShots
from sonar.sites import SiteConfig


def build_navigator(
    mode: str, site: SiteConfig, context: BrowserContext, port: int, is_seen: Callable[[str], bool], cutoff: date,
    shots: FailureShots | None = None, guard: GuardState | None = None,
) -> Navigator:
    if mode == "dom":
        return DomNavigator(site, context, agent_collector(port), is_seen, cutoff, shots, guard)
    if mode == "hermes":
        # Same navigator (stop rule, dedup, opening postings); only the list pages are read by Hermes.
        return DomNavigator(site, context, HermesCollector(load_hermes_settings(), port, context), is_seen, cutoff, shots, guard)
    raise NotImplementedError(f"navigator '{mode}' is not built yet")
