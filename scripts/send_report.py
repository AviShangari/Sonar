"""Email the report of a past run (to test the email settings), without running the pipeline.
Needs SONAR_SMTP_PASSWORD in .env. Works even when `email: false` in settings.
--dry-run prints the plain-text part of the email instead of sending it (no password or network needed).
Run:  .venv\Scripts\python.exe scripts/send_report.py RUN_ID [--dry-run]
"""

import argparse

from sonar.config import load_settings
from sonar.db.tables import make_engine
from sonar.report.build import build_report
from sonar.report.send import build_message, send_report
from sonar.sites import note_sites, site_accounts

parser = argparse.ArgumentParser()
parser.add_argument("run_id", type=int)
parser.add_argument("--dry-run", action="store_true", help="print the email, do not send it")
args = parser.parse_args()

settings = load_settings()
report = build_report(make_engine(settings.database_url), args.run_id, settings.matcher, settings.tz, note_sites(), site_accounts())
if args.dry_run:
    message = build_message(report, settings)
    print(f"To: {message['To']}\nSubject: {message['Subject']}\n\n{message.get_body(('plain',)).get_content()}")
else:
    send_report(report, settings)
    print(f"sent to {settings.email_to}")
