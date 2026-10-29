"""Base template to HTML (Jinja2), then HTML to PDF in a headless Chromium that can do nothing but print.

Everything from a posting is untrusted text: the template escapes it and only emits http(s) links. The browser that
prints the page has JavaScript off and refuses every network request, so a page that held a script, a picture or a
style sheet from the web could not fetch or send anything. It is a separate throwaway browser (no profile, no
logins), not the dedicated one the scans use."""

import asyncio
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.async_api import Route, async_playwright

from sonar.pdf.data import PdfData
from sonar.pdf.spec import PdfSpec
from sonar.report.render import highlight, partial_note, portal_name, safe_url

TEMPLATES = Path(__file__).resolve().parent / "templates"
BASE_TEMPLATE = "run_pdf.html"
RENDER_TIMEOUT_S = 60
MARGIN = {"top": "16mm", "bottom": "18mm", "left": "14mm", "right": "14mm"}
FOOTER = (  # Chromium fills in the page numbers; only our own numbers go in here
    '<div style="width:100%;font-size:8px;font-family:Arial,sans-serif;color:#7b8794;text-align:center;">'
    "Sonar &middot; run {run_id} &middot; page <span class=\"pageNumber\"></span> of <span class=\"totalPages\"></span></div>"
)


def _environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]), trim_blocks=True, lstrip_blocks=True
    )
    env.filters.update(highlight=highlight, safe_url=safe_url, portal_name=portal_name)
    env.globals["partial_note"] = partial_note
    return env


def render_html(data: PdfData, spec: PdfSpec) -> str:
    return _environment().get_template(BASE_TEMPLATE).render(data=data, spec=spec)


async def _refuse(route: Route) -> None:
    await route.abort()


async def html_to_pdf(html: str, spec: PdfSpec, run_id: int) -> bytes:
    """Print the page to a PDF. Nothing is fetched from anywhere while doing so."""
    async with asyncio.timeout(RENDER_TIMEOUT_S):
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context(java_script_enabled=False, service_workers="block")
                await context.route("**/*", _refuse)
                page = await context.new_page()
                await page.set_content(html, wait_until="load")
                return await page.pdf(
                    format=spec.page_size, landscape=spec.orientation == "landscape", print_background=True,
                    display_header_footer=True, header_template="<div></div>",
                    footer_template=FOOTER.format(run_id=int(run_id)), margin=MARGIN,
                )
            finally:
                await browser.close()
