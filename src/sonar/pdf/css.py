"""The check for extra styling that a person asks for ("make the headings green and the text bigger").

The CSS is appended after the base template's own, so it can change how a PDF looks and nothing else. It is a
second line of defence: the PDF is also made in a browser that has JavaScript off and refuses every network
request, so even CSS that slipped through could not fetch or send anything. Rejected CSS comes back to the
model as a short error, so it can try again without the forbidden part."""

import re

MAX_CSS_CHARS = 4000
ALLOWED_AT_RULES = {"media", "page"}
FORBIDDEN = {  # text that has no place in styling for a printed page
    "url(": "links to files or addresses",
    "image-set": "links to images",
    "expression": "scripts",
    "javascript": "scripts",
    "behavior": "scripts",
    "binding": "scripts",
    "<": "HTML",
    "\\": "escape sequences",
}


def check_css(css: str) -> str:
    """The CSS, stripped, or ValueError saying what is not allowed."""
    text = css.strip()
    if len(text) > MAX_CSS_CHARS:
        raise ValueError(f"custom_css is longer than {MAX_CSS_CHARS} characters")
    if any(ord(c) < 32 and c not in "\n\t\r" for c in text):
        raise ValueError("custom_css may not contain control characters")
    without_comments = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL).lower()
    for needle, what in FORBIDDEN.items():
        if needle in without_comments or needle in text.lower():
            raise ValueError(f"custom_css may not contain '{needle}' ({what})")
    for rule in re.findall(r"@([a-z-]+)", without_comments):
        if rule not in ALLOWED_AT_RULES:
            raise ValueError(f"custom_css may not use @{rule} (only @media and @page are allowed)")
    return text
