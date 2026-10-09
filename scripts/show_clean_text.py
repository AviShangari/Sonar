"""Show what boilerplate stripping does to the stored postings of a site. Read-only.
Run:  $env:PYTHONPATH="src"; .venv\\Scripts\\python.exe scripts/show_clean_text.py [site-name] [job-id]
"""

import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from sonar.db.queries import site_posting_texts
from sonar.db.tables import ListingRow, make_engine
from sonar.extraction.boilerplate import find_boilerplate, strip_boilerplate


def main(site: str, job_id: str | None) -> None:
    engine = make_engine()
    texts = site_posting_texts(engine, site)
    boilerplate = find_boilerplate(texts)
    print(f"{len(texts)} postings, {len(boilerplate)} boilerplate lines")
    with Session(engine) as session:
        query = select(ListingRow).where(ListingRow.site == site)
        if job_id:
            query = query.where(ListingRow.job_id == job_id)
        row = session.scalars(query).first()
        before, after = row.posting_text, strip_boilerplate(row.posting_text, boilerplate)
    print(f"\n{row.job_id}: {len(before)} chars -> {len(after)} chars\n\n--- cleaned text ---\n{after}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "canadabuys", sys.argv[2] if len(sys.argv) > 2 else None)
