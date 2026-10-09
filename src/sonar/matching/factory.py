"""Picks the matcher from config/settings.yaml. The only place that knows the matchers by name."""

from sonar.config import Settings
from sonar.matching.base import Matcher
from sonar.matching.keywords import KeywordMatcher, load_keywords


def build_matcher(settings: Settings) -> Matcher:
    if settings.matcher == "keywords":
        return KeywordMatcher(load_keywords(settings.keywords_path))
    raise NotImplementedError(f"matcher '{settings.matcher}' is not built yet")
