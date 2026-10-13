"""The ONLY place secrets are read. They come from environment variables or the gitignored .env file at the repo root."""

import os
from pathlib import Path

from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"
SMTP_PASSWORD_VAR = "SONAR_SMTP_PASSWORD"
OPENROUTER_KEY_VAR = "OPENROUTER_API_KEY"


def _secret(name: str, needed_for: str) -> str:
    """A real environment variable wins over .env."""
    load_dotenv(ENV_FILE, override=False)
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set; put it in .env or set it in the environment before {needed_for}.")
    return value


def smtp_password() -> str:
    """The password (or app password) for the sending mailbox."""
    return _secret(SMTP_PASSWORD_VAR, "sending email")


def openrouter_api_key() -> str:
    """The OpenRouter API key used when `auth: openrouter`."""
    return _secret(OPENROUTER_KEY_VAR, "running with auth: openrouter")
