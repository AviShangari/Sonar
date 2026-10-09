"""Picks the navigator from config/settings.yaml. The only place that knows the modes by name."""

from collections.abc import Callable
from datetime import date

from playwright.async_api import BrowserContext

from sonar.navigation.base import Navigator
from sonar.navigation.dom import DomNavigator, agent_collector
from sonar.sites import SiteConfig


def build_navigator(
    mode: str, site: SiteConfig, context: BrowserContext, port: int, is_seen: Callable[[str], bool], cutoff: date
) -> Navigator:
    if mode == "dom":
        return DomNavigator(site, context, agent_collector(port), is_seen, cutoff)
    raise NotImplementedError(f"navigator '{mode}' is not built yet")
