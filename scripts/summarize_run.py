"""Read the matches of a past run with the agent: summary, how to apply, closing date.
By default skips listings already read in the current format; --redo reads them all again.
Uses your Claude login. Only updates the JSON `fields` of existing listings; the schema is never changed.
Run:  .venv\Scripts\python.exe scripts/summarize_run.py RUN_ID [--redo]
"""

import argparse
import asyncio

from sonar.config import load_settings
from sonar.db.tables import make_engine
from sonar.summarize.summaries import summarize_matches

parser = argparse.ArgumentParser()
parser.add_argument("run_id", type=int)
parser.add_argument("--redo", action="store_true", help="read postings again even if already read")
args = parser.parse_args()

settings = load_settings()
engine = make_engine(settings.database_url)
count = asyncio.run(summarize_matches(engine, args.run_id, settings.matcher, workers=settings.summary_workers, redo=args.redo))
print(f"read {count} postings")
