"""Does a stored posting link to downloadable documents? Plain code on the saved HTML, never an agent.

A link counts when its address ends in a document file type (pdf, Word, Excel, PowerPoint, zip ...).
Sites that hide their documents behind an account step (SAP Ariba) cannot be detected this way; those
carry `attachment_note: true` in config/sites.yaml instead."""

import re

DOCUMENT_LINK_RE = re.compile(
    r"""href\s*=\s*["'][^"']*?\.(?:pdf|docx?|xlsx?|pptx?|zip|rar|7z|csv)(?:[?#][^"']*)?["']""",
    re.IGNORECASE,
)


def has_document_links(html: str | None) -> bool:
    return bool(html and DOCUMENT_LINK_RE.search(html))
