"""What a PDF should contain and how it should look: one validated description (PdfSpec).

The assistant fills it in from what a person asks for ("only CanadaBuys, sorted by closing date, landscape, bigger
text"). Every field has a limit, an unknown field is an error, and nothing in it is code: the base template
(templates/run_pdf.html) is ours, and a person can change a PDF only through these fields and `custom_css`.
Named starting points (presets) live in config/pdf.yaml, where a developer can add or change them."""

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from sonar.assistant.safe import clean
from sonar.config import ROOT
from sonar.pdf.css import check_css

PRESETS_PATH = ROOT / "config" / "pdf.yaml"
Section = Literal["overview", "matches", "near_misses"]


class PdfSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")  # an option we did not define is an error, not ignored

    run_id: int | None = None  # None = the latest run
    title: str | None = Field(default=None, max_length=120)
    subtitle: str | None = Field(default=None, max_length=200)
    sections: list[Section] = ["overview", "matches", "near_misses"]  # in page order
    layout: Literal["cards", "table"] = "cards"  # how the matches are shown
    sites: list[str] = Field(default=[], max_length=10)  # only these portals' matches and near misses; empty = all
    keyword: str | None = Field(default=None, max_length=80)  # only matches whose keywords, title or summary hold this
    sort: Literal["rank", "closing", "site", "title"] = "rank"
    max_matches: int = Field(default=100, ge=1, le=300)
    max_near_misses: int = Field(default=25, ge=0, le=100)
    show_summaries: bool = True
    show_keyword_hits: bool = True
    orientation: Literal["portrait", "landscape"] = "portrait"
    page_size: Literal["Letter", "A4"] = "Letter"
    font_size_pt: float = Field(default=10, ge=8, le=14)
    accent_color: str = Field(default="#0B6E79", pattern=r"^#[0-9A-Fa-f]{6}$")
    custom_css: str | None = None  # extra styling, checked by css.check_css

    @field_validator("title", "subtitle", "keyword")
    @classmethod
    def _one_clean_line(cls, value: str | None) -> str | None:
        return clean(value, 200) or None if value else None

    @field_validator("sites")
    @classmethod
    def _clean_sites(cls, value: list[str]) -> list[str]:
        return [name for name in (clean(v, 40).lower() for v in value) if name]

    @field_validator("sections")
    @classmethod
    def _sections_once(cls, value: list[Section]) -> list[Section]:
        unique = list(dict.fromkeys(value))
        if not unique:
            raise ValueError("sections may not be empty")
        return unique

    @field_validator("custom_css")
    @classmethod
    def _safe_css(cls, value: str | None) -> str | None:
        return check_css(value) or None if value else None


class Preset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str
    spec: dict[str, Any] = {}


def load_presets(path: Path = PRESETS_PATH) -> dict[str, Preset]:
    """The named presets of config/pdf.yaml. Each one's options are checked now, so a typo fails at start-up."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    presets = {name: Preset(**value) for name, value in (raw.get("presets") or {}).items()}
    for name, preset in presets.items():
        try:
            PdfSpec(**preset.spec)
        except ValueError as exc:
            raise ValueError(f"preset '{name}' in {path.name} is not valid: {exc}") from exc
    return presets


def resolve(explicit: dict[str, Any], preset: str | None, presets: dict[str, Preset]) -> PdfSpec:
    """A preset's options with the person's own options on top. Raises ValueError for an unknown preset."""
    if preset is not None and preset not in presets:
        raise ValueError(f"there is no preset named '{clean(preset, 40)}'; the presets are: {', '.join(presets)}")
    base = presets[preset].spec if preset else {}
    return PdfSpec(**{**base, **explicit})
