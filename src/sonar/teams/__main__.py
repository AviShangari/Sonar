"""Start the Teams bot:  uv run python -m sonar.teams

It listens on config/teams.yaml host:port at /api/messages. On this computer Teams reaches it through a Dev
Tunnel (see README). Read-only like the terminal assistant: it never starts a run. Each question calls the
model (OpenRouter, `assistant_model`), which costs a little. Stop it with Ctrl-C."""

import asyncio
import logging
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from sonar import llm, secrets
from sonar.assistant.agent import TEAMS_FORMAT, Assistant
from sonar.assistant.tools import AssistantTools, read_only_engine
from sonar.config import load_settings, load_teams_settings
from sonar.db.tables import ListingRow
from sonar.sites import site_accounts
from sonar.teams.bot import SonarBot, build_app


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    settings, teams = load_settings(), load_teams_settings()
    try:
        llm.configure(settings)  # stops here, with a clear message, if OPENROUTER_API_KEY is missing
        app_id, app_password, tenant_id = secrets.teams_credentials()
        engine = read_only_engine(settings.database_url)
    except (RuntimeError, ValueError, FileNotFoundError) as exc:
        print(exc, file=sys.stderr)
        return 1
    with Session(engine) as session:
        sites = list(session.scalars(select(ListingRow.site).distinct().order_by(ListingRow.site)))
    tools = AssistantTools(engine, settings, site_accounts())
    bot = SonarBot(teams, tenant_id, lambda: Assistant(tools, sites, format_rule=TEAMS_FORMAT))
    app = build_app(teams, app_id, app_password, tenant_id, bot)
    print(f"Sonar's Teams bot is listening on http://{teams.host}:{teams.port}/api/messages  (Ctrl-C to stop)")
    try:
        asyncio.run(app.start(teams.port))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
