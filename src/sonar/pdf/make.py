"""The one entry point: a PdfSpec in, a PDF file out. Used by the assistant's make_pdf tool; also runnable on its own:

    uv run python -m sonar.pdf.make --run 5 --preset brief

The file name is built here from the run number, the time and a random part, never from anything a person or a
posting wrote, so a request cannot choose where a file goes."""

import argparse
import asyncio
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy.engine import Engine

from sonar.config import Settings, load_settings
from sonar.pdf.data import collect
from sonar.pdf.render import html_to_pdf, render_html
from sonar.pdf.spec import PdfSpec, load_presets, resolve


class PdfError(RuntimeError):
    """The PDF could not be made (the browser failed or timed out). The message is safe to show."""


@dataclass(frozen=True)
class PdfResult:
    path: Path
    run_id: int
    matches: int
    near_misses: int
    size_bytes: int


async def make_pdf(
    engine: Engine, settings: Settings, spec: PdfSpec, note_sites: frozenset[str] = frozenset(),
    accounts: dict[str, str] | None = None,
) -> PdfResult:
    """Raises ValueError (no such run, nothing recorded yet) or PdfError."""
    data = collect(engine, settings, spec, note_sites, accounts)
    html = render_html(data, spec)
    run_id = data.report.run_id
    try:
        pdf = await html_to_pdf(html, spec, run_id)
    except Exception as exc:  # the browser is missing, crashed or timed out
        raise PdfError(f"the PDF could not be made ({type(exc).__name__}); is Chromium installed (uv run playwright install chromium)?") from exc
    settings.pdf_path.mkdir(parents=True, exist_ok=True)
    path = settings.pdf_path / f"sonar-run-{run_id}-{datetime.now(settings.tz):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}.pdf"
    path.write_bytes(pdf)
    shown_matches = len(data.report.matches) if "matches" in spec.sections else 0
    shown_near_misses = len(data.near_misses) if "near_misses" in spec.sections else 0
    return PdfResult(path, run_id, shown_matches, shown_near_misses, len(pdf))


def main() -> int:
    parser = argparse.ArgumentParser(description="Make a PDF of a run from the stored data.")
    parser.add_argument("--run", type=int, help="run number (default: the latest)")
    parser.add_argument("--preset", help="a preset from config/pdf.yaml (default: standard)")
    args = parser.parse_args()
    from sonar.db.tables import make_engine
    from sonar.sites import note_sites, site_accounts

    settings = load_settings()
    try:
        spec = resolve({"run_id": args.run} if args.run else {}, args.preset or "standard", load_presets())
        result = asyncio.run(make_pdf(make_engine(settings.database_url), settings, spec, note_sites(), site_accounts()))
    except (ValueError, PdfError) as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"Wrote {result.path}  ({result.matches} matches, {result.near_misses} near misses, {result.size_bytes // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
