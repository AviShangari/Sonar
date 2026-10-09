"""Write the report for a past run again (HTML and text), without running the pipeline.
Run:  $env:PYTHONPATH="src"; .venv\Scripts\python.exe scripts/make_report.py RUN_ID
"""

import sys

from sonar.config import load_settings
from sonar.db.tables import make_engine
from sonar.report.build import build_report
from sonar.report.render import write_report

settings = load_settings()
report = build_report(make_engine(settings.database_url), int(sys.argv[1]), settings.matcher)
for path in write_report(report, settings.report_path):
    print(path)
