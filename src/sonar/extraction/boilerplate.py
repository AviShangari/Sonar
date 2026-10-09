"""Generic removal of site-wide boilerplate (banners, menus, labels, footers) from posting text.

No per-site rules: a line that appears in nearly all of a site's stored postings is not about
any one posting. The stored `posting_text` is never changed (raw data is kept); the cleaned
text is computed when it is read, for example just before matching.
"""

MIN_POSTINGS = 5  # with fewer postings we cannot tell boilerplate from content, so strip nothing
MIN_SHARE = 0.9  # a line is boilerplate if it appears in at least this share of postings


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def find_boilerplate(texts: list[str], min_share: float = MIN_SHARE, min_postings: int = MIN_POSTINGS) -> set[str]:
    """Lines present in at least `min_share` of the postings (each posting counts a line once)."""
    if len(texts) < min_postings:
        return set()
    counts: dict[str, int] = {}
    for text in texts:
        for line in set(_lines(text)):
            counts[line] = counts.get(line, 0) + 1
    needed = min_share * len(texts)
    return {line for line, count in counts.items() if count >= needed}


def strip_boilerplate(text: str, boilerplate: set[str]) -> str:
    """The posting's lines without the boilerplate ones, whitespace-trimmed, one per line."""
    return "\n".join(line for line in _lines(text) if line not in boilerplate)
