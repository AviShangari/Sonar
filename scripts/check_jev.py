"""Paid trial of the Jev matcher on stored postings. Writes NOTHING to the database or to any report.

    uv run python scripts/check_jev.py --dry-run            # shows what would be sent; no call, no cost
    uv run python scripts/check_jev.py --limit 40           # real calls through OpenRouter (costs money; ask the developer first)

The sample is every posting the keyword matcher matched (up to the limit) plus a random fill of the rest
(fixed seed, so a repeat run asks about the same postings). Postings are cleaned the same way as in a real
run (site-wide boilerplate removed). Needs OPENROUTER_API_KEY in .env and `what_we_do` in config/company.yaml.
"""

import argparse
import json
import random

from sqlalchemy import select
from sqlalchemy.orm import Session

from sonar.config import load_settings
from sonar.db.tables import DecisionRow, ListingRow, make_engine
from sonar.extraction.boilerplate import find_boilerplate, strip_boilerplate
from sonar.matching.jev import build_jev_matcher, load_company

SEED = 20261015


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=40, help="how many postings to ask Jev about (default 40)")
    parser.add_argument("--dry-run", action="store_true", help="show the first request and the sample; make no call")
    args = parser.parse_args()

    settings = load_settings()
    engine = make_engine(settings.database_url)
    with Session(engine) as session:
        listings = list(session.scalars(select(ListingRow).order_by(ListingRow.id)))
        keyword_yes = set(session.scalars(select(DecisionRow.listing_id).where(DecisionRow.matcher == "keywords", DecisionRow.matched.is_(True))))
    texts_by_site: dict[str, list[str]] = {}
    for listing in listings:
        texts_by_site.setdefault(listing.site, []).append(listing.posting_text)
    boilerplate = {site: find_boilerplate(texts) for site, texts in texts_by_site.items()}

    flagged = [x for x in listings if x.id in keyword_yes][: args.limit]
    rest = [x for x in listings if x.id not in keyword_yes]
    random.Random(SEED).shuffle(rest)
    sample = flagged + rest[: max(0, args.limit - len(flagged))]
    print(f"{len(listings)} stored postings; sample of {len(sample)} ({len(flagged)} keyword matches + {len(sample) - len(flagged)} others)")

    if args.dry_run:
        from sonar.matching.jev import JevMatcher

        matcher = JevMatcher(load_company(settings.company_path), "dry-run", settings.jev_model)
        first = strip_boilerplate(sample[0].posting_text, boilerplate[sample[0].site])
        body = matcher.request_body(first)
        sent = len(body["state"]["posting_text"])
        body["state"]["posting_text"] = body["state"]["posting_text"][:300] + f" ...[{sent} characters are sent; cut here for display]"
        print(json.dumps(body, indent=2))
        print("dry run: no call was made")
        return

    matcher = build_jev_matcher(settings)
    print(f"model {settings.jev_model}; match >= {settings.jev_match_threshold}, review >= {settings.jev_review_threshold}")
    print(f"{'jev':>5}  {'band':<8} {'keywords':<8}  title")
    counts = {"match": 0, "review": 0, "no": 0, "unscored": 0}
    for listing in sample:
        result = matcher.match(strip_boilerplate(listing.posting_text, boilerplate[listing.site]))
        band = "unscored" if result.unscored else "review" if result.needs_review else "match" if result.matched else "no"
        counts[band] += 1
        score = "  -  " if result.score is None else f"{result.score:5.2f}"
        print(f"{score}  {band:<8} {'yes' if listing.id in keyword_yes else 'no':<8}  {(listing.title or listing.url)[:90]}")
        if result.error:
            print(f"       error: {result.error}")
    print(f"\n{counts}")
    print(f"Jev cost reported by the service: ${matcher.total_cost_usd:.6f} for {len(sample)} postings")


if __name__ == "__main__":
    main()
