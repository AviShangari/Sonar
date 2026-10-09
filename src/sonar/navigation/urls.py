"""URL normalization and job IDs, with generic rules that work on any site."""

import re
from urllib.parse import parse_qs, urljoin, urlparse

ID_PARAMS = ("id", "noticeid", "tenderid", "jobid", "opportunityid", "bidid", "rfpid")


def normalize_url(url: str, base: str | None = None) -> str:
    """Resolve relative links, drop the query string and fragment, lowercase the host,
    and strip a trailing slash."""
    parsed = urlparse(urljoin(base, url) if base else url)
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.scheme}://{parsed.netloc.lower()}{path}"


def job_id_from_url(url: str) -> str | None:
    """Best guess at the posting's ID from its address, or None if the URL has none.

    Tries an ID-like query parameter first, then the last path segment if it contains a
    digit (so /tender-notice/ws1234567 gives "ws1234567" but /tender-opportunities does not).
    None means the caller falls back to title + client + posted date.
    """
    parsed = urlparse(url)
    params = {key.lower(): values for key, values in parse_qs(parsed.query).items()}
    for name in ID_PARAMS:
        if params.get(name):
            return params[name][0]
    last = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    if last and re.search(r"\d", last):
        return last
    return None
