"""Sonar in Microsoft Teams: the bot (bot.py), its start-up (__main__.py) and its app package (package.py)."""

import warnings

# The Teams SDK prints "preview feature" notices on import for parts Sonar does not use
warnings.filterwarnings("ignore", module=r"microsoft_teams\.")
